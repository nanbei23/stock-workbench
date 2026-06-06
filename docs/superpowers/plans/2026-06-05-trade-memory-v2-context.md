# Trade Memory v2 Context Injection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade trade memory to v2.0 so report prompts receive relevant, constrained, self-evolving trade lessons instead of a flat memory list.

**Architecture:** Keep trade memory persistence in `services/trade_memory_service.py`, add deterministic scenario extraction and similarity ranking, and expose the retrieval result through the existing investment profile context chain. The context block must state that memories only adjust account action, sizing, entry vetoes, and exit discipline; they must not rewrite the raw stock research signal.

**Tech Stack:** FastAPI, SQLite, existing investment profile context, Python unittest.

---

### Task 1: Similar Memory Retrieval

**Files:**
- Modify: `tests/test_trade_memory_service.py`
- Modify: `services/trade_memory_service.py`

- [ ] **Step 1: Write failing retrieval tests**

Add tests that save active memories for `002156` and `601138`, then assert:

```python
related = trade_memory_service.related_trade_memories(
    code="002156",
    report_text="涨停后追入，高估值，AI 卖出信号冲突",
    db_path=self.db_path,
)
self.assertEqual(related["matches"][0]["code"], "002156")
self.assertIn("涨停", related["matches"][0]["match_reason"])
```

- [ ] **Step 2: Run test to verify failure**

Run: `.venv312/bin/python -m unittest tests.test_trade_memory_service -v`
Expected: fail because `related_trade_memories` does not exist.

- [ ] **Step 3: Implement deterministic ranking**

Add scenario tag extraction from code, report text, lesson tags, rules, and veto lessons. Rank exact-code matches first, then tag overlap, then risk keywords such as `涨停`, `高估值`, `仓位过重`, `信号冲突`, `左侧`, and `回踩`.

- [ ] **Step 4: Run tests**

Run: `.venv312/bin/python -m unittest tests.test_trade_memory_service -v`
Expected: retrieval tests pass.

### Task 2: Hard Context Constraints

**Files:**
- Modify: `tests/test_trade_memory_service.py`
- Modify: `services/trade_memory_service.py`
- Modify: `services/investment_profile_service.py`

- [ ] **Step 1: Write failing context tests**

Assert `trade_memory_context(report_text=...)` includes:

```text
【交易复盘记忆约束】
只校准账户动作
不得直接覆盖股票研究信号
memory_match
memory_adjustments
```

Also assert `investment_profile_from_db(self.db_path)["context"]` includes the same constraint block.

- [ ] **Step 2: Run test to verify failure**

Run: `.venv312/bin/python -m unittest tests.test_trade_memory_service -v`
Expected: fail because the context block does not yet include v2.0 constraints.

- [ ] **Step 3: Implement v2.0 context block**

Update `trade_memory_context` to call `related_trade_memories`, render a compact ordered memory list, and prepend the constraint contract:

```text
- 适用范围：只校准账户动作、买入否决、试仓/加仓、仓位上限、退出纪律。
- 禁止事项：不得直接覆盖股票研究信号；不得把历史亏损自动等同于当前标的看空。
- 输出字段：必须补充 memory_match 与 memory_adjustments。
```

- [ ] **Step 4: Run tests**

Run: `.venv312/bin/python -m unittest tests.test_trade_memory_service -v`
Expected: context tests pass.

### Task 3: API and Injection Map

**Files:**
- Modify: `tests/test_trade_memory_service.py`
- Modify: `api/portfolio_api.py`
- Modify: `services/trade_memory_service.py`

- [ ] **Step 1: Write failing API tests**

Add FastAPI tests for:

```http
POST /api/trade-memories/related
GET /api/trade-memories/injection-map
```

The injection map must include `single_stock_report`, `batch_snapshot_report`, `daily_holding_review`, and `position_plan`.

- [ ] **Step 2: Run test to verify failure**

Run: `.venv312/bin/python -m unittest tests.test_trade_memory_service -v`
Expected: fail with 404.

- [ ] **Step 3: Add API endpoints**

Add request model `TradeMemoryRelatedRequest` and handlers that delegate to `trade_memory_service.related_trade_memories` and `trade_memory_service.context_injection_status`.

- [ ] **Step 4: Run tests**

Run: `.venv312/bin/python -m unittest tests.test_trade_memory_service -v`
Expected: API tests pass.

### Task 4: Verification

**Files:**
- Test-only verification.

- [ ] **Step 1: Focused tests**

Run: `.venv312/bin/python -m unittest tests.test_trade_memory_service -v`
Expected: pass.

- [ ] **Step 2: Adjacent tests**

Run: `.venv312/bin/python -m unittest tests.test_portfolio_service tests.test_settings_service -v`
Expected: pass.

- [ ] **Step 3: Compile changed Python**

Run: `.venv312/bin/python -m py_compile services/trade_memory_service.py api/portfolio_api.py services/investment_profile_service.py models/database.py`
Expected: exit 0.
