#!/usr/bin/env bash
set -euo pipefail

# stock-workbench-local batch worker log helper.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs"

mkdir -p "$LOG_DIR"
touch "${LOG_DIR}/batch-worker.out.log" "${LOG_DIR}/batch-worker.err.log"
tail -n 120 -f "${LOG_DIR}/batch-worker.out.log" "${LOG_DIR}/batch-worker.err.log"
