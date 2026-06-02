# 数据初始化与批量研究脚本

适用版本：`v2.7.3`

这两个脚本用于自用场景下跳过页面初始化，直接从本地文件初始化数据库，并在空仓状态下分批生成建仓候选研究。

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

## 6. 全量拉取七层数据并写库

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

## 7. 全量分批提交 AI 研究并生成建仓建议

完整链路会按顺序执行：

```text
1. 先为每只股票拉取七层数据并写入 stock_data_snapshots
2. 再分批提交每只股票的完整 AI 分析，报告写入 analysis_reports / analysis_tasks
3. 最后读取 125 份报告，生成一份批量建仓建议文件
```

全量 125 只建议小批量执行，避免接口限流：

```bash
.venv312/bin/python scripts/batch_research.py \
  --group all \
  --top-n 0 \
  --skip-recent-days 0 \
  --batch-size 2 \
  --snapshot-concurrency 3 \
  --depth standard \
  --debate-rounds 1 \
  --risk-rounds 1 \
  --plan-top-n 10 \
  --apply
```

如果想先小规模验证，可以限制前 10 只：

```bash
.venv312/bin/python scripts/batch_research.py \
  --group 默认 \
  --top-n 10 \
  --batch-size 2 \
  --depth standard \
  --debate-rounds 1 \
  --risk-rounds 1 \
  --apply
```

## 8. 结果保存

批处理摘要保存为文件：

```text
data/batch_research/batch_research_*.json
data/batch_research/batch_research_*.md
data/batch_research/position_plan_*.json
data/batch_research/position_plan_*.md
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

## 9. 推荐执行顺序

```text
1. init_from_files.py 预览
2. init_from_files.py --apply 写库
3. 打开 / 确认自选股和观察池
4. 打开 /portfolio 确认可用现金为空仓状态
5. batch_research.py dry-run
6. batch_research.py --data-only --apply 全量拉七层数据并写库
7. batch_research.py --apply 全量分批跑完整分析并生成建仓建议
8. 在 /ai 查看任务和报告
```
