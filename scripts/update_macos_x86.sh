#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_ID="com.nanbei23.stock-workbench"

log() {
  printf '[update] %s\n' "$*"
}

cd "$ROOT_DIR"

log "Creating pre-update backup"
if [[ -x "$ROOT_DIR/.venv312/bin/python" ]]; then
  "$ROOT_DIR/.venv312/bin/python" - <<'PY'
from services import settings_service
try:
    backup = settings_service.create_backup_file()
    print(f"backup={backup.get('filename')}")
except Exception as exc:
    print(f"backup_failed={exc}")
PY
else
  log "Skip backup: .venv312 is not ready"
fi

log "Pulling latest code"
git pull --ff-only

log "Deploying updated app"
"$ROOT_DIR/scripts/deploy_macos_x86.sh" --install-service

log "Restarting launchd service"
launchctl kickstart -k "gui/$(id -u)/$SERVICE_ID" >/dev/null 2>&1 || true

log "Update complete"
