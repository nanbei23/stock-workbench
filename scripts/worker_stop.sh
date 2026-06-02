#!/usr/bin/env bash
set -euo pipefail

# stock-workbench-local batch worker stop helper.
LABEL="com.stockworkbench.batch-worker"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
echo "stopped ${LABEL}"
