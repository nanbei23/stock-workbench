# Changelog

## Unreleased

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
