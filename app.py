"""RunTime Agent: a small, rule-based running-time recommender."""

from __future__ import annotations

from typing import Any

import pandas as pd
import requests
import streamlit as st


GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_SECONDS = 12

# These are the original notebook's hard rules.  They deliberately stay fixed
# in this first version so the reflection/replanning behaviour is easy to see.
HARD_RULES = {
    "min_temperature": 14.0,
    "max_temperature": 22.0,
    "max_rain_probability": 5.0,
    "max_wind_speed": 15.0,
}


class AgentError(Exception):
    """An error that can be shown to the app's visitor in plain language."""


class CityNotFoundError(AgentError):
    """The geocoding service could not find the supplied city."""


class WeatherServiceError(AgentError):
    """The weather service could not provide a usable response."""


def get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """Request JSON from Open-Meteo with a timeout and helpful error messages."""
    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except requests.Timeout as error:
        raise WeatherServiceError("天气服务响应超时，请稍后再试。") from error
    except requests.RequestException as error:
        raise WeatherServiceError("暂时无法连接天气服务，请检查网络后重试。") from error
    except ValueError as error:
        raise WeatherServiceError("天气服务返回了无法读取的数据，请稍后再试。") from error

    if not isinstance(payload, dict):
        raise WeatherServiceError("天气服务返回的数据格式不正确，请稍后再试。")
    if payload.get("error"):
        reason = payload.get("reason", "服务暂时无法处理这次查询")
        raise WeatherServiceError(f"天气服务提示：{reason}")
    return payload


def find_city(city: str) -> dict[str, Any]:
    """Turn a city name into the location fields needed by the forecast API."""
    payload = get_json(
        GEOCODING_URL,
        {"name": city, "count": 1, "language": "zh", "format": "json"},
    )
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise CityNotFoundError(
            f"找不到“{city}”。请试试城市的英文名，或加上国家，例如 “Sydney, Australia”。"
        )

    place = results[0]
    if not isinstance(place, dict) or "latitude" not in place or "longitude" not in place:
        raise WeatherServiceError("地理位置服务返回的信息不完整，请稍后再试。")
    return place


def fetch_hourly_forecast(place: dict[str, Any]) -> tuple[pd.DataFrame, str]:
    """Fetch the three-day forecast and turn the hourly data into a DataFrame."""
    payload = get_json(
        FORECAST_URL,
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "hourly": ["temperature_2m", "precipitation_probability", "wind_speed_10m"],
            "forecast_days": 3,
            "timezone": "auto",
            "temperature_unit": "celsius",
            "wind_speed_unit": "kmh",
        },
    )

    hourly = payload.get("hourly")
    timezone = payload.get("timezone")
    required_fields = ("time", "temperature_2m", "precipitation_probability", "wind_speed_10m")
    if not isinstance(hourly, dict) or not isinstance(timezone, str) or not all(
        field in hourly for field in required_fields
    ):
        raise WeatherServiceError("天气预报数据不完整，请稍后再试。")

    try:
        forecast = pd.DataFrame(
            {
                "time": pd.to_datetime(hourly["time"]),
                "temperature": hourly["temperature_2m"],
                "rain_probability": hourly["precipitation_probability"],
                "wind_speed": hourly["wind_speed_10m"],
            }
        )
    except (TypeError, ValueError) as error:
        raise WeatherServiceError("天气预报数据无法整理，请稍后再试。") from error

    if forecast.empty or forecast[["temperature", "rain_probability", "wind_speed"]].isna().any().any():
        raise WeatherServiceError("天气预报缺少可用于决策的时段，请稍后再试。")
    return forecast, timezone


def make_candidates(forecast: pd.DataFrame, timezone: str, ideal_temperature: float) -> pd.DataFrame:
    """Keep daytime future slots and calculate the notebook's original scores."""
    try:
        now = pd.Timestamp.now(tz=timezone).tz_localize(None).floor("h")
    except (TypeError, ValueError) as error:
        raise WeatherServiceError("无法识别该地点的时区，请稍后再试。") from error

    candidates = forecast[
        (forecast["time"] >= now)
        & (forecast["time"].dt.hour >= 6)
        & (forecast["time"].dt.hour <= 20)
    ].copy()
    if candidates.empty:
        raise WeatherServiceError("未来三天没有可供分析的白天时段，请稍后再试。")

    # Same scoring formula as the notebook: temperature 40%, rain 40%, wind 20%.
    candidates["temperature_score"] = (
        100 - (candidates["temperature"] - ideal_temperature).abs() * 8
    ).clip(lower=0)
    candidates["rain_score"] = (100 - candidates["rain_probability"] * 2).clip(lower=0)
    candidates["wind_score"] = (100 - candidates["wind_speed"] * 3).clip(lower=0)
    candidates["total_score"] = (
        candidates["temperature_score"] * 0.4
        + candidates["rain_score"] * 0.4
        + candidates["wind_score"] * 0.2
    )
    return candidates


def hard_rule_failures(row: pd.Series) -> list[str]:
    """Explain exactly why a candidate failed the reflection stage."""
    failures: list[str] = []
    if not HARD_RULES["min_temperature"] <= row["temperature"] <= HARD_RULES["max_temperature"]:
        failures.append("温度不在 14–22°C")
    if row["rain_probability"] > HARD_RULES["max_rain_probability"]:
        failures.append("降雨概率高于 5%")
    if row["wind_speed"] > HARD_RULES["max_wind_speed"]:
        failures.append("风速高于 15 km/h")
    return failures


def passes_hard_rules(row: pd.Series) -> bool:
    return not hard_rule_failures(row)


def run_agent(city: str, ideal_temperature: float = 18.0) -> dict[str, Any]:
    """Run tool use -> scoring plan -> reflection -> optional replan."""
    cleaned_city = city.strip()
    if not cleaned_city:
        raise CityNotFoundError("请输入一个城市名称后再开始分析。")

    place = find_city(cleaned_city)
    forecast, timezone = fetch_hourly_forecast(place)
    candidates = make_candidates(forecast, timezone, ideal_temperature)

    first_choice = candidates.loc[candidates["total_score"].idxmax()]
    candidates["passes_hard_rules"] = candidates.apply(passes_hard_rules, axis=1)

    if passes_hard_rules(first_choice):
        reflection = "第一次计划通过全部硬条件，无需重新规划。"
        final_choice: pd.Series | None = first_choice
        replanned = False
    else:
        reasons = "、".join(hard_rule_failures(first_choice))
        valid_candidates = candidates[candidates["passes_hard_rules"]]
        if valid_candidates.empty:
            reflection = f"第一次计划未通过硬条件（{reasons}）；未来三天没有时段同时满足全部硬条件。"
            final_choice = None
        else:
            final_choice = valid_candidates.loc[valid_candidates["total_score"].idxmax()]
            reflection = f"第一次计划未通过硬条件（{reasons}）；已排除不合格时段并重新规划。"
        replanned = True

    return {
        "place": place,
        "timezone": timezone,
        "ideal_temperature": ideal_temperature,
        "first_choice": first_choice,
        "final_choice": final_choice,
        "reflection": reflection,
        "replanned": replanned,
        "candidates": candidates,
    }


def format_time(timestamp: pd.Timestamp) -> str:
    weekdays = ("一", "二", "三", "四", "五", "六", "日")
    return f"{timestamp:%Y-%m-%d}（星期{weekdays[timestamp.weekday()]}）{timestamp:%H:%M}"


def show_choice(choice: pd.Series, heading: str) -> None:
    st.subheader(heading)
    st.write(f"**{format_time(choice['time'])}**")
    metric_columns = st.columns(4)
    metric_columns[0].metric("综合评分", f"{choice['total_score']:.1f} / 100")
    metric_columns[1].metric("温度", f"{choice['temperature']:.1f} °C")
    metric_columns[2].metric("降雨概率", f"{choice['rain_probability']:.0f}%")
    metric_columns[3].metric("风速", f"{choice['wind_speed']:.1f} km/h")


def main() -> None:
    st.set_page_config(page_title="RunTime Agent", page_icon="🏃", layout="centered")
    st.title("🏃 RunTime Agent")
    st.caption("根据未来三天的天气，为你找出更适合跑步的时段。")

    with st.form("agent_form"):
        city = st.text_input("城市", value="Sydney", max_chars=100, placeholder="例如：Sydney")
        ideal_temperature = st.slider("理想跑步温度（°C）", min_value=5, max_value=30, value=18)
        submitted = st.form_submit_button("帮我找最佳跑步时间", type="primary")

    if submitted:
        try:
            with st.spinner("Agent 正在查询天气、评分并检查硬条件…"):
                result = run_agent(city, float(ideal_temperature))
        except CityNotFoundError as error:
            st.warning(str(error))
            return
        except AgentError as error:
            st.error(str(error))
            return
        except Exception:
            # Keep unexpected technical details out of the visitor-facing interface.
            st.error("发生了意外问题，请稍后重试。")
            return

        place = result["place"]
        place_name = ", ".join(
            value for value in (place.get("name"), place.get("country")) if value
        )
        st.caption(f"分析地点：{place_name or city} · 时区：{result['timezone']}")

        final_choice = result["final_choice"]
        if final_choice is None:
            st.warning("没有找到同时满足全部硬条件的跑步时段。")
            st.info(result["reflection"])
        else:
            if result["replanned"]:
                st.success("已完成重新规划，下面是满足硬条件的最佳选择。")
            else:
                st.success("第一次计划已通过硬条件检查。")
            show_choice(final_choice, "最终推荐")
            st.info(f"**Reflection：** {result['reflection']}")

        if result["replanned"]:
            with st.expander("查看第一次计划"):
                show_choice(result["first_choice"], "第一次选择")

        with st.expander("查看 Agent 的评分与硬条件"):
            st.markdown(
                "评分：温度接近理想温度 40% + 低降雨概率 40% + 低风速 20%。  "
                "硬条件：温度 14–22°C、降雨概率不高于 5%、风速不高于 15 km/h。"
            )
            table = result["candidates"][
                ["time", "temperature", "rain_probability", "wind_speed", "total_score", "passes_hard_rules"]
            ].copy()
            table.columns = ["时间", "温度 (°C)", "降雨概率 (%)", "风速 (km/h)", "综合评分", "通过硬条件"]
            table["时间"] = table["时间"].dt.strftime("%m-%d %H:%M")
            table["综合评分"] = table["综合评分"].round(1)
            table["通过硬条件"] = table["通过硬条件"].map({True: "✓", False: "—"})
            st.dataframe(table, hide_index=True, width="stretch")

    st.divider()
    st.caption(
        "天气数据由 [Open-Meteo.com](https://open-meteo.com/) 提供。"
        "本工具仅作练习与出行参考，请在出发前查看当地天气预警。"
    )


if __name__ == "__main__":
    main()
