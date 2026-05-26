# 🐂 炒股小牛马 — Stock Workbench

本地运行的 A 股盯盘 + AI 深度分析工作台，面向个人投资者的决策辅助工具。

## ✨ 功能

| 模块 | 功能 |
|------|------|
| 📊 **自选股** | 实时行情（腾讯数据源）、K线分时图、异动监控（10种类型）、新闻聚合（东财/财联社/公众号）、公告搜索、AI分析报告 |
| 💼 **持仓管理** | 持仓列表、盈亏统计、交易计划、止损设置、盈亏日历 |
| 🤖 **AI分析台** | TradingAgents 12 阶段深度分析、多Agent（技术/情绪/新闻/基本面/政策/游资/解禁+5阶段Pipeline）、SSE进度、事实账本+旁观者复核 |
| 📈 **信号绩效** | AI报告信号前向验证、7档信号统计、月度收益率趋势、持仓跟踪 |
| ⚙️ **设置** | 行情监控、AI引擎（9供应商+获取模型）、通知、费率、数据导入导出 |
| 🎨 **三套皮肤** | 赛博朋克 / 午夜 / 阳光户外，CSS变量驱动一键切换 |

## 🚀 快速开始

```bash
# 1. 克隆
git clone <your-repo-url>
cd stock-workbench

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动（A股数据源需要中国大陆网络）
python -m uvicorn app:app --host 0.0.0.0 --port 8000

# 4. 浏览器打开
open http://localhost:8000
```

## 🧱 技术栈

| 层 | 技术 |
|---|------|
| Web 框架 | FastAPI + Jinja2 |
| 前端 | Vanilla JS（无框架依赖） |
| 图表 | Lightweight Charts v4 (TradingView) |
| 数据库 | SQLite + aiosqlite |
| HTTP | aiohttp |
| AI 引擎 | TradingAgents-astock（进程内调用） |
| 数据源 | 腾讯行情、东方财富、财联社、搜狗微信 |
| 定时任务 | APScheduler |

## 📁 目录结构

```
stock-workbench/
├── app.py                    # FastAPI 入口
├── config.py                 # 配置（DB路径等）
├── requirements.txt
├── PRD.md                    # 产品需求文档
├── api/                      # API 路由层
│   ├── ai_api.py             # AI分析、异动
│   ├── portfolio_api.py      # 持仓管理
│   ├── settings_api.py       # 设置、获取模型
│   ├── signal_api.py         # 信号跟踪
│   ├── quote_api.py          # 行情
│   ├── news_api.py           # 新闻
│   ├── strategy_api.py       # 策略
│   └── pdf_export.py         # PDF导出
├── data/                     # 数据获取层
│   ├── quote.py              # 腾讯行情
│   ├── kline.py              # K线（腾讯+百度）
│   ├── news.py               # 新闻聚合
│   ├── announce.py           # 公告（东方财富）
│   ├── research.py           # 研报
│   ├── market.py             # 市场指数
│   ├── signal.py             # 7档信号
│   └── helpers.py            # 工具函数
├── models/                   # 数据库模型
│   ├── database.py           # SQLite表结构（14张表）
│   ├── watchlist.py          # 自选股
│   ├── portfolio.py          # 持仓
│   └── strategy.py           # 策略
├── scheduler/                # 定时任务 & AI引擎
│   ├── ta_bridge.py          # TradingAgents桥接
│   ├── signal_tracker.py     # 信号跟踪
│   ├── anomaly_checker.py    # 异动检测
│   ├── fact_checker.py       # 事实账本
│   └── scheduler.py          # 定时任务注册
├── templates/                # Jinja2 模板
│   ├── base.html             # 基础布局+导航+浮窗
│   ├── index.html            # 自选股页
│   ├── ai.html               # AI分析台
│   ├── portfolio.html        # 持仓页
│   ├── settings.html         # 设置页
│   └── signal.html           # 信号绩效页
├── static/
│   ├── css/
│   │   ├── style.css         # 基础样式（~4800行）
│   │   ├── cyberpunk.css     # 赛博朋克主题
│   │   ├── midnight.css      # 午夜主题
│   │   ├── sunny.css         # 阳光户外主题
│   │   └── SKIN_SPEC.md      # 皮肤开发规范
│   └── js/
│       ├── stock.js          # 自选股逻辑
│       ├── ai.js             # AI分析台逻辑
│       ├── portfolio.js      # 持仓逻辑
│       ├── settings.js       # 设置逻辑
│       └── chart.js          # K线图表
└── data/                     # 运行时数据
    └── workbench.db          # SQLite数据库
```

## 🎨 主题

| 主题 | 特点 |
|------|------|
| 🌆 **赛博朋克** | 暗黑玻璃态，青蓝渐变+粉红涨+翡翠绿跌 |
| 🌙 **午夜** | 纯黑平铺，琥珀金强调+标准红涨绿跌 |
| ☀️ **阳光户外** | 暖白底#FFF8F0，天蓝+阳光金+标准红涨绿跌 |

主题切换：点击Banner右侧按钮，`localStorage` 持久化。

## ⚙️ 配置

所有设置存 SQLite 数据库，通过 `/settings` 页面可视化修改：

- **行情监控**：自动刷新间隔、异动阈值
- **AI 引擎**：LLM 供应商（9个）、模型选择、🔍 一键获取远程模型列表
- **旁观者核对**：独立模型复核分析报告
- **通知 / 费率 / 数据**：导入导出备份

## 📡 API 端点

完整端点清单见 [PRD.md#十一](./PRD.md#十一api端点清单)，主要分组：

- `/api/settings/*` — 设置 CRUD + 获取模型 + 测试连接
- `/api/watchlist/*` / `/api/quote/*` — 自选股 + 行情
- `/api/ai/*` — AI 分析 + 异动 + 报告 + SSE 进度
- `/api/signal/*` — 信号跟踪 + 绩效统计
- `/api/news/*` / `/api/announce/*` — 新闻 + 公告

## 🧪 开发

```bash
# 语法检查
python _check_syntax.py

# 导入检查
python _check_imports.py

# 变更验证
python verify_changes.py
```

## ⚠️ 注意事项

- **需要中国大陆网络**：数据源（腾讯行情、东方财富等）依赖国内网络
- **macOS aiohttp**：需使用 `TCPConnector(family=socket.AF_INET)` 绕过 IPv6 问题
- **AI 引擎**：需要 `TradingAgents-astock` pip 包 + LLM API Key
- **皮肤规范**：开发新主题请参考 `static/css/SKIN_SPEC.md`

## 📄 许可证

[MIT](LICENSE)
