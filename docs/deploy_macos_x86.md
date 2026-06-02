# Intel macOS 自动部署

目标平台：

- Intel x86_64 Mac
- macOS 12.7.6 Monterey, Build 21H1320
- Python 3.12
- Node.js 20+

## 一键部署

在项目根目录执行：

```bash
chmod +x scripts/deploy_macos_x86.sh scripts/run_macos_x86.sh
scripts/deploy_macos_x86.sh
```

脚本会完成：

1. 校验 macOS 和 Intel x86_64 架构。
2. 创建 `.venv312`。
3. 安装 Python 依赖。
4. 安装 Node 依赖。
5. 执行编译、测试、TypeScript 检查和 Vite 构建。
6. 初始化 SQLite 数据库和迁移。
7. 安装当前用户的 launchd 服务。
8. 做 `http://127.0.0.1:8000/` smoke test。

部署完成后打开：

```text
http://127.0.0.1:8000
```

## 页面式安装包

v2.4 起可以生成一个带步骤页面的安装包：

```bash
scripts/build_macos_x86_installer.sh
```

输出位置：

```text
dist/stock-workbench-v2.8.0-macos-x86.tar.gz
```

解压后先打开 `安装向导.html`，再双击 `安装.command`。安装包内还包含 `升级.command`，用于后续复用现有升级流程。

安装包也包含自用初始化和批量研究脚本：

```text
scripts/init_from_files.py
scripts/batch_research.py
docs/data_initialization_and_batch_research.md
```

`batch_research.py` 支持先并发拉取七层数据写入 `stock_data_snapshots`，再复用已入库快照生成 AI 报告，最后基于已入库报告生成 `position_plan_*.md` 建仓建议文件。原生 TradingAgents 在线链路保留为 `--analysis-mode tradingagents`，需要网络稳定或代理支持。

`v2.8.0` 起页面内批量报告会创建后台任务并写入 `batch_report_jobs / batch_report_items`，长任务在后台执行，不阻塞页面操作。

## 常用参数

```bash
# 只安装依赖并构建，不安装 launchd 服务
scripts/deploy_macos_x86.sh --no-service

# 指定端口
scripts/deploy_macos_x86.sh --port 8010

# 指定 Python
scripts/deploy_macos_x86.sh --python /usr/local/bin/python3.12

# 跳过全量单测
scripts/deploy_macos_x86.sh --skip-tests

# 卸载 launchd 服务
scripts/deploy_macos_x86.sh --uninstall-service
```

## 一键更新

已安装过服务后，可以执行：

```bash
scripts/update_macos_x86.sh
```

更新脚本会先尝试创建数据备份，然后 `git pull --ff-only`，再复用部署脚本安装依赖、构建、迁移并重启 launchd 服务。

## launchd 服务

服务标识：

```text
com.nanbei23.stock-workbench
```

plist 路径：

```text
~/Library/LaunchAgents/com.nanbei23.stock-workbench.plist
```

日志路径：

```text
~/Library/Logs/stock-workbench-local/service.out.log
~/Library/Logs/stock-workbench-local/service.err.log
```

手动查看状态：

```bash
launchctl print gui/$(id -u)/com.nanbei23.stock-workbench
```

手动停止：

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.nanbei23.stock-workbench.plist
```

手动启动：

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nanbei23.stock-workbench.plist
launchctl kickstart -k gui/$(id -u)/com.nanbei23.stock-workbench
```

## 环境变量

脚本会在没有 `.env` 时从 `.env.example` 创建一份。`scripts/run_macos_x86.sh` 会在启动前加载 `.env`。

常用配置：

```bash
HOST=127.0.0.1
PORT=8000
WORKBENCH_DB_PATH=data/workbench.db
DEEPSEEK_API_KEY=
```

AI 模型供应商也可以在页面 `设置 -> AI 引擎` 中配置。

## 端口冲突

如果 `8000` 已被占用，脚本会停止并提示。可以先停止旧服务，或改用其他端口：

```bash
scripts/deploy_macos_x86.sh --port 8010
```
