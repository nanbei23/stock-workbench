# Holding Context Report Signals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every single-stock report distinguish objective stock research from account-specific execution advice by injecting real holding context and saving both research and account signals.

**Architecture:** Add one focused service that builds a per-stock `holding_context` from portfolio, account assets, shadow positions, signal tracking, and previous reports. Feed that context into both batch snapshot report generation (`scripts/batch_research.py`) and native TradingAgents persistence (`scheduler/ta_bridge.py`), while preserving `analysis_reports.signal` as the account-action signal used by UI, AI performance, and shadow-trading flows. Store the richer fields in `analysis_reports.raw_state` so no schema migration is required.

**Tech Stack:** Python 3.12, SQLite, existing `unittest` suite, FastAPI app, current report rendering in `templates/report_detail.html` and `static/js/report-detail.js`.

**Execution status (2026-06-04):** Implemented through v2.9.12. Completed the holding context service, batch snapshot prompt/save changes, native TradingAgents result normalization, report detail UI, AI shadow regression coverage, release guard coverage, version metadata, changelog, focused Python tests, TypeScript check, Vite build, and Playwright smoke. No database migration is required.

---

## File Structure

- Create `services/holding_context_service.py`: read-only service that builds `holding_context` for one or more codes.
- Modify `scripts/batch_research.py`: inject holding context into snapshot prompts, debate prompts, snapshot-tradingagents flow, normalization, and report save.
- Modify `scheduler/ta_bridge.py`: inject holding context into native TradingAgents task result before saving and normalize account signal before signal tracking.
- Modify `static/js/report-detail.js`: render research signal, account signal, and holding context when present.
- Modify `templates/report_detail.html`: add a compact account-context section in the report detail page.
- Add tests in `tests/test_holding_context_service.py`.
- Extend tests in `tests/test_batch_research.py`, `tests/test_batch_research_service.py`, and `tests/test_ta_bridge_persistence.py`.
- Extend release guard tests in `tests/test_release_migration.py`.

---

### Task 1: Add Holding Context Service

**Files:**
- Create: `services/holding_context_service.py`
- Test: `tests/test_holding_context_service.py`

- [ ] **Step 1: Write failing tests for held, empty, and shadow-context stocks**

Create `tests/test_holding_context_service.py`:

```python
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import models.database as database
from services import holding_context_service


class HoldingContextServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    async def test_build_context_for_real_holding(self):
        await database.init_db()
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("cash_balance_default", "200000"))
            db.execute(
                "INSERT INTO portfolio (code, name, total_shares, avg_cost, current_price, account_id) VALUES (?, ?, ?, ?, ?, ?)",
                ("002241", "歌尔股份", 1000, 26.006, 25.5, "default"),
            )
            db.execute(
                "INSERT INTO analysis_reports (id, code, task_id, signal, confidence, risk_score, raw_state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (7, "002241", "old", "BUY", 0.72, 33, json.dumps({"research_signal": "BUY"}), "2026-06-03 14:00:00"),
            )
            db.execute(
                "INSERT INTO signal_tracking (report_id, code, name, signal, signal_date, entry_price, current_price, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (7, "002241", "歌尔股份", "BUY", "2026-06-03", 26.2, 25.5, "open"),
            )
            db.commit()

        ctx = await holding_context_service.build_holding_context("002241", account_id="default")

        self.assertTrue(ctx["is_holding"])
        self.assertEqual(ctx["code"], "002241")
        self.assertEqual(ctx["shares"], 1000.0)
        self.assertEqual(ctx["avg_cost"], 26.006)
        self.assertEqual(ctx["current_price"], 25.5)
        self.assertLess(ctx["holding_pnl"], 0)
        self.assertEqual(ctx["last_report"]["signal"], "BUY")
        self.assertEqual(ctx["signal_tracking"]["status"], "open")
        self.assertIn("真实持仓", ctx["prompt_context"])

    async def test_build_context_for_empty_watch_stock(self):
        await database.init_db()
        ctx = await holding_context_service.build_holding_context("000001", account_id="default")

        self.assertFalse(ctx["is_holding"])
        self.assertEqual(ctx["position_action_scope"], "watch_only")
        self.assertIn("当前账户未持仓", ctx["prompt_context"])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv312/bin/python -m unittest tests.test_holding_context_service -v
```

Expected: FAIL with `ImportError` or missing `build_holding_context`.

- [ ] **Step 3: Implement the service**

Create `services/holding_context_service.py`:

```python
"""Per-stock holding context for account-aware report generation."""

from __future__ import annotations

import json
from typing import Any

from models.database import get_db


def _loads(value: Any, fallback: Any):
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _float(value: Any) -> float:
    try:
        return round(float(value or 0), 3)
    except (TypeError, ValueError):
        return 0.0


def _prompt_context(ctx: dict[str, Any]) -> str:
    if not ctx["is_holding"]:
        return (
            "## 当前账户持仓上下文\n"
            f"- 股票: {ctx['name']} {ctx['code']}\n"
            "- 当前账户未持仓，只能输出观察、建仓或回避建议。\n"
            "- 研究信号可以独立判断股票质量；账户信号应按空仓视角判断是否值得新开仓。\n"
        )
    return (
        "## 当前账户持仓上下文\n"
        f"- 股票: {ctx['name']} {ctx['code']}\n"
        f"- 真实持仓: {ctx['shares']:.3f} 股\n"
        f"- 持仓成本: {ctx['avg_cost']:.3f}\n"
        f"- 当前价: {ctx['current_price']:.3f}\n"
        f"- 持仓市值: {ctx['market_value']:.3f}\n"
        f"- 持仓盈亏: {ctx['holding_pnl']:.3f} ({ctx['holding_pnl_pct']:.3f}%)\n"
        f"- 仓位占比: {ctx['position_pct_of_assets']:.3f}%\n"
        f"- 上次账户信号: {(ctx.get('last_report') or {}).get('signal') or '--'}\n"
        "- 研究信号只判断股票本身；账户信号必须结合成本、仓位、浮盈亏和可用资金。\n"
    )


async def build_holding_context(code: str, *, account_id: str = "default") -> dict[str, Any]:
    code = str(code or "")[:6]
    account_id = account_id or "default"
    db = await get_db()
    try:
        position = await (
            await db.execute(
                """
                SELECT code, name, total_shares, avg_cost, current_price, market_value,
                       unrealized_pnl, unrealized_pnl_pct, account_id
                FROM portfolio
                WHERE account_id = ? AND code = ?
                """,
                (account_id, code),
            )
        ).fetchone()
        cash_row = await (
            await db.execute("SELECT value FROM settings WHERE key = ?", (f"cash_balance_{account_id}",))
        ).fetchone()
        report = await (
            await db.execute(
                """
                SELECT id, code, signal, confidence, risk_score, raw_state, created_at
                FROM analysis_reports
                WHERE code = ?
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT 1
                """,
                (code,),
            )
        ).fetchone()
        tracking = await (
            await db.execute(
                """
                SELECT id, report_id, code, signal, status, entry_price, current_price, pnl_pct, excess_return
                FROM signal_tracking
                WHERE code = ?
                ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, id DESC
                LIMIT 1
                """,
                (code,),
            )
        ).fetchone()
        shadow = await (
            await db.execute(
                """
                SELECT code, name, shares, avg_cost, market_value, unrealized_pnl, unrealized_pnl_pct, status
                FROM ai_shadow_positions
                WHERE code = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (code,),
            )
        ).fetchone()
    finally:
        await db.close()

    pos = dict(position) if position else {}
    last_report = dict(report) if report else {}
    last_report["raw_state"] = _loads(last_report.get("raw_state"), {}) if last_report else {}
    cash = _float(cash_row["value"] if cash_row else 0)
    shares = _float(pos.get("total_shares"))
    avg_cost = _float(pos.get("avg_cost"))
    current_price = _float(pos.get("current_price"))
    market_value = _float(pos.get("market_value")) or round(shares * current_price, 3)
    total_assets = round(cash + market_value, 3)
    holding_pnl = _float(pos.get("unrealized_pnl")) or round((current_price - avg_cost) * shares, 3)
    cost_amount = round(avg_cost * shares, 3)
    holding_pnl_pct = _float(pos.get("unrealized_pnl_pct")) or (round(holding_pnl / cost_amount * 100, 3) if cost_amount else 0.0)

    ctx = {
        "version": "holding-context-v1",
        "account_id": account_id,
        "code": code,
        "name": pos.get("name") or (dict(shadow).get("name") if shadow else code),
        "is_holding": shares > 0,
        "shares": shares,
        "avg_cost": avg_cost,
        "current_price": current_price,
        "market_value": market_value,
        "cash": cash,
        "total_assets": total_assets,
        "position_pct_of_assets": round(market_value / total_assets * 100, 3) if total_assets else 0.0,
        "holding_pnl": holding_pnl,
        "holding_pnl_pct": holding_pnl_pct,
        "last_report": last_report,
        "signal_tracking": dict(tracking) if tracking else {},
        "shadow_position": dict(shadow) if shadow else {},
        "position_action_scope": "holding_action" if shares > 0 else "watch_only",
    }
    ctx["prompt_context"] = _prompt_context(ctx)
    return ctx
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
.venv312/bin/python -m unittest tests.test_holding_context_service -v
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add services/holding_context_service.py tests/test_holding_context_service.py
git commit -m "Add holding context service for report generation"
```

---

### Task 2: Extend Snapshot Report Schema and Save Logic

**Files:**
- Modify: `scripts/batch_research.py`
- Test: `tests/test_batch_research.py`

- [ ] **Step 1: Write failing test for account-aware report persistence**

Add to `tests/test_batch_research.py`:

```python
async def test_snapshot_report_saves_research_and_account_signals_with_holding_context(self):
    with sqlite3.connect(self.db_path) as db:
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("cash_balance_default", "200000"))
        db.execute(
            "INSERT INTO portfolio (code, name, total_shares, avg_cost, current_price, account_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("000001", "平安银行", 1000, 12.0, 10.0, "default"),
        )
        db.execute(
            "INSERT INTO stock_data_snapshots (code, name, snapshot_json, validation_json, summary_json) VALUES (?, ?, ?, ?, ?)",
            ("000001", "平安银行", json.dumps({"market": {"quote": {"price": 10.0}}}), json.dumps({"ok": True}), "{}"),
        )
        db.commit()

    item = batch_research.RankedCandidate(code="000001", name="平安银行", group_name="默认", quote={"price": 10.0}, score=1)
    snapshot_row = batch_research._latest_snapshot(self.db_path, "000001")
    ctx = await holding_context_service.build_holding_context("000001", account_id="default")
    report_id = batch_research._save_snapshot_report(
        self.db_path,
        item,
        {
            "research_signal": "BUY",
            "account_signal": "HOLD",
            "position_action": "hold",
            "action_reason": "已有持仓且浮亏，先等待站回成本线。",
            "signal": "BUY",
            "confidence": 0.7,
            "risk_score": 40,
            "final_decision": "研究偏多，但账户动作持有。",
        },
        snapshot_row,
        run_id="test",
        duration_seconds=1.2,
        model="test-model",
        holding_context=ctx,
    )

    with sqlite3.connect(self.db_path) as db:
        row = db.execute("SELECT signal, raw_state FROM analysis_reports WHERE id = ?", (report_id,)).fetchone()
    raw = json.loads(row["raw_state"])
    self.assertEqual(row["signal"], "HOLD")
    self.assertEqual(raw["research_signal"], "BUY")
    self.assertEqual(raw["account_signal"], "HOLD")
    self.assertEqual(raw["position_action"], "hold")
    self.assertTrue(raw["holding_context"]["is_holding"])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv312/bin/python -m unittest tests.test_batch_research.BatchResearchScriptTests.test_snapshot_report_saves_research_and_account_signals_with_holding_context -v
```

Expected: FAIL because `_save_snapshot_report()` has no `holding_context` parameter and saves `signal`.

- [ ] **Step 3: Update normalization and save logic**

Modify `scripts/batch_research.py`:

```python
ACCOUNT_SIGNAL_VALUES = POSITIVE_SIGNALS | WATCH_SIGNALS | {"UNDERWEIGHT", "SELL", "STRONG_SELL"}


def _normalise_snapshot_result(result: dict[str, Any]) -> dict[str, Any]:
    final_decision = _text_value(result, "final_decision")
    trader_plan = _text_value(result, "trader_plan")
    parse_text = "\n".join([final_decision, trader_plan, json.dumps(result, ensure_ascii=False, default=_json_default)])
    research_signal = str(result.get("research_signal") or result.get("signal") or "").upper().strip()
    account_signal = str(result.get("account_signal") or "").upper().strip()
    if research_signal not in ACCOUNT_SIGNAL_VALUES:
        research_signal = extract_signal(parse_text)
    if account_signal not in ACCOUNT_SIGNAL_VALUES:
        account_signal = research_signal or extract_signal(parse_text)
    confidence = _float_or_none(result.get("confidence"))
    if confidence is None:
        confidence = extract_confidence(parse_text)
    if confidence is not None and confidence > 1:
        confidence = round(confidence / 100, 3)
    risk_score = _float_or_none(result.get("risk_score"))
    if risk_score is None:
        risk_score = extract_risk_score(parse_text)
    if risk_score is not None and risk_score <= 1:
        risk_score = round(risk_score * 100, 3)
    return {
        **result,
        "research_signal": research_signal or "HOLD",
        "account_signal": account_signal or "HOLD",
        "signal": account_signal or "HOLD",
        "position_action": result.get("position_action") or "watch",
        "action_reason": result.get("action_reason") or "",
        "confidence": round(confidence, 3) if confidence is not None else None,
        "risk_score": round(risk_score, 3) if risk_score is not None else None,
        "target_price": extract_target_price(parse_text),
    }
```

Change `_save_snapshot_report()` signature:

```python
def _save_snapshot_report(..., investment_profile: dict[str, Any] | None = None, holding_context: dict[str, Any] | None = None) -> int:
```

Add to `raw_state`:

```python
"research_signal": normalized["research_signal"],
"account_signal": normalized["account_signal"],
"position_action": normalized.get("position_action"),
"action_reason": normalized.get("action_reason"),
```

And:

```python
if holding_context:
    raw_state["holding_context"] = holding_context
```

Keep DB insert `signal` as:

```python
normalized["account_signal"]
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
.venv312/bin/python -m unittest tests.test_batch_research.BatchResearchScriptTests.test_snapshot_report_saves_research_and_account_signals_with_holding_context -v
```

Expected: OK.

- [ ] **Step 5: Run existing batch tests**

Run:

```bash
.venv312/bin/python -m unittest tests.test_batch_research tests.test_batch_research_service -v
```

Expected: OK.

- [ ] **Step 6: Commit**

```bash
git add scripts/batch_research.py tests/test_batch_research.py
git commit -m "Persist account-aware report signals"
```

---

### Task 3: Inject Holding Context Into Batch Prompt Flows

**Files:**
- Modify: `scripts/batch_research.py`
- Test: `tests/test_batch_research.py`, `tests/test_batch_research_service.py`

- [ ] **Step 1: Write failing test for prompt injection**

Add to `tests/test_batch_research.py`:

```python
def test_snapshot_prompt_includes_holding_context_and_two_layer_signal_schema(self):
    stock = batch_research.RankedCandidate(code="002241", name="歌尔股份", group_name="默认", quote={"price": 25.5}, score=1)
    snapshot_row = {
        "id": 9,
        "snapshot": {"market": {"quote": {"price": 25.5}}},
        "validation": {"ok": True},
        "summary": {},
        "created_at": "2026-06-04",
    }
    prompt = batch_research._snapshot_prompt(
        stock,
        snapshot_row,
        investment_profile_context="",
        holding_context={"prompt_context": "真实持仓: 1000.000 股\n持仓成本: 26.006"},
    )

    self.assertIn("research_signal", prompt)
    self.assertIn("account_signal", prompt)
    self.assertIn("position_action", prompt)
    self.assertIn("真实持仓: 1000.000 股", prompt)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv312/bin/python -m unittest tests.test_batch_research.BatchResearchScriptTests.test_snapshot_prompt_includes_holding_context_and_two_layer_signal_schema -v
```

Expected: FAIL because `_snapshot_prompt()` does not accept `holding_context`.

- [ ] **Step 3: Update prompt builders**

Modify `_snapshot_prompt()` and `_snapshot_debate_prompt()` signatures:

```python
def _snapshot_prompt(stock, snapshot_row, investment_profile_context: str = "", holding_context: dict[str, Any] | None = None) -> str:
```

In JSON schema text replace `"signal"` only schema with:

```text
"research_signal": "STRONG_BUY|BUY|OVERWEIGHT|HOLD|UNDERWEIGHT|SELL|STRONG_SELL",
"account_signal": "STRONG_BUY|BUY|OVERWEIGHT|HOLD|UNDERWEIGHT|SELL|STRONG_SELL",
"position_action": "buy|add|hold|reduce|sell|watch|avoid|take_profit",
"action_reason": "解释为什么账户动作不同于股票研究信号",
```

Add prompt text:

```python
holding_context_text = (holding_context or {}).get("prompt_context") or "## 当前账户持仓上下文\n- 未提供账户上下文，按空仓观察口径生成账户信号。"
```

Insert before snapshot payload:

```python
{holding_context_text}
```

Update final-role debate JSON schema the same way.

- [ ] **Step 4: Pass holding context at execution sites**

In `scripts/batch_research.py`, import:

```python
from services import holding_context_service
```

Before calling snapshot prompt/debate/save for each code:

```python
holding_context = await holding_context_service.build_holding_context(item.code, account_id="default")
```

Pass `holding_context=holding_context` into:

- `_snapshot_prompt(...)`
- `_snapshot_debate_prompt(...)`
- snapshot-tradingagents prompt/state builder
- `_save_snapshot_report(...)`

- [ ] **Step 5: Run prompt and service tests**

Run:

```bash
.venv312/bin/python -m unittest tests.test_batch_research tests.test_batch_research_service tests.test_holding_context_service -v
```

Expected: OK.

- [ ] **Step 6: Commit**

```bash
git add scripts/batch_research.py tests/test_batch_research.py tests/test_batch_research_service.py
git commit -m "Inject holding context into batch report prompts"
```

---

### Task 4: Update Native TradingAgents Persistence

**Files:**
- Modify: `scheduler/ta_bridge.py`
- Test: `tests/test_ta_bridge_persistence.py`

- [ ] **Step 1: Write failing persistence test**

Add to `tests/test_ta_bridge_persistence.py`:

```python
def test_ta_bridge_persists_account_signal_and_holding_context(self):
    # Existing test setup should create a completed task and call persistence.
    # Add portfolio row before persistence:
    with sqlite3.connect(self.db_path) as db:
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("cash_balance_default", "100000"))
        db.execute(
            "INSERT INTO portfolio (code, name, total_shares, avg_cost, current_price, account_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("000001", "平安银行", 1000, 12.0, 10.0, "default"),
        )
        db.commit()
    # The task result should include research_signal BUY and account_signal HOLD.
    # Assert persisted row signal is HOLD and raw_state contains holding_context.
```

Use the existing task/persistence helper in this test file; do not create a second custom persistence path.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv312/bin/python -m unittest tests.test_ta_bridge_persistence -v
```

Expected: FAIL because `raw_state.holding_context` is not saved.

- [ ] **Step 3: Implement native TradingAgents signal normalization**

In `scheduler/ta_bridge.py`, import:

```python
from services import holding_context_service
```

After signal extraction and before `task.result = {...}`:

```python
holding_context = await holding_context_service.build_holding_context(task.code, account_id="default")
research_signal = signal_str
account_signal = signal_str
position_action = "watch"
action_reason = ""
if holding_context.get("is_holding") and signal_str in {"STRONG_BUY", "BUY", "OVERWEIGHT"}:
    position_action = "hold"
    account_signal = "HOLD"
    action_reason = "已有真实持仓，最终账户动作需结合成本、仓位和浮盈亏，默认不把研究买入直接转为加仓。"
```

Add fields into `task.result`:

```python
"research_signal": research_signal,
"account_signal": account_signal,
"position_action": position_action,
"action_reason": action_reason,
"holding_context": holding_context,
"signal": account_signal,
"action": account_signal,
```

In persistence, save:

```python
result.get("account_signal") or result.get("signal")
```

When calling `create_tracking`, use the account signal:

```python
signal = result.get("account_signal") or result.get("signal")
```

- [ ] **Step 4: Run native persistence tests**

Run:

```bash
.venv312/bin/python -m unittest tests.test_ta_bridge_persistence tests.test_signal_tracker tests.test_shadow_portfolio_service -v
```

Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add scheduler/ta_bridge.py tests/test_ta_bridge_persistence.py
git commit -m "Apply holding context to native TradingAgents reports"
```

---

### Task 5: Render Two-Layer Signals in Report Detail UI

**Files:**
- Modify: `templates/report_detail.html`
- Modify: `static/js/report-detail.js`
- Test: `tests/test_release_migration.py`

- [ ] **Step 1: Write failing UI guard test**

Add to `tests/test_release_migration.py`:

```python
def test_report_detail_renders_research_and_account_signal_context(self):
    js = (ROOT / "static/js/report-detail.js").read_text(encoding="utf-8")
    html = (ROOT / "templates" / "report_detail.html").read_text(encoding="utf-8")

    self.assertIn("reportSignalContext", html)
    self.assertIn("renderSignalContext", js)
    self.assertIn("research_signal", js)
    self.assertIn("account_signal", js)
    self.assertIn("holding_context", js)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv312/bin/python -m unittest tests.test_release_migration.ReleaseMigrationTests.test_report_detail_renders_research_and_account_signal_context -v
```

Expected: FAIL.

- [ ] **Step 3: Add template container**

In `templates/report_detail.html`, add near the final decision area:

```html
<section class="card">
  <div class="card-title">账户信号上下文</div>
  <div id="reportSignalContext" class="decision-summary-grid">正在加载...</div>
</section>
```

Update script cache version:

```html
<script src="/static/js/report-detail.js?v=2.9.12-holding-context"></script>
```

- [ ] **Step 4: Add renderer**

In `static/js/report-detail.js`, add:

```javascript
function renderSignalContext(result) {
  const raw = result?.result || result?.raw_state || {};
  const holding = raw.holding_context || {};
  const researchSignal = raw.research_signal || raw.signal || result.signal || '--';
  const accountSignal = raw.account_signal || result.signal || '--';
  const positionAction = raw.position_action || '--';
  return `
    <div><span>股票研究信号</span><strong>${escapeHtml(signalLabel(researchSignal))}</strong></div>
    <div><span>账户执行信号</span><strong>${escapeHtml(signalLabel(accountSignal))}</strong></div>
    <div><span>账户动作</span><strong>${escapeHtml(positionAction)}</strong></div>
    <div><span>是否持仓</span><strong>${holding.is_holding ? '已持仓' : '空仓/观察'}</strong></div>
    <div><span>持仓成本</span><strong>${formatNumber(holding.avg_cost || 0)}</strong></div>
    <div><span>仓位占比</span><strong>${formatNumber(holding.position_pct_of_assets || 0)}%</strong></div>
  `;
}
```

Call it after report load:

```javascript
setHtml('reportSignalContext', renderSignalContext(report));
```

- [ ] **Step 5: Run UI guard test**

Run:

```bash
.venv312/bin/python -m unittest tests.test_release_migration.ReleaseMigrationTests.test_report_detail_renders_research_and_account_signal_context -v
node --check static/js/report-detail.js
```

Expected: OK.

- [ ] **Step 6: Commit**

```bash
git add templates/report_detail.html static/js/report-detail.js tests/test_release_migration.py
git commit -m "Show account-aware signals in report detail"
```

---

### Task 6: Regression Coverage for AI Performance and Shadow Sync

**Files:**
- Modify: `tests/test_shadow_portfolio_service.py`
- Modify: `tests/test_ai_report_service.py`
- Modify: `tests/test_performance_service.py`

- [ ] **Step 1: Add regression tests that use account signal**

Add or extend tests so a report with:

```json
{
  "raw_state": {
    "research_signal": "BUY",
    "account_signal": "HOLD",
    "position_action": "hold"
  },
  "signal": "HOLD"
}
```

is grouped under `HOLD` in:

- AI report list filters
- shadow portfolio sync
- performance signal stats

- [ ] **Step 2: Run tests to verify current behavior**

Run:

```bash
.venv312/bin/python -m unittest tests.test_shadow_portfolio_service tests.test_ai_report_service tests.test_performance_service -v
```

Expected: OK after Task 2 and Task 4; if a test fails because code reads `raw_state.signal`, update that service to prefer row `signal`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_shadow_portfolio_service.py tests/test_ai_report_service.py tests/test_performance_service.py services/shadow_portfolio_service.py services/ai_report_service.py services/performance_service.py
git commit -m "Guard account signal usage in performance flows"
```

---

### Task 7: Final Verification and Release

**Files:**
- Modify: `package.json`
- Modify: `app_metadata.py`
- Modify: `README.md`

- [ ] **Step 1: Run full checks**

Run:

```bash
.venv312/bin/python -m unittest discover -s tests
npm run build
git diff --check
node --check static/js/report-detail.js
node --check static/js/portfolio.js
node --check static/js/reports.js
```

Expected:

- `Ran ... tests` and `OK`
- Vite build succeeds
- No diff whitespace errors
- Node syntax checks return exit code 0

- [ ] **Step 2: Browser smoke**

Open:

```text
http://127.0.0.1:8000/reports
```

Verify:

- Existing old reports still render without `holding_context`.
- New report detail shows “账户信号上下文”.
- If `raw_state.research_signal != analysis_reports.signal`, both are visible.

- [ ] **Step 3: Bump release metadata**

Set next patch version, for example:

```json
// package.json
"version": "2.9.12"
```

```python
# app_metadata.py
APP_VERSION = "2.9.12"
```

```markdown
当前发布版本为 `2.9.12`。
```

- [ ] **Step 4: Commit and tag**

```bash
git add package.json app_metadata.py README.md
git commit -m "Release v2.9.12 account-aware report signals"
git tag v2.9.12
git push origin main
git push origin v2.9.12
git ls-remote origin main refs/tags/v2.9.12
```

Expected: `main` and `refs/tags/v2.9.12` point to the same commit SHA.

---

## Self-Review

- Spec coverage: The plan covers holding context construction, batch snapshot prompts, native TradingAgents persistence, raw_state storage, UI rendering, AI performance/shadow sync, tests, and release metadata.
- No schema migration: Intentional. `analysis_reports.raw_state` already stores extensible JSON and prior investment-profile work uses it for durable context.
- Compatibility: Old reports degrade gracefully because UI reads optional `raw_state.research_signal`, `raw_state.account_signal`, and `raw_state.holding_context`.
- Main risk: Defaulting held `BUY` to account `HOLD` may be too conservative. This is acceptable for v1 because Portfolio Manager / position-plan remains the proper place for aggressive add decisions; later versions can let model output `ADD/OVERWEIGHT` with explicit justification.
