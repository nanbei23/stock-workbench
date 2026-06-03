import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SCRIPT = ROOT / "scripts" / "migrate_2_8_1_to_2_9.py"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_macos_x86.sh"
BUILD_INSTALLER_SCRIPT = ROOT / "scripts" / "build_macos_x86_installer.sh"
WORKER_INSTALL_SCRIPT = ROOT / "scripts" / "worker_install_launchd.sh"
AI_JS = ROOT / "static" / "js" / "ai.js"
AI_TEMPLATE = ROOT / "templates" / "ai.html"
BATCH_WORKER_SCRIPT = ROOT / "scripts" / "run_batch_worker.py"
WORKER_POOL_SCRIPT = ROOT / "scripts" / "run_batch_worker_pool.py"
SETTINGS_JS = ROOT / "static" / "js" / "settings.js"
SETTINGS_TEMPLATE = ROOT / "templates" / "settings.html"
STYLE_CSS = ROOT / "static" / "css" / "style.css"


def load_migration_module():
    spec = importlib.util.spec_from_file_location("migrate_2_8_1_to_2_9", MIGRATION_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ReleaseMigrationTests(unittest.TestCase):
    def test_deploy_plist_template_has_one_port_value(self):
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn("<string>$port</string>\n    <string>$port</string>", source)

    def test_deploy_script_runs_release_migration(self):
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("scripts/migrate_2_8_1_to_2_9.py", source)
        self.assertNotIn("from models.database import init_db; asyncio.run(init_db())", source)

    def test_deploy_script_installs_batch_worker_service(self):
        source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
        worker_source = WORKER_INSTALL_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("worker_install_launchd.sh", source)
        self.assertIn("worker_start.sh", source)
        self.assertIn("run_batch_worker_pool.py", worker_source)
        self.assertNotIn("run_batch_worker.py</string>", worker_source)

    def test_installer_package_excludes_runtime_data(self):
        source = BUILD_INSTALLER_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("--exclude 'data/batch_research'", source)
        self.assertIn("--exclude 'logs'", source)

    def test_ai_batch_research_requires_selected_codes(self):
        js = AI_JS.read_text(encoding="utf-8")
        html = AI_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("const rawCodes = getSelectedCodes();", js)
        self.assertIn("StockMarketPermissions?.isAllowed", js)
        self.assertIn("function getBatchResearchOptions()", js)
        self.assertIn("请先在左侧自选股列表勾选", js)
        self.assertIn("codes,", js)
        self.assertIn("allow_all: false", js)
        self.assertIn("analysis_depth: batchOptions.depth", js)
        self.assertIn("model_mode: batchOptions.modelMode", js)
        self.assertIn("snapshot_model_tier: batchOptions.modelTier", js)
        self.assertIn("forceReanalysis", js)
        self.assertIn("batchOptions.forceReanalysis ? 0 : 30", js)
        self.assertIn("preflightBatchResearch(payload)", js)
        self.assertIn("预计总调用", js)
        self.assertNotIn("group: 'all',\n            top_n: 0", js)
        self.assertNotIn("snapshot_model_tier: 'deep'", js)
        self.assertIn('id="batchDepthSelect"', html)
        self.assertIn('id="batchModelModeSelect"', html)
        self.assertIn('id="batchForceReanalysis"', html)
        self.assertIn("生成所选报告", html)

    def test_batch_worker_supports_model_provider_pool(self):
        content = BATCH_WORKER_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("--model-provider-ids", content)
        self.assertIn("--model-tier", content)
        self.assertIn("model_provider_ids=provider_ids", content)

    def test_analysis_report_timeout_defaults_to_one_hour(self):
        tasks_source = (ROOT / "tasks.py").read_text(encoding="utf-8")
        api_source = (ROOT / "api" / "batch_report_api.py").read_text(encoding="utf-8")
        service_source = (ROOT / "services" / "batch_report_service.py").read_text(encoding="utf-8")
        script_source = (ROOT / "scripts" / "batch_research.py").read_text(encoding="utf-8")

        self.assertIn("DEFAULT_ANALYSIS_TIMEOUT_SECONDS = 3600", tasks_source)
        self.assertIn('DEFAULT_ANALYSIS_TIMEOUT_LABEL = "1小时"', tasks_source)
        self.assertIn("timeout_seconds: int = Field(default=3600", api_source)
        self.assertIn('payload.get("timeout_seconds") or 3600', service_source)
        self.assertIn('parser.add_argument("--timeout-seconds", type=int, default=3600)', script_source)
        self.assertNotIn("分析超时（15分钟）", tasks_source)

    def test_worker_pool_has_config_script_and_settings_ui(self):
        script = WORKER_POOL_SCRIPT.read_text(encoding="utf-8")
        js = SETTINGS_JS.read_text(encoding="utf-8")
        html = SETTINGS_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("get_worker_pool_config", script)
        self.assertIn("subprocess.Popen", script)
        self.assertIn("asyncio.run(init_db())", script)
        self.assertIn("worker pool is idle", script)
        self.assertNotIn("init_db_sync", script)
        self.assertIn("/worker-pool/config", js)
        self.assertIn("saveWorkerPoolConfig", js)
        self.assertIn("workerPoolList", html)

    def test_reports_page_exposes_worker_panel_preflight_and_failure_retry(self):
        js = (ROOT / "static" / "js" / "reports.js").read_text(encoding="utf-8")
        html = (ROOT / "templates" / "reports.html").read_text(encoding="utf-8")

        self.assertIn("workerStatusPanel", html)
        self.assertIn("planWorkerGrid", html)
        self.assertIn("planPrimaryProviderGrid", html)
        self.assertIn("planFallbackProviderGrid", html)
        self.assertIn("previewFailureGroups", js)
        self.assertIn("previewRuntimeStats", js)
        self.assertIn("/failure-groups", js)
        self.assertIn("/runtime-stats", js)
        self.assertIn("allowed_worker_ids", js)
        self.assertIn("primary_provider_ids", js)
        self.assertIn("previewBatchAnalysis", js)
        self.assertIn("/analysis", js)
        self.assertIn("信号分布", js)

    def test_reports_page_supports_grouped_collapsible_report_list(self):
        js = (ROOT / "static" / "js" / "reports.js").read_text(encoding="utf-8")
        html = (ROOT / "templates" / "reports.html").read_text(encoding="utf-8")

        self.assertIn('id="reportGroupBy"', html)
        self.assertIn('<option value="date" selected>按日期折叠</option>', html)
        self.assertIn("renderGroupedReportRows", js)
        self.assertIn("collapsedReportGroups", js)
        self.assertIn("expandedReportGroups", js)
        self.assertIn("groupBy === 'date' ? !expandedReportGroups.has(groupToken)", js)
        self.assertIn("toggleReportGroup", js)
        self.assertIn("report-group-header", js)

    def test_reports_page_links_to_full_report_detail(self):
        js = (ROOT / "static" / "js" / "reports.js").read_text(encoding="utf-8")
        html = (ROOT / "templates" / "reports.html").read_text(encoding="utf-8")

        self.assertIn("<th>操作</th>", html)
        self.assertIn('href="/reports/${id}"', js)
        self.assertIn("打开完整详情页", js)

    def test_report_preview_renders_structured_json_sections(self):
        js = (ROOT / "static" / "js" / "reports.js").read_text(encoding="utf-8")
        css = STYLE_CSS.read_text(encoding="utf-8")

        self.assertIn("formatStructuredValue", js)
        self.assertIn("structured-report", js)
        self.assertIn("formatMarkdown(report.risk_debate || '暂无')", js)
        self.assertNotIn("formatMarkdown(typeof report.risk_debate === 'string' ? report.risk_debate : JSON.stringify", js)
        self.assertIn(".structured-report", css)
        self.assertIn(".structured-key", css)

    def test_ai_page_supports_last_report_signal_filter_and_batch_select(self):
        js = AI_JS.read_text(encoding="utf-8")
        html = AI_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("aiLastSignalFilters", html)
        self.assertIn("selectVisibleAIStocks", html)
        self.assertIn("selectAIStocksByLastSignals", html)
        self.assertIn("AI_SIGNAL_RANK", js)
        self.assertIn("last_report_signal", js)
        self.assertIn("selectedLastReportSignals", js)
        self.assertIn("renderAIStockCards", js)
        self.assertIn("selectAIStocksByLastSignals", js)

    def test_left_stock_panels_expose_unified_search(self):
        ai_js = AI_JS.read_text(encoding="utf-8")
        ai_html = AI_TEMPLATE.read_text(encoding="utf-8")
        stock_js = (ROOT / "static/js/stock.js").read_text(encoding="utf-8")
        stock_html = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        portfolio_js = (ROOT / "static/js/portfolio.js").read_text(encoding="utf-8")
        portfolio_html = (ROOT / "templates/portfolio.html").read_text(encoding="utf-8")
        css = STYLE_CSS.read_text(encoding="utf-8")

        self.assertIn("stockListFilter", stock_html)
        self.assertIn("filterWatchlistStocks", stock_js)
        self.assertIn("portfolioListFilter", portfolio_html)
        self.assertIn("filterPortfolioStocks", portfolio_js)
        self.assertIn("aiStockSearch", ai_html)
        self.assertIn("filterAIStocks", ai_js)
        self.assertIn(".stock-list-filter", css)

    def test_watchlist_selection_only_shows_in_batch_mode(self):
        stock_js = (ROOT / "static/js/stock.js").read_text(encoding="utf-8")
        stock_html = (ROOT / "templates/index.html").read_text(encoding="utf-8")
        css = STYLE_CSS.read_text(encoding="utf-8")

        self.assertIn('id="watchlistBatchToggle"', stock_html)
        self.assertIn("toggleWatchlistBatchMode()", stock_html)
        self.assertIn('id="watchlistBatchActions"', stock_html)
        self.assertIn('data-action="batch-delete"', stock_html)
        self.assertIn("let watchlistBatchMode = false", stock_js)
        self.assertIn("function toggleWatchlistBatchMode", stock_js)
        self.assertIn("classList.toggle('batch-active', watchlistBatchMode)", stock_js)
        self.assertIn("bar.style.display = watchlistBatchMode ? '' : 'none'", stock_js)
        self.assertIn(".stock-select-check {\n  flex: 0 0 32px;\n  display: none;", css)
        self.assertIn(".stock-list.batch-active .stock-select-check", css)

    def test_ai_right_sections_use_content_height_not_equal_flex_stack(self):
        css = STYLE_CSS.read_text(encoding="utf-8")

        self.assertIn(".ai-right-section {\n  flex: 0 0 auto;\n  min-height: auto;", css)
        self.assertNotIn(".ai-right-section {\n  flex: 1;\n  min-height: 0;", css)

    def test_market_permission_filters_are_exposed_on_ai_reports_and_settings(self):
        ai_js = AI_JS.read_text(encoding="utf-8")
        ai_html = AI_TEMPLATE.read_text(encoding="utf-8")
        reports_js = (ROOT / "static/js/reports.js").read_text(encoding="utf-8")
        reports_html = (ROOT / "templates/reports.html").read_text(encoding="utf-8")
        settings_html = SETTINGS_TEMPLATE.read_text(encoding="utf-8")
        base_html = (ROOT / "templates/base.html").read_text(encoding="utf-8")

        self.assertIn("market-permissions.js", base_html)
        self.assertIn("trade_market_star", settings_html)
        self.assertIn("trade_market_bse", settings_html)
        self.assertIn("aiMarketFilters", ai_html)
        self.assertIn("setAIMarketFilter", ai_js)
        self.assertIn("filterByTradingMarket", ai_js)
        self.assertIn("reportMarketFilters", reports_html)
        self.assertIn("setReportMarketFilter", reports_js)
        self.assertIn("filterByTradingMarket", reports_js)

    def test_position_plans_have_decision_market_snapshot_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workbench.db"
            with sqlite3.connect(db_path) as db:
                db.execute(
                    """
                    CREATE TABLE position_plans (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        plan_id TEXT UNIQUE NOT NULL,
                        title TEXT,
                        status TEXT NOT NULL DEFAULT 'active'
                    )
                    """
                )
                db.commit()

            import models.database as database
            import asyncio
            import aiosqlite

            async def migrate():
                async with aiosqlite.connect(str(db_path)) as adb:
                    await database.ensure_position_plan_market_context_columns(adb)
                    await adb.commit()

            asyncio.run(migrate())
            with sqlite3.connect(db_path) as db:
                columns = {row[1] for row in db.execute("PRAGMA table_info(position_plans)").fetchall()}

        self.assertIn("decision_market_snapshot_json", columns)
        self.assertIn("market_context_captured_at", columns)

    def test_migration_script_upgrades_2_8_1_database(self):
        module = load_migration_module()
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "workbench.db"
            with sqlite3.connect(db_path) as db:
                db.executescript(
                    """
                    CREATE TABLE schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    );
                    INSERT INTO schema_migrations(version, name) VALUES (1, 'baseline_schema_tracking');
                    CREATE TABLE batch_jobs (
                        job_id TEXT PRIMARY KEY,
                        job_type TEXT NOT NULL,
                        name TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        total_count INTEGER DEFAULT 0,
                        completed_count INTEGER DEFAULT 0,
                        failed_count INTEGER DEFAULT 0,
                        skipped_count INTEGER DEFAULT 0,
                        waiting_count INTEGER DEFAULT 0,
                        running_count INTEGER DEFAULT 0,
                        current_code TEXT,
                        payload_json TEXT DEFAULT '{}',
                        result_json TEXT DEFAULT '{}',
                        error TEXT,
                        created_at TEXT DEFAULT (datetime('now')),
                        started_at TEXT,
                        completed_at TEXT,
                        updated_at TEXT DEFAULT (datetime('now'))
                    );
                    CREATE TABLE batch_job_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_id TEXT NOT NULL,
                        code TEXT NOT NULL,
                        name TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        snapshot_id INTEGER,
                        report_id INTEGER,
                        task_id TEXT,
                        error TEXT,
                        retry_count INTEGER DEFAULT 0,
                        created_at TEXT DEFAULT (datetime('now')),
                        started_at TEXT,
                        completed_at TEXT,
                        updated_at TEXT DEFAULT (datetime('now'))
                    );
                    INSERT INTO batch_jobs(job_id, job_type, name, status)
                    VALUES ('old-running', 'report_generation', '旧运行任务', 'running');
                    """
                )
                db.commit()

            result = module.migrate_database(db_path, create_backup=True)

            self.assertTrue(Path(result["backup_path"]).exists())
            self.assertEqual(result["status"], "ok")
            with sqlite3.connect(db_path) as db:
                job_columns = {row[1] for row in db.execute("PRAGMA table_info(batch_jobs)")}
                item_columns = {row[1] for row in db.execute("PRAGMA table_info(batch_job_items)")}
                step_columns = {row[1] for row in db.execute("PRAGMA table_info(batch_job_item_steps)")}
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                status, error = db.execute("SELECT status, error FROM batch_jobs WHERE job_id='old-running'").fetchone()

            self.assertIn("lease_owner", job_columns)
            self.assertIn("runtime_json", job_columns)
            self.assertIn("error_type", item_columns)
            self.assertIn("model_config_json", step_columns)
            self.assertIn("position_plans", tables)
            self.assertEqual(status, "interrupted")
            self.assertIn("2.9", error)


if __name__ == "__main__":
    unittest.main()
