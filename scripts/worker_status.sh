#!/usr/bin/env bash
set -euo pipefail

# stock-workbench-local batch worker status helper.
LABEL="com.stockworkbench.batch-worker"

if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
  launchctl print "gui/$(id -u)/${LABEL}" | sed -n '1,80p'
else
  echo "${LABEL} is not loaded"
  exit 1
fi
