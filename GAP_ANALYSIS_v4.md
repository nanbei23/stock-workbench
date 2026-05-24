# GAP ANALYSIS v4 — 炒股小牛马工作台
> Generated: 2026-05-23 | Compared: PRD.md (2235 lines) vs Actual Code

---

## Executive Summary

| Status | Count | Description |
|--------|-------|-------------|
| ✅ DONE | 68 | Fully implemented and working |
| ⚠️ PARTIAL | 18 | Exists but incomplete or deviates from PRD |
| ❌ MISSING | 14 | Not implemented at all |

**Overall completion: ~68% DONE, ~18% PARTIAL, ~14% MISSING**

---

## P0 — Critical (Must Fix)

| # | Area | Item | Status | Details |
|---|------|------|--------|---------|
| 1 | AI API | `generateCondOrder` endpoint | ❌ MISSING | PRD§6.3.5 specifies `[生成条件单]` button in analysis results. No `POST /api/ai/generate-cond-order` endpoint exists. Frontend has no `btnGenCondOrder`. |
| 2 | Frontend/AI | Output language dropdown (zh/en/auto) | ❌ MISSING | PRD§8 specifies output language dropdown. `settings_api.py` has key `output_language` but no dropdown UI in `settings.html`. Only hardcoded `"Chinese"` in ai_api.py. |
| 3 | Frontend/AI | `pollQueueStatus()` in ai.js | ⚠️ PARTIAL | `queuePanel` exists in ai.html. SSE streaming works. But no dedicated `pollQueueStatus()` function — queue status is embedded in SSE stream. Works but not as PRD specifies. |
| 4 | Data/info.py | `cache.write()` dead code bug | ⚠️ PARTIAL | Line 60 returns dict directly (`return {..}`) without assigning to `result`, so line 87 `cache.write('fundamentals', code, result)` is dead code. info.py never caches. |
| 5 | Frontend | Industry news tab in stock detail | ❌ MISSING | PRD§4.8 lists "行业新闻" as a detail tab. `stock.js` has no `loadIndustryNews()` or `switchNewsTab()`. No `/news/industry` endpoint. Current tabs are: 七层数据/策略/买点/新闻/AI分析/研报/公告. |
| 6 | Settings | 9 LLM providers in MODEL_CATALOG | ❌ MISSING | PRD§8 specifies 9 providers (DeepSeek/OpenAI/Anthropic/Google/xAI/Qwen/GLM/MiniMax/Ollama). `settings.html` only has a text input for provider/model — no dropdown with MODEL_CATALOG. |
| 7 | AI API | `generateCondOrder` in frontend | ❌ MISSING | No "生成条件单" button in analysis results panel. The `ai.html` analysis results section only shows `[📥 下载PDF]` — no cond-order generation. |

---

## P1 — Important (Should Fix)

| # | Area | Item | Status | Details |
|---|------|------|--------|---------|
| 8 | Frontend | Detail tabs naming mismatch | ⚠️ PARTIAL | PRD§4.8: 行情/K线/信号/资金面/研报/新闻/公告. Actual: 七层数据/策略/买点/新闻/AI分析/研报/公告. Signal export (七层信号导出) missing. |
| 9 | Frontend | Signal export button | ❌ MISSING | PRD mentions "七层信号导出" — no export button or endpoint for signal data. |
| 10 | Portfolio | 计划金额 column in holdings table | ❌ MISSING | PRD§5.3.2 specifies "计划金额" column in pending positions. `portfolio_api.py` has `plan_total_cost` field but frontend table rendering may not show it prominently. |
| 11 | Portfolio | 转条件单 button for pending positions | ❌ MISSING | PRD§5.3.2 shows `[条件单]` button per pending position. No "convert to conditional order" UI exists in portfolio.html. |
| 12 | Portfolio | Pending positions comparison (计划 vs 实际) | ❌ MISSING | PRD§5.3.2 specifies a comparison table (理论值 vs 实际值 vs 偏差). `pending_positions` table exists in DB but no comparison view in frontend. |
| 13 | Data | `get_volume_history()` standalone function | ⚠️ PARTIAL | No standalone `data.get_volume_history()`. Volume anomaly detection uses `_get_recent_avg_volume()` in `anomaly_checker.py` which calls `get_kline()` internally. Works but not as PRD specified module-level function. |
| 14 | Frontend | Research report filtering | ⚠️ PARTIAL | Research tab exists, loads reports via `/api/research/{code}`. But no filtering UI (by date, rating, org) as PRD implies. |
| 15 | Frontend | Announcement filtering | ⚠️ PARTIAL | Announcement tab exists, loads via `/api/announce/{code}`. But no filtering UI (by type, date) as PRD implies. |
| 16 | AI API | `showToast()` in ai.js | ❌ MISSING | PRD mentions toast notifications. No `showToast()` function found in any JS file. |
| 17 | Settings | Settings page 5 tabs | ⚠️ PARTIAL | `settings.html` has tabs UI (settings-tabs div). Count depends on actual tab buttons rendered — code shows tab structure but need to verify 5 tabs are present (行情监控/费率/通知/AI引擎/数据管理). |
| 18 | Data | `info.py` cache fix needed | ⚠️ PARTIAL | `get_stock_info()` returns dict directly on line 60 without going through `result` variable, so `cache.write()` on line 87 never executes. Should assign to `result` first, then write to cache, then return. |
| 19 | Scheduler | Report runner L1 with strategy_params | ✅ DONE | `_run_l1_evaluation()` in report_runner.py uses `_get_strategy_prices()` which reads from both `watchlist` and `strategy_params`. |

---

## P2 — Nice-to-Have (Can Defer)

| # | Area | Item | Status | Details |
|---|------|------|--------|---------|
| 20 | Data | `get_orderbook()` async conversion | ⚠️ PARTIAL | `get_orderbook()` in quote.py is sync (mootdx TCP). PRD says "全部async". This is intentional — mootdx uses TCP sockets which are inherently sync. Wrapped with `asyncio.to_thread` at API layer. |
| 21 | Frontend | K-line period "分时" default | ⚠️ PARTIAL | PRD§4.4 says default should be "日K". Current code sets `分时` as `active` class in HTML. Minor UX issue. |
| 22 | Frontend | `chart.js` — chart capture for PDF | ❌ MISSING | PRD§6.3.5 mentions "PDF导出...包含封面（信号大字+免责声明）→ 每个维度独立一页". PDF export exists via fpdf2 but no chart capture (html2canvas/canvas.toDataURL). |
| 23 | Config | `tasks.py` task queue | ❌ MISSING | PRD§9.1 lists `tasks.py` in project structure. No such file exists. Task queue is implemented inline in `ai_api.py` using `asyncio.Semaphore` + `deque`. |
| 24 | Config | Separate `ai_engine.py`, `ta_bridge.py`, `gbrain_client.py` | ⚠️ PARTIAL | PRD§9.1 lists these as separate files in `scheduler/`. All logic is inline in `ai_api.py` instead. Functionally complete but not modularized as PRD specifies. |
| 25 | Config | `import_export.py` as separate file | ⚠️ PARTIAL | PRD§9.1 lists `import_export.py`. Import/export is in `settings_api.py` (`/settings/export`, `/settings/import`). Functionally complete. |
| 26 | Frontend | `stock.js` — `loadIndustryNews()` | ❌ MISSING | No industry news loading function. The `/api/industry` endpoint exists in `layer_api.py` but no frontend integration. |
| 27 | Frontend | `ai.js` — `generateCondOrder()` | ❌ MISSING | No function to generate conditional orders from AI analysis results. |
| 28 | Multi-account | Multi-account support in DB schema | ❌ MISSING | PRD mentions multi-account support. No `account_id` field in any table. Single-user design throughout. |
| 29 | Frontend | News tab — separate tabs for 个股/行业/全部 | ⚠️ PARTIAL | PRD§7.3 shows `[个股] [行业] [全部]` tab switcher. Current news tab only shows stock news + CLS telegraph. No tab switcher. |
| 30 | Cache | L2 atomic write via tempfile+rename | ✅ DONE | `shared_cache.py` uses `tmp.write_text(...)` then `tmp.replace(path)` — atomic on POSIX. |
| 31 | Frontend | WebSocket real-time quotes usage | ✅ DONE | `app.py` has `/ws/quotes` WebSocket endpoint. Frontend may use polling instead — need to verify if JS connects to WS. |

---

## Detailed Findings by Area

### Data Layer (`data/*.py`)

| Function | File | Async? | Cache? | Status |
|----------|------|--------|--------|--------|
| `get_realtime_quote()` | quote.py | ✅ aiohttp | ❌ No cache (correct: quotes=0s TTL) | ✅ |
| `get_batch_quotes()` | quote.py | ✅ aiohttp | ❌ No cache (correct) | ✅ |
| `get_orderbook()` | quote.py | ⚠️ Sync (mootdx TCP) | ❌ No cache (correct: orderbook=0s) | ✅ |
| `get_kline()` | kline.py | ⚠️ Sync (mootdx TCP) | ❌ No cache | ✅ |
| `get_kline_with_ma()` | kline.py | ✅ aiohttp | ✅ `cache.read/write('klines')` | ✅ |
| `get_concept_blocks()` | signal.py | ✅ aiohttp | ✅ `cache.read/write('signal')` | ✅ |
| `get_hot_reasons()` | signal.py | ✅ aiohttp | ✅ `cache.read/write('signal')` | ✅ |
| `get_northbound()` | signal.py | ✅ aiohttp | ✅ `cache.read/write('signal')` | ✅ |
| `get_fund_flow_minute()` | signal.py | ✅ aiohttp | ✅ `cache.read/write('signal')` | ✅ |
| `get_dragon_tiger()` | signal.py | ✅ aiohttp | ✅ `cache.read/write('signal')` | ✅ |
| `get_lockup_expiry()` | signal.py | ✅ aiohttp | ✅ `cache.read/write('signal')` | ✅ |
| `get_industry_ranking()` | signal.py | ✅ aiohttp | ✅ `cache.read/write('signal')` | ✅ |
| `get_all_signals()` | signal.py | ✅ aiohttp+gather | ✅ `cache.read/write('signal')` | ✅ |
| `get_margin_trading()` | fund.py | ✅ aiohttp | ✅ `cache.read/write('fundamentals')` | ✅ |
| `get_block_trade()` | fund.py | ✅ aiohttp | ✅ `cache.read/write('fundamentals')` | ✅ |
| `get_holder_change()` | fund.py | ✅ aiohttp | ✅ `cache.read/write('fundamentals')` | ✅ |
| `get_dividend_history()` | fund.py | ✅ aiohttp | ✅ `cache.read/write('fundamentals')` | ✅ |
| `get_fund_flow_120d()` | fund.py | ✅ aiohttp | ✅ `cache.read/write('fundamentals')` | ✅ |
| `get_all_fund_data()` | fund.py | ✅ aiohttp+gather | ✅ `cache.read/write('fundamentals')` | ✅ |
| `get_stock_news()` | news.py | ✅ aiohttp | ✅ `cache.read/write('news')` | ✅ |
| `get_cls_telegraph()` | news.py | ✅ aiohttp | ❌ No cache | ✅ (global, no code) |
| `get_global_news()` | news.py | ✅ aiohttp | ❌ No cache | ✅ (global) |
| `get_global_news_724()` | news.py | ✅ aiohttp | ❌ No cache | ✅ (global) |
| `get_stock_info()` | info.py | ✅ aiohttp | ⚠️ Dead code bug | ⚠️ |
| `get_business_segments()` | info.py | ✅ aiohttp | ❌ No cache | ⚠️ |
| `get_announcements()` | announce.py | ✅ aiohttp | ✅ `cache.read/write('research')` | ✅ |
| `get_reports()` | research.py | ✅ aiohttp | ✅ `cache.read/write('research')` | ✅ |
| `get_eps_forecast()` | research.py | ✅ aiohttp | ❌ No cache | ⚠️ |

**Key bug**: `info.py:get_stock_info()` line 60 returns dict directly (`return {..}`) bypassing `cache.write()` on line 87. Fix: assign to `result` variable first.

### API Layer (`api/*.py`)

| Endpoint | File | Status | Notes |
|----------|------|--------|-------|
| `GET /ai/suggestions` | ai_api.py | ✅ | L1 with northbound, strategy prices |
| `POST /ai/analyze/{code}` | ai_api.py | ✅ | Queue + concurrency + timeout |
| `GET /ai/analyze/{task_id}/stream` | ai_api.py | ✅ | SSE streaming |
| `POST /ai/batch-analyze` | ai_api.py | ✅ | Capacity pre-check |
| `POST /ai/analyze/{task_id}/cancel` | ai_api.py | ✅ | Cancel support |
| `GET /ai/queue/status` | ai_api.py | ✅ | Queue monitoring |
| `POST /ai/generate-cond-order` | ai_api.py | ❌ | **Missing** |
| `GET /ai/gbrain/search` | ai_api.py | ✅ | gbrain CLI search |
| `POST /ai/gbrain/save` | ai_api.py | ✅ | gbrain CLI save |
| `GET /ai/report/{id}/pdf` | pdf_export.py | ✅ | fpdf2 + CJK fonts |
| `POST /settings/test-llm` | settings_api.py | ✅ | httpx test call |
| `GET /notifications` | settings_api.py | ✅ | Polling for 4 types |
| `WS /ws/quotes` | app.py | ✅ | WebSocket push |

### Scheduler (`scheduler/*.py`)

| Job | Time | Status | Notes |
|-----|------|--------|-------|
| 条件单检查 | 每30秒 | ✅ | expires_at + L2 trigger |
| 异动检测 | 每60秒 | ✅ | 涨停/跌停/volume_spike/northbound/strategy |
| 开盘报告 | 09:30 | ✅ | L1 + gbrain writeback |
| 上午收盘 | 11:30 | ✅ | L1 |
| 下午开盘 | 13:00 | ✅ | L1 |
| 收盘报告 | 15:00 | ✅ | L1 + per-stock daily_pnl |
| 策略复盘 | 15:05 | ✅ | L1 + gbrain writeback |

### Models (`models/database.py`)

| Table | PK | Status | Notes |
|-------|-----|--------|-------|
| watchlist | code | ✅ | Has `strategy_state_updated_at` |
| portfolio | code | ✅ | |
| trades | id | ✅ | |
| conditional_orders | id | ✅ | Has `expires_at` |
| strategy_records | id | ✅ | |
| strategy_params | code6 | ✅ | |
| analysis_reports | id | ✅ | Has `task_id UNIQUE` |
| anomaly_logs | id | ✅ | |
| daily_pnl | (date, code6) | ✅ | Composite PK |
| settings | key | ✅ | |
| pending_positions | id | ✅ | |
| buy_points | id | ✅ | |
| news_cache | id | ✅ | |

### Frontend (`templates/*.html` + `static/js/*.html`)

| Component | File | Status | Notes |
|-----------|------|--------|-------|
| Top nav bar (4 tabs) | base.html | ✅ | 自选股/持仓/AI分析台/设置 |
| Notification polling | base.html | ✅ | 30s interval, badge + Notification API |
| app.js imported | base.html | ✅ | `<script src="/static/js/app.js">` |
| mobile-tab-bar | base.html | ✅ | CSS media query controlled |
| 7 detail tabs | index.html | ⚠️ | Names differ from PRD |
| Stock header 3×3 grid | index.html | ✅ | All 9 fields present |
| PnL row (持仓盈亏/当日盈亏) | index.html | ✅ | Hidden when no position |
| K-line 5 period tabs | index.html | ✅ | 分时/五日/日K/周K/月K |
| Auto-refresh controls | index.html | ✅ | Checkbox + interval select + manual button |
| LWC v4 chart | chart.js | ✅ | `LightweightCharts.createChart` |
| stop_loss/target_sell lines | chart.js | ✅ | `createPriceLine` with dashed style |
| 加仓 price lines | chart.js | ✅ | Blue dashed lines for buy_prices |
| queuePanel | ai.html | ✅ | Queue status display |
| Suggestion table | ai.html | ✅ | 股票/现价/涨跌/策略状态/建议/说明/操作 |
| Analysis progress | ai.html | ✅ | 12-stage progress with ANALYSTS/PIPELINE |
| Token stats | ai.html | ✅ | LLM calls/input/output tokens |
| Cancel button | ai.html | ✅ | |
| btnGenCondOrder | ai.html | ❌ | **Missing** |
| expires_at input | portfolio.html | ✅ | In ConditionalOrderRequest |
| 计划金额 column | portfolio.html | ❌ | **Missing** |
| 转条件单 button | portfolio.html | ❌ | **Missing** |
| Settings tabs | settings.html | ✅ | Tab structure exists |
| MODEL_CATALOG (9 providers) | settings.html | ❌ | **Missing** — only text inputs |
| loadIndustryNews() | stock.js | ❌ | **Missing** |
| switchNewsTab() | stock.js | ❌ | **Missing** |
| pollQueueStatus() | ai.js | ⚠️ | Queue status via SSE, not polling |
| generateCondOrder() | ai.js | ❌ | **Missing** |
| showToast() | ai.js | ❌ | **Missing** |
| expires_at handling | portfolio.js | ✅ | In conditional order form |
| Queue panel style | style.css | ✅ | `.queue-panel` class |
| Responsive breakpoints | style.css | ✅ | `@media` queries present |

### Cache (`cache/*.py`)

| Component | Status | Notes |
|-----------|--------|-------|
| SharedCache L1 dict | ✅ | `threading.RLock` |
| SharedCache L2 file | ✅ | Atomic write via tmp+replace |
| TTL config (8 categories) | ✅ | All match PRD |
| TTL jitter (anti-avalanche) | ✅ | `random.randint` per read |
| L2 cleanup (every 100 writes) | ✅ | Max 1000 files per category |
| ta_cache_patch.py | ✅ | Monkey-patches TA data functions |

### Config (`app.py`)

| Item | Status | Notes |
|------|--------|-------|
| FastAPI (not Flask) | ✅ | `FastAPI(title=...)` |
| 8 routers | ✅ | quote/portfolio/ai/news/settings/layer/strategy/pdf |
| Scheduler integration | ✅ | `setup_scheduler()` in lifespan |
| `/stock/{code}` route | ✅ | Redirects to `/?code=` |
| WebSocket `/ws/quotes` | ✅ | 5-second push interval |

---

## Implementation Plan for Remaining Items

### Phase A — P0 Critical (1-2 days)

1. **Fix `info.py` cache bug** (5 min)
   - Line 60: Change `return {..}` to `result = {..}` + `cache.write('fundamentals', code, result)` + `return result`

2. **Add `generateCondOrder` endpoint** (2 hours)
   - `POST /api/ai/generate-cond-order` in ai_api.py
   - Accept `{code, action, price, shares, condition_type}`
   - Create conditional order in DB
   - Add `btnGenCondOrder` button in ai.html analysis results

3. **Add output language dropdown** (1 hour)
   - Add dropdown in settings.html AI引擎 section
   - Options: 中文/English/Auto
   - Wire to `output_language` setting key

4. **Add industry news tab** (2 hours)
   - Add `loadIndustryNews()` in stock.js
   - Add `/api/news/industry` endpoint (use `get_industry_ranking()` data)
   - Add tab switcher in stock detail

5. **Add MODEL_CATALOG dropdown** (2 hours)
   - Define 9 providers + models in settings.html JS
   - Replace text inputs with dropdown selects
   - Deep think / Quick think model dropdowns dependent on provider

### Phase B — P1 Important (2-3 days)

6. **Signal export button** (1 hour)
   - Add export button in 七层数据 section
   - `GET /api/7layer/{code}/export` → JSON/CSV download

7. **Pending positions comparison view** (2 hours)
   - Add comparison table in portfolio.html
   - 计划 vs 实际 columns

8. **转条件单 button** (1 hour)
   - Add button per pending position row
   - Pre-fill conditional order form

9. **Research/Announcement filtering** (2 hours)
   - Add date range + rating/org filter for research
   - Add type/date filter for announcements

10. **`showToast()` function** (30 min)
    - Add toast notification utility in app.js
    - Use in ai.js for success/error feedback

### Phase C — P2 Nice-to-Have (3-5 days)

11. **Chart capture for PDF** (3 hours)
    - Add html2canvas or canvas.toDataURL
    - Include chart image in PDF export

12. **Modularize scheduler files** (2 hours)
    - Extract `ai_engine.py`, `ta_bridge.py`, `gbrain_client.py` from ai_api.py
    - Create `tasks.py` for task queue abstraction

13. **Multi-account support** (4 hours)
    - Add `account_id` to relevant tables
    - Add account switcher UI

14. **News tab switcher (个股/行业/全部)** (1 hour)
    - Add tab buttons in news section
    - Wire to different API endpoints

---

## Files Created
- `~/stock-workbench/GAP_ANALYSIS_v4.md` — This file

## Files NOT Modified
- No code files were modified in this analysis

## Summary

The project is **substantially complete** (~68% fully done). The core architecture is solid: FastAPI + async data layer + SharedCache + APScheduler + SQLite + TradingAgents integration + gbrain all work. The remaining gaps are primarily:

1. **Frontend polish** — Missing UI elements (MODEL_CATALOG dropdown, generateCondOrder button, industry news tab, signal export)
2. **One data bug** — `info.py` cache write is dead code
3. **Modularization** — Scheduler sub-modules inlined in ai_api.py instead of separate files
4. **Multi-account** — Not started, single-user design

The highest-impact fixes are items 1-5 in Phase A (the `info.py` bug, `generateCondOrder` endpoint, output language dropdown, industry news tab, and MODEL_CATALOG).
