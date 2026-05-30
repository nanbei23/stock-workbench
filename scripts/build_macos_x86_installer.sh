#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="${VERSION:-2.4.0}"
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
    --exclude '.venv' \
    --exclude '.venv312' \
    --exclude 'node_modules' \
    --exclude 'data/*.db' \
    --exclude 'data/backups' \
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
EOF

  log "Creating archive $ARCHIVE"
  (cd "$DIST_DIR" && tar -czf "$(basename "$ARCHIVE")" "$(basename "$PACKAGE_DIR")")
  log "Done: $ARCHIVE"
}

main "$@"
