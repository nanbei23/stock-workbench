# Login Securities Account Gap Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining multi-account gaps after the first login-user/securities-account implementation, so every private workflow is scoped to a login account and every money/action workflow is scoped to an owned securities account.

**Architecture:** Keep the current persisted `account_id` columns as the compatibility name for securities-account ids. Enforce ownership at API boundaries through `services/auth_service.py`, add `login_user_id` only to user-owned research/worklist data, and keep public market/reference data global. Deliver a minimal login/account-management UI and data-health checks so the feature is operable, testable, and auditable.

**Tech Stack:** FastAPI, SQLite/aiosqlite, Jinja templates, vanilla JavaScript, Python `unittest`, local Browser QA.

---

## File Structure

- `api/account_scope.py`: shared FastAPI helpers for resolving the current login user and an owned securities account id.
- `api/auth_api.py`: login, logout, session, and login-account management endpoints.
- `api/portfolio_api.py`: securities-account ownership checks for trades, cash, plans, pending positions, PnL, watchlist, and trade-memory endpoints.
- `api/holding_review_api.py`: ownership checks for daily decision report list/run/archive/adoption endpoints.
- `api/position_plan_api.py`: ownership checks for position plan adoption/archive/action endpoints.
- `api/performance_api.py`: ownership checks for performance endpoints that read account positions or trades.
- `api/hermes_api.py`: ownership checks before Hermes tool execution can mutate trades, plans, watchlist, or account state.
- `repositories/portfolio_repository.py`: user-scoped watchlist methods and securities-account update/delete methods.
- `repositories/ai_report_repository.py`: user-scoped analysis-report methods.
- `services/auth_service.py`: password/session helpers, ownership checks, and compatibility contract comments.
- `services/portfolio_service.py`: accepts verified securities account ids and login user ids from API callers.
- `services/ai_report_service.py`: accepts `login_user_id` for private report/history reads.
- `services/holding_review_service.py`: uses verified account ids for account-action output.
- `services/position_plan_service.py`: uses verified account ids for adoption/action output.
- `services/trade_memory_service.py`: uses verified securities account ids for memory generation, search, embedding, and context injection.
- `models/database.py`: idempotent migrations for login-user ownership columns and securities-account alias views.
- `templates/login.html`: minimal login page.
- `templates/base.html`: login-user badge, securities-account selector, logout action.
- `templates/settings.html`: structured login-account and securities-account management UI.
- `static/js/app.js`: session loading, logout, auth redirect support, account selector state.
- `static/js/settings.js`: account-management forms and data-health rendering.
- `static/js/portfolio.js`, `static/js/reports.js`, `static/js/holding-review-detail.js`, `static/js/position-plan-detail.js`: send selected securities-account id only for account-action calls and rely on session scope for login-owned data.
- `tests/test_identity_account_gap_closure.py`: new cross-user, ownership, UI contract, and migration tests.
- `tests/test_release_migration.py`: static contract checks for routes, templates, JS hooks, and schema migrations.

---

### Task 1: Formal Login Entry And Session Controls

**Files:**
- Create: `templates/login.html`
- Modify: `app.py`
- Modify: `api/auth_api.py`
- Modify: `static/js/app.js`
- Modify: `templates/base.html`
- Test: `tests/test_identity_account_gap_closure.py`
- Test: `tests/test_release_migration.py`

- [ ] **Step 1: Write failing login tests**

Append to `tests/test_identity_account_gap_closure.py`:

```python
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import models.database as database
from api.auth_api import router as auth_router
from services import auth_service


ROOT = Path(__file__).resolve().parents[1]


class LoginEntrySessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_login_sets_session_cookie_and_logout_clears_it(self):
        import asyncio
        asyncio.run(database.init_db())
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "UPDATE login_users SET password_hash=? WHERE id='local_owner'",
                (auth_service.hash_password("owner-pass"),),
            )
            db.commit()

        app = FastAPI()
        app.include_router(auth_router, prefix="/api")
        with TestClient(app) as client:
            login = client.post("/api/auth/login", json={"username": "local_owner", "password": "owner-pass"})
            self.assertEqual(login.status_code, 200)
            self.assertIn(auth_service.SESSION_COOKIE, client.cookies)
            session = client.get("/api/auth/session")
            self.assertEqual(session.json()["user"]["id"], "local_owner")
            logout = client.post("/api/auth/logout")
            self.assertEqual(logout.status_code, 200)
            self.assertNotIn(auth_service.SESSION_COOKIE, client.cookies)

    def test_login_page_contract_is_renderable(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        base_html = (ROOT / "templates/base.html").read_text(encoding="utf-8")
        app_js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
        login_html = (ROOT / "templates/login.html").read_text(encoding="utf-8")

        self.assertIn('@app.get("/login"', app_source)
        self.assertIn("loginUserBadge", base_html)
        self.assertIn("logoutLoginUser", app_js)
        self.assertIn("/api/auth/login", login_html)
        self.assertIn("登录账户", login_html)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
./.venv312/bin/python -m unittest tests.test_identity_account_gap_closure.LoginEntrySessionTests -v
```

Expected: fails because `templates/login.html`, `/login`, or `logoutLoginUser` is missing.

- [ ] **Step 3: Add the login page**

Modify `app.py` near other page routes:

```python
@app.get("/login", response_class=HTMLResponse)
async def page_login(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")
```

Create `templates/login.html`:

```html
{% extends "base.html" %}
{% set active = "settings" %}
{% block title %}登录账户 - 炒股小牛马{% endblock %}
{% block content %}
<div class="detail-area" style="max-width:520px;margin:48px auto;">
  <div class="card">
    <div class="card-header">
      <div>
        <div class="card-title">登录账户</div>
        <div class="cash-source-line">登录账户决定数据归属；证券账户决定资金、持仓和交易动作。</div>
      </div>
    </div>
    <form id="loginForm" onsubmit="submitLogin(event)" class="form-grid" style="margin-top:12px;">
      <div class="form-group">
        <label>用户名</label>
        <input type="text" id="loginUsername" value="local_owner" autocomplete="username" required>
      </div>
      <div class="form-group">
        <label>密码</label>
        <input type="password" id="loginPassword" autocomplete="current-password">
      </div>
      <button class="btn btn-primary" type="submit">登录</button>
    </form>
  </div>
</div>
<script>
async function submitLogin(event) {
  event.preventDefault();
  const username = document.getElementById('loginUsername').value.trim();
  const password = document.getElementById('loginPassword').value;
  const resp = await fetch('/api/auth/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({username, password})
  });
  if (!resp.ok) {
    alert('登录失败');
    return;
  }
  location.href = '/portfolio';
}
</script>
{% endblock %}
```

- [ ] **Step 4: Add visible session controls**

Modify `templates/base.html` near the account selector:

```html
<span id="loginUserBadge" class="login-user-badge" title="登录账户">登录账户：本机账户</span>
<button class="theme-switcher" id="logoutLoginUserBtn" title="退出登录" onclick="logoutLoginUser()">
  <span class="theme-icon ui-glyph" data-icon="退"></span>
  <span class="theme-name">退出</span>
</button>
```

Modify `static/js/app.js`:

```javascript
async function logoutLoginUser() {
    await fetch('/api/auth/logout', { method: 'POST' });
    localStorage.removeItem('accountId');
    location.href = '/login';
}

window.logoutLoginUser = logoutLoginUser;
```

- [ ] **Step 5: Run tests to verify GREEN**

Run:

```bash
./.venv312/bin/python -m unittest tests.test_identity_account_gap_closure.LoginEntrySessionTests -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app.py api/auth_api.py templates/login.html templates/base.html static/js/app.js tests/test_identity_account_gap_closure.py
git commit -m "feat: add login entry and session controls"
```

---

### Task 2: Close Securities-Account Ownership Gaps

**Files:**
- Create: `api/account_scope.py`
- Modify: `api/portfolio_api.py`
- Modify: `api/holding_review_api.py`
- Modify: `api/position_plan_api.py`
- Modify: `api/performance_api.py`
- Modify: `api/hermes_api.py`
- Modify: `services/portfolio_service.py`
- Modify: `services/trade_memory_service.py`
- Modify: `services/holding_review_service.py`
- Modify: `services/position_plan_service.py`
- Test: `tests/test_identity_account_gap_closure.py`

- [ ] **Step 1: Write failing cross-account tests**

Append to `tests/test_identity_account_gap_closure.py`:

```python
from api.portfolio_api import router as portfolio_router
from api.holding_review_api import router as holding_review_router
from api.position_plan_api import router as position_plan_router
from api.performance_api import router as performance_router


class SecuritiesAccountOwnershipGapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        import asyncio
        asyncio.run(database.init_db())
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO login_users (id, username, password_hash, display_name) VALUES (?, ?, ?, ?)",
                ("user_b", "userb", auth_service.hash_password("pw-b"), "User B"),
            )
            db.execute(
                "INSERT INTO securities_accounts (id, login_user_id, name, broker) VALUES (?, ?, ?, ?)",
                ("b_account", "user_b", "B账户", "测试券商"),
            )
            db.execute(
                "INSERT INTO trades (code, name, direction, price, shares, amount, trade_time, account_id) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?)",
                ("000001", "平安银行", "buy", 10, 100, 1000, "default"),
            )
            db.commit()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def _client(self):
        app = FastAPI()
        app.include_router(auth_router, prefix="/api")
        app.include_router(portfolio_router, prefix="/api")
        app.include_router(holding_review_router, prefix="/api")
        app.include_router(position_plan_router, prefix="/api")
        app.include_router(performance_router, prefix="/api")
        client = TestClient(app)
        client.post("/api/auth/login", json={"username": "userb", "password": "pw-b"})
        return client

    def test_delete_trade_rejects_foreign_account(self):
        with self._client() as client:
            resp = client.delete("/api/trades/1?account_id=default")
        self.assertEqual(resp.status_code, 403)

    def test_trade_memory_candidates_reject_foreign_account(self):
        with self._client() as client:
            resp = client.get("/api/trade-memories/candidates?account_id=default")
        self.assertEqual(resp.status_code, 403)

    def test_holding_review_list_rejects_foreign_account(self):
        with self._client() as client:
            resp = client.get("/api/daily-decision-reports?account_id=default")
        self.assertEqual(resp.status_code, 403)

    def test_position_plans_reject_foreign_account_filter(self):
        with self._client() as client:
            resp = client.get("/api/position-plans?account_id=default")
        self.assertEqual(resp.status_code, 403)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
./.venv312/bin/python -m unittest tests.test_identity_account_gap_closure.SecuritiesAccountOwnershipGapTests -v
```

Expected: at least one endpoint still returns `200` or mutates data because it accepts raw `account_id`.

- [ ] **Step 3: Add shared ownership helpers**

Create `api/account_scope.py`:

```python
from typing import Optional

from fastapi import Depends, Query

from services import auth_service


async def current_user(user: dict = Depends(auth_service.require_login_user)) -> dict:
    return user


async def owned_account_id(
    account_id: Optional[str] = Query(None),
    user: dict = Depends(current_user),
) -> str:
    return await auth_service.resolve_securities_account_id(user, account_id)


async def resolve_owned_account_id(user: dict, account_id: str | None = None) -> str:
    return await auth_service.resolve_securities_account_id(user, account_id)
```

- [ ] **Step 4: Protect remaining portfolio and trade-memory endpoints**

Modify `api/portfolio_api.py` imports and helper usage:

```python
from api.account_scope import current_user, resolve_owned_account_id
```

Use verified ids in these routes:

```python
@router.delete("/trades/{trade_id}")
async def delete_trade(
    trade_id: int,
    account_id: Optional[str] = Query(None),
    user: dict = Depends(current_user),
):
    aid = await resolve_owned_account_id(user, account_id)
    return await portfolio_service.delete_trade(trade_id, account_id=aid)


@router.get("/trade-memories/candidates")
async def list_trade_memory_candidates(
    account_id: Optional[str] = Query(None),
    user: dict = Depends(current_user),
):
    aid = await resolve_owned_account_id(user, account_id)
    return trade_memory_service.list_closed_trade_candidates(account_id=aid)


@router.get("/trade-memories")
async def list_trade_memories(
    status: Optional[str] = Query(None),
    code: Optional[str] = Query(None),
    account_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(current_user),
):
    aid = await resolve_owned_account_id(user, account_id)
    return trade_memory_service.list_trade_memories(status=status, code=code, account_id=aid, limit=limit)


@router.get("/trade-memories/embeddings/status")
async def get_trade_memory_embedding_status(
    account_id: Optional[str] = Query(None),
    user: dict = Depends(current_user),
):
    aid = await resolve_owned_account_id(user, account_id)
    return trade_memory_service.trade_memory_embedding_status(account_id=aid)
```

For request-body routes in `api/portfolio_api.py`, set `req.account_id` before calling the service:

```python
aid = await resolve_owned_account_id(user, getattr(req, "account_id", None))
req.account_id = aid
```

Apply that exact assignment in `/trade-memories/draft`, `/trade-memories`, `/trade-memories/context`, `/trade-memories/related`, and `/trade-memories/embeddings/backfill`.

- [ ] **Step 5: Make trade deletion verify the row belongs to the resolved account**

Modify `services/portfolio_service.py`:

```python
async def delete_trade(trade_id: int, account_id: str | None = None):
    async def _delete(db):
        trade = await repo.fetch_trade(db, trade_id)
        if not trade:
            raise HTTPException(status_code=404, detail="未找到交易记录")
        trade_account_id = trade.get("account_id") or "default"
        requested_account_id = account_id or trade_account_id
        if trade_account_id != requested_account_id:
            raise HTTPException(status_code=403, detail="交易记录不属于当前证券账户")
        await repo.delete_trade(db, trade_id)
        await repo.apply_trade_cash_effect(db, trade, reverse=True)
        portfolio = await repo.recalc_portfolio(db, trade["code"], requested_account_id)
        return {"status": "ok", "deleted_id": trade_id, "portfolio": portfolio}

    return await _with_db(_delete)
```

- [ ] **Step 6: Protect holding-review endpoints**

Modify `api/holding_review_api.py`:

```python
from fastapi import Depends, Query
from api.account_scope import current_user, resolve_owned_account_id


@router.get("/daily-decision-reports")
async def list_daily_decision_reports(
    limit: int = Query(default=30, ge=1, le=200),
    account_id: str | None = Query(default=None),
    user: dict = Depends(current_user),
):
    aid = await resolve_owned_account_id(user, account_id)
    return await holding_review_service.list_reviews(limit=limit, account_id=aid)


@router.post("/daily-decision-reports/run")
async def run_daily_decision_report(payload: DailyDecisionRunRequest, user: dict = Depends(current_user)):
    payload.account_id = await resolve_owned_account_id(user, payload.account_id)
    return await holding_review_service.run_daily_review(account_id=payload.account_id, date_text=payload.date)
```

For item status, archive, flags, markdown, and detail routes that take only `review_id`, load the review through `holding_review_service.get_review(review_id)`, call `await resolve_owned_account_id(user, review["account_id"])`, then continue.

- [ ] **Step 7: Protect position-plan and performance endpoints**

Modify `api/position_plan_api.py`:

```python
from fastapi import Depends, Query
from api.account_scope import current_user, resolve_owned_account_id


@router.get("/position-plans")
async def list_position_plans(
    account_id: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=200),
    user: dict = Depends(current_user),
):
    aid = await resolve_owned_account_id(user, account_id)
    return await position_plan_service.list_plans(account_id=aid, limit=limit)
```

For `/position-plans/{plan_id}/adopt`, `/partial-adopt`, `/abandon`, and `/archive`, fetch the plan first, resolve `plan["account_id"]`, then perform the action.

Modify `api/performance_api.py` for account-derived routes:

```python
from fastapi import Depends, Query
from api.account_scope import current_user, resolve_owned_account_id


@router.get("/performance/summary")
async def performance_summary(
    account_id: str | None = Query(default=None),
    user: dict = Depends(current_user),
):
    aid = await resolve_owned_account_id(user, account_id)
    return await performance_service.summary(account_id=aid)
```

Keep market-wide benchmark and quote endpoints without a securities-account dependency.

- [ ] **Step 8: Protect Hermes tool execution**

Modify `api/hermes_api.py` before dispatching any tool call:

```python
from api.account_scope import current_user, resolve_owned_account_id


async def _scope_hermes_payload(payload: dict, user: dict) -> dict:
    scoped = dict(payload)
    if "account_id" in scoped:
        scoped["account_id"] = await resolve_owned_account_id(user, scoped.get("account_id"))
    return scoped
```

In the Hermes execution route, call:

```python
payload = await _scope_hermes_payload(payload, user)
```

and require:

```python
user: dict = Depends(current_user)
```

- [ ] **Step 9: Run tests to verify GREEN**

Run:

```bash
./.venv312/bin/python -m unittest tests.test_identity_account_gap_closure.SecuritiesAccountOwnershipGapTests -v
./.venv312/bin/python -m unittest tests.test_portfolio_service tests.test_trade_memory_service tests.test_holding_review_api tests.test_position_plan_service tests.test_performance_service -v
```

Expected: all tests pass.

- [ ] **Step 10: Commit**

```bash
git add api/account_scope.py api/portfolio_api.py api/holding_review_api.py api/position_plan_api.py api/performance_api.py api/hermes_api.py services/portfolio_service.py services/trade_memory_service.py services/holding_review_service.py services/position_plan_service.py tests/test_identity_account_gap_closure.py
git commit -m "fix: enforce securities account ownership at action boundaries"
```

---

### Task 3: Scope User-Owned Research Data By Login Account

**Files:**
- Modify: `models/database.py`
- Modify: `repositories/portfolio_repository.py`
- Modify: `repositories/ai_report_repository.py`
- Modify: `services/portfolio_service.py`
- Modify: `services/ai_report_service.py`
- Modify: `api/portfolio_api.py`
- Modify: `api/ai_api.py`
- Modify: `api/batch_report_api.py`
- Modify: `api/report_selection_api.py`
- Modify: `static/js/reports.js`
- Test: `tests/test_identity_account_gap_closure.py`
- Test: `tests/test_portfolio_service.py`
- Test: `tests/test_ai_analysis_service.py`
- Test: `tests/test_report_selection_service.py`

- [ ] **Step 1: Write failing login-user scope tests**

Append to `tests/test_identity_account_gap_closure.py`:

```python
from api.ai_api import router as ai_router


class LoginUserResearchScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        import asyncio
        asyncio.run(database.init_db())
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO login_users (id, username, password_hash, display_name) VALUES (?, ?, ?, ?)",
                ("user_b", "userb", auth_service.hash_password("pw-b"), "User B"),
            )
            db.execute("INSERT INTO watchlist (code, name, login_user_id) VALUES ('000001', '平安银行', 'local_owner')")
            db.execute("INSERT INTO watchlist (code, name, login_user_id) VALUES ('000002', '万科A', 'user_b')")
            db.execute("INSERT INTO analysis_reports (code, signal, raw_state, login_user_id) VALUES ('000001', 'BUY', '{}', 'local_owner')")
            db.execute("INSERT INTO analysis_reports (code, signal, raw_state, login_user_id) VALUES ('000002', 'SELL', '{}', 'user_b')")
            db.commit()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_watchlist_returns_only_current_login_user_rows(self):
        app = FastAPI()
        app.include_router(auth_router, prefix="/api")
        app.include_router(portfolio_router, prefix="/api")
        with TestClient(app) as client:
            client.post("/api/auth/login", json={"username": "userb", "password": "pw-b"})
            resp = client.get("/api/watchlist")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([row["code"] for row in resp.json()["stocks"]], ["000002"])

    def test_report_list_returns_only_current_login_user_reports(self):
        app = FastAPI()
        app.include_router(auth_router, prefix="/api")
        app.include_router(ai_router, prefix="/api")
        with TestClient(app) as client:
            client.post("/api/auth/login", json={"username": "userb", "password": "pw-b"})
            resp = client.get("/api/reports?limit=20")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([row["code"] for row in resp.json()["reports"]], ["000002"])
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
./.venv312/bin/python -m unittest tests.test_identity_account_gap_closure.LoginUserResearchScopeTests -v
```

Expected: fails because `watchlist` and `analysis_reports` are not fully scoped by `login_user_id`.

- [ ] **Step 3: Add login-user ownership columns**

Modify `models/database.py`:

```python
async def ensure_login_user_scope_columns(db):
    watchlist_columns = await _table_columns(db, "watchlist")
    if "login_user_id" not in watchlist_columns:
        await db.execute("ALTER TABLE watchlist ADD COLUMN login_user_id TEXT DEFAULT 'local_owner'")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_watchlist_login_user_sort ON watchlist(login_user_id, sort_order, added_at)"
    )

    report_columns = await _table_columns(db, "analysis_reports")
    if "login_user_id" not in report_columns:
        await db.execute("ALTER TABLE analysis_reports ADD COLUMN login_user_id TEXT DEFAULT 'local_owner'")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_analysis_reports_user_code_created ON analysis_reports(login_user_id, code, created_at DESC)"
    )
    await db.commit()
```

Call it from `init_db()` after `ensure_identity_tables(db)`:

```python
await ensure_login_user_scope_columns(db)
```

- [ ] **Step 4: Scope watchlist repository methods**

Modify `repositories/portfolio_repository.py`:

```python
async def fetch_watchlist_and_positions(db, login_user_id: str = "local_owner"):
    stocks = await db.execute_fetchall(
        """
        SELECT *
        FROM watchlist
        WHERE COALESCE(login_user_id, 'local_owner') = ?
        ORDER BY sort_order ASC, added_at ASC
        """,
        (login_user_id,),
    )
    portfolio_rows = await fetch_positions(db, None)
    portfolio_map = {row["code"]: dict(row) for row in portfolio_rows}
    return [dict(row) for row in stocks], portfolio_map
```

For writes, include the login user:

```python
async def insert_watchlist_stock(db, req, login_user_id: str = "local_owner"):
    await db.execute(
        """
        INSERT OR IGNORE INTO watchlist (
            code, name, group_name, sort_order, strategy_state,
            target_buy_price, target_sell_price, stop_loss_price, notes, login_user_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            req.code,
            req.name,
            getattr(req, "group", None) or getattr(req, "group_name", None) or "默认",
            getattr(req, "sort_order", 0),
            getattr(req, "strategy_state", None),
            getattr(req, "target_buy_price", None),
            getattr(req, "target_sell_price", None),
            getattr(req, "stop_loss_price", None),
            getattr(req, "notes", None),
            login_user_id,
        ),
    )
```

Add `WHERE COALESCE(login_user_id, 'local_owner') = ?` to watchlist delete, batch delete, update, import, reorder, and code lookup methods.

- [ ] **Step 5: Pass login-user scope through portfolio API and service**

Modify `api/portfolio_api.py` watchlist routes:

```python
@router.get("/watchlist")
async def get_watchlist(user: dict = Depends(current_user)):
    return await portfolio_service.get_watchlist(login_user_id=user["id"])


@router.post("/watchlist")
async def add_watchlist_stock(req: WatchlistRequest, user: dict = Depends(current_user)):
    return await portfolio_service.add_watchlist_stock(req, login_user_id=user["id"])
```

Modify `services/portfolio_service.py`:

```python
async def get_watchlist(login_user_id: str = "local_owner"):
    async def _load(db):
        stocks, portfolio_map = await repo.fetch_watchlist_and_positions(db, login_user_id)
        latest_reports = await repo.fetch_latest_report_map(
            db,
            [stock["code"] for stock in stocks],
            login_user_id=login_user_id,
        )
        return stocks, portfolio_map, latest_reports
    stocks, portfolio_map, latest_reports = await _with_db(_load)
    return build_watchlist_response(stocks, portfolio_map, latest_reports)
```

- [ ] **Step 6: Scope analysis reports, batch reports, and report selections**

Modify `repositories/ai_report_repository.py`:

```python
async def list_reports(db, limit: int, code: str | None = None, login_user_id: str = "local_owner"):
    where = ["COALESCE(login_user_id, 'local_owner') = ?"]
    params: list[object] = [login_user_id]
    if code:
        where.append("code = ?")
        params.append(code)
    params.append(limit)
    return await db.execute_fetchall(
        f"""
        SELECT *
        FROM analysis_reports
        WHERE {' AND '.join(where)}
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
        """,
        params,
    )
```

Modify `api/ai_api.py`:

```python
@router.get("/reports")
async def list_reports(
    limit: int = Query(default=50, ge=1, le=500),
    code: str | None = Query(default=None),
    user: dict = Depends(current_user),
):
    return await ai_report_service.list_reports(limit=limit, code=code, login_user_id=user["id"])
```

Modify `api/batch_report_api.py` and `api/report_selection_api.py` so new report runs and selection sets write `login_user_id=user["id"]`, while quote/news/factor inputs remain global.

- [ ] **Step 7: Run tests to verify GREEN**

Run:

```bash
./.venv312/bin/python -m unittest tests.test_identity_account_gap_closure.LoginUserResearchScopeTests -v
./.venv312/bin/python -m unittest tests.test_portfolio_service tests.test_ai_analysis_service tests.test_report_selection_service tests.test_release_migration -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add models/database.py repositories/portfolio_repository.py repositories/ai_report_repository.py services/portfolio_service.py services/ai_report_service.py api/portfolio_api.py api/ai_api.py api/batch_report_api.py api/report_selection_api.py static/js/reports.js tests/test_identity_account_gap_closure.py tests/test_portfolio_service.py tests/test_ai_analysis_service.py tests/test_report_selection_service.py tests/test_release_migration.py
git commit -m "feat: scope user-owned research data by login account"
```

---

### Task 4: Account Management UI And Compatibility Contract

**Files:**
- Modify: `api/auth_api.py`
- Modify: `api/portfolio_api.py`
- Modify: `services/auth_service.py`
- Modify: `services/portfolio_service.py`
- Modify: `repositories/portfolio_repository.py`
- Modify: `models/database.py`
- Modify: `templates/settings.html`
- Modify: `static/js/settings.js`
- Test: `tests/test_identity_account_gap_closure.py`
- Test: `tests/test_release_migration.py`

- [ ] **Step 1: Write failing account-management tests**

Append to `tests/test_identity_account_gap_closure.py`:

```python
class AccountManagementAndCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        import asyncio
        asyncio.run(database.init_db())

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_can_update_own_securities_account_and_cannot_delete_default(self):
        app = FastAPI()
        app.include_router(auth_router, prefix="/api")
        app.include_router(portfolio_router, prefix="/api")
        with TestClient(app) as client:
            update = client.put("/api/accounts/default", json={"name": "主账户", "broker": "方正证券", "notes": "主用"})
            self.assertEqual(update.status_code, 200)
            accounts = client.get("/api/accounts").json()["accounts"]
            self.assertEqual(accounts[0]["name"], "主账户")
            self.assertEqual(accounts[0]["broker"], "方正证券")
            delete = client.delete("/api/accounts/default")
            self.assertEqual(delete.status_code, 400)
            self.assertIn("默认证券账户不能删除", delete.json()["detail"])

    def test_account_id_semantic_alias_is_exposed(self):
        source = (ROOT / "models/database.py").read_text(encoding="utf-8")
        auth_source = (ROOT / "services/auth_service.py").read_text(encoding="utf-8")
        self.assertIn("portfolio_securities_view", source)
        self.assertIn("account_id AS securities_account_id", source)
        self.assertIn("physical account_id columns are securities account ids", auth_source)
```

Append to `tests/test_release_migration.py`:

```python
    def test_settings_has_structured_login_and_securities_account_management(self):
        html = SETTINGS_TEMPLATE.read_text(encoding="utf-8")
        js = SETTINGS_JS.read_text(encoding="utf-8")

        self.assertIn("loginAccountPanel", html)
        self.assertIn("securitiesAccountForm", html)
        self.assertIn("saveSecuritiesAccount", js)
        self.assertIn("deleteSecuritiesAccount", js)
        self.assertNotIn("prompt('证券账户名称", js)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
./.venv312/bin/python -m unittest \
  tests.test_identity_account_gap_closure.AccountManagementAndCompatibilityTests \
  tests.test_release_migration.ReleaseMigrationTests.test_settings_has_structured_login_and_securities_account_management -v
```

Expected: fails because structured update/delete UI, compatibility view, or contract comment is missing.

- [ ] **Step 3: Add account update and archive methods**

Modify `repositories/portfolio_repository.py`:

```python
async def update_account(db, account_id: str, login_user_id: str, values: dict):
    allowed = {key: values[key] for key in ("name", "broker", "account_no_mask", "notes", "display_order") if key in values}
    if not allowed:
        return 0
    assignments = [f"{key} = ?" for key in allowed]
    params = list(allowed.values()) + [account_id, login_user_id]
    cursor = await db.execute(
        f"UPDATE securities_accounts SET {', '.join(assignments)}, updated_at=datetime('now') WHERE id=? AND login_user_id=?",
        params,
    )
    await db.commit()
    return cursor.rowcount


async def archive_account(db, account_id: str, login_user_id: str):
    cursor = await db.execute(
        """
        UPDATE securities_accounts
        SET status='archived', updated_at=datetime('now')
        WHERE id=? AND login_user_id=? AND is_default=0
        """,
        (account_id, login_user_id),
    )
    await db.commit()
    return cursor.rowcount
```

Modify `services/portfolio_service.py`:

```python
async def update_account(account_id: str, login_user_id: str, values: dict):
    async def _update(db):
        rowcount = await repo.update_account(db, account_id, login_user_id, values)
        if rowcount == 0:
            raise HTTPException(status_code=404, detail="证券账户不存在")
        return {"success": True, "id": account_id}
    return await _with_db(_update)


async def archive_account(account_id: str, login_user_id: str):
    if account_id == "default":
        raise HTTPException(status_code=400, detail="默认证券账户不能删除")
    async def _archive(db):
        rowcount = await repo.archive_account(db, account_id, login_user_id)
        if rowcount == 0:
            raise HTTPException(status_code=404, detail="证券账户不存在")
        return {"success": True, "id": account_id}
    return await _with_db(_archive)
```

- [ ] **Step 4: Add update/delete endpoints**

Modify `api/portfolio_api.py`:

```python
class AccountUpdateRequest(BaseModel):
    name: Optional[str] = None
    broker: Optional[str] = None
    account_no_mask: Optional[str] = None
    notes: Optional[str] = None
    display_order: Optional[int] = None


@router.put("/accounts/{account_id}")
async def update_account(
    account_id: str,
    req: AccountUpdateRequest,
    user: dict = Depends(current_user),
):
    await resolve_owned_account_id(user, account_id)
    return await portfolio_service.update_account(account_id, user["id"], req.model_dump(exclude_none=True))


@router.delete("/accounts/{account_id}")
async def archive_account(account_id: str, user: dict = Depends(current_user)):
    await resolve_owned_account_id(user, account_id)
    return await portfolio_service.archive_account(account_id, user["id"])
```

- [ ] **Step 5: Add compatibility views and contract comment**

Modify `models/database.py`:

```python
async def ensure_securities_account_alias_views(db):
    await db.executescript(
        """
        CREATE VIEW IF NOT EXISTS portfolio_securities_view AS
        SELECT *, account_id AS securities_account_id FROM portfolio;
        CREATE VIEW IF NOT EXISTS trades_securities_view AS
        SELECT *, account_id AS securities_account_id FROM trades;
        CREATE VIEW IF NOT EXISTS cash_ledger_securities_view AS
        SELECT *, account_id AS securities_account_id FROM cash_ledger;
        """
    )
    await db.commit()
```

Call it from `init_db()` after daily PnL migration:

```python
await ensure_securities_account_alias_views(db)
```

Modify `services/auth_service.py` near constants:

```python
# Compatibility contract:
# physical account_id columns are securities account ids. New code should use
# variable names like securities_account_id or aid at API and service boundaries,
# while persisted columns stay stable for legacy scripts and migrations.
```

- [ ] **Step 6: Replace prompt-based settings account UI**

Modify `templates/settings.html` account section:

```html
<div class="setting-group">
  <div class="setting-group-title">登录账户 / 证券账户管理</div>
  <div id="loginAccountPanel" class="account-management-panel"></div>
  <form id="securitiesAccountForm" class="form-grid" onsubmit="saveSecuritiesAccount(event)">
    <input type="hidden" id="securitiesAccountId">
    <div class="form-group"><label>证券账户名称</label><input id="securitiesAccountName" required></div>
    <div class="form-group"><label>券商</label><input id="securitiesAccountBroker"></div>
    <div class="form-group"><label>备注</label><input id="securitiesAccountNotes"></div>
    <button class="btn-primary" type="submit">保存证券账户</button>
  </form>
  <div id="accountList" style="margin-top:10px;"></div>
</div>
```

Modify `static/js/settings.js`:

```javascript
async function saveSecuritiesAccount(event) {
    event.preventDefault();
    const id = document.getElementById('securitiesAccountId').value;
    const payload = {
        name: document.getElementById('securitiesAccountName').value.trim(),
        broker: document.getElementById('securitiesAccountBroker').value.trim(),
        notes: document.getElementById('securitiesAccountNotes').value.trim(),
    };
    const method = id ? 'PUT' : 'POST';
    const url = id ? `${API_BASE}/accounts/${encodeURIComponent(id)}` : `${API_BASE}/accounts`;
    const resp = await fetch(url, {
        method,
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
    });
    if (!resp.ok) {
        toast('error', '证券账户保存失败');
        return;
    }
    toast('success', '证券账户已保存');
    document.getElementById('securitiesAccountForm').reset();
    document.getElementById('securitiesAccountId').value = '';
    await loadAccountList();
    if (typeof loadAccounts === 'function') await loadAccounts();
}

async function deleteSecuritiesAccount(id) {
    const resp = await fetch(`${API_BASE}/accounts/${encodeURIComponent(id)}`, {method: 'DELETE'});
    if (!resp.ok) {
        toast('error', '证券账户删除失败');
        return;
    }
    toast('success', '证券账户已停用');
    await loadAccountList();
}
```

- [ ] **Step 7: Run tests to verify GREEN**

Run:

```bash
./.venv312/bin/python -m unittest \
  tests.test_identity_account_gap_closure.AccountManagementAndCompatibilityTests \
  tests.test_release_migration.ReleaseMigrationTests.test_settings_has_structured_login_and_securities_account_management \
  tests.test_settings_service -v
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add api/portfolio_api.py services/portfolio_service.py repositories/portfolio_repository.py models/database.py services/auth_service.py templates/settings.html static/js/settings.js tests/test_identity_account_gap_closure.py tests/test_release_migration.py
git commit -m "feat: add account management UI and compatibility contract"
```

---

### Task 5: Identity Data Health, Deployment Check, And Browser QA

**Files:**
- Modify: `services/settings_service.py`
- Modify: `scripts/migrate_2_8_1_to_2_9.py`
- Modify: `templates/settings.html`
- Modify: `static/js/settings.js`
- Test: `tests/test_settings_service.py`
- Test: `tests/test_release_migration.py`

- [ ] **Step 1: Write failing health-check tests**

Append to `tests/test_settings_service.py`:

```python
    def test_data_health_reports_identity_integrity(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO portfolio (code, name, total_shares, account_id) VALUES ('000001', '平安银行', 100, 'missing-account')"
            )
            db.commit()

        health = settings_service.data_health()

        self.assertIn("identity", health)
        self.assertEqual(health["identity"]["orphan_account_rows"], 1)
        self.assertEqual(health["identity"]["orphan_securities_account_ids"], ["missing-account"])
```

Append to `tests/test_release_migration.py`:

```python
    def test_data_health_ui_mentions_identity_integrity(self):
        settings_html = SETTINGS_TEMPLATE.read_text(encoding="utf-8")
        settings_js = SETTINGS_JS.read_text(encoding="utf-8")

        self.assertIn("登录账户完整性", settings_html)
        self.assertIn("orphan_securities_account_ids", settings_js)
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
./.venv312/bin/python -m unittest \
  tests.test_settings_service.SettingsServiceTests.test_data_health_reports_identity_integrity \
  tests.test_release_migration.ReleaseMigrationTests.test_data_health_ui_mentions_identity_integrity -v
```

Expected: fails because identity integrity is not reported in service or UI.

- [ ] **Step 3: Add identity integrity to data health**

Modify `services/settings_service.py` inside `data_health()`:

```python
identity_rows = conn.execute(
    """
    SELECT DISTINCT account_id
    FROM portfolio
    WHERE account_id IS NOT NULL AND account_id NOT IN (SELECT id FROM securities_accounts)
    UNION
    SELECT DISTINCT account_id
    FROM trades
    WHERE account_id IS NOT NULL AND account_id NOT IN (SELECT id FROM securities_accounts)
    UNION
    SELECT DISTINCT account_id
    FROM cash_ledger
    WHERE account_id IS NOT NULL AND account_id NOT IN (SELECT id FROM securities_accounts)
    UNION
    SELECT DISTINCT account_id
    FROM daily_pnl
    WHERE account_id IS NOT NULL AND account_id NOT IN (SELECT id FROM securities_accounts)
    """
).fetchall()
orphan_ids = sorted({row[0] for row in identity_rows if row[0]})
result["identity"] = {
    "login_user_count": conn.execute("SELECT COUNT(*) FROM login_users").fetchone()[0],
    "securities_account_count": conn.execute("SELECT COUNT(*) FROM securities_accounts WHERE status='active'").fetchone()[0],
    "orphan_account_rows": len(orphan_ids),
    "orphan_securities_account_ids": orphan_ids,
}
```

- [ ] **Step 4: Add migration script coverage**

Modify `scripts/migrate_2_8_1_to_2_9.py`:

```python
async def run_identity_migrations(db):
    await database.ensure_identity_tables(db)
    await database.ensure_daily_pnl_account_key(db)
    await database.ensure_login_user_scope_columns(db)
    await database.ensure_securities_account_alias_views(db)
```

Call `await run_identity_migrations(db)` from the script’s existing main migration path after opening the database connection.

- [ ] **Step 5: Render identity health in settings**

Modify `templates/settings.html` data-health section:

```html
<div class="setting-label">登录账户完整性</div>
```

Modify `static/js/settings.js` in the data-health renderer:

```javascript
if (data.identity) {
    rows.push(`<div><span>登录账户完整性</span><strong class="${data.identity.orphan_account_rows ? 'down' : 'up'}">${data.identity.orphan_account_rows || 0}</strong><small>孤立证券账户引用</small></div>`);
    if ((data.identity.orphan_securities_account_ids || []).length) {
        rows.push(`<div><span>异常证券账户</span><small>${escapeHtml(data.identity.orphan_securities_account_ids.join(', '))}</small></div>`);
    }
}
```

- [ ] **Step 6: Run targeted tests**

Run:

```bash
./.venv312/bin/python -m unittest tests.test_settings_service tests.test_release_migration -v
```

Expected: all tests pass.

- [ ] **Step 7: Run full backend verification**

Run:

```bash
./.venv312/bin/python -m unittest discover -s tests -v
```

Expected: all tests pass.

- [ ] **Step 8: Run browser QA**

Start a temporary server:

```bash
./.venv312/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8766
```

Open these URLs with the Browser plugin:

```text
http://127.0.0.1:8766/login
http://127.0.0.1:8766/portfolio
http://127.0.0.1:8766/settings
```

Verify these visible states:

```text
/login: shows 登录账户 and can submit local_owner
/portfolio: top bar shows 登录账户 and 证券账户
/settings: shows 登录账户 / 证券账户管理 and 登录账户完整性
browser console: no relevant error or warn entries
```

Stop the temporary server after QA.

- [ ] **Step 9: Commit**

```bash
git add services/settings_service.py scripts/migrate_2_8_1_to_2_9.py templates/settings.html static/js/settings.js tests/test_settings_service.py tests/test_release_migration.py
git commit -m "feat: add identity integrity health checks"
```

---

## Completion Criteria

- Login account: user can visit `/login`, log in, see the active login account, and log out.
- Securities accounts: each login account can manage multiple securities accounts, and account-action APIs reject another login user’s `account_id`.
- Research ownership: watchlist, analysis reports, batch report history, and selection sets are scoped by `login_user_id`.
- Signal boundary: market data, quotes, news, and generic research inputs stay global; positions, costs, cash, trades, plans, trade memories, and holding reviews stay securities-account scoped.
- Compatibility: persisted `account_id` columns remain stable, and alias views expose `securities_account_id` for new readers.
- Health: settings data health reports missing securities-account references.
- Verification: targeted tests, full `unittest discover`, and Browser QA all pass.

## Self-Review

- Spec coverage: the plan covers login-account UX, multiple securities accounts per login account, ownership checks, user-private research scoping, management UI, compatibility naming, health checks, migration, tests, and rendered QA.
- Placeholder scan: task steps are concrete and do not rely on unresolved markers or cross-task shorthand instructions.
- Type consistency: `login_user_id` means identity ownership; persisted `account_id` means securities-account id; API and service boundaries use verified ids from `auth_service`.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-06-login-securities-account-gap-closure.md`. Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.
