# Hermes 数据库写入操作手册

Hermes 对话台允许 LLM 把自然语言转换为受控工具调用。LLM 只能选择本手册列出的工具，后端会再次校验参数并生成草稿；用户确认草稿后才会真正写入 SQLite。

## 安全原则

- 不允许 LLM 直接生成 SQL。
- 不允许执行删除、批量覆盖、清空数据或任意表写入。
- 股票代码必须是 6 位 A 股代码，当前只接受 0、3、6 开头。
- 股数必须是正整数；1 手等于 100 股。
- 金额和价格必须是正数。
- 信息不足时返回缺失字段，由后端生成可补充的草稿，不要编造。
- 写库操作必须保留来源：Hermes 会记录原始用户输入、工具名、参数、结果和错误。

## 可写表

### watchlist

用途：自选股和观察池。

关键字段：

- `code`: 6 位股票代码，主键。
- `name`: 股票名称。
- `group_name`: 分组，默认 `默认`。
- `strategy_state`: 策略状态，默认 `watch`。
- `notes`: 来源和备注。

对应工具：`add_watchlist`

### trades

用途：真实或手工录入交易流水。写入后会重新计算 `portfolio`。

关键字段：

- `code`: 6 位股票代码。
- `name`: 股票名称。
- `direction`: `buy` 或 `sell`。
- `price`: 成交价格。
- `shares`: 成交股数。
- `notes`: 来源和备注。

对应工具：`record_trade`

### portfolio

用途：当前持仓汇总。Hermes 不直接覆盖该表；持仓校准通过生成差额交易来更新。

对应工具：`set_position`

### conditional_orders

用途：条件单和到价提醒。

关键字段：

- `code`: 6 位股票代码。
- `name`: 股票名称。
- `condition_type`: `price_lte`、`price_gte`、`change_pct_gte`、`change_pct_lte`。
- `target_price`: 触发价。
- `action`: `buy` 或 `sell`。
- `shares`: 计划股数，可为 0。

对应工具：`create_conditional_order`

## 工具参数

### add_watchlist

```json
{
  "tool": "add_watchlist",
  "args": {
    "code": "600519",
    "name": "贵州茅台"
  }
}
```

### record_trade

```json
{
  "tool": "record_trade",
  "args": {
    "code": "000001",
    "name": "平安银行",
    "direction": "buy",
    "shares": 200,
    "price": 10.5
  }
}
```

### set_position

```json
{
  "tool": "set_position",
  "args": {
    "code": "000001",
    "name": "平安银行",
    "shares": 500,
    "price": 10.5
  }
}
```

`price` 可为空；为空时后端优先使用已有持仓均价。如果没有均价，草稿不可执行。

### create_conditional_order

```json
{
  "tool": "create_conditional_order",
  "args": {
    "code": "600519",
    "name": "贵州茅台",
    "trade_action": "buy",
    "condition_type": "price_lte",
    "target_price": 1680,
    "shares": 100
  }
}
```

## 返回格式

LLM 应返回单个 JSON object：

```json
{
  "tool": "record_trade",
  "args": {
    "code": "000001",
    "name": "平安银行",
    "direction": "buy",
    "shares": 200,
    "price": 10.5
  },
  "confidence": 0.9,
  "reason": "用户明确表达买入两手，价格十块五"
}
```

查询类请求不写库，使用 `action=query_position` 的兼容格式即可。

## 多步任务计划

当用户一次提出多个动作时，返回 `plan.steps`。只读步骤可以自动执行并展示预览；写库步骤仍然必须由用户确认后才执行。

```json
{
  "plan": {
    "title": "茅台观察计划",
    "steps": [
      {
        "title": "查询当前持仓",
        "action": "query_position",
        "code": "600519",
        "name": "贵州茅台",
        "requires_confirmation": false
      },
      {
        "title": "加入自选股",
        "tool": "add_watchlist",
        "args": {
          "code": "600519",
          "name": "贵州茅台"
        },
        "requires_confirmation": true
      },
      {
        "title": "创建买入条件单",
        "tool": "create_conditional_order",
        "args": {
          "code": "600519",
          "name": "贵州茅台",
          "trade_action": "buy",
          "condition_type": "price_lte",
          "target_price": 1680,
          "shares": 100
        },
        "requires_confirmation": true
      }
    ]
  }
}
```

多步计划规则：

- `query_position` 是只读步骤，不写库。
- 写库步骤必须使用本手册列出的 `tool`。
- 不要把不确定的股票代码、价格、股数编造成计划步骤。
- 如果某个写库步骤缺少必填参数，可以保留空值，让后端生成阻塞项。
- 后端会按步骤顺序执行写库工具，并对每个步骤写入审计记录。
