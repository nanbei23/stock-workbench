# Changelog

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
