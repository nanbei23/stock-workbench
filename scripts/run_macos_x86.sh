#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv312}"

cd "$ROOT_DIR"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

export HOST="${HOST:-127.0.0.1}"
export PORT="${PORT:-8000}"
export WORKBENCH_DB_PATH="${WORKBENCH_DB_PATH:-$ROOT_DIR/data/workbench.db}"

exec "$VENV_DIR/bin/python" "$ROOT_DIR/app.py"
