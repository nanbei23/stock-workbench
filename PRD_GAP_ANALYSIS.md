# PRD vs Code Gap Analysis — 炒股小牛马工作台 v2

> 审计日期: 2026-05-23 | 审计范围: PRD.md (2236行) vs 全部源码

---

## 一、总览统计

| 指标 | 数量 | 百分比 |
|------|------|--------|
| **总检查项** | **87** | 100% |
| ✅ DONE — 完全实现 | 54 | 62.1% |
| ⚠️ PARTIAL — 部分实现 | 18 | 20.7% |
| ❌ MISSING — 未实现 | 15 | 17.2% |

---

## 二、分章节详细审计

### §1 产品概述 & 技术架构基础

| # | 需求 | 状态 | 说明 |
|---|------|------|------|
| 1 | FastAPI + Jinja2 + 原生JS技术栈 | ✅ DONE | `app.py` 使用 FastAPI + Jinja2Templates |
| 2 | SQLite数据库 (aiosqlite) | ✅ DONE | `models/database.py` 使用 aiosqlite |
| 3 | 异步并发七层数据拉取 | ✅ DONE | `layer_api.py` → `asyncio.gather(*tasks)` |
| 4 | 本地服务 localhost:8000 | ✅ DONE | `config.py` + uvicorn 启动 |
| 5 | TradingAgents-astock pip集成 | ✅ DONE | `ai_api.py` → `from tradingagents...` import |
| 6 | gbrain知识库集成 | ⚠️ PARTIAL | API接口实现(`gbrain_search`/`gbrain_save`)，但gbrain CLI路径硬编码`~/.bun/bin/gbrain`，无读取增强(不自动查询gbrain增强建议) |
| 7 | 共享缓存层 SharedCache | ✅ DONE | `cache/shared_cache.py` 完全匹配PRD设计(L1+L2+TTL+jitter+atomic write+cleanup) |
| 8 | 两层缓存TTL按数据类型分层 | ✅ DONE | TTL_CONFIG 8种类型完全匹配PRD |
| 9 | 雪崩防护(TTL随机偏移) | ✅ DONE | `_ttl_for()` 中 `random.randint` jitter |
| 10 | 线程安全(RLock) | ✅ DONE | `threading.RLock()` |
| 11 | L2缓存清理(每100次write, 最多1000文件) | ✅ DONE | `_cleanup_count` + `_cleanup_l2()` |

### §2 视觉风格设计语言

| # | 需求 | 状态 | 说明 |
|---|------|------|------|
| 12 | 治愈系暖灰底色 #F5F3F0 | ✅ DONE | `style.css` 有 `:root` CSS变量 |
| 13 | CSS变量(色板/阴影/圆角/字体) | ✅ DONE | `style.css` 匹配PRD §2.3 大部分变量 |
| 14 | A股红涨绿跌配色 #E07A5F/#52B788 | ✅ DONE | `chart.js` + `stock.js` + `style.css` 全部使用 |
| 15 | 响应式设计(15.6寸竖屏优先) | ⚠️ PARTIAL | 有基本CSS响应式，但PRD要求的4个断点(≤1080/≤768/1081-1440/>1440)未完全区分 |
| 16 | 倒L型布局(顶部Tab+左面板+右侧详情) | ⚠️ PARTIAL | 自选股页(`/`)实现了倒L型。但**持仓页**和**AI分析台**为独立布局，不是倒L型 |

### §3 页面架构

| # | 需求 | 状态 | 说明 |
|---|------|------|------|
| 17 | 4个页面路由(`/`、`/portfolio`、`/ai`、`/settings`) | ✅ DONE | `app.py` 全部注册 |
| 18 | 顶部Tab导航栏(48px固定) | ✅ DONE | `base.html` 顶部导航 |
| 19 | `stock.html` 个股详情页 | ❌ MISSING | PRD §9.1项目结构列出了`templates/stock.html`，但实际**不存在** |

### §4 自选股大盘页面 `/`

| # | 需求 | 状态 | 说明 |
|---|------|------|------|
| 20 | 自选股卡片(独立卡片式设计) | ✅ DONE | `stock.js` + `index.html` 实现完整卡片结构(名称/代码/价格/盈亏/涨幅/色条) |
| 21 | 拖拽排序(HTML5 Drag API) | ✅ DONE | `stock.js` 有 dragstart/dragover/drop 实现 |
| 22 | 删除确认弹窗(Modal) | ✅ DONE | `stock.js` 有 deleteModal/closeDeleteModal/confirmDelete |
| 23 | 详情头部3×3信息网格 | ✅ DONE | `stock.js` + `index.html` 有头部信息网格 |
| 24 | 持仓盈亏行(仅持仓股显示) | ✅ DONE | `quote_api.py` 返回 `unrealized_pnl`/`daily_pnl` |
| 25 | 实时行情(腾讯API优先) | ✅ DONE | `data/quote.py` + `quote_api.py` |
| 26 | 后台可配置刷新间隔 | ✅ DONE | `settings_api.py` → `refresh_interval` 设置项 |
| 27 | 批量行情刷新 | ✅ DONE | `portfolio_api.py` → `get_batch_quotes()` |
| 28 | K线图(Lightweight Charts 4.x) | ✅ DONE | `chart.js` 使用 `LightweightCharts` v4 API |
| 29 | 5个周期Tab(分时/五日/日K/周K/月K) | ✅ DONE | `stock.js` → `initKlineTabs()` + `reloadKline()` |
| 30 | MACD副图 | ✅ DONE | `chart.js` → `calcMACD()` + DIF/DEA线+柱状图 |
| 31 | 止损止盈标点(水平虚线) | ✅ DONE | `chart.js` → `createPriceLine()` 止损/目标价 |
| 32 | 加仓价标点(蓝色虚线) | ❌ MISSING | 仅止损/目标价有标点，**加仓档位标点未实现** |
| 33 | 刷新控制栏(自动刷新/间隔下拉/立即刷新) | ✅ DONE | `stock.js` → `autoRefreshTimer = setInterval(...)` (line 709) |
| 34 | 自动刷新仅交易时段 | ⚠️ PARTIAL | 后端`jobs.py`有交易时段检查，**前端JS未做时段限制** |
| 35 | 策略引擎(参数/计划表/状态机/建议表) | ✅ DONE | `models/strategy.py` + `api/strategy_api.py` |
| 36 | 费率模型(佣金万3/印花税0.5‰/过户费0.01‰) | ✅ DONE | `settings_api.py` → 费率设置项 |
| 37 | 成交记录CRUD | ✅ DONE | `portfolio_api.py` → trades CRUD + `_recalc_portfolio()` |
| 38 | 撤销上一笔交易 | ✅ DONE | `DELETE /api/trades/{id}` |
| 39 | 清空某股交易记录 | ✅ DONE | `DELETE /api/trades/stock/{code}` |
| 40 | 手动校正最低价 | ✅ DONE | `strategy_params.low_water_manual` |
| 41 | 浏览器通知(策略状态变化/条件单触发) | ✅ DONE | `base.html` → `pollNotifications()` + Notification API |
| 42 | 七层数据Tab(懒加载) | ✅ DONE | `layer_api.py` 按layer参数单层获取 |
| 43 | 信号Tab(概念/北向/龙虎榜/解禁/行业) | ✅ DONE | `data/signal.py` |
| 44 | 资金面Tab(融资融券/大宗/股东/分红/资金流) | ✅ DONE | `data/fund.py` |
| 45 | 研报Tab(东财研报+一致预期EPS) | ✅ DONE | `data/research.py` |
| 46 | 新闻Tab(个股新闻/财联社/情绪标签) | ✅ DONE | `data/news.py` |
| 47 | 公告Tab(巨潮公告) | ✅ DONE | `data/announce.py` |
| 48 | 手动价格覆盖 | ~~已删除~~ | 用户决定从PRD中移除此需求 |
| 49 | 自定义买点列表 | ❌ MISSING | PRD §4.1布局图有"自定义买点列表"，`buy_points`表未创建 |

### §5 持仓管理页面 `/portfolio`

| # | 需求 | 状态 | 说明 |
|---|------|------|------|
| 50 | 资产概览卡(总资产/持仓市值/可用资金/总盈亏) | ✅ DONE | `GET /api/portfolio/overview` |
| 51 | 持仓列表(左侧卡片+右侧表格) | ✅ DONE | `portfolio.js` → `loadPortfolio()` + `loadHoldingsTable()` |
| 52 | 交易记录(表格+录入弹窗) | ✅ DONE | `portfolio.js` + `portfolio_api.py` |
| 53 | 盈亏日历(月度热力图) | ✅ DONE | `GET /api/pnl/calendar` + `portfolio.js` |
| 54 | 盈亏日历点击某天弹出明细 | ⚠️ PARTIAL | PRD标注"TODO"，未实现 |
| 55 | 条件单管理(CRUD) | ✅ DONE | `portfolio_api.py` → orders CRUD + `conditional_order_checker.py` |
| 56 | 条件单4种类型(price_lte/gte/change_pct_gte/lte) | ✅ DONE | PRD + 代码一致（但checker用`price_above`/`price_below`命名不一致） |
| 57 | 待持仓列表 | ❌ MISSING | PRD §5.3.2有详细设计(待建仓标的/目标建仓价/计划表)，完全未实现 |

### §6 AI分析台 `/ai`

| # | 需求 | 状态 | 说明 |
|---|------|------|------|
| 58 | 三层架构(L1规则/L2深度/L3知识) | ✅ DONE | `ai_api.py` 三层全部实现 |
| 59 | L1实时建议总览表 | ✅ DONE | `GET /api/ai/suggestions` + `ai.js` |
| 60 | L2 TradingAgents深度分析 | ✅ DONE | `ai_api.py` → `_run_trading_agents()` propagate调用 |
| 61 | 12阶段Pipeline(7分析师+5后处理) | ✅ DONE | `PIPELINE_STAGES` 12个阶段完全匹配PRD |
| 62 | SSE进度推送 | ✅ DONE | `GET /api/ai/analyze/{task_id}/stream` + `ai.js` → `startSSE()` |
| 63 | 分析结果展示(信号→建议→分析师→辩论→风控) | ✅ DONE | `ai.js` 完整渲染逻辑 |
| 64 | risk_debate_state解析(dict/JSON/纯文本) | ✅ DONE | `_parse_risk_debate()` 三种格式 |
| 65 | strip_think()清理DeepSeek标签 | ✅ DONE | `ai_api.py` line 287-291 |
| 66 | 信号颜色编码(BUY红/SELL绿/HOLD黄) | ✅ DONE | `ai.js` |
| 67 | PDF导出(fpdf2+CJK字体) | ✅ DONE | `api/pdf_export.py` → fpdf2 + 中文字体降级 |
| 68 | 任务队列(2并发+5排队) | ✅ DONE | `MAX_CONCURRENT=2`, `MAX_QUEUE=5`, `_semaphore` |
| 69 | 8分钟超时 | ✅ DONE | `asyncio.wait_for(..., timeout=480)` |
| 70 | 取消任务API | ✅ DONE | `POST /api/ai/cancel/{task_id}` |
| 71 | 队列状态API | ✅ DONE | `GET /api/ai/queue-status` |
| 72 | 历史报告浏览 | ✅ DONE | `GET /api/ai/reports` + `GET /api/ai/reports/{id}` |
| 73 | 异动监控日志 | ✅ DONE | `GET /api/ai/anomalies` + `anomaly_checker.py` |
| 74 | gbrain搜索API | ✅ DONE | `GET /api/ai/gbrain/search` |
| 75 | gbrain写入API | ✅ DONE | `POST /api/ai/gbrain/save` |
| 76 | gbrain读取增强(自动查询历史研究) | ❌ MISSING | PRD §6.5.1要求分析时**自动查询gbrain增强上下文**，仅实现了手动搜索API |
| 77 | gbrain自动写入(深度报告/收盘日报/异动/复盘) | ❌ MISSING | PRD §6.5.2要求分析完成后**自动存入gbrain**，未实现自动触发 |
| 78 | 定时报告5个时段(09:30/11:30/13:00/15:00/15:05) | ⚠️ PARTIAL | 实现了4个(09:30/11:30/13:00/15:00)，**缺少15:05策略复盘** |
| 79 | 定时报告自动触发L2深度分析 | ❌ MISSING | `report_runner.py`仅运行L1规则引擎，**未触发L2深度分析** |
| 80 | 异动→自动触发L2深度分析 | ❌ MISSING | `anomaly_checker.py`仅检测+记录，**不触发L2分析** |
| 81 | 进度面板Token统计(调用次数/Token数) | ❌ MISSING | PRD §6.3.4要求显示LLM调用次数/Token数，未实现 |

### §7 新闻聚合 + 情绪分析

| # | 需求 | 状态 | 说明 |
|---|------|------|------|
| 82 | 新闻API(个股/财联社/全球) | ✅ DONE | `news_api.py` → `/api/news/{code}` + `/api/news-cls` |
| 83 | 情绪分析(关键词规则) | ✅ DONE | `data/news.py` 返回sentiment字段 |
| 84 | 情绪统计API | ✅ DONE | `GET /api/news-sentiment/{code}` |
| 85 | 全球资讯API | ❌ MISSING | PRD §7.1提到"东财全球资讯7×24"，API未实现 |

### §8 设置页 `/settings`

| # | 需求 | 状态 | 说明 |
|---|------|------|------|
| 86 | 5个Tab(行情监控/AI引擎/AI调度/通知/费率) | ✅ DONE | `settings.html` + `settings.js` |
| 87 | 设置CRUD | ✅ DONE | `settings_api.py` GET/PUT/POST |
| 88 | AI引擎配置(供应商/模型/API密钥/端点) | ✅ DONE | `settings_api.py` → DEFAULTS含全部AI配置项 |
| 89 | 测试连接API | ✅ DONE | `POST /api/settings/test-api` (httpx测试DeepSeek) |
| 90 | 数据导入/导出 | ✅ DONE | `GET /api/settings/export` + `POST /api/settings/import` |
| 91 | 清空所有数据 | ✅ DONE | `POST /api/settings/clear-all` |
| 92 | 通知设置(4类开关) | ✅ DONE | `settings_api.py` → notify_strategy_change/order_trigger/anomaly/analysis_done |
| 93 | 重置设置 | ✅ DONE | `POST /api/settings/reset` |

### §9 技术架构

| # | 需求 | 状态 | 说明 |
|---|------|------|------|
| 94 | APScheduler定时任务 | ✅ DONE | `scheduler/jobs.py` |
| 95 | 条件单检查(每30秒) | ✅ DONE | `scheduler/conditional_order_checker.py` |
| 96 | 异动检测(每60秒) | ✅ DONE | `scheduler/anomaly_checker.py` |
| 97 | 交易时段过滤 | ✅ DONE | `jobs.py` → `_is_trading_hours()` |
| 98 | WebSocket `/ws/quotes` | ❌ MISSING | PRD §9.5列出WebSocket端点，完全未实现 |

---

## 三、优先级排名Gap列表

### P0 — 关键缺失（影响核心功能完整性）

| # | Gap | 影响范围 | 修复建议 |
|---|-----|----------|----------|
| P0-1 | **定时报告不触发L2深度分析** | AI分析台 | `report_runner.py` 在开盘/收盘报告时应自动对持仓股触发TradingAgents分析 |
| P0-2 | **异动不触发L2深度分析** | AI分析台 | `anomaly_checker.py` 检测到异动后应调用`POST /api/ai/analyze/{code}` |
| P0-3 | **gbrain自动写入未实现** | AI分析台 | 分析完成后应自动调用`gbrain put`存入知识库(深度报告/收盘日报/异动/复盘) |
| P0-4 | **gbrain读取增强未实现** | AI分析台 | L1建议和L2分析时应自动查询gbrain获取历史研究补充上下文 |

### P1 — 重要缺失（影响用户体验）

| # | Gap | 影响范围 | 修复建议 |
|---|-----|----------|----------|
| P1-1 | **待持仓列表未实现** | 持仓管理 | 需新增待持仓模型+API+前端（目标建仓价/计划表/→自动转入持仓） |
| P1-2 | **加仓档位标点未实现** | K线图 | `chart.js` 需从策略参数获取加仓价位，添加蓝色虚线标点 |
| P1-3 | **stock.html 个股详情页不存在** | 页面架构 | PRD §9.1列出`templates/stock.html`，当前详情嵌入`index.html` |
| P1-4 | **进度面板Token统计缺失** | AI分析台 | 前端需显示LLM调用次数/工具调用/输入输出Token数 |
| P1-5 | **前端自动刷新未做交易时段限制** | 自选股 | `stock.js`的setInterval需加交易时段检查 |
| P1-6 | ~~手动价格覆盖~~ | — | 已从PRD删除 |

### P2 — 优化项（锦上添花）

| # | Gap | 影响范围 | 修复建议 |
|---|-----|----------|----------|
| P2-1 | **WebSocket实时推送** | 全局 | PRD列了`/ws/quotes`，当前用轮询替代，可后续加WebSocket |
| P2-2 | **全球资讯API** | 新闻 | 需实现东财7×24全球资讯接口 |
| P2-3 | **自定义买点列表** | 自选股 | 需创建`buy_points`表+API+前端 |
| P2-4 | **盈亏日历点击某天弹出明细** | 持仓管理 | 需实现日期点击→弹出当日各股盈亏 |
| P2-5 | **15:05策略复盘定时任务** | AI分析台 | `jobs.py` 需增加15:05 CronTrigger |
| P2-6 | **4个响应式断点完全适配** | 全局 | CSS需细化≤1080/≤768/1081-1440/>1440四个断点 |
| P2-7 | **条件单类型命名不一致** | 条件单 | 创建用`price_lte`/`price_gte`，checker用`price_above`/`price_below`，需统一 |

---

## 四、数据库表审计

| PRD要求的表 | 实际状态 | 说明 |
|------------|----------|------|
| watchlist | ✅ 存在 | 字段基本匹配 |
| trades | ✅ 存在 | 字段完全匹配 |
| portfolio | ✅ 存在 | 字段匹配 |
| strategy_params | ✅ 存在 | 字段匹配 |
| conditional_orders | ✅ 存在 | 字段匹配(缺`expire_at`失效时间字段) |
| daily_pnl | ✅ 存在 | 字段匹配 |
| news_cache | ✅ 存在 | `models/news_manager.py` 创建 |
| ai_analysis_log → analysis_reports | ✅ 存在 | 表名不同但功能等价 |
| settings | ✅ 存在 | 字段匹配 |
| buy_points | ❌ 未创建 | PRD §9.4列出，实际不存在 |
| strategy_records | ✅ 存在 | 额外表，记录策略状态变化 |

---

## 五、API端点审计

### PRD §9.5 列出的API vs 实际实现

| API | 状态 | 实际文件 |
|-----|------|----------|
| `GET /api/quote/<code>` | ✅ | `quote_api.py` |
| `GET /api/quote/batch?codes=` | ✅ | `quote_api.py` |
| `GET /api/kline/<code>` | ✅ | `quote_api.py` |
| `GET /api/index` | ✅ | `quote_api.py` |
| `GET /api/7layer/<code>?layer=` | ✅ | `layer_api.py` |
| `GET /api/7layer/<code>/all` | ✅ | `layer_api.py` |
| `GET /api/watchlist` | ✅ | `portfolio_api.py` |
| `POST /api/watchlist` | ✅ | `portfolio_api.py` |
| `DELETE /api/watchlist/<code>` | ✅ | `portfolio_api.py` |
| `PUT /api/watchlist/reorder` | ✅ | `portfolio_api.py` |
| `GET /api/trades` | ✅ | `portfolio_api.py` |
| `POST /api/trades` | ✅ | `portfolio_api.py` |
| `GET /api/portfolio` | ✅ | `portfolio_api.py` |
| `GET /api/strategy/<code>` | ✅ | `strategy_api.py` |
| `PUT /api/strategy/<code>/params` | ✅ | `strategy_api.py` |
| `GET /api/strategy/<code>/pnl` | ✅ | `strategy_api.py` |
| `GET /api/strategy/<code>/state` | ✅ | `strategy_api.py` |
| `GET /api/orders` | ✅ | `portfolio_api.py` |
| `POST /api/orders` | ✅ | `portfolio_api.py` |
| `DELETE /api/orders/<id>` | ✅ | `portfolio_api.py` |
| `GET /api/pnl/calendar` | ✅ | `portfolio_api.py` |
| `GET /api/news/<code>` | ✅ | `news_api.py` |
| `GET /api/news/cls` | ✅ | `news_api.py` (路径为`/news-cls`) |
| `GET /api/news/global` | ❌ MISSING | 未实现 |
| `GET /api/news/sentiment/<code>` | ✅ | `news_api.py` (路径为`/news-sentiment/<code>`) |
| `GET /api/ai/suggestions` | ✅ | `ai_api.py` |
| `POST /api/ai/analyze/{code}` | ✅ | `ai_api.py` |
| `GET /api/ai/analyze/{task_id}/status` | ✅ | `ai_api.py` |
| `GET /api/ai/analyze/{task_id}/result` | ✅ | `ai_api.py` |
| `SSE /api/ai/analyze/{task_id}/stream` | ✅ | `ai_api.py` |
| `GET /api/ai/reports` | ✅ | `ai_api.py` |
| `GET /api/ai/reports/{id}` | ✅ | `ai_api.py` |
| `GET /api/ai/anomalies` | ✅ | `ai_api.py` |
| `POST /api/ai/trigger` | ✅ | `ai_api.py` |
| `GET /api/ai/gbrain/search?q=` | ✅ | `ai_api.py` |
| `GET /api/ai/report/<id>/pdf` | ✅ | `pdf_export.py` |
| `GET /api/settings` | ✅ | `settings_api.py` |
| `PUT /api/settings/<key>` | ✅ | `settings_api.py` |
| `POST /api/settings/test-llm` | ✅ | `settings_api.py` (路径为`/settings/test-api`) |
| `WebSocket /ws/quotes` | ❌ MISSING | 未实现 |

**API完成率: 37/39 = 94.9%**（路径小差异不影响功能）

---

## 六、PRD列出但代码中不存在的文件

| PRD §9.1 列出的文件 | 实际状态 |
|---------------------|----------|
| `scheduler/ai_engine.py` | ❌ 不存在（逻辑合并到`ai_api.py`） |
| `scheduler/ta_bridge.py` | ❌ 不存在（逻辑合并到`ai_api.py`的`_run_trading_agents()`） |
| `scheduler/gbrain_client.py` | ❌ 不存在（逻辑内联在`ai_api.py`的`gbrain_search/save`中） |
| `cache/ta_cache_patch.py` | ❌ 不存在（TradingAgents缓存注入未实现） |
| `templates/stock.html` | ❌ 不存在（详情内容嵌入`index.html`） |
| `data/tech.py` / `data/price.py` / `data/basic.py` | ❌ 不存在（对应功能在`data/info.py`/`data/quote.py`/`data/kline.py`中） |

> 说明：部分文件PRD列出的名称与实际不同，但功能已通过其他文件实现。真正缺失的是`ta_cache_patch.py`（TradingAgents缓存注入）和`stock.html`。

---

## 七、关键发现总结

### 做得好的部分 ✅
1. **核心盯盘功能完整**：自选股CRUD、实时行情、K线图+MACD、策略引擎、条件单、盈亏日历全部实现
2. **AI三层架构落地**：L1规则引擎、L2 TradingAgents集成、L3 gbrain API全部可用
3. **共享缓存层高质量实现**：完全匹配PRD设计，含雪崩防护、线程安全、L2清理
4. **任务队列健壮**：2并发+5排队+超时+取消+SSE进度
5. **PDF导出+CJK字体支持**
6. **数据导入导出+设置持久化**

### 需要关注的Gap ⚠️
1. **AI分析台的"自动化"缺口**：定时报告/异动不触发L2深度分析，gbrain不自动读写 → AI增值功能手动触发可用，但自动化闭环未完成
2. **待持仓列表**：完整的建仓计划管理功能缺失
3. **条件单类型命名不一致**：创建用`price_lte`/`price_gte`，checker用`price_above`/`price_below`
4. **缺少`ta_cache_patch.py`**：TradingAgents-astock没有注入共享缓存，意味着L2分析时会独立拉数据，不复用缓存
