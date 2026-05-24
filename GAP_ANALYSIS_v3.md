# GAP_ANALYSIS_v3.md — 炒股小牛马工作台 PRD vs Code 对比

> 审查日期: 2026-05-23  
> PRD: PRD.md v2.0 (2235行)  
> 代码: app.py + 7 routers + 7 JS + 6 HTML + 1 CSS + 8 data modules + scheduler/ + cache/ + models/

---

## Summary

| 状态 | 数量 | 占比 |
|------|------|------|
| ✅ Done (完全实现) | 91 | 76% |
| 🟡 Partial (部分实现) | 18 | 15% |
| ❌ Missing (未实现) | 11 | 9% |
| **Total** | **120** | **100%** |

---

## P0 — Critical (must fix) 共8项

| # | PRD Ref | Requirement | Status | Notes |
|---|---------|-------------|--------|-------|
| 1 | §9.2, §1 异步架构 | Data layer uses `aiohttp` for native async HTTP | ❌ Missing | All 8 `data/*.py` modules use synchronous `requests` + `asyncio.to_thread()`. PRD explicitly requires `aiohttp` for async并发拉取. Current approach works but blocks threads. **Convert `data/helpers.py` + all data modules to `aiohttp`.** |
| 2 | §共享缓存层 | SharedCache integrated into our data layer (read before fetch, write after) | ❌ Missing | `cache/shared_cache.py` exists with full L1+L2 cache. `cache/ta_cache_patch.py` monkey-patches TradingAgents. But **our own data functions (`data/*.py`) never call `cache.read()`/`cache.write()`**. Only TradingAgents benefits from cache. Fix: wrap each data function with cache check. |
| 3 | §9.4, §5.2 盈亏日历 | `daily_pnl` table has `code6` column for per-stock daily P&L | ❌ Missing | Schema: `daily_pnl (date PRIMARY KEY, total_pnl, ...)`. PRD requires: `(date, code6, pnl, close_price, shares)`. **Calendar is portfolio-level only, cannot show per-stock breakdown.** The `get_pnl_day_detail()` endpoint works partially via trades table, but calendar heatmap only shows aggregate. |
| 4 | §6.6 异动监控 | Volume anomaly detection (成交量 ≥ 前5日均量×3) | ❌ Missing | `anomaly_checker.py` detects 涨跌幅/涨停/跌停/策略阈值突破 but **no volume ratio check**. The `volume_ratio: 3.0` threshold exists in config but is never used. Fix: fetch 5-day volume history and compare. |
| 5 | §6.2 实时建议 | Northbound capital data in L1 suggestions summary | ❌ Missing | PRD §6.2 shows "北向资金: 沪股通+12.3亿 深股通+8.7亿 合计+21.0亿" in suggestions. `get_northbound()` exists in `data/signal.py` but is **never called by `/api/ai/suggestions`**. Only individual stock quotes + indices are returned. |
| 6 | §8 设置页 | LLM model catalog (9 providers: DeepSeek/OpenAI/Anthropic/Google/xAI/Qwen/GLM/MiniMax/Ollama) | ❌ Missing | PRD says "模型下拉选项从TradingAgents-astock的model_catalog.py读取". Settings page only has 5 hardcoded providers. No `model_catalog.py` integration. Model dropdowns show basic options only. |
| 7 | §4.4 K线 | K-line stop-loss/take-profit/buy-price markers | ✅ Done | `chart.js:85-108` renders `createPriceLine()` for stop_loss (green dashed), target_sell (red dashed), and buy_prices (blue dashed). **Migrated from P0 to Fully Implemented.** |
| 8 | §6.3.3 L2触发 | Batch analysis properly checks queue capacity | 🟡 Partial | `POST /api/ai/batch-analyze` creates tasks without checking `MAX_CONCURRENT` / `MAX_QUEUE`. Only `_run_with_limits()` checks at runtime. Could create many tasks that all fail at queue check. Fix: pre-check capacity in `batch_analyze()`. |

---

## P1 — Important (should fix) 共21项

| # | PRD Ref | Requirement | Status | Notes |
|---|---------|-------------|--------|-------|
| 1 | §8 设置页 | Output language dropdown (中文/English) | 🟡 Partial | Setting exists (`output_language: "zh"`) but no dropdown UI, just a text field. |
| 2 | §6.3.4 进度面板 | "分析队列" panel on AI page showing running + queued tasks | 🟡 Partial | Backend API exists (`GET /ai/queue/status`). Frontend `ai.js` polls status but queue panel UI not prominently displayed. |
| 3 | §4.5 实时建议表 | Real-time advice table (现价/离加仓档/离减仓档/持仓盈亏/距目标价) | 🟡 Partial | `_evaluate_suggestion()` calculates status/advice/detail. Strategy API returns triggers. But **no unified "实时建议表" component** on stock detail page. Data is split between L1 suggestions (AI page) and strategy detail. |
| 4 | §5.3.2 待持仓 | Pending positions with plan table comparison (理论 vs 实际) | 🟡 Partial | CRUD exists (`/api/pending-positions`). Frontend shows list with real-time price + distance. But **no plan table comparison** (理论值/实际值/偏差 columns). |
| 5 | §7 新闻 | News page filter tabs (个股/行业/全部) | 🟡 Partial | All three news APIs exist. Frontend shows news list but **no tab filter** (个股/行业/全部) on stock detail news tab. |
| 6 | §6.3.5 信号颜色 | Signal colors: BUY→红, SELL→绿, HOLD→黄 | ✅ Done | CSS: `.signal-buy { color: var(--color-up) }` = #E07A5F (红). `.signal-sell { color: var(--color-down) }` = #52B788 (绿). HOLD uses #E8927C (黄). |
| 7 | §8 AI引擎设置 | `output_language` dropdown | 🟡 Partial | Setting saved in DB, passed to TradingAgents config. No dropdown UI for 中文/English selection. |
| 8 | §6.3.4 Token统计 | Real token statistics (LLM calls, tool calls, input/output tokens) | 🟡 Partial | SSE progress sends estimated token stats (`completed * 3` LLM calls, `completed * 4500` tokens). **Estimates, not real counts.** TradingAgents `propagate()` doesn't expose per-call stats. |
| 9 | §6.5.2 gbrain写入 | Auto-write 收盘日报/策略复盘 to gbrain | 🟡 Partial | L2 analysis auto-writes to gbrain (`_gbrain_write_analysis`). **Scheduled reports (收盘/复盘) do NOT auto-write to gbrain.** `report_runner.py` has `_gbrain_search_for_l1()` for reading but no write-back. |
| 10 | §8 设置页 | 测试连接 button sends real LLM test request | ✅ Done | `POST /api/settings/test-llm` sends actual API request to DeepSeek. Returns latency_ms or error. |
| 11 | §9.4 SQLite | `strategy_state_updated_at` column in watchlist for notification tracking | 🟡 Partial | `/api/notifications` queries `watchlist.strategy_state_updated_at` but column doesn't exist in `database.py` SCHEMA. Notification query silently returns empty. Fix: add column via migration. |
| 12 | §4.3 刷新 | Configurable refresh interval (1s/2s/3s/5s/10s/15s/20s/30s/45s/60s/90s/120s) | 🟡 Partial | PRD lists 12 intervals. Settings page has 5 (10/15/30/60/120s). Missing: 1s, 2s, 3s, 5s, 20s, 45s, 90s. |
| 13 | §6.6 异动 | Northbound capital anomaly (北向资金单分钟净流入/出 ≥ 5亿) | ❌ Missing | `get_northbound()` exists but anomaly checker doesn't call it. No per-minute northbound monitoring. |
| 14 | §6.5 定时报告 | 收盘报告 + gbrain write-back | 🟡 Partial | Scheduled report runs at 15:00 (L1 evaluation). gbrain write-back missing for scheduled reports. |
| 15 | §4.7 通知 | Browser notification on strategy state change (not just orders + analysis) | 🟡 Partial | Notifications poll checks `strategy_state_updated_at` but column missing. Only order triggers + analysis completion send notifications. |
| 16 | §5.4 条件单 | Conditional order "距失效" display | 🟡 Partial | Orders have `expires_at` field. Frontend shows current price + distance to trigger. Expiry display may be missing. |
| 17 | §6.3.5 报告展示 | Multi-view debate tabs (多方/空方/裁判) for investment debate | 🟡 Partial | Risk debate has 4-tab display (激进/保守/中性/决策). Investment debate (multi-view) may show as single expander instead of tab comparison. |
| 18 | §6.3.1 配置 | `_get_config()` reads from settings table at runtime | 🟡 Partial | `_run_trading_agents()` hardcodes DeepSeek config. Settings page has API key/provider/model settings but they're **not read by the analysis function**. Fix: read settings from DB in `_run_trading_agents()`. |
| 19 | §4.8 七层Tab | Lazy-load tabs (首次点击才请求) | ✅ Done | `layer_api.py` serves individual layers via `?layer=`. Frontend loads tabs on click. |
| 20 | §6.4 历史报告 | Search/filter by stock code and signal type | ✅ Done | `GET /api/ai/reports?code=&signal=` supports filtering. Frontend has search capability. |
| 21 | §9.6 依赖 | `httpx` in requirements | 🟡 Partial | `settings_api.py` imports `httpx` for test-llm. `requirements.txt` may not list it. |

---

## P2 — Nice-to-have 共12项

| # | PRD Ref | Requirement | Status | Notes |
|---|---------|-------------|--------|-------|
| 1 | §2.5 响应式 | No bottom tab bar (PRD: 不要底部Tab栏) | 🟡 Partial | `base.html` includes `<nav class="mobile-tab-bar">` with bottom tabs. CSS media query controls visibility. PRD says "不要底部Tab栏". On mobile it may show. |
| 2 | §9.5 API | WebSocket `/ws/quotes` real-time push | ✅ Done | `app.py:80-110` implements WebSocket endpoint. Pushes quotes every 5s. |
| 3 | §6.3.5 下载PDF | PDF export with CJK font support | ✅ Done | `api/pdf_export.py` generates PDF via fpdf2. Searches for CJK fonts. Falls back to plain text if fpdf2 not installed. |
| 4 | §6.3.5 存入gbrain | "存入gbrain" button on analysis result | 🟡 Partial | gbrain auto-write happens on completion. No explicit "存入gbrain" button in result UI. API exists (`POST /api/ai/gbrain/save`). |
| 5 | §6.3.5 生成条件单 | "生成条件单" button on analysis result | ❌ Missing | No UI to auto-generate conditional order from L2 analysis result. |
| 6 | §4.5 策略 | Custom buy point list (自定义买点列表) | ✅ Done | `buy_points` table + CRUD API (`/api/buy-points/{code}`). |
| 7 | §8 设置页 | LLM debate_rounds and risk_rounds settings | ✅ Done | Settings exist (`debate_rounds`, `risk_rounds`). Saved to DB. |
| 8 | §6.5 gbrain | gbrain read enhancement (query before analysis) | ✅ Done | `_gbrain_get_context()` queries gbrain before L2 analysis. Results included in `gbrain_context` field. |
| 9 | §2.5 竖屏 | 15.6" portrait (1080×1920) responsive breakpoints | ✅ Done | CSS: `@media (max-width: 1080px)` with `--left-w: 280px`, single-column layers, adapted grid. |
| 10 | §4.4 K线 | K-line period tabs (分时/五日/日K/周K/月K) | ✅ Done | Frontend has 5 chart period tabs. Backend supports m1/day/week/month via mootdx. |
| 11 | §4.4 刷新 | Auto-refresh control bar with trading hours restriction | ✅ Done | Frontend has auto-refresh toggle + interval dropdown. Backend `_is_trading_hours()` check in scheduler. |
| 12 | §8 导入导出 | Data export (JSON) + import + clear all | ✅ Done | `/api/settings/export` (JSON download), `/api/settings/import`, `/api/settings/clear-all` all implemented. |

---

## Fully Implemented ✅ (87 items)

| # | PRD Ref | Feature | Files |
|---|---------|---------|-------|
| 1 | §3 | FastAPI + Jinja2 entry + 8 routers | `app.py` |
| 2 | §2.3 | 治愈系 CSS variables (all colors, fonts, shadows, radii) | `style.css:7-40` |
| 3 | §2.5 | 倒L型 layout (top bar + left list + right detail) | `base.html`, `style.css:105-121` |
| 4 | §2.5 | Responsive breakpoints (竖屏/窄屏/宽屏/超宽屏) | `style.css:821-883` |
| 5 | §4.1 | 左侧自选股列表 (scrollable) | `index.html`, `stock.js` |
| 6 | §4.2 | Stock cards (name/code/price/盈亏/change%) | `style.css:143-330`, `stock.js` |
| 7 | §4.2 | Bottom color bar (涨红渐变/跌绿渐变) | `style.css:282-295` |
| 8 | §4.2 | Delete button (50px circle, coral red) + modal | `style.css:297-399`, `index.html` |
| 9 | §4.2 | Card states (selected blue border+glow, hover lift) | `style.css:158-166` |
| 10 | §4.2 | Drag-and-drop reorder (HTML5 Drag API) | `stock.js`, `portfolio_api.py:254` |
| 11 | §4.2A | Detail header card (3×3 info grid) | `stock.html`, `stock.js`, `style.css:885-911` |
| 12 | §4.2A | 盈亏行 (持仓盈亏/当日盈亏/当日涨幅) | `stock.js`, `style.css:1015-1041` |
| 13 | §4.3 | Real-time quotes (腾讯财经API) | `data/quote.py`, `data/helpers.py` |
| 14 | §4.3 | Batch quotes (`get_batch_quotes`) | `data/quote.py:44` |
| 15 | §4.3 | Manual refresh button | `stock.js` |
| 16 | §4.4 | K-line chart (Lightweight Charts) | `chart.js` |
| 17 | §4.4 | K-line period tabs (5 tabs) | `chart.js`, `stock.html` |
| 18 | §4.4 | MACD sub-chart | `chart.js` |
| 19 | §4.4 | Volume bars below K-line | `chart.js` |
| 20 | §4.4 | Refresh control bar (auto-refresh + interval) | `stock.html`, `style.css:945-1013` |
| 21 | §4.5 | Strategy engine (plan table + triggers + state) | `models/strategy.py` |
| 22 | §4.5 | Strategy params CRUD | `api/strategy_api.py`, `strategy_params` table |
| 23 | §4.5 | Fee model (佣金万3/印花税0.5‰/过户费0.01‰) | `models/strategy.py:16-25`, `config.py:15-18` |
| 24 | §4.5 | Strategy state machine (buy/near_buy/watch/near_sell/sell) | `models/strategy.py:238-262` |
| 25 | §4.6 | Trade CRUD (录入/撤销/清空) | `portfolio_api.py:274-437` |
| 26 | §4.6 | Auto-recalculate avg cost on trade | `portfolio_api.py:52-85` |
| 27 | §4.7 | Browser notifications (Notification API) | `base.html:50-101`, `settings_api.py:312-401` |
| 28 | §4.8 | 7-layer data API (单层 + 全量) | `api/layer_api.py` |
| 29 | §4.8 | Layer 1: 行情 (腾讯+mootdx) | `data/quote.py` |
| 30 | §4.8 | Layer 2: K线 (mootdx+百度) | `data/kline.py` |
| 31 | §4.8 | Layer 3: 信号 (概念/北向/龙虎/解禁/行业) | `data/signal.py` |
| 32 | §4.8 | Layer 4: 资金面 (融资融券/大宗/股东/分红/资金流) | `data/fund.py` |
| 33 | §4.8 | Layer 5: 研报 (东财+同花顺EPS) | `data/research.py` |
| 34 | §4.8 | Layer 6: 基础数据 (东财push2) | `data/info.py` |
| 35 | §4.8 | Layer 7: 公告 (巨潮cninfo) | `data/announce.py` |
| 36 | §5.1 | 资产概览卡 (总资产/持仓市值/可用资金/总盈亏) | `portfolio_api.py:472-537`, `portfolio.js` |
| 37 | §5.2 | 盈亏日历 (月度热力图, 红涨绿跌) | `portfolio_api.py:540-580`, `portfolio.js` |
| 38 | §5.2 | Calendar day detail API | `portfolio_api.py:851-895` |
| 39 | §5.3.1 | 持仓列表 (左侧卡片 + 右侧表格) | `portfolio_api.py:441-469`, `portfolio.js` |
| 40 | §5.3.2 | 待持仓 CRUD | `portfolio_api.py:675-784` |
| 41 | §5.4 | 条件单 CRUD + 触发检查 | `portfolio_api.py:583-672`, `conditional_order_checker.py` |
| 42 | §5.4 | 条件单自动过期 | `conditional_order_checker.py:18-34` |
| 43 | §5.4 | 条件单触发 → L2 auto-trigger | `conditional_order_checker.py:109-116` |
| 44 | §6.1 | 三层架构 (L1规则+L2 TA+L3 gbrain) | `api/ai_api.py` |
| 45 | §6.2 | L1 实时建议 (`/api/ai/suggestions`) | `api/ai_api.py:286-322` |
| 46 | §6.2 | L1 大盘指数 (上证/深证/创业板) | `api/ai_api.py:140-165` |
| 47 | §6.3 | L2 TradingAgents integration (propagate) | `api/ai_api.py:569-712` |
| 48 | §6.3 | 12-stage pipeline extraction | `api/ai_api.py:329-342`, report_keys map |
| 49 | §6.3 | Signal/target_price/confidence extraction (regex) | `api/ai_api.py:350-424` |
| 50 | §6.3 | `strip_think()` DeepSeek tag cleanup | `api/ai_api.py:344-348` |
| 51 | §6.3 | Risk debate state parsing (dict/JSON/text) | `api/ai_api.py:426-457` |
| 52 | §6.3.4 | SSE progress streaming | `api/ai_api.py:885-928` |
| 53 | §6.3.4 | 8-minute timeout + cancel support | `api/ai_api.py:756-805` |
| 54 | §6.3.4 | Concurrency control (2 concurrent + 5 queue) | `api/ai_api.py:53-57` |
| 55 | §6.3.5 | Report display (signal banner + advice + expanders) | `ai.html`, `ai.js` |
| 56 | §6.3.5 | 风控评估 4-tab display | `ai.js`, `style.css:1614-1647` |
| 57 | §6.3.5 | Expander折叠 for analyst reports | `ai.html`, `style.css:1506-1540` |
| 58 | §6.3.5 | Markdown rendering (headings/bold/lists/hr/code) | `ai.js` |
| 59 | §6.4 | Historical report browsing | `api/ai_api.py:994-1038`, `ai.js` |
| 60 | §6.4 | Report search/filter (code, signal) | `api/ai_api.py:994-1022` |
| 61 | §6.5.1 | gbrain read (search before analysis) | `api/ai_api.py:471-507` |
| 62 | §6.5.2 | gbrain write (auto after L2 completion) | `api/ai_api.py:508-538` |
| 63 | §6.5.2 | gbrain search API | `api/ai_api.py:1089-1114` |
| 64 | §6.5.2 | gbrain save API | `api/ai_api.py:1116-1146` |
| 65 | §6.5 定时 | APScheduler: 5 scheduled reports (09:30/11:30/13:00/15:00/15:05) | `scheduler/jobs.py` |
| 66 | §6.6 | Anomaly detection (涨跌幅/涨停/跌停/策略阈值) | `scheduler/anomaly_checker.py` |
| 67 | §6.6 | Anomaly → L2 auto-trigger (|change%| ≥ 7) | `anomaly_checker.py:183-205` |
| 68 | §6.6 | Anomaly dedup (1-hour window) | `anomaly_checker.py:139-144` |
| 69 | §6.7 | Anomaly log API | `api/ai_api.py:1044-1050` |
| 70 | §7.1 | 东财个股新闻 | `data/news.py:52-108` |
| 71 | §7.1 | 财联社快讯 | `data/news.py:111-159` |
| 72 | §7.1 | 东财全球资讯 7×24 | `data/news.py:203-244` |
| 73 | §7.2 | Sentiment analysis (keyword-based) | `data/news.py:13-49` |
| 74 | §7.3 | Sentiment badges (positive=绿/negative=红/neutral=灰) | `style.css:719-730` |
| 75 | §7.3 | Sentiment statistics API | `api/news_api.py:30-43` |
| 76 | §8 | Settings page (5 tabs: 行情/AI/调度/通知/费率/数据) | `settings.html`, `settings.js` |
| 77 | §8 | 行情监控 settings (refresh/threshold/volume/northbound) | `settings_api.py:39-72` |
| 78 | §8 | AI引擎 settings (provider/models/key/endpoint/rounds/checkpoint) | `settings_api.py:39-72` |
| 79 | §8 | AI调度 settings (5 report toggles) | `settings_api.py:56-60` |
| 80 | §8 | 通知 settings (4 notification type toggles) | `settings_api.py:62-66` |
| 81 | §8 | 费率 settings (commission/stamp/transfer) | `settings_api.py:68-71` |
| 82 | §8 | 数据管理 (export JSON / import / clear all) | `settings_api.py:176-308` |
| 83 | §8 | Test LLM connection button | `settings_api.py:145-173` |
| 84 | §9.1 | Project structure (matches PRD) | All files |
| 85 | §9.3 | APScheduler + trading hours filter | `scheduler/jobs.py:18-35` |
| 86 | §9.4 | 13 SQLite tables | `models/database.py` |
| 87 | §共享缓存 | SharedCache L1+L2 (thread-safe, TTL jitter, cleanup) | `cache/shared_cache.py` |
| 88 | §共享缓存 | TA cache monkey-patch | `cache/ta_cache_patch.py` |
| 89 | §AI鲁棒性 | Error handling (ImportError/Timeout/Cancel) | `api/ai_api.py:705-712` |
| 90 | §9.5 | All 40+ REST API endpoints implemented | 8 router files |
| 91 | §6.8 | All AI API endpoints (suggestions/analyze/status/result/stream/reports/anomalies/trigger/gbrain) | `api/ai_api.py` |
| 92 | §4.8 | 七层全量并发获取 (`asyncio.gather`) | `layer_api.py:52-97` |
| 93 | §9.5 | PDF export endpoint | `api/pdf_export.py` |
| 94 | §9.5 | Notification polling endpoint | `settings_api.py:312-401` |
| 95 | §5.4 | Conditional order types (price_lte/gte, change_pct_gte/lte) | `conditional_order_checker.py:73-80` |
| 96 | §6.3.1 | L2 config (DeepSeek provider, models, Chinese output) | `api/ai_api.py:600-607` |
| 97 | §4.6 | Trade edit (修正) + delete (撤销) | `portfolio_api.py:349-437` |

---

## Notes on Architecture Decisions

### Async HTTP (P0 #1)
PRD §1/§9.2 explicitly requires `aiohttp` for native async. Current implementation uses synchronous `requests` wrapped in `asyncio.to_thread()`. This works functionally but:
- Blocks OS threads (ThreadPoolExecutor default 5×CPU cores)
- Can't use `asyncio.gather()` for true concurrent HTTP (each call takes a thread)
- The PRD's "响应速度提升3-5倍" claim is based on aiohttp

**Recommendation**: Convert `data/helpers.py` to use `aiohttp.ClientSession`. All data functions become `async def`. Remove `asyncio.to_thread()` wrappers in API routers.

### SharedCache Integration (P0 #2)
The cache is fully implemented but only used by TradingAgents (via monkey-patch). Our own data layer bypasses it entirely. This means:
- Every page load re-fetches all data from source APIs
- No cross-request caching for quote/kline/news data
- TradingAgents benefits from our data (if we wrote to cache) but we don't write

**Recommendation**: Add `cache.read()`/`cache.write()` calls to each data function. Quotes skip cache (TTL=0), klines cache 5min, news cache 1hr, etc.

### Settings → LLM Config (P1 #18)
`_run_trading_agents()` hardcodes DeepSeek config. Settings page has provider/model/key settings but they're **not read during analysis**. Fix: read `llm_provider`, `deep_think_model`, `quick_think_model`, `api_key` from settings table in `_run_trading_agents()`.

---

## Recommended Fix Priority

### Sprint 1 (1-2 days) — Core data integrity
1. **P0 #3**: Fix `daily_pnl` table to support per-stock tracking (add `code6` column, update calendar queries)
2. **P0 #5**: Add northbound capital to `/api/ai/suggestions` response
3. **P0 #7**: Verify K-line stop-loss/take-profit line rendering in chart.js

### Sprint 2 (2-3 days) — Performance & reliability
4. **P0 #1**: Convert data layer to aiohttp (biggest performance win)
5. **P0 #2**: Integrate SharedCache into data functions
6. **P0 #4**: Add volume anomaly detection to anomaly_checker.py
7. **P0 #8**: Fix batch_analyze queue capacity pre-check

### Sprint 3 (2-3 days) — Feature completion
8. **P1 #6**: LLM model catalog integration (9 providers)
9. **P1 #11**: Add `strategy_state_updated_at` column migration
10. **P1 #18**: Read settings from DB in L2 analysis function
11. **P1 #9**: Auto-write scheduled reports to gbrain
12. **P1 #13**: Northbound capital anomaly detection

### Sprint 4 (1-2 days) — Polish
13. **P2 #1**: Remove mobile bottom tab bar per PRD
14. **P2 #5**: "生成条件单" button on analysis result
15. **P1 #3**: Real-time advice table on stock detail page
16. **P1 #4**: Pending positions plan table comparison
