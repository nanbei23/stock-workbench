# 数据初始化与批量研究脚本

适用版本：`v2.8.1`

初始化脚本用于自用场景下跳过页面初始化，直接从本地文件初始化数据库。批量研究从 `v2.8.0` 起同时支持页面后台任务和 CLI 脚本：页面适合日常观察进度、续跑和重试；CLI 适合部署机长任务和批处理。

## 1. 准备文件

当前脚本匹配这两个 Markdown 文件格式：

```text
/Users/yuxuanfeng/Downloads/自选股清单 (1)
/Users/yuxuanfeng/Downloads/交易历史 (1)
```

自选股清单支持两个分区：

```text
## 自选股
## 观察池
```

交易历史支持：

- `交易明细`
- `账户总结`
- `待建仓条件单`

脚本会忽略交易历史里错误的 `初始本金`，改用 `当前现金 + 历史交易现金流` 倒算初始资金。

## 2. 预览初始化

先预览，不写库：

```bash
.venv312/bin/python scripts/init_from_files.py \
  --watchlist '/Users/yuxuanfeng/Downloads/自选股清单 (1)' \
  --trades '/Users/yuxuanfeng/Downloads/交易历史 (1)' \
  --cash '253,375.68' \
  --reset
```

确认输出里这些数字合理：

- `watchlist.self_selected`
- `watchlist.observation_pool`
- `trades.closed_positions`
- `trades.parsed`
- `cash.balance`
- `cash.inferred_initial_capital`
- `conditional_orders.parsed`

## 3. 执行初始化

确认预览无误后写入数据库：

```bash
.venv312/bin/python scripts/init_from_files.py \
  --watchlist '/Users/yuxuanfeng/Downloads/自选股清单 (1)' \
  --trades '/Users/yuxuanfeng/Downloads/交易历史 (1)' \
  --cash '253,375.68' \
  --reset \
  --apply
```

默认会先备份现有数据库。导入内容包括：

- 自选股 进入 `watchlist.group_name = 默认`
- 观察池 进入 `watchlist.group_name = 观察池`
- 历史交易拆成买入/卖出流水，写入 `trades`
- 按流水重算 `portfolio`
- 当前现金写入 `settings` 和 `cash_ledger`
- 待建仓计划写入 `conditional_orders`

## 4. 费用规则

默认费用模型来自券商截图核对：

```text
综合佣金 = max(成交金额 * 0.0001, 5.000)
交易规费 = 合并计入 commission
上海股票过户费 = 成交金额 * 0.00001
ETF 过户费 = 0
印花税 = 0
```

可用参数覆盖：

```bash
--commission-rate 0.0001
--min-commission 5
--transfer-fee-rate 0.00001
--sell-stamp-tax-rate 0
```

## 5. 批量研究 Dry Run

初始化后，先生成候选计划，不提交 AI：

```bash
.venv312/bin/python scripts/batch_research.py \
  --group 默认 \
  --top-n 15
```

输出文件在：

```text
data/batch_research/
```

## 6. 页面后台批量研究

打开：

```text
http://127.0.0.1:8000/ai
```

右侧 `批量研究` 面板提供三个后台任务：

- `预取数据`：批量拉取七层数据，写入 `stock_data_snapshots`
- `生成报告`：复用完整快照生成 AI 报告，写入 `analysis_reports`
- `生成建仓建议`：读取现有报告生成建仓建议文件

任务创建后页面不会被阻塞，前端会轮询 `/api/batch-research/jobs` 展示状态。服务重启或终端中断后，旧的运行中任务会被标记为 `interrupted`，不会一直卡在运行中。失败或等待项可以通过页面按钮或 API 重试。

大批量报告阅读入口是：

```text
http://127.0.0.1:8000/reports
```

`/reports` 适合 125 份报告级别的筛选、预览、导出，并支持选中报告后生成建仓建议。

相关 API：

```text
POST /api/batch-research/jobs
GET  /api/batch-research/jobs?limit=5
GET  /api/batch-research/jobs/{job_id}
GET  /api/batch-research/jobs/{job_id}/items
POST /api/batch-research/jobs/{job_id}/resume
POST /api/batch-research/jobs/{job_id}/retry-failed
```

示例 payload：

```json
{
  "job_type": "data_prefetch",
  "group": "all",
  "top_n": 0,
  "snapshot_concurrency": 3
}
```

```json
{
  "job_type": "report_generation",
  "group": "all",
  "top_n": 0,
  "skip_recent_days": 30,
  "analysis_concurrency": 1,
  "snapshot_model_tier": "deep"
}
```

```json
{
  "job_type": "position_plan",
  "report_ids": [101, 102, 103],
  "multi_role": true,
  "plan_top_n": 10
}
```

`v2.8.1` 起，报告库里的建仓建议默认使用勾选报告的 `report_ids`。后端会读取这些报告的完整字段作为上下文，按组合经理、风控经理、交易员、反方审查、最终裁决五个角色顺序讨论，最后生成组合级建仓建议。未勾选报告不会进入上下文。

旧接口 `/api/batch-reports` 仍保留兼容，但新功能以 `/api/batch-research` 为准。

## 7. 全量拉取七层数据并写库

只做七层数据快照，不跑 LLM。脚本会并发拉取每只股票的 `market / social / news / fundamentals / policy / hot_money / lockup` 七层数据，先做完整性校验，再写入：

```text
stock_data_snapshots
```

全量 125 只执行时使用 `--top-n 0`：

```bash
.venv312/bin/python scripts/batch_research.py \
  --group all \
  --top-n 0 \
  --skip-recent-days 0 \
  --snapshot-concurrency 3 \
  --data-only \
  --apply
```

快照校验结果会写入 `validation_json`。如果某层缺失或工具报错，数据仍会带缺口记录入库，方便后续排查，不会静默当成完整数据。

## 8. 复用快照生成 AI 研究报告并生成建仓建议

推荐链路会按顺序执行：

```text
1. 读取每只股票最新完整七层快照 stock_data_snapshots
2. 把快照作为上下文交给已配置 AI 引擎生成报告，写入 analysis_reports
3. 最后读取 125 份报告，生成一份批量建仓建议文件
```

这一路径不会调用 TradingAgents 原生在线取数，因此不会在报告阶段重新请求东财 `push2`。如果 125 只快照已经全部完整写库，可以直接执行：

```bash
.venv312/bin/python scripts/batch_research.py \
  --group all \
  --top-n 0 \
  --skip-recent-days 30 \
  --analysis-mode snapshot \
  --analysis-concurrency 1 \
  --snapshot-model-tier deep \
  --plan-top-n 10 \
  --apply
```

`--skip-recent-days 30` 会把最近 30 天已经生成报告的股票从下一批报告生成中剔除，但最终 `position_plan_*.md` 仍会读取这些已有报告一起生成总建仓建议。如果需要强制刷新七层快照，再增加 `--refresh-snapshots`。

如果想先小规模验证，可以限制前 10 只：

```bash
.venv312/bin/python scripts/batch_research.py \
  --group 默认 \
  --top-n 10 \
  --analysis-mode snapshot \
  --analysis-concurrency 1 \
  --apply
```

原生 TradingAgents 全链路仍保留，但它会在内部重新请求行情、指标、新闻、基本面等外部数据源，其中部分函数会访问东财。只有网络稳定或配置代理后才建议使用：

```bash
.venv312/bin/python scripts/batch_research.py \
  --group all \
  --top-n 0 \
  --skip-recent-days 30 \
  --analysis-mode tradingagents \
  --batch-size 1 \
  --depth standard \
  --apply
```

## 9. 结果保存

批处理摘要保存为文件：

```text
data/batch_research/batch_research_*.json
data/batch_research/batch_research_*.md
data/batch_research/position_plan_*.json
data/batch_research/position_plan_*.md
data/batch_research/multi_role_position_plan_*.json
data/batch_research/multi_role_position_plan_*.md
```

真正提交 AI 后，单股 AI 报告仍按现有系统写入：

```text
analysis_reports
analysis_tasks
```

七层数据快照写入：

```text
stock_data_snapshots
```

建仓建议默认只生成文件，不自动写入交易、持仓或条件单，避免把研究建议直接变成操作。

## 10. 推荐执行顺序

```text
1. init_from_files.py 预览
2. init_from_files.py --apply 写库
3. 打开 / 确认自选股和观察池
4. 打开 /portfolio 确认可用现金为空仓状态
5. batch_research.py dry-run
6. batch_research.py --data-only --apply 全量拉七层数据并写库
7. batch_research.py --analysis-mode snapshot --apply 复用快照生成分析报告并生成建仓建议
8. 在 /ai 查看后台任务进度
9. 在 /reports 筛选、预览、导出报告，并生成最终建仓建议
```
