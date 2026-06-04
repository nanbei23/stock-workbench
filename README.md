# 炒股小牛马 Stock Workbench

本地运行的 A 股盯盘、持仓、AI 分析和自然语言操作工作台。当前发布版本为 `2.9.11`。

本项目面向个人研究和交易工作流辅助，不构成投资建议。所有写库操作都应由用户确认后执行。

## 核心能力

| 模块 | 能力 |
| --- | --- |
| 自选股 | 腾讯行情、K 线分时、异动监控、新闻/公告/研报聚合、AI 报告入口 |
| 持仓 | 多账户资产看板、合并视图、交易记录、盈亏统计、交易计划、条件单 |
| AI 分析台 | TradingAgents-astock 分析任务、后台批量研究、数据预取、报告生成、建仓建议、队列状态、失败原因、重试和续跑 |
| 报告库 | 大批量 AI 报告筛选、预览、导出、勾选完整报告生成组合级多角色建仓建议 |
| 热点主线 | 市场状态、热点主题、研究节奏、策略生命周期、实时研究进度 |
| Hermes 对话台 | 自然语言意图识别、Hermes session 历史、多步任务计划、写库草稿、分步确认/跳过、审计记录 |
| AI 绩效 | 信号验证、AI 影子盘、执行偏差、模型校准、置信度 Brier Score 和实盘对比 |
| 运营中心 | 数据可信、全局风控、投资组合专业化、AI质量闭环、备份升级、通知和诊断总控 |
| 设置 | OpenAI 兼容模型供应商、Base URL 获取模型、快速/深度/旁观者模型、迁移状态、备份恢复 |
| 前端 | Vite + TypeScript 渐进式构建、Vanilla JS 页面、四套主题、竖屏盯盘优化、全局 Hermes 侧栏 |

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
- `/reports` AI 报告库
- `/hotspots` 热点主线
- `/hermes` Hermes 对话台
- `/shadow` AI 绩效
- `/performance` 兼容跳转到 AI 绩效
- `/ops` 运营中心
- `/settings` 设置

### Intel macOS 自动部署

目标机器是 Intel 芯片 macOS Monterey 时，可使用内置部署脚本：

```bash
chmod +x scripts/deploy_macos_x86.sh scripts/run_macos_x86.sh
scripts/deploy_macos_x86.sh
```

脚本会安装依赖、构建前端、初始化数据库，并安装当前用户的 launchd 服务。详细说明见 [docs/deploy_macos_x86.md](docs/deploy_macos_x86.md)。

### 自用数据初始化与批量研究

`v2.7.3` 起可以用脚本跳过页面初始化：

```bash
.venv312/bin/python scripts/init_from_files.py \
  --watchlist '/Users/yuxuanfeng/Downloads/自选股清单 (1)' \
  --trades '/Users/yuxuanfeng/Downloads/交易历史 (1)' \
  --cash '253,375.68' \
  --reset \
  --apply
```

空仓后做候选研究时，可以在 `/ai` 右侧的 `批量研究` 面板启动后台任务，也可以用脚本执行。全量执行时建议先拉七层数据写库，再复用已入库快照生成 AI 报告和建仓建议，避免 TradingAgents 在报告阶段重新请求东财：

```bash
.venv312/bin/python scripts/batch_research.py --group all --top-n 0
.venv312/bin/python scripts/batch_research.py --group all --top-n 0 --skip-recent-days 0 --data-only --apply
.venv312/bin/python scripts/batch_research.py --group all --top-n 0 --skip-recent-days 30 --analysis-mode snapshot --analysis-concurrency 1 --apply
```

125 只级别的报告不要再挤在 `/ai` 右下角阅读，统一进入 `/reports` 做筛选、对比、导出。勾选完整报告后，建仓建议会把选中报告全文作为上下文，进入组合经理、风控经理、交易员、反方审查和最终裁决的多角色讨论。

完整步骤见 [docs/data_initialization_and_batch_research.md](docs/data_initialization_and_batch_research.md)。

### 长任务后台 worker

网页负责创建任务和看进度，独立 worker 负责真正执行 5-6 小时级别的批量任务。调试时可以手动启动：

```bash
.venv312/bin/python scripts/run_batch_worker.py --sleep 5 --stale-minutes 15 --worker-id batch-worker-main
```

长期自用建议安装 macOS launchd 后台守护：

```bash
scripts/worker_install_launchd.sh
scripts/worker_start.sh
scripts/worker_status.sh
scripts/worker_logs.sh
```

停止后台 worker：

```bash
scripts/worker_stop.sh
```

长任务韧性规则：

- worker 通过 lease 原子领取任务，避免多个 worker 抢同一批任务。
- 心跳超过 `--stale-minutes` 后，任务会转为 `interrupted`，可以在 `/reports` 继续。
- 模型额度耗尽会进入 `quota_paused`，切换模型或等待额度恢复后点“继续”。
- 连续失败或失败率超过阈值会进入 `guard_paused`，避免错误状态下继续烧时间和额度。
- `/reports` 的“批量任务”页签会显示 worker 在线状态、最近心跳、lease、熔断原因和模型额度状态。

### 从 2.8.1 升级到 2.9

部署机器已经运行 2.8.1 时，拉取 2.9 代码后执行：

```bash
cd /path/to/stock-workbench-local
.venv312/bin/python scripts/migrate_2_8_1_to_2_9.py
scripts/deploy_macos_x86.sh --install-service
```

迁移脚本会先备份当前 SQLite 数据库，再补齐 2.9 的批量任务、建仓计划和 worker lease 字段。迁移时如果发现旧版本仍标记为 `running` 的批量任务，会转为 `interrupted`，可以在 `/reports` 继续。

如果数据库路径不是默认的 `data/workbench.db`：

```bash
.venv312/bin/python scripts/migrate_2_8_1_to_2_9.py --db-path /absolute/path/workbench.db
```

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
python -m compileall app.py api models repositories scheduler services schemas scripts tests
python -m unittest discover tests
python -m py_compile scripts/init_from_files.py scripts/batch_research.py scripts/clear_report_data.py
node --check static/js/hermes.js
node --check static/js/shadow.js
node --check static/js/ai-task-client.js static/js/ai.js static/js/reports.js
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
