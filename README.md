# RunTime Agent

一个最小的网页天气决策 Agent：输入城市和理想跑步温度，它会查询未来三天的逐小时天气、为白天时段评分，并在第一次选择不符合硬条件时自动重新规划。

这个版本刻意保持简单：没有 LLM、数据库、登录或复杂前后端。它的目标是让你完成一次从 Jupyter 实验到可部署网页工具的 0→1 体验。

## Agent 做了什么

1. 用 Open-Meteo 地理编码服务查找城市坐标。
2. 取得该地点未来三天的逐小时温度、降雨概率和风速。
3. 只保留未来的 06:00–20:00 时段，按原 Notebook 的公式评分：温度 40%、降雨 40%、风速 20%。
4. 先选出最高分的时段（第一次计划）。
5. Reflection：检查硬条件——温度 14–22°C、降雨概率 ≤5%、风速 ≤15 km/h。
6. 如果第一次计划未通过，过滤掉不合格时段，在合格候选中再次选择；若没有合格时段，明确告诉用户。

## 本地运行

需要 Python 3.10 或更高版本。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

浏览器会打开本地地址（通常是 `http://localhost:8501`）。停止服务可在终端按 `Ctrl+C`。

## 放到 GitHub

这个目录已初始化为本地 Git 仓库，且包含适合上传的 `app.py`、`requirements.txt` 和 `.gitignore`。建立第一个提交：

```bash
git add app.py requirements.txt .gitignore README.md
git commit -m "Create RunTime Agent"
```

随后在 GitHub 新建一个空仓库，并按照 GitHub 页面提供的步骤把这个本地仓库连接并推送。不要上传 `.venv` 或任何密码、token；它们已被 `.gitignore` 排除。

## 部署到 Streamlit Community Cloud

1. 将这个项目推送到 GitHub。
2. 登录 Streamlit Community Cloud，选择 **Create app**。
3. 选择 GitHub 仓库与分支，主文件填 `app.py`，然后部署。
4. Cloud 会根据 `requirements.txt` 安装依赖并给出公开网址。

部署后，只要 GitHub 默认分支有新的提交，通常可以从 Cloud 控制台重新部署。这个项目没有密钥，因此不需要设置 Secrets。

## 数据来源与提醒

天气和地理编码数据由 [Open-Meteo](https://open-meteo.com/) 提供，页面底部也保留了署名链接。Open-Meteo 的 API 使用条款与署名说明以其官网为准。

这是学习项目和跑步时段建议，不能替代官方天气预警或安全判断。出发前请再确认当地天气情况。
