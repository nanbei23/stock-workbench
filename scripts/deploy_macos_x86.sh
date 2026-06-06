#!/usr/bin/env bash
set -Eeuo pipefail

APP_ID="com.nanbei23.stock-workbench"
APP_NAME="stock-workbench-local"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv312"
PYTHON_BIN="${PYTHON_BIN:-python3.12}"
HOST_VALUE="${HOST:-127.0.0.1}"
PORT_VALUE="${PORT:-8000}"
DB_PATH_VALUE="${WORKBENCH_DB_PATH:-$ROOT_DIR/data/workbench.db}"
INSTALL_SERVICE=1
RUN_TESTS=1
RUN_SMOKE=1
SKIP_PIP=0
SKIP_NPM=0
PLIST_PATH="$HOME/Library/LaunchAgents/$APP_ID.plist"
LOG_DIR="$HOME/Library/Logs/$APP_NAME"

usage() {
  cat <<EOF
Usage: scripts/deploy_macos_x86.sh [options]

Deploy Stock Workbench on Intel macOS Monterey.

Options:
  --no-service          Install dependencies and build only; do not install launchd service.
  --install-service     Install or update the current-user launchd service. Default.
  --host HOST           Host for the app service. Default: 127.0.0.1
  --port PORT           Port for the app service. Default: 8000
  --python PATH         Python interpreter for creating the venv. Default: python3.12
  --venv PATH           Virtualenv directory. Default: .venv312
  --skip-pip            Skip Python dependency installation.
  --skip-npm            Skip Node dependency installation.
  --skip-tests          Skip Python unittest discovery.
  --no-smoke            Skip HTTP smoke test after service start.
  --uninstall-service   Stop and remove the launchd service, then exit.
  -h, --help            Show this help.

Examples:
  scripts/deploy_macos_x86.sh
  scripts/deploy_macos_x86.sh --port 8010
  scripts/deploy_macos_x86.sh --no-service
  scripts/deploy_macos_x86.sh --uninstall-service
EOF
}

log() {
  printf '[deploy] %s\n' "$*"
}

app_version() {
  sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' "$ROOT_DIR/app_metadata.py" | head -n 1
}

die() {
  printf '[deploy] ERROR: %s\n' "$*" >&2
  exit 1
}

xml_escape() {
  sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g' <<<"$1"
}

run() {
  log "$*"
  "$@"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --no-service)
        INSTALL_SERVICE=0
        shift
        ;;
      --install-service)
        INSTALL_SERVICE=1
        shift
        ;;
      --host)
        HOST_VALUE="${2:?missing host}"
        shift 2
        ;;
      --port)
        PORT_VALUE="${2:?missing port}"
        shift 2
        ;;
      --python)
        PYTHON_BIN="${2:?missing python path}"
        shift 2
        ;;
      --venv)
        VENV_DIR="${2:?missing venv path}"
        shift 2
        ;;
      --skip-pip)
        SKIP_PIP=1
        shift
        ;;
      --skip-npm)
        SKIP_NPM=1
        shift
        ;;
      --skip-tests)
        RUN_TESTS=0
        shift
        ;;
      --no-smoke)
        RUN_SMOKE=0
        shift
        ;;
      --uninstall-service)
        uninstall_service
        exit 0
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "Unknown option: $1"
        ;;
    esac
  done
}

check_platform() {
  [[ "$(uname -s)" == "Darwin" ]] || die "This script only supports macOS."

  local arch
  arch="$(uname -m)"
  [[ "$arch" == "x86_64" ]] || die "This deployment target is Intel x86_64 macOS. Current arch: $arch"

  local product_version build_version major_version
  product_version="$(sw_vers -productVersion)"
  build_version="$(sw_vers -buildVersion)"
  major_version="${product_version%%.*}"

  if [[ "$major_version" != "12" ]]; then
    log "Warning: expected macOS 12.x Monterey, detected macOS $product_version build $build_version"
  else
    log "Detected Intel macOS $product_version build $build_version"
  fi
}

check_tools() {
  command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "Python not found: $PYTHON_BIN. Install Python 3.12 first, for example with Homebrew."
  command -v npm >/dev/null 2>&1 || die "npm not found. Install Node.js 20+ first."
  command -v curl >/dev/null 2>&1 || die "curl not found."
  command -v lsof >/dev/null 2>&1 || die "lsof not found."

  local python_version node_version
  python_version="$("$PYTHON_BIN" -c 'import platform; print(platform.python_version())')"
  node_version="$(node -v 2>/dev/null || true)"
  log "Using Python $python_version from $(command -v "$PYTHON_BIN")"
  log "Using Node $node_version from $(command -v node)"
}

prepare_env_file() {
  if [[ ! -f "$ROOT_DIR/.env" ]]; then
    log "Creating .env from .env.example"
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  fi
}

install_python_deps() {
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    run "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi
  if [[ "$SKIP_PIP" -eq 1 ]]; then
    log "Skipping pip install"
    return
  fi
  run "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
  run "$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements.txt"
}

install_node_deps() {
  if [[ "$SKIP_NPM" -eq 1 ]]; then
    log "Skipping npm install"
    return
  fi
  if [[ -f "$ROOT_DIR/package-lock.json" ]]; then
    run npm ci
  else
    run npm install
  fi
}

build_and_check() {
  run "$VENV_DIR/bin/python" -m compileall app.py api models repositories scheduler services schemas scripts tests
  if [[ "$RUN_TESTS" -eq 1 ]]; then
    run "$VENV_DIR/bin/python" -m unittest discover tests
  else
    log "Skipping Python tests"
  fi
  run node --check static/js/app.js
  run node --check static/js/hermes.js
  run node --check static/js/stock.js
  run "$VENV_DIR/bin/python" -m py_compile scripts/init_from_files.py scripts/batch_research.py scripts/migrate_to_3_0.py
  run node -e "const fs=require('fs'); const html=fs.readFileSync('installer/macos_x86/index.html','utf8'); const scripts=[...html.matchAll(/<script>([\\s\\S]*?)<\\/script>/g)].map(m=>m[1]).join('\\n'); new Function(scripts);"
  run npm run typecheck
  run npm run build
}

init_database() {
  run "$VENV_DIR/bin/python" "$ROOT_DIR/scripts/migrate_to_3_0.py" --db-path "$DB_PATH_VALUE"
}

install_batch_worker() {
  log "Installing batch worker launchd service"
  run "$ROOT_DIR/scripts/worker_install_launchd.sh"
  run "$ROOT_DIR/scripts/worker_start.sh"
}

uninstall_service() {
  log "Stopping launchd service if present"
  launchctl bootout "gui/$(id -u)" "$PLIST_PATH" >/dev/null 2>&1 || true
  launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
  if [[ -f "$PLIST_PATH" ]]; then
    rm -f "$PLIST_PATH"
    log "Removed $PLIST_PATH"
  fi
}

check_port_free() {
  if lsof -nP -iTCP:"$PORT_VALUE" -sTCP:LISTEN >/dev/null 2>&1; then
    lsof -nP -iTCP:"$PORT_VALUE" -sTCP:LISTEN >&2 || true
    die "Port $PORT_VALUE is already in use. Stop that process or deploy with --port <other-port>."
  fi
}

write_plist() {
  mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"

  local run_script root_dir venv_dir db_path log_out log_err host port
  run_script="$(xml_escape "$ROOT_DIR/scripts/run_macos_x86.sh")"
  root_dir="$(xml_escape "$ROOT_DIR")"
  venv_dir="$(xml_escape "$VENV_DIR")"
  db_path="$(xml_escape "$DB_PATH_VALUE")"
  log_out="$(xml_escape "$LOG_DIR/service.out.log")"
  log_err="$(xml_escape "$LOG_DIR/service.err.log")"
  host="$(xml_escape "$HOST_VALUE")"
  port="$(xml_escape "$PORT_VALUE")"

  cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$APP_ID</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$run_script</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$root_dir</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOST</key>
    <string>$host</string>
    <key>PORT</key>
    <string>$port</string>
    <key>WORKBENCH_DB_PATH</key>
    <string>$db_path</string>
    <key>VENV_DIR</key>
    <string>$venv_dir</string>
    <key>PATH</key>
    <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$log_out</string>
  <key>StandardErrorPath</key>
  <string>$log_err</string>
</dict>
</plist>
EOF

  plutil -lint "$PLIST_PATH" >/dev/null
}

install_service() {
  uninstall_service
  check_port_free
  write_plist
  log "Installing launchd service: $PLIST_PATH"
  launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
  launchctl kickstart -k "gui/$(id -u)/$APP_ID" >/dev/null 2>&1 || true
}

smoke_test() {
  [[ "$RUN_SMOKE" -eq 1 ]] || return 0
  local url="http://$HOST_VALUE:$PORT_VALUE/"
  log "Waiting for $url"
  for _ in {1..40}; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "Smoke test passed: $url"
      return 0
    fi
    sleep 1
  done
  log "Recent service log:"
  tail -n 80 "$LOG_DIR/service.err.log" 2>/dev/null || true
  die "Smoke test failed: $url"
}

main() {
  parse_args "$@"
  cd "$ROOT_DIR"
  log "Deploying $APP_NAME v$(app_version)"
  check_platform
  check_tools
  prepare_env_file
  install_python_deps
  install_node_deps
  build_and_check
  init_database
  chmod +x "$ROOT_DIR/scripts/run_macos_x86.sh" "$ROOT_DIR/scripts/init_from_files.py" "$ROOT_DIR/scripts/batch_research.py" "$ROOT_DIR/scripts/migrate_to_3_0.py" "$ROOT_DIR/scripts/migrate_2_8_1_to_2_9.py"
  chmod +x "$ROOT_DIR/scripts/worker_install_launchd.sh" "$ROOT_DIR/scripts/worker_start.sh" "$ROOT_DIR/scripts/worker_stop.sh" "$ROOT_DIR/scripts/worker_status.sh" "$ROOT_DIR/scripts/worker_logs.sh"

  if [[ "$INSTALL_SERVICE" -eq 1 ]]; then
    install_service
    install_batch_worker
    smoke_test
    log "Deployment complete. Open http://$HOST_VALUE:$PORT_VALUE"
    log "Logs: $LOG_DIR/service.out.log and $LOG_DIR/service.err.log"
  else
    log "Deployment build complete without service install."
    log "Run manually: HOST=$HOST_VALUE PORT=$PORT_VALUE scripts/run_macos_x86.sh"
  fi
}

main "$@"
