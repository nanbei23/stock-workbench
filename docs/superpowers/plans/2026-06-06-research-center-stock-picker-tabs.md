# AI投研中心股票选择解耦 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move bulk stock selection and research-task creation out of `智能盯盘` and into `AI投研中心`, where the left sidebar has two modes: `股票选择` and `报告筛选`.

**Architecture:** `智能盯盘` becomes a real-time single-stock work surface. `AI投研中心` owns stock-level batch task creation and report-level filtering/export/research. The new sidebar stock picker uses existing `/api/watchlist`, existing market-permission helpers, and existing `/api/batch-research/preflight` / `/api/batch-research/jobs`; no new database table is required.

**Tech Stack:** FastAPI, SQLite, Jinja templates, plain JavaScript, existing CSS design system, unittest, Node syntax checks.

---

## File Structure

- Modify `templates/ai.html`
  - Remove batch-only UI from the smart-watch sidebar: `batchToggleBtn`, `batchBar`, and checkbox-driven send-to-research buttons.
  - Keep single-stock click/analysis and search/market/signal filters.
- Modify `static/js/ai.js`
  - Remove `createReportSelectionSet`, `sendSelectionToResearchCenter`, batch selection bar updates, and checkbox rendering from smart-watch cards.
  - Keep `selectedCardCode`, `loadAIStockCards`, `renderAIStockCards`, and single-stock analysis.
- Modify `templates/reports.html`
  - Add a sidebar segmented switch: `股票选择` and `报告筛选`.
  - Move existing report filters into `reportFilterPanel`.
  - Add `researchStockPickerPanel` with stock search, market filter, last-report-signal filter, selected-count summary, and task buttons.
- Modify `static/js/reports.js`
  - Add report-center stock picker state and rendering.
  - Load stocks from `/api/watchlist`.
  - Create batch jobs directly from selected stock codes.
  - Keep `selection_id` handoff as compatibility by importing codes into the stock picker instead of using a separate task path.
- Modify `static/css/style.css`
  - Add compact sidebar mode tabs and stock-card styles scoped to the report library.
- Modify `templates/base.html`
  - Bump `style.css` cache version if CSS changes.
- Modify `tests/test_release_migration.py`
  - Update decoupling assertions: `/ai` no longer has send-to-research controls; `/reports` owns stock picker and task creation.
- Optionally modify `tests/test_report_selection_service.py`
  - Keep existing tests for backward-compatible `report_selection_sets`; no new tests unless handoff behavior changes at API level.

---

### Task 1: Lock The New Product Boundary With Failing Tests

**Files:**
- Modify: `tests/test_release_migration.py`

- [ ] **Step 1: Add a failing test that smart-watch no longer creates research tasks**

Add this test near the existing smart-watch / research-center tests:

```python
def test_smart_watch_no_longer_owns_bulk_research_creation(self):
    ai_html = AI_TEMPLATE.read_text(encoding="utf-8")
    ai_js = AI_JS.read_text(encoding="utf-8")

    self.assertIn("{% block title %}智能盯盘", ai_html)
    self.assertNotIn("sendSelectionToResearchCenter('jobs')", ai_html)
    self.assertNotIn("sendSelectionToResearchCenter('plans')", ai_html)
    self.assertNotIn('id="batchBar"', ai_html)
    self.assertNotIn('id="batchToggleBtn"', ai_html)
    self.assertNotIn("createReportSelectionSet", ai_js)
    self.assertNotIn("sendSelectionToResearchCenter", ai_js)
    self.assertNotIn("ai-sc-check", ai_js)
```

- [ ] **Step 2: Add a failing test that AI投研中心 has two sidebar modes**

Add this test:

```python
def test_research_center_sidebar_has_stock_picker_and_report_filter_modes(self):
    reports_html = REPORTS_TEMPLATE.read_text(encoding="utf-8")
    reports_js = REPORTS_JS.read_text(encoding="utf-8")

    self.assertIn("researchSidebarTabs", reports_html)
    self.assertIn("researchStockPickerPanel", reports_html)
    self.assertIn("reportFilterPanel", reports_html)
    self.assertIn("股票选择", reports_html)
    self.assertIn("报告筛选", reports_html)
    self.assertIn("loadResearchStockPickerStocks", reports_js)
    self.assertIn("/api/watchlist", reports_js)
    self.assertIn("createBatchJobFromResearchStocks", reports_js)
    self.assertIn("selectedResearchStockCodes", reports_js)
```

- [ ] **Step 3: Add a failing test that task creation moved to the research-center stock picker**

Add this test:

```python
def test_research_center_stock_picker_creates_batch_jobs_directly(self):
    reports_html = REPORTS_TEMPLATE.read_text(encoding="utf-8")
    reports_js = REPORTS_JS.read_text(encoding="utf-8")

    self.assertIn("生成所选报告", reports_html)
    self.assertIn("预取七层数据", reports_html)
    self.assertIn("生成组合研究", reports_html)
    self.assertIn("researchStockTaskTypeInput", reports_html)
    self.assertIn("researchStockDepthInput", reports_html)
    self.assertIn("researchStockModelModeInput", reports_html)
    self.assertIn("/api/batch-research/preflight", reports_js)
    self.assertIn("/api/batch-research/jobs", reports_js)
    self.assertIn("source_page: 'research_center_stock_picker'", reports_js)
```

- [ ] **Step 4: Run tests and confirm they fail**

Run:

```bash
.venv312/bin/python -m unittest \
  tests.test_release_migration.ReleaseMigrationTests.test_smart_watch_no_longer_owns_bulk_research_creation \
  tests.test_release_migration.ReleaseMigrationTests.test_research_center_sidebar_has_stock_picker_and_report_filter_modes \
  tests.test_release_migration.ReleaseMigrationTests.test_research_center_stock_picker_creates_batch_jobs_directly \
  -v
```

Expected: all three fail because the UI and JS still use the previous handoff model.

---

### Task 2: Remove Bulk Research Controls From 智能盯盘

**Files:**
- Modify: `templates/ai.html`
- Modify: `static/js/ai.js`

- [ ] **Step 1: Remove smart-watch batch UI from `templates/ai.html`**

Remove the batch button from `.ai-left-actions`:

```html
<button class="btn btn-sm ai-batch-toggle" id="batchToggleBtn" onclick="toggleBatchMode()" title="批量选择">批量</button>
```

Remove the entire `batchBar` block:

```html
<div id="batchBar" class="ai-batch-bar" style="display:none">
    <span id="batchCount">已选 0 只</span>
    <button class="btn btn-sm btn-primary" onclick="sendSelectionToResearchCenter('jobs')">送投研生成报告</button>
    <button class="btn btn-sm" onclick="sendSelectionToResearchCenter('plans')">送投研做组合研究</button>
    <button class="btn btn-sm" onclick="clearSelection()">取消</button>
</div>
```

- [ ] **Step 2: Remove checkbox markup from `renderAIStockCards`**

Change the card template in `static/js/ai.js` so it no longer renders this block:

```js
<div class="ai-sc-check" onclick="event.stopPropagation()"><input type="checkbox" data-code="${escapeAttr(s.code)}" ${checked} onchange="setBatchSelection('${escapeAttr(s.code)}', this.checked)"></div>
```

Keep the rest of the card content intact. The card remains clickable through `onclick="selectCard(...)"`.

- [ ] **Step 3: Delete smart-watch batch handoff functions**

Remove these functions from `static/js/ai.js`:

```js
function setBatchSelection(code, checked) { ... }
function getSelectedCodes() { ... }
function visibleAIStocks() { ... }
function ensureBatchMode() { ... }
function updateBatchBar() { ... }
function clearSelection() { ... }
function selectVisibleAIStocks() { ... }
function selectAIStocksByLastSignals() { ... }
function toggleBatchMode() { ... }
async function createReportSelectionSet(targetTab = 'jobs') { ... }
async function sendSelectionToResearchCenter(targetTab = 'jobs') { ... }
```

Keep `batchAnalyze()` only if another call site still exists. If it remains, make it single-purpose:

```js
async function batchAnalyze() {
    window.location.href = '/reports?sidebar=stocks&tab=jobs';
}
```

- [ ] **Step 4: Run the smart-watch boundary test**

Run:

```bash
.venv312/bin/python -m unittest \
  tests.test_release_migration.ReleaseMigrationTests.test_smart_watch_no_longer_owns_bulk_research_creation \
  -v
```

Expected: PASS.

---

### Task 3: Add AI投研中心 Sidebar Modes

**Files:**
- Modify: `templates/reports.html`
- Modify: `static/css/style.css`

- [ ] **Step 1: Wrap the left sidebar body in two panels**

In `templates/reports.html`, inside `<aside class="report-library-filters">`, add the segmented control after `.library-panel-title`:

```html
<div class="research-sidebar-tabs" id="researchSidebarTabs">
    <button class="active" type="button" data-sidebar-tab="stocks" onclick="switchResearchSidebarTab('stocks')">股票选择</button>
    <button type="button" data-sidebar-tab="reports" onclick="switchResearchSidebarTab('reports')">报告筛选</button>
</div>
```

Wrap the current report filters, selection summary, and report export actions in:

```html
<div class="research-sidebar-panel" id="reportFilterPanel" data-sidebar-panel="reports">
    <!-- existing library-filter-stack, library-selection-summary, library-actions -->
</div>
```

- [ ] **Step 2: Add the stock picker panel before `reportFilterPanel`**

Add:

```html
<div class="research-sidebar-panel active" id="researchStockPickerPanel" data-sidebar-panel="stocks">
    <label class="library-filter">
        <span>搜索股票</span>
        <input id="researchStockSearch" class="form-input" placeholder="股票名称 / 代码" oninput="filterResearchStockPicker(this.value)">
    </label>
    <div class="library-filter">
        <span>交易市场</span>
        <div class="signal-filter-grid" id="researchStockMarketFilters" aria-label="股票选择市场筛选">
            <button type="button" class="signal-filter-chip active" data-market="tradable" onclick="setResearchStockMarketFilter('tradable')">可交易</button>
            <button type="button" class="signal-filter-chip" data-market="all" onclick="setResearchStockMarketFilter('all')">全部</button>
            <button type="button" class="signal-filter-chip" data-market="main" onclick="setResearchStockMarketFilter('main')">主板</button>
            <button type="button" class="signal-filter-chip" data-market="gem" onclick="setResearchStockMarketFilter('gem')">创业板</button>
            <button type="button" class="signal-filter-chip" data-market="star" onclick="setResearchStockMarketFilter('star')">科创板</button>
            <button type="button" class="signal-filter-chip" data-market="bse" onclick="setResearchStockMarketFilter('bse')">北交所</button>
        </div>
    </div>
    <div class="library-filter">
        <span>上次报告信号</span>
        <div class="signal-filter-grid" id="researchStockSignalFilters" aria-label="股票选择信号筛选">
            <button type="button" class="signal-filter-chip active" data-signal="" onclick="toggleResearchStockSignalFilter('')">全部</button>
            <button type="button" class="signal-filter-chip" data-signal="STRONG_BUY" onclick="toggleResearchStockSignalFilter('STRONG_BUY')">强烈买入</button>
            <button type="button" class="signal-filter-chip" data-signal="BUY" onclick="toggleResearchStockSignalFilter('BUY')">买入</button>
            <button type="button" class="signal-filter-chip" data-signal="OVERWEIGHT" onclick="toggleResearchStockSignalFilter('OVERWEIGHT')">增持</button>
            <button type="button" class="signal-filter-chip" data-signal="HOLD" onclick="toggleResearchStockSignalFilter('HOLD')">持有</button>
            <button type="button" class="signal-filter-chip" data-signal="UNDERWEIGHT" onclick="toggleResearchStockSignalFilter('UNDERWEIGHT')">减持</button>
            <button type="button" class="signal-filter-chip" data-signal="SELL" onclick="toggleResearchStockSignalFilter('SELL')">卖出</button>
            <button type="button" class="signal-filter-chip" data-signal="STRONG_SELL" onclick="toggleResearchStockSignalFilter('STRONG_SELL')">强烈卖出</button>
            <button type="button" class="signal-filter-chip" data-signal="NO_REPORT" onclick="toggleResearchStockSignalFilter('NO_REPORT')">无报告</button>
        </div>
    </div>
    <div class="library-action-row">
        <button class="btn btn-sm" type="button" onclick="selectVisibleResearchStocks()">全选当前</button>
        <button class="btn btn-sm" type="button" onclick="clearResearchStockSelection()">清空选择</button>
    </div>
    <div class="library-selection-summary">
        <span>已选</span>
        <strong id="selectedResearchStockCount">0</strong>
        <span>只股票</span>
    </div>
    <div class="library-actions">
        <button class="btn btn-primary" type="button" onclick="openResearchStockTaskModal('report_generation')">生成所选报告</button>
        <div class="library-action-row">
            <button class="btn" type="button" onclick="openResearchStockTaskModal('data_prefetch')">预取七层数据</button>
            <button class="btn" type="button" onclick="openResearchStockTaskModal('position_plan')">生成组合研究</button>
        </div>
    </div>
    <div class="research-stock-list" id="researchStockList">
        <div class="library-empty-state">正在加载自选股...</div>
    </div>
</div>
```

- [ ] **Step 3: Add task modal fields**

Replace or extend the existing `selectionTaskModal` so its IDs are task-source neutral:

```html
<div class="modal-overlay" id="researchStockTaskModal">
    <div class="modal-box" style="max-width:560px;">
        <div class="modal-title">用所选股票创建投研任务</div>
        <div class="modal-body">
            <div class="selection-task-summary" id="researchStockTaskSummary">等待选择股票...</div>
            <label class="library-filter">
                <span>任务类型</span>
                <select id="researchStockTaskTypeInput" class="form-input">
                    <option value="report_generation">批量生成单股报告</option>
                    <option value="data_prefetch">预取七层数据</option>
                    <option value="position_plan">生成组合研究</option>
                </select>
            </label>
            <label class="library-filter">
                <span>分析深度</span>
                <select id="researchStockDepthInput" class="form-input">
                    <option value="quick">快速</option>
                    <option value="standard" selected>标准</option>
                    <option value="deep">深度</option>
                </select>
            </label>
            <label class="library-filter">
                <span>模型模式</span>
                <select id="researchStockModelModeInput" class="form-input">
                    <option value="economy">经济</option>
                    <option value="balanced" selected>均衡</option>
                    <option value="flagship">旗舰</option>
                </select>
            </label>
            <label class="mini-check-row">
                <input type="checkbox" id="researchStockForceReanalysis">
                <span>强制重新分析，最近已有报告也重跑</span>
            </label>
        </div>
        <div class="modal-actions">
            <button class="btn" onclick="closeResearchStockTaskModal()">取消</button>
            <button class="btn btn-primary" onclick="submitResearchStockTaskModal()">创建任务</button>
        </div>
    </div>
</div>
```

- [ ] **Step 4: Add CSS for the mode tabs and stock list**

Add to `static/css/style.css` near the AI投研中心 section:

```css
.research-sidebar-tabs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px;
  margin-bottom: 12px;
}

.research-sidebar-tabs button {
  min-height: 32px;
  border: 1px solid var(--border-light);
  border-radius: 8px;
  background: var(--bg-hover);
  color: var(--text-secondary);
  font-size: 0.82rem;
  cursor: pointer;
}

.research-sidebar-tabs button.active {
  border-color: rgba(91,155,213,0.5);
  background: rgba(91,155,213,0.14);
  color: var(--color-accent);
  font-weight: 700;
}

.research-sidebar-panel {
  display: none;
}

.research-sidebar-panel.active {
  display: grid;
  gap: 10px;
}

.research-stock-list {
  display: grid;
  gap: 8px;
  overflow: auto;
  padding-right: 2px;
}

.research-stock-card {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  gap: 8px;
  padding: 10px;
  border: 1px solid var(--border-light);
  border-radius: 8px;
  background: var(--bg-card);
  cursor: pointer;
}

.research-stock-card.selected {
  border-color: rgba(91,155,213,0.65);
  background: rgba(91,155,213,0.12);
}

.research-stock-card strong,
.research-stock-card code {
  display: block;
}

.research-stock-card code {
  color: var(--color-accent);
  font-family: var(--font-mono);
  font-size: 0.82rem;
}
```

- [ ] **Step 5: Run the sidebar mode template test**

Run:

```bash
.venv312/bin/python -m unittest \
  tests.test_release_migration.ReleaseMigrationTests.test_research_center_sidebar_has_stock_picker_and_report_filter_modes \
  -v
```

Expected: PASS after template and CSS changes, unless JS function assertions are still failing. JS is implemented in Task 4.

---

### Task 4: Implement AI投研中心 Stock Picker Logic

**Files:**
- Modify: `static/js/reports.js`

- [ ] **Step 1: Add state variables**

Near existing report-center state variables, add:

```js
let researchStockCache = [];
let researchStockSearchText = '';
let researchStockMarketFilter = 'tradable';
const selectedResearchStockCodes = new Set();
const researchStockSignalFilters = new Set();
```

- [ ] **Step 2: Initialize stock picker**

In `initReportLibrary`, after `renderReportMarketFilterState();`, add:

```js
renderResearchStockMarketFilterState();
renderResearchStockSignalFilterState();
await loadResearchStockPickerStocks();
```

- [ ] **Step 3: Add sidebar tab switching**

Add:

```js
function switchResearchSidebarTab(tab) {
    document.querySelectorAll('#researchSidebarTabs button').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.sidebarTab === tab);
    });
    document.querySelectorAll('.research-sidebar-panel').forEach(panel => {
        panel.classList.toggle('active', panel.dataset.sidebarPanel === tab);
    });
}
```

- [ ] **Step 4: Add stock loading and filtering**

Add:

```js
async function loadResearchStockPickerStocks() {
    const box = document.getElementById('researchStockList');
    if (box) box.innerHTML = '<div class="library-empty-state">正在加载自选股...</div>';
    try {
        const data = await requestJson('/api/watchlist');
        researchStockCache = (data.stocks || []).slice();
        renderResearchStockPicker();
    } catch (err) {
        if (box) box.innerHTML = `<div class="library-empty-state">自选股加载失败：${escapeHtml(err.message)}</div>`;
    }
}

function normalizeResearchStockSearch(value) {
    return String(value || '').trim().toLowerCase();
}

function researchStockLastSignal(stock) {
    return stock.last_report_signal || 'NO_REPORT';
}

function researchStockMatchesSearch(stock, query) {
    if (!query) return true;
    return [stock.code, stock.name, stock.pinyin, stock.group_name]
        .some(value => String(value || '').toLowerCase().includes(query));
}

function researchStockMatchesMarket(stock) {
    return window.StockMarketPermissions?.matchesFilter?.(stock.code, researchStockMarketFilter) ?? true;
}

function researchStockMatchesSignal(stock) {
    return !researchStockSignalFilters.size || researchStockSignalFilters.has(researchStockLastSignal(stock));
}
```

- [ ] **Step 5: Render stock cards**

Add:

```js
function renderResearchStockPicker() {
    const box = document.getElementById('researchStockList');
    if (!box) return;
    const query = normalizeResearchStockSearch(researchStockSearchText);
    const stocks = researchStockCache
        .filter(stock => researchStockMatchesSearch(stock, query))
        .filter(researchStockMatchesMarket)
        .filter(researchStockMatchesSignal);
    if (!stocks.length) {
        box.innerHTML = '<div class="library-empty-state">暂无符合条件的股票</div>';
        updateResearchStockSelectionSummary();
        return;
    }
    box.innerHTML = stocks.map(stock => {
        const code = String(stock.code || '');
        const checked = selectedResearchStockCodes.has(code);
        const signal = researchStockLastSignal(stock);
        const market = window.StockMarketPermissions?.classify?.(code) || { label: '未知' };
        const price = Number(stock.price || 0);
        const changePct = Number(stock.change_pct || 0);
        return `<div class="research-stock-card ${checked ? 'selected' : ''}" onclick="toggleResearchStockSelection('${escapeAttr(code)}')">
            <input type="checkbox" ${checked ? 'checked' : ''} onclick="event.stopPropagation(); toggleResearchStockSelection('${escapeAttr(code)}', this.checked)">
            <div>
                <strong>${escapeHtml(stock.name || code)}</strong>
                <code>${escapeHtml(code)}</code>
                <span class="report-signal">${escapeHtml(SIG_LABEL[signal] || signal)} · ${escapeHtml(market.label)}</span>
                <small>${price.toFixed(3)} · ${changePct >= 0 ? '+' : ''}${changePct.toFixed(3)}%</small>
            </div>
        </div>`;
    }).join('');
    updateResearchStockSelectionSummary();
}
```

- [ ] **Step 6: Add selection operations**

Add:

```js
function visibleResearchStocks() {
    const query = normalizeResearchStockSearch(researchStockSearchText);
    return researchStockCache
        .filter(stock => researchStockMatchesSearch(stock, query))
        .filter(researchStockMatchesMarket)
        .filter(researchStockMatchesSignal);
}

function toggleResearchStockSelection(code, explicitValue) {
    const clean = String(code || '');
    const checked = explicitValue == null ? !selectedResearchStockCodes.has(clean) : !!explicitValue;
    if (checked) selectedResearchStockCodes.add(clean);
    else selectedResearchStockCodes.delete(clean);
    renderResearchStockPicker();
}

function selectVisibleResearchStocks() {
    visibleResearchStocks().forEach(stock => selectedResearchStockCodes.add(String(stock.code || '')));
    renderResearchStockPicker();
}

function clearResearchStockSelection() {
    selectedResearchStockCodes.clear();
    renderResearchStockPicker();
}

function updateResearchStockSelectionSummary() {
    const count = document.getElementById('selectedResearchStockCount');
    if (count) count.textContent = String(selectedResearchStockCodes.size);
}
```

- [ ] **Step 7: Add filter operations**

Add:

```js
function filterResearchStockPicker(value) {
    researchStockSearchText = value || '';
    renderResearchStockPicker();
}

function renderResearchStockMarketFilterState() {
    document.querySelectorAll('#researchStockMarketFilters .signal-filter-chip').forEach(btn => {
        btn.classList.toggle('active', (btn.dataset.market || 'tradable') === researchStockMarketFilter);
    });
}

function setResearchStockMarketFilter(filter) {
    researchStockMarketFilter = filter || 'tradable';
    renderResearchStockMarketFilterState();
    renderResearchStockPicker();
}

function renderResearchStockSignalFilterState() {
    document.querySelectorAll('#researchStockSignalFilters .signal-filter-chip').forEach(btn => {
        const signal = btn.dataset.signal || '';
        btn.classList.toggle('active', signal ? researchStockSignalFilters.has(signal) : !researchStockSignalFilters.size);
    });
}

function toggleResearchStockSignalFilter(signal) {
    if (!signal) researchStockSignalFilters.clear();
    else if (researchStockSignalFilters.has(signal)) researchStockSignalFilters.delete(signal);
    else researchStockSignalFilters.add(signal);
    renderResearchStockSignalFilterState();
    renderResearchStockPicker();
}
```

- [ ] **Step 8: Create batch jobs from selected stocks**

Add:

```js
function selectedResearchStockCodeList() {
    return [...selectedResearchStockCodes]
        .filter(code => window.StockMarketPermissions?.isAllowed?.(code) ?? true);
}

function researchStockTaskPayload(type) {
    const codes = selectedResearchStockCodeList();
    const depth = document.getElementById('researchStockDepthInput')?.value || 'standard';
    const modelMode = document.getElementById('researchStockModelModeInput')?.value || 'balanced';
    const forceReanalysis = !!document.getElementById('researchStockForceReanalysis')?.checked;
    const modelTier = modelMode === 'economy' ? 'quick' : (depth === 'quick' ? 'quick' : 'deep');
    return {
        job_type: type,
        codes,
        allow_all: false,
        skip_recent_days: type === 'data_prefetch' || forceReanalysis ? 0 : 30,
        snapshot_concurrency: 3,
        analysis_mode: type === 'report_generation' ? 'snapshot-tradingagents' : 'snapshot',
        analysis_concurrency: 1,
        analysis_depth: depth,
        model_mode: modelMode,
        snapshot_model_tier: modelTier,
        plan_top_n: 10,
        multi_role: type === 'position_plan',
        source_page: 'research_center_stock_picker',
        source_label: 'AI投研中心股票选择',
        resilience_mode: 'robust',
        quota_pause_scope: 'item',
        failure_retry_mode: 'auto_switch_model',
        max_auto_item_retries: 2,
        auto_retry_delay_seconds: 60,
        max_auto_retry_delay_seconds: 900,
        max_runtime_cooldown_seconds: 300,
        max_consecutive_failures: 20,
        max_failure_rate: 0.6,
        min_failure_rate_items: 20,
        guard_window_items: 20
    };
}

function openResearchStockTaskModal(defaultType = 'report_generation') {
    const codes = selectedResearchStockCodeList();
    if (!codes.length) return alert('请先在股票选择中勾选至少一只可交易股票');
    const typeInput = document.getElementById('researchStockTaskTypeInput');
    const summary = document.getElementById('researchStockTaskSummary');
    if (typeInput) typeInput.value = defaultType;
    if (summary) summary.textContent = `已选 ${codes.length} 只：${codes.slice(0, 12).join('、')}${codes.length > 12 ? '...' : ''}`;
    document.getElementById('researchStockTaskModal')?.classList.add('show');
}

function closeResearchStockTaskModal() {
    document.getElementById('researchStockTaskModal')?.classList.remove('show');
}

async function createBatchJobFromResearchStocks(type) {
    const payload = researchStockTaskPayload(type);
    if (!payload.codes.length) return alert('请先在股票选择中勾选至少一只可交易股票');
    const preflight = await requestJson('/api/batch-research/preflight', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    const estimate = [
        `股票数：${preflight.stock_count || payload.codes.length}`,
        `预计模型调用：${preflight.estimated_role_calls || '--'} 次`,
        `Worker：${preflight.worker_count || '--'} 个`,
        `预计耗时：${preflight.estimated_duration_text || '--'}`,
        ...(preflight.recommendations || [])
    ].join('\n');
    const warnings = preflight.warnings || [];
    if (!confirm(`${estimate}${warnings.length ? `\n\n风险提示：\n${warnings.join('\n')}` : ''}\n\n创建任务吗？`)) return;
    const resp = await requestJson('/api/batch-research/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });
    closeResearchStockTaskModal();
    showReportToast(`任务已创建：${resp.job_id}`, 'success');
    jobsLoaded = false;
    switchReportTab('jobs');
    await loadBatchJobs();
}

function submitResearchStockTaskModal() {
    const type = document.getElementById('researchStockTaskTypeInput')?.value || 'report_generation';
    return createBatchJobFromResearchStocks(type);
}
```

- [ ] **Step 9: Convert selection handoff into stock-picker import**

In `loadSelectionIntake`, after `selectedSelectionCodes` is loaded, add:

```js
selectedSelectionCodes.forEach(code => selectedResearchStockCodes.add(String(code)));
switchResearchSidebarTab('stocks');
renderResearchStockPicker();
```

Keep the banner because it explains where the selection came from. Its action buttons should call the stock-task modal:

```js
function openSelectionTaskModal(defaultType = 'report_generation') {
    selectedSelectionCodes.forEach(code => selectedResearchStockCodes.add(String(code)));
    switchResearchSidebarTab('stocks');
    renderResearchStockPicker();
    openResearchStockTaskModal(defaultType);
}
```

- [ ] **Step 10: Expose functions to templates**

At the bottom of `reports.js`, add:

```js
window.switchResearchSidebarTab = switchResearchSidebarTab;
window.filterResearchStockPicker = filterResearchStockPicker;
window.setResearchStockMarketFilter = setResearchStockMarketFilter;
window.toggleResearchStockSignalFilter = toggleResearchStockSignalFilter;
window.toggleResearchStockSelection = toggleResearchStockSelection;
window.selectVisibleResearchStocks = selectVisibleResearchStocks;
window.clearResearchStockSelection = clearResearchStockSelection;
window.openResearchStockTaskModal = openResearchStockTaskModal;
window.closeResearchStockTaskModal = closeResearchStockTaskModal;
window.submitResearchStockTaskModal = submitResearchStockTaskModal;
window.createBatchJobFromResearchStocks = createBatchJobFromResearchStocks;
```

- [ ] **Step 11: Run research-center JS tests**

Run:

```bash
.venv312/bin/python -m unittest \
  tests.test_release_migration.ReleaseMigrationTests.test_research_center_sidebar_has_stock_picker_and_report_filter_modes \
  tests.test_release_migration.ReleaseMigrationTests.test_research_center_stock_picker_creates_batch_jobs_directly \
  -v
```

Expected: PASS.

---

### Task 5: Update Compatibility Tests And Remove Old Handoff As Primary UX

**Files:**
- Modify: `tests/test_release_migration.py`
- Modify: `static/js/reports.js`
- Modify: `templates/reports.html`

- [ ] **Step 1: Replace the old handoff-primary test**

Remove `test_smart_watch_hands_selected_codes_to_research_center`.

Keep `test_research_center_accepts_selection_sets_for_tasks`, but change its assertions so handoff is compatibility-only:

```python
def test_research_center_imports_selection_sets_into_stock_picker(self):
    js = REPORTS_JS.read_text(encoding="utf-8")
    html = REPORTS_TEMPLATE.read_text(encoding="utf-8")

    self.assertIn("selectionIntakeBanner", html)
    self.assertIn("loadSelectionIntake", js)
    self.assertIn("/api/report-selections/${encodeURIComponent(selectionId)}", js)
    self.assertIn("selectedSelectionCodes.forEach", js)
    self.assertIn("selectedResearchStockCodes.add", js)
    self.assertIn("switchResearchSidebarTab('stocks')", js)
    self.assertIn("openResearchStockTaskModal(defaultType)", js)
```

- [ ] **Step 2: Keep the `report_selection_sets` service tests unchanged**

Do not delete `tests/test_report_selection_service.py`. The API remains useful for external deep links and backward-compatible smart-watch handoff URLs.

- [ ] **Step 3: Run the focused compatibility tests**

Run:

```bash
.venv312/bin/python -m unittest \
  tests.test_report_selection_service \
  tests.test_release_migration.ReleaseMigrationTests.test_research_center_imports_selection_sets_into_stock_picker \
  -v
```

Expected: PASS.

---

### Task 6: Browser Verification

**Files:**
- No code changes unless a browser issue is found.

- [ ] **Step 1: Restart local app**

Run:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
```

If the process is this project’s uvicorn, restart:

```bash
pkill -f "uvicorn app:app --host 127.0.0.1 --port 8000" || true
nohup .venv312/bin/uvicorn app:app --host 127.0.0.1 --port 8000 > /tmp/stock-workbench-8000.log 2>&1 &
```

Expected: `http://127.0.0.1:8000` serves the updated templates.

- [ ] **Step 2: Verify `/ai`**

Open:

```text
http://127.0.0.1:8000/ai?smart_watch_decouple=1
```

Expected:
- Header contains `智能盯盘`.
- Left stock cards have no checkboxes.
- There is no bottom `已选 N 只` batch bar.
- Single-stock click and `开始分析` remain visible.

- [ ] **Step 3: Verify `/reports` sidebar modes**

Open:

```text
http://127.0.0.1:8000/reports?sidebar=stocks&stock_picker_verify=1
```

Expected:
- Left sidebar shows `股票选择 / 报告筛选`.
- `股票选择` shows stock cards.
- Market and last-report-signal filters update visible cards.
- `全选当前` updates selected count.
- `生成所选报告` opens the task modal.

- [ ] **Step 4: Verify report filtering still works**

Switch sidebar to `报告筛选`.

Expected:
- Existing report search, signal filters, group-by, selected report count, Markdown/JSON export still work.

- [ ] **Step 5: Verify selection-set compatibility**

Create a temporary selection set:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/report-selections \
  -H 'Content-Type: application/json' \
  --data '{"source_page":"smart_watch","source_label":"智能盯盘兼容验证","codes":["600000","000001"],"filters":{"target_tab":"jobs","last_report_signals":["BUY"]},"ttl_hours":1}'
```

Open:

```text
http://127.0.0.1:8000/reports?tab=jobs&selection_id=<selection_id>
```

Expected:
- Banner shows source label and selected stock count.
- Sidebar switches to `股票选择`.
- The two codes are selected.
- Creating a task from the modal uses those codes.

Delete the temporary selection:

```bash
curl -sS -X DELETE http://127.0.0.1:8000/api/report-selections/<selection_id>
```

---

### Task 7: Final Regression

**Files:**
- No code changes unless a test fails.

- [ ] **Step 1: Run focused tests**

Run:

```bash
.venv312/bin/python -m unittest tests.test_report_selection_service tests.test_release_migration -v
```

Expected: all tests pass.

- [ ] **Step 2: Run syntax checks**

Run:

```bash
.venv312/bin/python -m py_compile api/report_selection_api.py api/batch_report_api.py services/report_selection_service.py models/database.py app.py
node --check static/js/ai.js
node --check static/js/reports.js
node --check static/js/portfolio.js
node --check static/js/shadow.js
git diff --check
```

Expected: every command exits `0`.

- [ ] **Step 3: Summarize without committing**

Report:
- What changed in `/ai`.
- What changed in `/reports`.
- Whether selection-set compatibility remains.
- Test commands and results.
- Whether database migration is required. Expected answer: no new migration beyond the existing `report_selection_sets` already added in the previous step.

---

## Self-Review

- Spec coverage: The plan removes bulk research from `智能盯盘`, adds `股票选择 / 报告筛选` modes to `AI投研中心`, creates batch tasks from selected stocks, and keeps report filtering intact.
- Placeholder scan: No placeholder tasks are present; every code-facing step includes concrete selectors, function names, and commands.
- Type consistency: The selected stock set is consistently named `selectedResearchStockCodes`; task creation uses `researchStockTaskPayload`, `openResearchStockTaskModal`, and `createBatchJobFromResearchStocks`.
