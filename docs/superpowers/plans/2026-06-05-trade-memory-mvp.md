# Trade Memory MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first version of a self-evolving trade memory system that turns closed trades into reviewed memory cards and injects active lessons into future AI report context.

**Architecture:** Add a focused trade memory service backed by SQLite, expose portfolio API endpoints for pending closed trades and memory CRUD, and append active memory context to the existing investment profile prompt so existing report generation paths inherit it without touching dirty batch-report files. The first version is API-first and context-enabled; richer UI and vector retrieval are next-phase work.

**Tech Stack:** FastAPI, SQLite/aiosqlite, existing `models.database` schema initialization, Python unittest.

---

### Task 1: Trade Memory Schema

**Files:**
- Modify: `models/database.py`
- Test: `tests/test_trade_memory_service.py`

- [ ] **Step 1: Write failing schema/service test**

Create `tests/test_trade_memory_service.py` with a test that initializes a temporary database and verifies `trade_memories` exists after `init_db`.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_trade_memory_service -v`
Expected: fail because the service/table does not exist.

- [ ] **Step 3: Add `trade_memories` schema**

Add `CREATE TABLE IF NOT EXISTS trade_memories` to `models/database.py` with fields for account, code, name, trade ids, time window, pnl, lessons JSON, status, and timestamps.

- [ ] **Step 4: Run schema test**

Run: `python -m unittest tests.test_trade_memory_service -v`
Expected: schema part passes, later service tests fail until implemented.

### Task 2: Trade Memory Service

**Files:**
- Create: `services/trade_memory_service.py`
- Test: `tests/test_trade_memory_service.py`

- [ ] **Step 1: Write failing service tests**

Add tests for:
- closed-trade candidate detection
- draft memory generation from a completed buy/sell cycle
- active memory context formatting

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_trade_memory_service -v`
Expected: fail due to missing service functions.

- [ ] **Step 3: Implement service**

Implement:
- `list_closed_trade_candidates`
- `generate_memory_draft`
- `save_trade_memory`
- `list_trade_memories`
- `trade_memory_context`

- [ ] **Step 4: Run service tests**

Run: `python -m unittest tests.test_trade_memory_service -v`
Expected: pass.

### Task 3: API Endpoints

**Files:**
- Modify: `api/portfolio_api.py`
- Test: `tests/test_trade_memory_service.py`

- [ ] **Step 1: Write failing API tests**

Add FastAPI TestClient tests for:
- `GET /api/trade-memories/candidates`
- `POST /api/trade-memories/draft`
- `POST /api/trade-memories`
- `GET /api/trade-memories/context`

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_trade_memory_service -v`
Expected: 404 or missing API functions.

- [ ] **Step 3: Add endpoints to existing portfolio router**

Add API models and route handlers in `api/portfolio_api.py`, delegating to `trade_memory_service`.

- [ ] **Step 4: Run API tests**

Run: `python -m unittest tests.test_trade_memory_service -v`
Expected: pass.

### Task 4: Report Context Injection

**Files:**
- Modify: `services/investment_profile_service.py`
- Test: `tests/test_trade_memory_service.py`

- [ ] **Step 1: Write failing context injection test**

Add a test that saves an active trade memory and verifies `investment_profile_from_db(temp_db)["context"]` contains `【交易复盘记忆】`.

- [ ] **Step 2: Run tests to verify failure**

Run: `python -m unittest tests.test_trade_memory_service -v`
Expected: fail because profile context does not include trade memories.

- [ ] **Step 3: Inject trade memory context**

Import `trade_memory_service` lazily in `investment_profile_context` and append compact active memory lessons when available.

- [ ] **Step 4: Run focused tests**

Run: `python -m unittest tests.test_trade_memory_service -v`
Expected: pass.

### Task 5: Verification

**Files:**
- Test-only verification.

- [ ] **Step 1: Run focused tests**

Run: `python -m unittest tests.test_trade_memory_service -v`
Expected: all tests pass.

- [ ] **Step 2: Run adjacent tests**

Run: `python -m unittest tests.test_portfolio_service tests.test_settings_service tests.test_release_migration -v`
Expected: pass or report pre-existing failures clearly.

- [ ] **Step 3: Compile changed Python files**

Run: `python -m py_compile services/trade_memory_service.py api/portfolio_api.py services/investment_profile_service.py models/database.py`
Expected: exit 0.

