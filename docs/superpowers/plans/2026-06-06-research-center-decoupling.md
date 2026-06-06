# Research Center Decoupling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split "智能盯盘" from "AI投研中心" so real-time single-stock work and durable research tasks have clear product and code boundaries.

**Architecture:** Add a lightweight report selection set as the only cross-page handoff from the watch desk to the research center. Keep existing batch, position plan, daily decision, and report services as the durable task owners.

**Tech Stack:** FastAPI, SQLite, Jinja templates, plain JavaScript, unittest.

---

### Task 1: Navigation And Naming

**Files:**
- Modify: `templates/base.html`
- Modify: `templates/ai.html`
- Modify: `templates/reports.html`
- Test: `tests/test_release_migration.py`

- [ ] Verify top navigation shows `智能盯盘` for `/ai` and `AI投研中心` for `/reports`.
- [ ] Verify reports page title and tab hint use AI投研中心 language.
- [ ] Keep routes stable: `/ai` and `/reports` are not renamed.

### Task 2: Selection Set Backend

**Files:**
- Modify: `models/database.py`
- Create: `services/report_selection_service.py`
- Create: `api/report_selection_api.py`
- Modify: `app.py`
- Test: `tests/test_report_selection_service.py`

- [ ] Create `report_selection_sets` with selection id, source, code payload, filters, and expiry.
- [ ] Add create/get/delete service functions.
- [ ] Add `/api/report-selections` endpoints.
- [ ] Ensure expired selections return 404.

### Task 3: Smart Watch Desk Handoff

**Files:**
- Modify: `templates/ai.html`
- Modify: `static/js/ai.js`
- Test: `tests/test_release_migration.py`

- [ ] Remove the full batch research panel from 智能盯盘.
- [ ] Keep current-stock recent report summary.
- [ ] Batch bar creates a selection set and navigates to `/reports?tab=jobs&selection_id=...`.
- [ ] Add a second handoff button for `/reports?tab=plans&selection_id=...`.

### Task 4: AI Research Center Intake

**Files:**
- Modify: `templates/reports.html`
- Modify: `static/js/reports.js`
- Test: `tests/test_release_migration.py`

- [ ] Read `selection_id` from URL.
- [ ] Fetch selection set and show an intake banner.
- [ ] Apply selected codes to batch report / data prefetch / position plan creation.
- [ ] Keep old selected-report based position plan flow working.

### Task 5: Verification

**Files:**
- Test: `tests/test_report_selection_service.py`
- Test: `tests/test_release_migration.py`

- [ ] Run selection service tests.
- [ ] Run release migration UI contract tests.
- [ ] Run Python syntax checks and JS syntax checks.
