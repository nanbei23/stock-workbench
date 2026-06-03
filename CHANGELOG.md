# Changelog

## Unreleased

No unreleased changes.

## 2.9.5 - 2026-06-03

### Added

- Added a full AI report detail page at `/reports/{report_id}` with final decision, trade plan, debates, seven-layer details, fact-check, bystander verification, and raw-state audit sections.
- Added report-library links from each report row and preview panel to the full report detail page.
- Added regression coverage for report-detail routing, default date-grouped report lists, Sina financial report parsing, and semantic snapshot validation.

### Changed

- Changed the report library default grouping to collapsed date groups for large report sets.
- Changed seven-layer batch prefetch to parse Sina's current `report_list` financial payload for balance-sheet and cash-flow data instead of relying on TradingAgents' stale A-share parser.
- Changed saved snapshot loading to recompute validation dynamically so old semantic failures are no longer treated as complete snapshots.
- Updated release metadata, package metadata, frontend cache versions, and README version to `2.9.5`.

### Fixed

- Fixed 600699-like cases where Sina had balance-sheet and cash-flow data but the old parser returned `No ... data found`.
- Fixed snapshot validation so tool outputs such as `No balance sheet data found` and `Error retrieving ...` are treated as data errors rather than successful layers.
- Fixed report-detail error states so missing reports no longer leave lower sections stuck on loading text.

### Deployment

- No database migration is required from `2.9.4`.
- Existing malformed seven-layer snapshots and reports are not rewritten automatically; rerun data prefetch and report generation for affected stocks to pick up the corrected financial statements.

## 2.8.1 - 2026-06-02

### Added

- Added portfolio-level multi-role position-plan generation from selected full AI reports.
- Added selected `report_ids` support to `/api/batch-research/jobs` so the report library can pass exact checked reports as context.
- Added `scripts/clear_report_data.py` to clear generated AI reports, AI tasks, signal tracking, and batch job records while preserving seven-layer snapshots and business data.
- Added regression coverage for forwarding selected report IDs and generating multi-role position plans from full report content.

### Changed

- Changed the report-library position-plan action to require checked reports and create a multi-role discussion job.
- Changed the AI page position-plan action to use the multi-role portfolio discussion path.
- Updated release metadata, package metadata, installer badge, and frontend cache versions to `2.8.1`.

## 2.8.0 - 2026-06-02

### Added

- Added persistent batch research jobs for data prefetch, report generation, and position-plan generation.
- Added `/api/batch-research` API surfaces for non-blocking job creation, progress polling, resume, and failed-item retry.
- Added the `/reports` AI report library with report filtering, preview, selected-report position-plan generation, and Markdown/JSON export.
- Added regression coverage for snapshot prefetch skipping, report-generation resume, failed-item retry, interruption recovery, legacy batch-report API compatibility, and report-list metadata.

### Changed

- Changed AI batch analysis from direct queue submission to background research jobs, so long 125-stock batches do not block the page.
- Changed `scripts/batch_research.py` full-analysis default to `--analysis-mode snapshot`, reusing validated `stock_data_snapshots` to generate and persist AI reports without re-entering TradingAgents' Eastmoney-dependent online data path.
- Added explicit `--analysis-mode tradingagents`, `--analysis-concurrency`, `--snapshot-model-tier`, and `--refresh-snapshots` controls for batch research.
- Changed the AI page report area to a compact "recent reports" panel and moved large-batch report consumption into the dedicated report library.
- Updated release metadata, package metadata, installer badge, frontend cache versions, release docs, and macOS x86 package default version to `2.8.0`.

### Fixed

- Recovered stale running batch research jobs on service restart by marking interrupted jobs and items explicitly.
- Kept `/api/batch-reports` compatibility while routing new batches through the v2.8 snapshot-first research pipeline.

## 2.7.3 - 2026-06-02

### Added

- Added `scripts/init_from_files.py` for one-shot local database initialization from Markdown watchlist and trade-history files.
- Added `scripts/batch_research.py` for offline, throttled batch research with dry-run, data-only, top-N, batching, and recent-report skipping.
- Added `stock_data_snapshots` for seven-layer prefetch snapshots with validation records before batch AI analysis.
- Added batch position-plan output generated from stored AI reports after full-chain research.
- Added `docs/data_initialization_and_batch_research.md` with the full initialization and batch research execution sequence.
- Added regression tests for file-based initialization, brokerage fee calculation, observation-pool import, and batch research dry-run/data-only safety.

### Changed

- Changed batch research defaults toward full-chain research: `--top-n 0` covers all selected stocks and `--depth standard` runs the complete analysis path.
- Updated release metadata, package metadata, installer badge, release docs, and macOS x86 package default version to `2.7.3`.
- Extended macOS x86 deployment checks to compile `scripts` and validate the new initialization and batch research scripts.

### Fixed

- Ensured the initialization flow ignores the incorrect reported initial capital in trade-history Markdown and instead reports an inferred initial capital from current cash and historical trade cash flows.

## 2.7.2 - 2026-06-02

### Added

- Added database initialization controls to the Intel macOS installer page for Markdown watchlist import, cash balance, cash notes, and initial holdings.
- Added Markdown table parsing for watchlist files using the `| # | 股票名称 | 代码 |` format.
- Added installer page and onboarding frontend regression tests.

### Changed

- Updated release metadata, frontend cache versions, package metadata, docs, installer guide, and macOS x86 package default version to `2.7.2`.
- Strengthened macOS x86 deployment checks to validate the global app script and installer page inline script.

### Fixed

- Fixed installer-page initialization so `file://` installer pages can call local write APIs for watchlist, cash balance, and initial trades.
- Fixed onboarding modal usability by making the desktop dialog scrollable and adding a pasted Markdown fallback.

## 2.7.1 - 2026-06-02

### Added

- Added onboarding asset initialization for cash balance and initial holdings.
- Added Markdown watchlist import in the onboarding wizard, supporting `name + code` lines and duplicate skipping.

### Changed

- Updated release metadata, frontend cache versions, package metadata, docs, installer guide, and macOS x86 package default version to `2.7.1`.
- Updated macOS x86 deploy and update scripts to print the app version from `app_metadata.py`.

### Fixed

- Fixed cash balance initialization on legacy settings tables without `updated_at`.
- Fixed portfolio trade entry and onboarding initial holdings to preserve the selected account.
- Fixed the left watchlist add form to use the correct `group_name` API contract and provide clearer feedback.

## 2.7.0 - 2026-05-31

### Added

- Added the Hotspot Themes page with market regime, research pulse, hotspot board, strategy lifecycle, and real-time research progress summaries.
- Added read-only cache-friendly APIs for market regime, hotspots, hotspot detail, research pulse, strategy lifecycle, and research progress.
- Added the global Hermes side drawer so the AI operation console is available from every top-level page.
- Added the `清爽渐变` theme inspired by the OpenAshare-style airy dashboard layout.
- Added the AI Shadow Portfolio and unified AI Performance workspace.
- Added AI performance overview API with signal validation, shadow portfolio, execution deviation, model/depth filters, and calibration data in one payload.
- Added confidence calibration with Brier Score, model/depth breakdowns, execution deviation buckets, and shadow portfolio simulation diagnostics.

### Changed

- Merged the previous signal performance page into `/shadow`, now labeled as `AI绩效`.
- Redirected `/performance` to `/shadow` to keep one performance workspace.
- Updated signal performance to support time-window, model-mode, and analysis-depth filters.
- Updated release metadata, frontend cache versions, package metadata, and Intel macOS installer default version to `2.7.0`.

### Fixed

- Fixed SELL, UNDERWEIGHT, and STRONG_SELL signal performance direction so price declines count as positive signal returns.
- Fixed sell-side stop-loss and target-hit direction handling for signal tracking.
- Fixed the macOS installer package builder to exclude local `.env` files.

## 2.6.0 - 2026-05-30

### Added

- Added the Operations Center page for data trust, global risk, portfolio analysis, AI quality, release operations, notifications, and diagnostics.
- Added operations dashboard, risk center, portfolio professional summary, and notification digest APIs.
- Added configurable global risk thresholds for concentration, cash buffer, daily loss, pending order amount, and quote freshness.

### Changed

- Updated release metadata, frontend cache versions, package metadata, and Intel macOS installer default version to `2.6.0`.

## 2.4.0 - 2026-05-30

### Added

- Added a page-style Intel macOS installer package generator with `安装向导.html`, `安装.command`, and `升级.command`.
- Added Hermes Agent v2 undo support for the most recent audited write operation in a session.
- Added a data audit API and Settings data-audit panel with counts, score, fixable items, and warnings.
- Added AI report review rollups by model mode and signal after-return.
- Added a global 15.6-inch portrait dense watch mode toggle.

### Changed

- AI report quality UI now surfaces best model mode and signal posterior performance.
- Hermes write-audit UI now exposes a guarded undo action.
- Updated release metadata to `2.4.0`.

## 2.3.0 - 2026-05-30

### Added

- Added first-start onboarding status API and global setup guide for account, cash, AI model, watchlist, and Hermes readiness.
- Expanded data-health checks with portfolio/trade consistency, invalid account references, cash-ledger gaps, and a health score.
- Added Hermes tool policy API with per-tool disable support and risk grading on operation drafts.
- Added Settings UI for Hermes write-tool permissions and richer data-health issue details.

### Changed

- Data-health repair now expires stale orders, normalizes invalid account references, and recalculates portfolio rows from trade records.
- Hermes drafts now surface visible low/medium/high risk badges in the confirmation panel.
- Updated release metadata to `2.3.0`.

## 2.2.0 - 2026-05-30

### Added

- Added Hermes task center API and UI panel for persisted multi-step task status, progress, and recovery.
- Added cash ledger persistence, cash-balance update API, and Portfolio cash-source display so asset data is traceable.
- Added Intel macOS update script with pre-update backup and service redeploy flow.

### Changed

- Improved Portfolio vertical-screen ergonomics with a compact mode and inline cash controls.
- Clarified the AI report action that converts traceable reports into conditional-order drafts.
- Updated release metadata to `2.2.0`.

## 2.1.0 - 2026-05-30

### Added

- Added Hermes as a dedicated AI operation console with session history, natural-language intent parsing, write drafts, confirmation, cancellation, and auditable tool runs.
- Added Hermes Agent v1 multi-step task plans with read-only previews, per-step confirmation, per-step skipping, persisted task timeline, and recovery from session history.
- Added controlled Hermes write tools for watchlist changes, trade records, position calibration, and conditional-order drafts.
- Added model-provider settings flow for custom OpenAI-compatible Base URL, API key, model fetching, and separate quick/deep/bystander model selection.
- Added migration and backup status surfaces for release operations.
- Added Vite + TypeScript build pipeline for gradually typed frontend contracts.

### Changed

- Refined the top navigation, Hermes page layout, vertical-screen ergonomics, and visual states without emoji-dependent controls.
- Improved AI task and report quality surfaces with queue state, retry/cancel affordances, fact-checking fields, bystander verification, and signal tracking hooks.
- Centralized Hermes database write guidance in `docs/hermes_db_write_manual.md` and inject it into the LLM context for safer tool planning.
- Updated release metadata to `2.1.0`.

### Fixed

- Prevented model-provider API keys from leaking in public settings responses.
- Fixed Hermes session aggregation so chatty sessions do not hide older sessions.
- Fixed multi-step Hermes execution state so confirming one step does not mark the whole plan as executed.
- Removed stale inline Hermes code from the AI page and deleted a temporary frontend cleanup script.

### Verification

- `python -m compileall app.py models/database.py api/hermes_api.py services/hermes_console_service.py services/hermes_tool_registry.py tests/test_hermes_console_service.py`
- `python -m unittest tests.test_hermes_console_service`
- `node --check static/js/hermes.js`
- `npm run typecheck`
- `npm run build`
- Local smoke checks for `/`, `/hermes`, `/settings`, `/api/hermes/sessions`, and model-provider endpoints.
