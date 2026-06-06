# Login And Securities Accounts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the existing default account model into login users and per-user securities accounts while keeping existing portfolio data compatible.

**Architecture:** Add `login_users`, `login_sessions`, and `securities_accounts` as the identity layer. Keep existing money tables using physical `account_id` for compatibility, but treat it as a securities account id and validate ownership in service/API boundaries. Account-action data is scoped by securities account; research data remains separate from account actions.

**Tech Stack:** FastAPI, SQLite/aiosqlite, existing vanilla JS templates, unittest/pytest.

---

### Task 1: Identity Schema And Migration

**Files:**
- Modify: `models/database.py`
- Create: `services/auth_service.py`
- Test: `tests/test_identity_accounts.py`

- [ ] Write failing tests proving default `local_owner` and `default` securities account are created.
- [ ] Add schema tables and an idempotent migration helper.
- [ ] Expose helpers for current login user and securities account ownership.
- [ ] Run `python -m pytest tests/test_identity_accounts.py -q`.

### Task 2: Auth API

**Files:**
- Create: `api/auth_api.py`
- Modify: `app.py`
- Test: `tests/test_identity_accounts.py`

- [ ] Write failing tests for session status, login, logout, and scoped account listing.
- [ ] Add password hashing and session-cookie helpers.
- [ ] Register auth router.
- [ ] Run `python -m pytest tests/test_identity_accounts.py -q`.

### Task 3: Securities Account API Boundary

**Files:**
- Modify: `api/portfolio_api.py`
- Modify: `services/portfolio_service.py`
- Modify: `repositories/portfolio_repository.py`
- Test: `tests/test_identity_accounts.py`, `tests/test_portfolio_service.py`

- [ ] Write failing tests that user A cannot read or mutate user B securities account.
- [ ] Change `/api/accounts` to return securities accounts owned by current login user.
- [ ] Validate ownership for portfolio, cash, trades, plans, pending positions, and calendar endpoints.
- [ ] Run focused account and portfolio tests.

### Task 4: Account-Scoped Data Gaps

**Files:**
- Modify: `models/database.py`
- Modify: `repositories/portfolio_repository.py`
- Modify: `services/portfolio_service.py`
- Test: `tests/test_portfolio_service.py`

- [ ] Write failing tests for account-scoped `daily_pnl`, trading plans, conditional orders, pending positions, and stock trade clearing.
- [ ] Migrate `daily_pnl` to include `account_id` in the primary key.
- [ ] Write `account_id` on insert/update paths.
- [ ] Ensure destructive actions filter by securities account.
- [ ] Run focused service tests.

### Task 5: Frontend Surfaces And Final Verification

**Files:**
- Modify: `templates/base.html`
- Modify: `templates/settings.html`
- Modify: `static/js/app.js`
- Modify: `static/js/settings.js`
- Modify: `static/js/portfolio.js`
- Test: `tests/test_release_migration.py`

- [ ] Write failing release checks for login-user and securities-account UI labels.
- [ ] Update the top bar to show login user plus securities-account switcher.
- [ ] Update settings account management copy to distinguish login users from securities accounts.
- [ ] Ensure portfolio actions continue sending selected securities account id.
- [ ] Run focused release migration checks and account tests.
