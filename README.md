# 炒股小牛马 Stock Workbench

本地运行的 A 股盯盘、持仓、AI 分析和自然语言操作工作台。当前发布版本为 `2.4.0`。

本项目面向个人研究和交易工作流辅助，不构成投资建议。所有写库操作都应由用户确认后执行。

## 核心能力

| 模块 | 能力 |
| --- | --- |
| 自选股 | 腾讯行情、K 线分时、异动监控、新闻/公告/研报聚合、AI 报告入口 |
| 持仓 | 多账户资产看板、合并视图、交易记录、盈亏统计、交易计划、条件单 |
| AI 分析台 | TradingAgents-astock 分析任务、队列状态、历史任务、失败原因、重试/取消、报告质量和信号跟踪 |
| Hermes 对话台 | 自然语言意图识别、Hermes session 历史、多步任务计划、写库草稿、分步确认/跳过、审计记录 |
| 设置 | OpenAI 兼容模型供应商、Base URL 获取模型、快速/深度/旁观者模型、迁移状态、备份恢复 |
| 前端 | Vite + TypeScript 渐进式构建、Vanilla JS 页面、三套主题、竖屏盯盘优化 |

## 快速开始

### 环境要求

- Python 3.12 推荐
- Node.js 20+ 推荐
- 可访问 A 股行情和资讯数据源的网络环境
- 可选：LLM API Key，用于 AI 分析和 Hermes 自然语言解析

### 安装

```bash
git clone <your-repo-url>
cd stock-workbench-local

python3.12 -m venv .venv312
. .venv312/bin/activate
pip install -r requirements.txt

npm ci
```

首次配置可复制环境样例作为本地部署参考：

```bash
cp .env.example .env
```

也可以直接在页面的 `设置 -> AI 引擎` 中配置 Base URL、API Key 和模型列表。`.env` 不会自动加载；需要环境变量时，请用 shell、进程管理器或部署平台注入。

### 启动

```bash
python app.py
```

打开：

```text
http://127.0.0.1:8000
```

主要页面：

- `/` 自选股
- `/portfolio` 持仓
- `/ai` AI 分析台
- `/hermes` Hermes 对话台
- `/performance` 信号绩效
- `/settings` 设置

### Intel macOS 自动部署

目标机器是 Intel 芯片 macOS Monterey 时，可使用内置部署脚本：

```bash
chmod +x scripts/deploy_macos_x86.sh scripts/run_macos_x86.sh
scripts/deploy_macos_x86.sh
```

脚本会安装依赖、构建前端、初始化数据库，并安装当前用户的 launchd 服务。详细说明见 [docs/deploy_macos_x86.md](docs/deploy_macos_x86.md)。

## Hermes 写库安全模型

Hermes 只允许通过受控工具写入本地数据库：

- `add_watchlist`
- `record_trade`
- `set_position`
- `create_conditional_order`

流程是：

1. 用户用自然语言描述目标。
2. LLM 或规则解析生成只读查询、写库草稿或多步计划。
3. 只读步骤自动预览。
4. 写库步骤必须由用户确认，支持单步确认、单步跳过和整单取消。
5. 所有工具调用记录到审计表，可从 Hermes 页面回溯。

写库上下文手册见 [docs/hermes_db_write_manual.md](docs/hermes_db_write_manual.md)。

## 技术栈

| 层 | 技术 |
| --- | --- |
| Web | FastAPI + Jinja2 |
| 数据库 | SQLite + aiosqlite |
| 前端 | Vanilla JS + Vite + TypeScript contracts |
| 图表 | Lightweight Charts |
| HTTP | aiohttp + httpx |
| 任务调度 | APScheduler |
| AI 引擎 | TradingAgents-astock + OpenAI 兼容模型供应商 |

## 目录结构

```text
stock-workbench-local/
├── app.py                         # FastAPI 入口
├── app_metadata.py                # 发布版本元信息
├── config.py                      # 本地配置
├── api/                           # API 路由
├── services/                      # 业务服务和 Hermes 工具注册
├── repositories/                  # 数据访问封装
├── models/                        # SQLite schema 与模型
├── scheduler/                     # 定时任务和 AI 任务桥接
├── schemas/                       # Pydantic 契约
├── templates/                     # Jinja2 页面
├── static/                        # CSS 和页面脚本
├── frontend/src/                  # Vite/TypeScript 渐进式前端源码
├── docs/                          # 操作文档
├── tests/                         # 单元测试
├── CHANGELOG.md                   # 版本变更
└── RELEASE_CHECKLIST.md           # 发布检查清单
```

## 开发命令

```bash
python -m compileall app.py api models repositories scheduler services schemas tests
python -m unittest discover tests
node --check static/js/hermes.js
npm run typecheck
npm run build
```

针对 Hermes 的快速回归：

```bash
python -m unittest tests.test_hermes_console_service
```

## 发布

发布前按 [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) 执行完整检查。

当前版本重点见 [CHANGELOG.md](CHANGELOG.md)。

## 数据和密钥

以下内容不应提交：

- `.env`
- `data/*.db`
- `data/backups/`
- `node_modules/`
- `.venv*/`
- LLM API Key 或模型供应商密钥

## License

[MIT](LICENSE)
