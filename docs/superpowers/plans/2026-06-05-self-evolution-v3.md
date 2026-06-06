# Self Evolution v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the v3.0 self-evolving AI stock recommendation loop that evaluates research signals, account actions, trade memories, and realized outcomes, then injects the latest learning snapshot into future report context.

**Architecture:** Add a `self_evolution_snapshots` table and a focused `services/self_evolution_service.py` that computes a deterministic snapshot from existing `analysis_reports`, `signal_tracking`, `position_plans`, `position_plan_items`, `trade_memories`, and `trades`. Expose the latest snapshot through `/api/self-evolution/*` and append its compact context to `investment_profile_context` so every existing report path inherits the learning loop without rewriting batch-report internals.

**Tech Stack:** FastAPI, SQLite, existing investment profile context, existing trade memory service, Python unittest.

---

### Task 1: Snapshot Schema and Service Contract

**Files:**
- Modify: `models/database.py`
- Create: `services/self_evolution_service.py`
- Test: `tests/test_self_evolution_service.py`

- [ ] **Step 1: Write failing tests**

Add tests that initialize a temporary database and assert:

```python
row = conn.execute("SELECT name FROM sqlite_master WHERE name='self_evolution_snapshots'").fetchone()
self.assertIsNotNone(row)
snapshot = self_evolution_service.build_snapshot(db_path=self.db_path)
self.assertEqual(snapshot["version"], "self-evolution-v3")
self.assertIn("research_signal", snapshot["layers"])
self.assertIn("account_action", snapshot["layers"])
self.assertIn("trade_memory", snapshot["layers"])
self.assertIn("realized_outcome", snapshot["layers"])
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv312/bin/python -m unittest tests.test_self_evolution_service -v`
Expected: fail because the table and service do not exist.

- [ ] **Step 3: Add schema and minimal service**

Add `self_evolution_snapshots` with `snapshot_id`, `version`, `status`, `source_counts_json`, `layers_json`, `rules_json`, `context`, and timestamps. Implement `build_snapshot`.

- [ ] **Step 4: Run tests**

Run: `.venv312/bin/python -m unittest tests.test_self_evolution_service -v`
Expected: schema and snapshot tests pass.

### Task 2: Four-Layer Evaluation and Context

**Files:**
- Modify: `services/self_evolution_service.py`
- Modify: `services/investment_profile_service.py`
- Test: `tests/test_self_evolution_service.py`

- [ ] **Step 1: Write failing evaluation tests**

Seed one losing `BUY` signal, one active failure memory, one adopted position plan, and one closed losing trade. Assert:

```python
snapshot = self_evolution_service.build_snapshot(db_path=self.db_path)
self.assertLess(snapshot["system_score"], 70)
self.assertTrue(snapshot["rules"])
context = self_evolution_service.snapshot_context(snapshot)
self.assertIn("【AI自我进化画像】", context)
self.assertIn("research_signal", context)
self.assertIn("account_action", context)
self.assertIn("不得改写股票研究信号", context)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv312/bin/python -m unittest tests.test_self_evolution_service -v`
Expected: fail until scoring/context are implemented.

- [ ] **Step 3: Implement scoring**

Implement:
- `research_signal`: signal tracking win rate and average pnl.
- `account_action`: adopted position plan count, tracked plan items, follow evidence.
- `trade_memory`: active success/failure lessons and veto pressure.
- `realized_outcome`: closed trade count, win rate, total realized pnl.

Generate rules that affect only account action, sizing, vetoes, and review questions.

- [ ] **Step 4: Inject latest context**

Update `investment_profile_context(..., db_path=...)` to append `self_evolution_service.latest_context(db_path=db_path)` after trade memory context.

### Task 3: Persistence and API

**Files:**
- Create: `api/self_evolution_api.py`
- Modify: `app.py`
- Test: `tests/test_self_evolution_service.py`

- [ ] **Step 1: Write failing API tests**

Add FastAPI tests for:

```http
POST /api/self-evolution/run
GET /api/self-evolution/latest
GET /api/self-evolution/context
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv312/bin/python -m unittest tests.test_self_evolution_service -v`
Expected: fail with missing routes.

- [ ] **Step 3: Add API endpoints**

Add API handlers that call `run_cycle`, `latest_snapshot`, and `latest_context`. Register router in `app.py`.

- [ ] **Step 4: Run tests**

Run: `.venv312/bin/python -m unittest tests.test_self_evolution_service -v`
Expected: pass.

### Task 4: Verification

**Files:**
- Test-only verification.

- [ ] **Step 1: Focused tests**

Run: `.venv312/bin/python -m unittest tests.test_self_evolution_service tests.test_trade_memory_service -v`
Expected: pass.

- [ ] **Step 2: Adjacent tests**

Run: `.venv312/bin/python -m unittest tests.test_portfolio_service tests.test_settings_service tests.test_batch_research tests.test_holding_review_service tests.test_release_migration -v`
Expected: pass.

- [ ] **Step 3: Compile changed Python**

Run: `.venv312/bin/python -m py_compile services/self_evolution_service.py api/self_evolution_api.py app.py services/investment_profile_service.py models/database.py`
Expected: exit 0.
