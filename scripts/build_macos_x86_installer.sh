#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_VERSION="$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' "$ROOT_DIR/app_metadata.py" | head -n 1)"
VERSION="${VERSION:-${DEFAULT_VERSION:-2.9.1}}"
DIST_DIR="$ROOT_DIR/dist"
PACKAGE_DIR="$DIST_DIR/stock-workbench-v$VERSION-macos-x86"
ARCHIVE="$DIST_DIR/stock-workbench-v$VERSION-macos-x86.tar.gz"

log() {
  printf '[installer] %s\n' "$*"
}

write_command() {
  local target="$1"
  local body="$2"
  cat > "$target" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
cd "\$(dirname "\$0")/stock-workbench-local"
$body
EOF
  chmod +x "$target"
}

main() {
  cd "$ROOT_DIR"
  rm -rf "$PACKAGE_DIR" "$ARCHIVE"
  mkdir -p "$PACKAGE_DIR"

  log "Copying application files"
  rsync -a \
    --exclude '.git' \
    --include '.env.example' \
    --exclude '.env' \
    --exclude '.env.*' \
    --exclude '.venv' \
    --exclude '.venv312' \
    --exclude 'node_modules' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'data/*.db' \
    --exclude 'data/backups' \
    --exclude 'data/batch_research' \
    --exclude 'logs' \
    --exclude 'cache' \
    --exclude 'dist' \
    "$ROOT_DIR/" "$PACKAGE_DIR/stock-workbench-local/"

  cp "$ROOT_DIR/installer/macos_x86/index.html" "$PACKAGE_DIR/安装向导.html"
  write_command "$PACKAGE_DIR/安装.command" './scripts/deploy_macos_x86.sh'
  write_command "$PACKAGE_DIR/升级.command" './scripts/update_macos_x86.sh'

  cat > "$PACKAGE_DIR/README.txt" <<EOF
炒股小牛马 v$VERSION macOS x86 安装包

1. 先打开「安装向导.html」阅读步骤。
2. 双击「安装.command」开始安装。
3. 安装完成后打开 http://127.0.0.1:8000
4. 后续升级可双击「升级.command」。
5. 数据初始化和批量研究脚本见 docs/data_initialization_and_batch_research.md。
EOF

  log "Creating archive $ARCHIVE"
  (cd "$DIST_DIR" && tar -czf "$(basename "$ARCHIVE")" "$(basename "$PACKAGE_DIR")")
  log "Done: $ARCHIVE"
}

main "$@"
