# Release Checklist

Use this checklist before tagging a local release.

## 1. Clean Workspace

- [ ] Confirm intentional changed files with `git status --short`.
- [ ] Keep runtime data out of the release: `data/*.db`, `data/backups/`, `.env`, `node_modules/`, `.venv*/`.
- [ ] Confirm no API keys or provider credentials appear in tracked files.

## 2. Install From Scratch

```bash
python3.12 -m venv .venv312
. .venv312/bin/activate
pip install -r requirements.txt
npm ci
```

## 3. Static Checks

```bash
python -m compileall app.py api models repositories scheduler services schemas scripts tests
python -m unittest discover tests
python -m py_compile scripts/init_from_files.py scripts/batch_research.py scripts/clear_report_data.py scripts/migrate_to_3_0.py
node --check static/js/hermes.js
node --check static/js/shadow.js
node --check static/js/ai-task-client.js static/js/ai.js static/js/reports.js
npm run typecheck
npm run build
```

## 4. Local Smoke Test

```bash
python app.py
```

Open these pages on `http://127.0.0.1:8000`:

- [ ] `/` loads the watchlist workspace.
- [ ] `/portfolio` loads accounts and positions.
- [ ] `/ai` loads the smart-watch workspace, single-stock analysis controls, recent report signals, and account action context.
- [ ] `/reports` loads the AI research center, stock picker, report filters, report preview, export actions, worker status, retry/resume controls, and position-plan action.
- [ ] `/hotspots` loads hotspot themes and research progress.
- [ ] `/hermes` loads the Hermes console, history, draft panel, and audit panel.
- [ ] `/shadow` loads AI performance, signal validation, shadow portfolio, execution deviation, and model calibration.
- [ ] `/ops` loads operations center diagnostics.
- [ ] `/settings` loads model-provider, migration, and backup settings.
- [ ] `/self-evolution` loads memory snapshots, rules, attributions, and semantic search.

Check these APIs:

- [ ] `GET /api/settings`
- [ ] `GET /api/settings/backup/status`
- [ ] `GET /api/hermes/sessions?limit=5`
- [ ] `GET /api/model-providers`
- [ ] `GET /api/performance/overview?window=30`
- [ ] `GET /api/shadow/execution-deviation`
- [ ] `GET /api/market-regime`
- [ ] `GET /api/hotspots?limit=12`
- [ ] `GET /api/ai/reports?limit=500`
- [ ] `GET /api/batch-research/jobs?limit=5`
- [ ] `GET /api/batch-reports?limit=5`

Check release scripts:

- [ ] `.venv312/bin/python scripts/init_from_files.py --help`
- [ ] `.venv312/bin/python scripts/batch_research.py --help`
- [ ] `.venv312/bin/python scripts/clear_report_data.py --help`
- [ ] `.venv312/bin/python scripts/migrate_to_3_0.py --help`
- [ ] `.venv312/bin/python scripts/migrate_to_3_0.py --db-path <copy-of-prod.db>` creates a backup and verifies 3.0 schema without data loss.
- [ ] `.venv312/bin/python scripts/init_from_files.py --watchlist <file> --trades <file> --cash <cash> --reset` dry-run does not write the database.
- [ ] `.venv312/bin/python scripts/clear_report_data.py` dry-run reports counts without deleting rows.
- [ ] `.venv312/bin/python scripts/batch_research.py --group 默认 --top-n 5` dry-run does not submit AI tasks.
- [ ] `.venv312/bin/python scripts/batch_research.py --group all --top-n 0 --data-only --apply` writes validated seven-layer snapshots before AI submission.
- [ ] `.venv312/bin/python scripts/batch_research.py --group 默认 --top-n 5 --analysis-mode snapshot --apply` reuses complete snapshots and writes reports without TradingAgents online data calls.
- [ ] Restarting the service with a running batch research job marks it as interrupted rather than leaving it permanently running.
- [ ] Retrying a failed batch research job resets only failed or waiting items.

## 5. Hermes Safety Checks

- [ ] Query-only prompt returns an answer without a write draft.
- [ ] Single write prompt generates a draft and writes only after confirmation.
- [ ] Multi-step prompt shows read preview plus per-step write confirmations.
- [ ] Skip step does not write business tables.
- [ ] Cancelled drafts cannot be confirmed later.
- [ ] Audit records appear in the Hermes write audit panel.

## 6. Tag

```bash
git add .
git commit -m "Release v3.0.0"
git tag v3.0.0
```
