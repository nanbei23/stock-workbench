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
python -m compileall app.py api models repositories scheduler services schemas tests
python -m unittest discover tests
node --check static/js/hermes.js
node --check static/js/shadow.js
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
- [ ] `/ai` loads the AI task center.
- [ ] `/hotspots` loads hotspot themes and research progress.
- [ ] `/hermes` loads the Hermes console, history, draft panel, and audit panel.
- [ ] `/shadow` loads AI performance, signal validation, shadow portfolio, execution deviation, and model calibration.
- [ ] `/ops` loads operations center diagnostics.
- [ ] `/settings` loads model-provider, migration, and backup settings.

Check these APIs:

- [ ] `GET /api/settings`
- [ ] `GET /api/settings/backup/status`
- [ ] `GET /api/hermes/sessions?limit=5`
- [ ] `GET /api/model-providers`
- [ ] `GET /api/performance/overview?window=30`
- [ ] `GET /api/shadow/execution-deviation`
- [ ] `GET /api/market-regime`
- [ ] `GET /api/hotspots?limit=12`

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
git commit -m "Release v2.7.0"
git tag v2.7.0
```
