#!/usr/bin/env bash
set -euo pipefail

# stock-workbench-local batch worker launchd installer.
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.stockworkbench.batch-worker"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
PYTHON="${ROOT_DIR}/.venv312/bin/python"
LOG_DIR="${ROOT_DIR}/logs"

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>WorkingDirectory</key>
  <string>${ROOT_DIR}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON}</string>
    <string>${ROOT_DIR}/scripts/run_batch_worker.py</string>
    <string>--sleep</string>
    <string>5</string>
    <string>--stale-minutes</string>
    <string>15</string>
    <string>--worker-id</string>
    <string>launchd-batch-worker</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/batch-worker.out.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/batch-worker.err.log</string>
</dict>
</plist>
PLIST

echo "installed ${PLIST}"
echo "run: ${ROOT_DIR}/scripts/worker_start.sh"
