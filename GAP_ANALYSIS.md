# 炒股小牛马工作台 v2 — PRD vs 实际代码 Gap 分析

> 生成时间：2026-05-23
> PRD 行数：2236行
> 分析方法：逐条对比PRD要求与实际代码实现

---

## 一、产品概述 & 架构

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| FastAPI + Jinja2 模板 | ✅ | app.py 使用 FastAPI，templates/ 下有 Jinja2 模板 |
| 原生 JS 前端 | ✅ | static/js/ 下全部原生 JS，无框架依赖 |
| SQLite 数据库 | ✅ | models/database.py 定义 9 张表，使用 aiosqlite |
| 本地服务 localhost:8000 | ✅ | config.py 配置 HOST/PORT，uvicorn 启动 |
| 4 个页面：自选股/持仓/AI/设置 | ✅ | base.html 顶部 4 Tab，4 个路由模板 |
| 倒 L 型布局（顶部 Tab + 左侧列表 + 右侧详情） | ✅ | CSS 实现 .banner + .stock-list + .detail-area |
| 治愈系暖灰主题 | ✅ | style.css 定义完整 CSS 变量，暖米白底色 |
| A 股红涨绿跌 | ✅ | --color-up=#E07A5F(红), --color-down=#52B788(绿) |
| 15.6 寸竖屏适配 | ⚠️ | CSS 有基本响应式，但竖屏 1080×1920 专项优化不完整 |
| WebSocket 实时行情推送 | ❌ | PRD 列为可选，代码中无 WebSocket 实现 |
| 两层缓存 (SharedCache) | ✅ | cache/shared_cache.py 完整实现 L1+L2 缓存 |

---

## 二、视觉风格设计语言 (§2)

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 暖米白底色 #F5F3F0 | ✅ | style.css :root --bg: #F5F3F0 |
| 卡片圆角 12-16px | ✅ | --radius-md: 12px, --radius-lg: 16px |
| 柔散阴影 | ✅ | --shadow-card: 0 4px 12px rgba(0,0,0,0.06) |
| 低饱和色板 | ✅ | 柔和珊瑚红/薄荷绿/安静蓝均已实现 |
| Inter/思源黑体字体 | ✅ | --font-sans 定义 |
| JetBrains Mono 等宽数字 | ✅ | --font-mono 定义 |
| 数据块渐变（米黄→浅蓝） | ✅ | --gradient-warm / --gradient-cool 定义 |
| hover 轻柔放大/阴影加深 | ✅ | CSS transition 实现 |
| 响应式断点：竖屏≤1080/窄屏≤768/宽屏/超宽屏 | ⚠️ | 有基本媒体查询，但竖屏主屏断点优化不完整 |
| 移动端底部 Tab 栏 | ✅ | base.html 有 .mobile-tab-bar，CSS 媒体查询控制显示 |

---

## 三、页面架构 (§3)

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 顶部 Tab 导航（4 Tab） | ✅ | base.html .nav-tabs 4 个 Tab |
| 每页倒 L 型布局 | ✅ | .stock-list(左) + .detail-area(右) |
| Tab 切换无刷新（页面路由） | ✅ | 4 个独立路由页面，Jinja2 渲染 |

---

## 四、自选股大盘 `/` (§4)

### 4.1 布局

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 左侧自选栏 + 右侧详情区 | ✅ | index.html 实现 |
| 股票卡片列表（可滚动） | ✅ | .stock-list-body 可滚动 |
| 右侧大盘指数条 | ✅ | .index-bar 显示上证/深证/创业板 |
| 选中蓝色边框+外发光 | ✅ | .stock-card.active 样式 |

### 4.2 自选卡片

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 左侧：拖拽手柄+名称+代码+价格 | ✅ | .sc-grip + .sc-left 实现 |
| 右侧：当日盈亏/持仓盈亏/当日涨幅 | ✅ | .sc-right 三行数据 |
| 底部色条（涨红渐变/跌绿渐变） | ✅ | .stock-card-bar 涨/跌/平三种样式 |
| 删除按钮（hover 弹出，50px 圆形） | ✅ | .btn-remove 实现 |
| 删除确认弹窗（自定义 Modal） | ✅ | #deleteModal 弹窗实现 |
| 拖拽排序 | ❌ | PRD 提到 HTML5 Drag API，代码中有拖拽手柄 UI 但无排序逻辑 |
| 数据刷新仅更新 DOM 文字不重绘 | ✅ | refreshQuotes() 逐字段更新，不重绘整个卡片 |

### 4.2A 详情头部卡片

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 名称+代码+价格+涨跌幅 | ✅ | #d-name/#d-code/#d-price/#d-change |
| 3×3 信息网格（今开/最高/成交额/昨收/最低/成交量/总市值/市盈TTM/换手率） | ✅ | .info-grid 9 个 .info-cell |
| 高低价相对于昨收自动着色 | ✅ | stock.js priceClass() 着色逻辑 |
| 成交量/成交额自动万/亿单位转换 | ✅ | formatVolume()/formatAmount() |
| 持仓盈亏行（仅持仓股显示） | ✅ | #pnlRow 条件显示 |

### 4.3 实时行情

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 数据源：腾讯财经 API 优先 | ✅ | data/quote.py 使用腾讯接口 |
| 刷新间隔可配置（1s-120s） | ⚠️ | index.html 下拉 10-120 秒（每 10 秒一档），缺少 1s/2s/3s/5s 选项 |
| 手动刷新按钮 | ✅ | #btnRefreshNow 实现 |
| 批量刷新 | ✅ | /api/quote/batch 实现 |
| ~~手动价格覆盖~~ | 已删除 | 用户决定从PRD中移除 |

### 4.4 K 线图 + MACD + 止损止盈标点

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| Lightweight Charts 4.x | ✅ | chart.js 使用 LWC v4 API |
| 周期 Tab：分时/五日/日K/周K/月K | ✅ | 5 个 .chart-tab 按钮 |
| MACD 副图（DIF/DEA/柱状图） | ✅ | chart.js calcMACD() + 三个 series |
| 止损/止盈水平线+标签 | ✅ | chart.js createPriceLine() 支持 stop_loss_price/target_sell_price |
| 左右拖动平移、滚轮缩放 | ✅ | LWC 默认交互 |
| 默认选中"日K"，切换股票重置 | ✅ | stock.js loadStockDetail() 重置到 day/120 |

### 4.4 刷新控制栏

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| ☑ 自动刷新勾选框 | ✅ | #autoRefreshToggle |
| 间隔下拉（10-120 秒） | ✅ | #refreshInterval 10/20/.../120 |
| 立即刷新行情按钮 | ✅ | #btnRefreshNow |
| 仅 A 股交易时段自动刷新 | ✅ | stock.js startAutoRefresh() 检查 9:25-11:35, 12:55-15:05 |
| 刷新范围：行情+指数+K线三路并行 | ✅ | manualRefresh() 调用三个函数 |

### 4.5 策略引擎

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 策略参数（8 个：总预算/参考买入价/跌幅触发/加仓倍数/反弹减仓/减仓比例/一手股数/目标净利） | ⚠️ | strategy_api.py 有参数 CRUD，但 UI 只在 index.html 占位"策略引擎将在 Phase 3 实现" |
| 策略状态机（buy/near_buy/watch/near_sell/sell） | ⚠️ | models/strategy.py 有 get_strategy_state()，但前端未集成显示 |
| 计划表（理论触发价） | ⚠️ | models/strategy.py calc_plan_table() 实现，但前端未展示 |
| 关键价位面板 | ❌ | 后端有 calc_next_triggers()，前端无展示 |
| 费用测算 | ⚠️ | models/strategy.py calc_pnl() 实现，前端未展示 |
| 实时建议表 | ❌ | 前端 index.html 策略 Tab 显示占位文字 |

### 4.6 成交记录

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 登记初始建仓 | ❌ | 无自动按预算+初始价算股数的功能 |
| 确认加仓/卖出 | ✅ | portfolio 页面有交易录入弹窗 |
| 撤销上一笔 | ❌ | 无此功能 |
| 清空记录 | ❌ | 无此功能（settings 有清空全部，但不是单股） |
| 手动校正最低价 | ❌ | 无此功能 |
| 持仓汇总（总股数/均价/名义成本/最低买入价/最近买入价） | ⚠️ | 有总股数/均价，缺最低买入价/最近买入价 |

### 4.7 浏览器通知

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 现价触及加仓区通知 | ❌ | 通知仅限条件单触发和 AI 完成，无策略状态变化通知 |
| 现价触及减仓区通知 | ❌ | 同上 |
| 条件单触发通知 | ✅ | base.html 全局轮询 + Notification API |
| 前端轮询 /api/strategy/{code}/state | ❌ | 无此轮询逻辑 |

### 4.8 七层数据 Tab

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 行情 Tab（五档盘口/实时报价/PE/PB/市值/换手率/涨跌停） | ⚠️ | 基本信息在详情头部显示，无独立五档盘口 Tab |
| K 线 Tab | ✅ | 独立 K 线图区域 |
| 信号 Tab（概念板块/题材/北向/龙虎榜/解禁/行业排名） | ⚠️ | index.html 七层数据显示基本面+技术指标+资金面+消息面+政策面+风险面，但 load7Layer() 只填充 fundamental+technical |
| 资金面 Tab（融资融券/大宗交易/股东户数/分红送转/资金流） | ⚠️ | HTML 有 l-mainflow/l-north/l-margin-bal/l-dragon 占位，JS 未填充 |
| 研报 Tab（研报列表+PDF/一致预期 EPS） | ❌ | 无研报 Tab |
| 新闻 Tab（个股新闻/财联社快讯/全球资讯+情绪标签） | ⚠️ | index.html 有新闻 Tab 但显示占位文字"新闻聚合将在 Phase 3 实现" |
| 公告 Tab（巨潮公告全文/F10 摘要） | ❌ | 无公告 Tab |
| Tab 懒加载 | ❌ | 七层数据在选中时一次性加载，非 Tab 懒加载 |

---

## 五、持仓管理 `/portfolio` (§5)

### 5.1 仓位总览卡

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 总资产/持仓市值/可用资金/总盈亏 | ✅ | portfolio.html 4 个数据卡片 |
| API: GET /api/portfolio/overview | ✅ | portfolio_api.py 实现 |

### 5.2 盈亏日历

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 月度热力图（红涨绿跌） | ✅ | portfolio.js loadCalendar() 实现 |
| 左右箭头切换月份 | ✅ | changeMonth() 实现 |
| 顶部统计（本月盈亏/胜率） | ✅ | #monthPnl/#winRate |
| 点击某天弹出当日盈亏明细 | ❌ | showDayDetail() 只有 console.log TODO |

### 5.3 持仓列表

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 左侧卡片（名称/代码/持仓/成本/现价/盈亏） | ✅ | portfolio.js loadPortfolio() |
| 右侧表格（股票/持仓/成本/现价/市值/盈亏/盈亏%） | ✅ | loadHoldingsTable() |
| 待持仓列表 | ❌ | 无待持仓功能 |

### 5.4 条件单管理

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 条件类型：price_lte/price_gte/change_pct_gte/change_pct_lte | ✅ | portfolio.html 4 种类型 |
| 创建条件单 | ✅ | POST /api/orders |
| 取消条件单 | ✅ | DELETE /api/orders/{id} |
| 条件单触发检查 | ❌ | 后端无定时检查逻辑（scheduler 目录为空） |
| 条件单失效时间 | ❌ | conditional_orders 表无 expires_at 字段 |

---

## 六、AI 分析台 `/ai` (§6)

### 6.1 三层分析架构

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| L1 规则引擎（实时建议） | ✅ | ai_api.py _evaluate_suggestion() |
| L2 TradingAgents 深度分析 | ✅ | ai_api.py _run_trading_agents() |
| L3 gbrain 知识库 | ✅ | ai_api.py gbrain_search()/gbrain_save() |

### 6.2 实时建议总览表 (L1)

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 综合持仓+待持仓+自选的所有股票 | ⚠️ | 无待持仓 |
| 大盘指数（上证/深证/创业板） | ✅ | _get_index_quotes() 返回 3 指数 |
| 建议表格（股票/现价/涨跌/策略状态/建议/说明） | ✅ | ai.html suggestions-table |
| 手动刷新按钮 | ✅ | #btnRefreshSuggestions |

### 6.3 TradingAgents 深度分析 (L2)

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| propagate() 直接调用 | ✅ | _run_trading_agents() 使用 graph.propagate() |
| 7 类分析师 + 12 阶段 pipeline | ✅ | PIPELINE_STAGES 定义 12 阶段 |
| 结构化输出（action/target_price/confidence/risk_score） | ⚠️ | 通过正则从文本提取，非结构化输出 |
| 超时控制 8 分钟 | ❌ | 无 asyncio.wait_for 超时，propagate() 阻塞可能无限 |
| 并发限制（max_workers=2） | ❌ | 使用默认线程池，无并发限制 |
| 任务队列（2 并发 + 5 排队） | ❌ | 无排队机制，直接 run_in_executor |
| 任务取消 API | ❌ | 无 POST /api/ai/cancel/{task_id} |
| 图表实例每次新建 | ✅ | 每次分析创建新 TradingAgentsGraph |
| 错误处理与降级（8 种场景） | ⚠️ | 有基本 try/except，但缺少详细的降级 UI 反馈 |
| SSE 进度流 | ✅ | /api/ai/analyze/{task_id}/stream 实现 |
| strip_think() 清理 | ✅ | ai_api.py strip_think() 实现 |
| 信号颜色编码 BUY→红/SELL→绿/HOLD→黄 | ✅ | ai.js signalColors 实现 |

### 6.3.4 分析进度展示

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 12 阶段流水线（ANALYSTS + PIPELINE 分组） | ✅ | ai.html #analystStages + #pipelineStages |
| 三态指示器（○待运行/◉运行中/●完成） | ⚠️ | 有○和●，但运行中状态在 propagate() 阻塞期间无法实时更新 |
| 进度条 N/12 阶段完成 | ✅ | #progressBar + #progressText |
| 已完成报告 Expander（最新展开） | ✅ | ai.js onStageCompleted() |
| Token 统计面板 | ❌ | 无 LLM 调用次数/Token 数统计 |
| 预计剩余时间 | ❌ | 无此功能 |

### 6.3.5 分析结果展示

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 信号大字+颜色编码 | ✅ | #signalBanner |
| 最终投资建议（目标价/置信度/风险） | ✅ | #finalAdvice |
| 分析师报告（7 个 Expander，默认折叠） | ✅ | #analystReports details 元素 |
| 多空辩论（Tab 切换） | ⚠️ | #debateSection 只有单个 Expander，无多方/空方/裁判 Tab |
| 风控评估（激进/保守/中性/决策 Tab） | ✅ | #riskSection 4 个 .risk-tab |
| PDF 导出 | ❌ | ai.html 无"下载 PDF"按钮，无 pdf_export.py |
| 存入 gbrain 按钮 | ✅ | saveToGbrain() 实现 |
| 生成条件单按钮 | ❌ | 无此功能 |
| Markdown 渲染 | ✅ | formatReport() 支持标题/粗体/斜体/列表/分隔线/代码 |

### 6.4 历史分析浏览

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 按日期倒序排列 | ✅ | /api/ai/reports ORDER BY created_at DESC |
| 按股票代码/信号类型搜索筛选 | ✅ | 支持 code/signal 参数 |
| 点击"查看"加载完整报告 | ✅ | viewReport() 复用 renderResult() |

### 6.5 gbrain 知识增强 (L3)

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| gbrain 读取（subprocess 调用 CLI） | ✅ | gbrain_search() 使用 subprocess |
| gbrain 写入（深度报告/收盘日报/异动/复盘） | ⚠️ | gbrain_save() API 就绪，但无自动写入逻辑（无定时任务） |
| 5 秒超时 | ⚠️ | timeout=10 秒（PRD 要求 5 秒） |

### 6.5 定时分析报告

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 早盘开盘 09:30 报告 | ❌ | scheduler/ 目录为空（只有 __init__.py），无 APScheduler |
| 上午收盘 11:30 报告 | ❌ | 同上 |
| 下午开盘 13:00 报告 | ❌ | 同上 |
| 下午收盘 15:00 报告 | ❌ | 同上 |
| 策略复盘 15:05 | ❌ | 同上 |

### 6.6 异动监控 (L1)

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 涨幅异动（≥5%） | ✅ | _evaluate_suggestion() 检查 |
| 跌幅异动（≥5%） | ✅ | 同上 |
| 条件单触发异动 | ❌ | 无定时检查 |
| 策略状态变化异动 | ❌ | 无此逻辑 |
| 放量异动（≥前 5 日均量×3） | ❌ | 无此逻辑 |
| 涨停/跌停异动 | ❌ | 无此逻辑 |
| 北向资金异动 | ❌ | 无此逻辑 |
| L1→L2 联动（异动自动触发深度分析） | ❌ | 无自动触发 |

### 6.7 异动日志

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 异动日志展示 | ✅ | ai.html #anomalyLog |
| 清空/导出按钮 | ❌ | 无清空/导出功能 |
| L1 建议 + L2 深度结果 | ⚠️ | 只有 L1 建议，无 L2 关联展示 |

### 6.8 API 接口

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| GET /api/ai/suggestions | ✅ | |
| POST /api/ai/analyze/{code} | ✅ | |
| GET /api/ai/analyze/{task_id}/status | ✅ | |
| GET /api/ai/analyze/{task_id}/result | ✅ | |
| SSE /api/ai/analyze/{task_id}/stream | ✅ | |
| GET /api/ai/reports | ✅ | |
| GET /api/ai/reports/{id} | ✅ | |
| GET /api/ai/anomalies | ✅ | |
| POST /api/ai/trigger | ✅ | |
| GET /api/ai/gbrain/search?q= | ✅ | |
| POST /api/ai/batch-analyze | ✅ | |
| POST /api/ai/cancel/{task_id} | ❌ | 无取消 API |
| GET /api/ai/report/{id}/pdf | ❌ | 无 PDF 导出 |

---

## 七、新闻聚合 + 情绪分析 (§7)

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 东财个股新闻 | ⚠️ | data/news.py 有 get_stock_news()，但 news_api.py 是 TODO 空壳 |
| 财联社快讯 | ⚠️ | data/news.py 有 get_cls_telegraph()，但 API 未暴露 |
| 东财全球资讯 | ❌ | 无全球资讯 API |
| 情绪分析（关键词规则） | ⚠️ | data/news.py 可能有，但 API 未暴露情绪统计 |
| 新闻列表 UI（个股/行业/全部 Tab） | ❌ | index.html 新闻 Tab 是占位文字 |
| 情绪统计显示（正面/中性/负面数量） | ❌ | 无此 UI |

---

## 八、设置页 `/settings` (§8)

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| **行情监控 Tab** | | |
| 自动刷新间隔（10/15/30/60/120s） | ✅ | settings.html select 选项 |
| 涨跌幅异动阈值 | ✅ | #set-change_threshold |
| 放量异动倍数 | ✅ | #set-volume_threshold |
| 北向资金异动阈值 | ✅ | #set-northbound_threshold |
| **AI 引擎 Tab** | | |
| LLM 供应商（5 家） | ✅ | DeepSeek/OpenAI/Anthropic/Qwen/GLM |
| 深度思考模型 | ✅ | #set-deep_think_model |
| 快速思考模型 | ✅ | #set-quick_think_model |
| API 密钥（加密显示） | ✅ | password 类型 + 显示切换 |
| 自定义 API 端点 | ✅ | #set-custom_endpoint |
| 输出语言 | ❌ | PRD 要求有下拉，代码中无此设置项 UI |
| 辩论轮数 | ✅ | #set-debate_rounds |
| 风险讨论轮数 | ✅ | #set-risk_rounds |
| Crash 恢复开关 | ✅ | #set-checkpoint_enabled |
| 测试连接按钮 | ✅ | testApiConnection() |
| **AI 调度 Tab** | | |
| 5 个定时任务开关 | ✅ | 5 个 toggle checkbox |
| **通知 Tab** | | |
| 浏览器通知权限请求 | ✅ | requestNotifyPermission() |
| 策略变化/条件单触发/异动/AI 完成 4 个开关 | ✅ | 4 个 toggle |
| **费率 Tab** | | |
| 佣金费率/最低佣金/印花税/过户费 | ✅ | 4 个 input |
| **数据管理 Tab** | | |
| 导出全部数据 JSON | ✅ | exportData() + /api/settings/export |
| 导入数据 | ✅ | importData() + /api/settings/import |
| 清空所有数据 | ✅ | confirmClearAll() + /api/settings/clear-all |
| 重置设置 | ✅ | resetSettings() + /api/settings/reset |
| 保存设置 | ✅ | saveSettings() + /api/settings/bulk |
| 模型下拉从 model_catalog.py 读取 | ⚠️ | settings.js MODEL_CATALOG 硬编码 5 家，PRD 要求支持 9 家 |

---

## 九、技术架构 (§9)

### 9.1 项目结构

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| scheduler/jobs.py (APScheduler) | ❌ | scheduler/ 目录为空 |
| scheduler/ai_engine.py (L1 规则引擎) | ❌ | L1 逻辑在 ai_api.py 内 |
| scheduler/ta_bridge.py (L2 适配器) | ❌ | L2 逻辑在 ai_api.py 内 |
| scheduler/gbrain_client.py | ❌ | gbrain 逻辑在 ai_api.py 内 |
| scheduler/pdf_export.py | ❌ | 不存在 |
| cache/ta_cache_patch.py | ❌ | 不存在（无 monkey-patch TradingAgents） |
| templates/stock.html | ❌ | 不存在（个股详情在 index.html 内） |

### 9.2 异步数据加载

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| /api/7layer/{code}/all 并发拉取 | ⚠️ | layer_api.py 存在但是 Flask Blueprint（非 FastAPI Router），且是串行调用 |
| asyncio.gather 并发 | ❌ | layer_api.py 使用同步调用 |

### 9.3 定时任务 (APScheduler)

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| 早盘报告 09:30 | ❌ | 无 APScheduler，scheduler/ 空目录 |
| 午盘报告 11:30 | ❌ | |
| 午后报告 13:00 | ❌ | |
| 收盘报告 15:00 | ❌ | |
| 策略复盘 15:05 | ❌ | |
| 异动检查每 N 秒 | ❌ | |
| 条件单检查每 N 秒 | ❌ | |

### 9.4 SQLite 表

| PRD 要求 | 状态 | 说明 |
|----------|------|------|
| watchlist | ✅ | database.py 定义 |
| trades | ✅ | |
| portfolio | ✅ | |
| strategy_params | ❌ | database.py 中无此表（strategy_api.py 引用但未在 SCHEMA 中创建） |
| buy_points | ❌ | 无此表 |
| conditional_orders | ✅ | |
| daily_pnl | ✅ | |
| news_cache | ❌ | 无此表 |
| ai_analysis_log | ✅ | analysis_reports 表 |
| settings | ✅ | |

### 9.5 REST API 清单

| PRD 要求的 API | 状态 | 说明 |
|----------|------|------|
| GET /api/quote/{code} | ✅ | |
| GET /api/quote/batch | ✅ | |
| GET /api/kline/{code} | ✅ | |
| GET /api/index | ✅ | |
| GET /api/7layer/{code} | ⚠️ | Flask Blueprint，未迁移到 FastAPI |
| GET /api/7layer/{code}/all | ⚠️ | 同上，且串行非并发 |
| GET /api/watchlist | ✅ | |
| POST /api/watchlist | ✅ | |
| DELETE /api/watchlist/{code} | ✅ | |
| PUT /api/watchlist/reorder | ❌ | 无排序 API |
| GET /api/trades | ✅ | |
| POST /api/trades | ✅ | |
| GET /api/portfolio | ✅ | |
| GET /api/strategy/{code} | ⚠️ | strategy_api.py 是 Flask Blueprint |
| PUT /api/strategy/{code}/params | ⚠️ | 同上 |
| GET /api/strategy/{code}/pnl | ⚠️ | 同上 |
| GET /api/strategy/{code}/state | ⚠️ | 同上 |
| GET /api/orders | ✅ | |
| POST /api/orders | ✅ | |
| DELETE /api/orders/{id} | ✅ | |
| GET /api/pnl/calendar | ✅ | |
| GET /api/news/{code} | ⚠️ | 空壳实现，返回空数组 |
| GET /api/news/cls | ❌ | 无此 API |
| GET /api/news/global | ❌ | 无此 API |
| GET /api/news/sentiment/{code} | ❌ | 无此 API |
| GET /api/ai/suggestions | ✅ | |
| GET /api/ai/reports | ✅ | |
| GET /api/ai/anomalies | ✅ | |
| POST /api/ai/trigger | ✅ | |
| SSE /api/ai/analyze/{task_id}/stream | ✅ | |
| GET /api/ai/report/{id}/pdf | ❌ | |
| GET /api/ai/gbrain/search | ✅ | |
| GET /api/settings | ✅ | |
| PUT /api/settings/{key} | ✅ | |
| POST /api/settings/test-llm | ✅ | 路径为 /api/settings/test-api |
| WebSocket /ws/quotes | ❌ | 无 WebSocket |

---

## 十一、开发优先级完成度

| Phase | PRD 要求 | 完成度 | 说明 |
|-------|---------|--------|------|
| Phase 0 重构基础 | Flask→FastAPI, 响应式 CSS, 竖屏原型 | ⚠️ 80% | FastAPI 已迁移，layer_api/strategy_api 仍是 Flask Blueprint |
| Phase 1 骨架 | 路由+模板+SQLite+自选股+行情+持仓页 | ✅ 90% | 核心骨架完成 |
| Phase 2 策略引擎 | 策略引擎+成交记录+条件单+K线图 | ⚠️ 60% | 策略后端有但前端未集成，条件单缺触发检查 |
| Phase 3 七层数据 | 七层 Tab+信号/资金/研报/新闻/公告+情绪 | ⚠️ 30% | 数据层代码存在但 API 未正确暴露，前端占位 |
| Phase 4 AI 分析台 | TradingAgents+规则引擎+异动+定时+gbrain | ⚠️ 50% | L1/L2/L3 核心功能有，缺定时任务/队列/取消/PDF |
| Phase 5 增强 | 通知+设置+导入导出+响应式 | ✅ 85% | 设置页完整，通知有，导入导出有 |

---

## 总结统计

| 状态 | 数量 | 占比 |
|------|------|------|
| ✅ 已完成 | ~95 | ~55% |
| ⚠️ 部分完成 | ~35 | ~20% |
| ❌ 未实现 | ~45 | ~25% |

### 关键缺失（影响核心功能）

1. **定时任务系统 (APScheduler)** — scheduler/ 目录为空，无自动报告/异动检查/条件单触发
2. **策略引擎前端集成** — 后端 calc_plan_table()/calc_next_triggers() 有但前端 index.html 策略 Tab 是占位
3. **七层数据层 API 迁移** — layer_api.py 和 strategy_api.py 仍是 Flask Blueprint，未注册到 FastAPI
4. **新闻聚合 API** — news_api.py 是空壳，数据层代码有但未暴露
5. **拖拽排序** — UI 有拖拽手柄但无排序逻辑
6. **待持仓列表** — 完全未实现
7. **PDF 报告导出** — 无实现
8. **任务队列/并发限制/取消** — L2 分析无队列管理
9. **WebSocket 实时推送** — 未实现（PRD 标为可选）
10. **条件单自动触发检查** — 无定时检查逻辑
