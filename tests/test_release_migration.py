import importlib.util
import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

import aiosqlite

from models import database


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
CHART_JS = ROOT / "static" / "js" / "chart.js"
INDEX_TEMPLATE = ROOT / "templates" / "index.html"
STOCK_JS = ROOT / "static" / "js" / "stock.js"
AURORA_FLOW_CSS = ROOT / "static" / "css" / "aurora-flow.css"
WIND_DASHBOARD_CSS = ROOT / "static" / "css" / "wind-dashboard.css"
PORTFOLIO_TEMPLATE = ROOT / "templates" / "portfolio.html"
PORTFOLIO_JS = ROOT / "static" / "js" / "portfolio.js"
REPORT_DETAIL_TEMPLATE = ROOT / "templates" / "report_detail.html"
REPORT_DETAIL_JS = ROOT / "static" / "js" / "report-detail.js"


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
        self.assertIn("function isPlanSelectableWorker", js)
        self.assertIn("new Set(['idle', 'online', 'running'])", js)
        self.assertIn("workers.filter(isPlanSelectableWorker)", js)
        self.assertIn("暂无在线 Worker", js)
        self.assertIn("离线或卡死 Worker 不会参与本次选择", js)

    def test_reports_page_separates_stale_workers_and_position_plan_decisions(self):
        js = (ROOT / "static" / "js" / "reports.js").read_text(encoding="utf-8")
        api_source = (ROOT / "api" / "position_plan_api.py").read_text(encoding="utf-8")
        service_source = (ROOT / "services" / "position_plan_service.py").read_text(encoding="utf-8")

        self.assertIn("const staleWorkerStates = new Set(['stale', 'offline', 'disabled', 'not_started'])", js)
        self.assertIn("const activeWorkers = workers.filter(worker => !staleWorkerStates.has(worker.state))", js)
        self.assertIn("const staleWorkers = workers.filter(worker => staleWorkerStates.has(worker.state))", js)
        self.assertIn("worker-stale-fold", js)
        self.assertIn("陈旧/离线 Worker", js)
        self.assertIn("放弃", js)
        self.assertIn("abandonPositionPlan", js)
        self.assertIn("/position-plans/{plan_id}/abandon", api_source)
        self.assertIn("def abandon_position_plan", service_source)

    def test_reports_preview_subviews_have_back_navigation(self):
        js = (ROOT / "static" / "js" / "reports.js").read_text(encoding="utf-8")
        css = STYLE_CSS.read_text(encoding="utf-8")

        self.assertIn("function previewBackAction", js)
        self.assertIn("返回任务详情", js)
        self.assertIn("previewBatchItemSteps(${Number(item.id)}, '${escapeAttr(job.job_id)}')", js)
        self.assertIn("previewBackAction(jobId, '角色执行流水')", js)
        self.assertIn("previewBackAction(jobId, '批量分析')", js)
        self.assertIn("previewBackAction(jobId, '失败分组')", js)
        self.assertIn("previewBackAction(jobId, '耗时统计')", js)
        self.assertIn("previewBackAction(jobId, '运行日志')", js)
        self.assertIn("previewBackAction(jobId, '批次产物')", js)
        self.assertIn(".preview-nav", css)

    def test_position_plan_preview_localizes_recommendation_actions(self):
        js = (ROOT / "static" / "js" / "reports.js").read_text(encoding="utf-8")

        self.assertIn("function positionActionLabel", js)
        self.assertIn("watch: '观察'", js)
        self.assertIn("avoid: '回避'", js)

    def test_report_detail_renders_holding_context_and_two_layer_signals(self):
        html = REPORT_DETAIL_TEMPLATE.read_text(encoding="utf-8")
        js = REPORT_DETAIL_JS.read_text(encoding="utf-8")

        self.assertIn('id="reportHoldingContext"', html)
        self.assertIn('id="reportHoldingContextBody"', html)
        self.assertIn("持仓上下文", html)
        self.assertIn("research_signal: '股票研究信号'", js)
        self.assertIn("account_signal: '账户动作信号'", js)
        self.assertIn("position_action: '账户动作'", js)
        self.assertIn("function renderHoldingContext", js)
        self.assertIn("buy: '买入'", js)
        self.assertIn("sell: '卖出'", js)

    def test_portfolio_page_uses_change_pct_and_holding_pnl_copy(self):
        js = PORTFOLIO_JS.read_text(encoding="utf-8")
        html = PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        stock_js = STOCK_JS.read_text(encoding="utf-8")
        ai_js = AI_JS.read_text(encoding="utf-8")
        index_html = INDEX_TEMPLATE.read_text(encoding="utf-8")
        ai_html = AI_TEMPLATE.read_text(encoding="utf-8")
        scheduler_source = (ROOT / "scheduler" / "report_runner.py").read_text(encoding="utf-8")

        for source in (html, js, stock_js, ai_js, index_html, ai_html):
            self.assertNotIn("当日盈亏", source)
        self.assertIn("当日涨跌幅", html)
        self.assertIn("当日涨跌幅", js)
        self.assertIn("当日涨跌幅", stock_js)
        self.assertIn("当日涨跌幅", ai_js)
        self.assertIn("持仓盈亏日历", html)
        self.assertIn("本月持仓盈亏", html)
        self.assertIn("持仓盈亏明细", js)
        self.assertIn("stock.js?v=2.9.20-pnl-pct", index_html)
        self.assertIn("ai.js?v=2.9.20-pnl-pct", ai_html)
        self.assertIn("(price - avg_cost) * total_shares", scheduler_source)
        self.assertNotIn("(price - prev_close) * total_shares", scheduler_source)

    def test_portfolio_holding_review_exposes_force_report_refresh_options(self):
        js = PORTFOLIO_JS.read_text(encoding="utf-8")
        html = PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        batch_service = (ROOT / "services" / "batch_report_service.py").read_text(encoding="utf-8")

        self.assertIn('id="holdingReviewForceHoldings"', html)
        self.assertIn('id="holdingReviewForceCandidates"', html)
        self.assertIn('id="holdingReviewRefreshSnapshots"', html)
        self.assertIn("后台补跑持仓报告", html)
        self.assertIn("后台补跑候选报告", html)
        self.assertIn("补跑前刷新七层快照", html)
        self.assertIn("force_refresh_holdings: forceRefreshHoldings", js)
        self.assertIn("force_refresh_candidates: forceRefreshCandidates", js)
        self.assertIn("refresh_snapshots_for_reports: refreshSnapshots", js)
        self.assertIn("请先勾选“从自选股选择候选”并选择候选股，再补跑候选报告。", js)
        self.assertIn("刷新七层快照需要配合持仓或候选报告补跑使用。", js)
        self.assertIn("review.status === 'waiting_reports'", js)
        self.assertIn("完成后自动生成作战计划", js)
        self.assertIn("finalize_waiting_reviews_for_batch_job", batch_service)
        self.assertIn("holding_review_finalized", batch_service)
        reports_js = (ROOT / "static" / "js" / "reports.js").read_text(encoding="utf-8")
        self.assertIn("waiting_reports: '等待补报告'", reports_js)
        self.assertIn("report_refresh_failed: '补报告失败'", reports_js)

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

    def test_reports_page_treats_sqlite_datetime_as_utc_for_local_display(self):
        js = (ROOT / "static" / "js" / "reports.js").read_text(encoding="utf-8")
        html = (ROOT / "templates" / "reports.html").read_text(encoding="utf-8")

        self.assertIn("function parseAppTime", js)
        self.assertIn("raw.replace(' ', 'T') + 'Z'", js)
        self.assertIn("const d = parseAppTime(value);", js)
        self.assertIn("function formatTime(value)", js)
        self.assertIn("function formatDate(value)", js)
        self.assertIn("reports.js?v=2.9.13-holding-review-refresh-flow", html)

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

    def test_portfolio_table_separates_holding_pnl_and_change_pct(self):
        html = PORTFOLIO_TEMPLATE.read_text(encoding="utf-8")
        js = PORTFOLIO_JS.read_text(encoding="utf-8")

        self.assertIn("<th>持仓盈亏</th>", html)
        self.assertIn("<th>持仓盈亏%</th>", html)
        self.assertIn("<th>涨跌幅</th>", html)
        self.assertNotIn("<th>当日涨跌盈亏</th>", html)
        self.assertIn("${formatMoney(p.unrealized_pnl)}</td>", js)
        self.assertIn("${formatPct(p.unrealized_pnl_pct)}</td>", js)
        self.assertIn("${formatPct(p.change_pct)}</td>", js)

    def test_portfolio_schema_migrates_to_account_code_unique_key(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                db_path = Path(tmp) / "workbench.db"
                async with aiosqlite.connect(db_path) as db:
                    db.row_factory = aiosqlite.Row
                    await db.executescript(
                        """
                        CREATE TABLE portfolio (
                            code TEXT PRIMARY KEY,
                            name TEXT,
                            total_shares REAL DEFAULT 0,
                            available_shares REAL DEFAULT 0,
                            avg_cost REAL DEFAULT 0,
                            current_price REAL DEFAULT 0,
                            market_value REAL DEFAULT 0,
                            unrealized_pnl REAL DEFAULT 0,
                            unrealized_pnl_pct REAL DEFAULT 0,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            account_id TEXT DEFAULT 'default'
                        );
                        INSERT INTO portfolio (code, name, total_shares, available_shares, avg_cost, account_id)
                        VALUES ('000001', '平安银行', 100, 100, 10, 'default');
                        """
                    )
                    await database.ensure_portfolio_account_key(db)
                    await db.execute(
                        """
                        INSERT INTO portfolio (code, name, total_shares, available_shares, avg_cost, account_id)
                        VALUES ('000001', '平安银行', 200, 200, 12, 'account-a')
                        """
                    )
                    await db.commit()
                    rows = await db.execute_fetchall(
                        "SELECT account_id, total_shares, avg_cost FROM portfolio WHERE code='000001' ORDER BY account_id"
                    )
                    indexes = await db.execute_fetchall("PRAGMA index_list(portfolio)")
                    unique_columns = []
                    for index in indexes:
                        if not index[2]:
                            continue
                        columns = await db.execute_fetchall(f"PRAGMA index_info({index[1]})")
                        unique_columns.append([column[2] for column in columns])
                    return rows, unique_columns

        rows, unique_columns = asyncio.run(run())
        self.assertEqual([(row["account_id"], row["total_shares"], row["avg_cost"]) for row in rows], [
            ("account-a", 200.0, 12.0),
            ("default", 100.0, 10.0),
        ])
        self.assertIn(["account_id", "code"], unique_columns)

    def test_reports_batch_jobs_have_manual_refresh_and_skip_reason(self):
        js = (ROOT / "static" / "js" / "reports.js").read_text(encoding="utf-8")
        html = (ROOT / "templates" / "reports.html").read_text(encoding="utf-8")
        service_source = (ROOT / "services" / "batch_report_service.py").read_text(encoding="utf-8")

        self.assertIn("refreshBatchJobs", js)
        self.assertIn("window.refreshBatchJobs", js)
        self.assertIn("刷新任务", html)
        self.assertIn("skipReasonForItem", js)
        self.assertIn("已有近期报告", service_source)
        self.assertIn("已有完整七层快照", service_source)

    def test_watchlist_exports_filtered_markdown(self):
        js = (ROOT / "static" / "js" / "stock.js").read_text(encoding="utf-8")
        html = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")

        self.assertIn("exportWatchlistMarkdown", js)
        self.assertIn("visibleWatchlistStocks()", js)
        self.assertIn("自选股导出", js)
        self.assertIn("window.exportWatchlistMarkdown", js)
        self.assertIn("导出MD", html)

    def test_report_detail_final_decision_has_dedicated_structured_renderer(self):
        js = (ROOT / "static" / "js" / "report-detail.js").read_text(encoding="utf-8")
        html = (ROOT / "templates" / "report_detail.html").read_text(encoding="utf-8")

        self.assertIn("function renderFinalDecision", js)
        self.assertIn("decision-summary-grid", js)
        self.assertIn("trader_plan: '交易计划'", js)
        self.assertIn("setHtml('reportDecisionBody', renderFinalDecision", js)
        self.assertIn("report-detail.js?v=2.9.17-holding-context", html)

    def test_report_groups_support_batch_select_and_full_markdown_export(self):
        js = (ROOT / "static" / "js" / "reports.js").read_text(encoding="utf-8")
        html = (ROOT / "templates" / "reports.html").read_text(encoding="utf-8")

        self.assertIn("function toggleReportGroupSelection", js)
        self.assertIn("report-group-select", js)
        self.assertIn("data-group-token", js)
        self.assertIn("window.toggleReportGroupSelection", js)
        self.assertIn("async function exportReportLibrary", js)
        self.assertIn("fetchReportsForExport", js)
        self.assertIn("/api/ai/reports/${encodeURIComponent(report.id)}", js)
        self.assertIn("formatReportMarkdown", js)
        self.assertIn("请选择要导出的报告", js)
        self.assertIn("reports.js?v=2.9.13-holding-review-refresh-flow", html)

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

    def test_kline_chart_render_cleans_old_instance_and_normalizes_data(self):
        js = CHART_JS.read_text(encoding="utf-8")
        html = INDEX_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("function cleanupKlineContainer", js)
        self.assertIn("container.__lwc_resize_observer.disconnect()", js)
        self.assertIn("container.__lwc_chart.remove()", js)
        self.assertIn("function normalizeKlineData", js)
        self.assertIn("const byTime = new Map()", js)
        self.assertIn("duplicateCount", js)
        self.assertIn("function chartContainerSize", js)
        self.assertIn("Math.max(240", js)
        self.assertIn("Math.max(320", js)
        self.assertIn("window._lastKlineDebug", js)
        self.assertIn("renderCount: safeData.length", js)
        self.assertIn("container.__lwc_resize_observer = ro", js)
        self.assertIn("static/js/chart.js?v=2.9.14-kline-resilience", html)

    def test_kline_chart_resilience_guards_are_present(self):
        chart_js = CHART_JS.read_text(encoding="utf-8")
        stock_js = (ROOT / "static/js/stock.js").read_text(encoding="utf-8")
        html = INDEX_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("function appendKlineDebug", chart_js)
        self.assertIn("window._klineDebugHistory", chart_js)
        self.assertIn("function delayKlineRender", chart_js)
        self.assertIn("K线图等待容器尺寸稳定", chart_js)
        self.assertIn("function checkKlineHealth", chart_js)
        self.assertIn("function scheduleKlineHealthCheck", chart_js)
        self.assertIn("_recoveryStep", chart_js)
        self.assertIn("renderKlineMessage(container, 'K线图渲染失败'", chart_js)
        self.assertIn("container.__kline_retry_render", chart_js)
        self.assertIn("let klineRequestSeq = 0", stock_js)
        self.assertIn("const requestSeq = ++klineRequestSeq", stock_js)
        self.assertIn("const isStale = () => requestSeq !== klineRequestSeq", stock_js)
        self.assertIn("if (isStale()) return", stock_js)
        self.assertIn("图表组件加载失败，请刷新页面", stock_js)
        self.assertIn("source: data?.source", stock_js)
        self.assertIn("fallbackSource: data?.fallback_source", stock_js)
        self.assertIn("static/js/stock.js?v=2.9.20-pnl-pct", html)

    def test_kline_resilience_state_machine_fallback_and_diagnostics_are_present(self):
        chart_js = CHART_JS.read_text(encoding="utf-8")
        stock_js = (ROOT / "static/js/stock.js").read_text(encoding="utf-8")
        html = INDEX_TEMPLATE.read_text(encoding="utf-8")

        self.assertIn("const KLINE_STATES", stock_js)
        self.assertIn("function setKlineState", stock_js)
        self.assertIn("function collectKlineDiagnostics", stock_js)
        self.assertIn("function exportKlineDiagnostics", stock_js)
        self.assertIn("window.exportKlineDiagnostics = exportKlineDiagnostics", stock_js)
        self.assertIn("id=\"btnKlineDiagnose\"", html)
        self.assertIn("onclick=\"exportKlineDiagnostics()\"", html)
        self.assertIn("quality: data?.quality", stock_js)
        self.assertIn("function klineRecoveryOptions", chart_js)
        self.assertIn("_recoveryStep", chart_js)
        self.assertIn("chartType: 'line'", chart_js)
        self.assertIn("renderKlineMessage(container, 'K线图渲染异常'", chart_js)
        self.assertIn("container.__kline_render_meta", chart_js)
        self.assertIn("setDataSuccess", chart_js)
        self.assertIn("static/js/chart.js?v=2.9.14-kline-resilience", html)

    def test_aurora_flow_theme_is_available_and_drives_chart_colors(self):
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        css = AURORA_FLOW_CSS.read_text(encoding="utf-8")
        chart_js = CHART_JS.read_text(encoding="utf-8")

        self.assertIn("aurora-flow.css?v=2.9.10", base)
        self.assertIn("id: 'aurora-flow'", base)
        self.assertIn("name: '蓝紫流光'", base)
        self.assertIn("icon: '流'", base)
        self.assertIn('body[data-theme="aurora-flow"]', css)
        self.assertIn("--chart-bg", css)
        self.assertIn("--chart-grid", css)
        self.assertIn("--chart-text", css)
        self.assertIn("linear-gradient(135deg, #1D6BFF 0%, #6938EF 56%, #19D5FF 100%)", css)
        self.assertIn("function chartThemeColors", chart_js)
        self.assertIn("cssVar('--chart-bg'", chart_js)
        self.assertIn("themeColors.background", chart_js)

    def test_wind_dashboard_theme_is_available_and_drives_chart_colors(self):
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        css = WIND_DASHBOARD_CSS.read_text(encoding="utf-8")

        self.assertIn("wind-dashboard.css?v=2.9.12", base)
        self.assertIn("id: 'wind-dashboard'", base)
        self.assertIn("name: '风能仪表'", base)
        self.assertIn("icon: '风'", base)
        self.assertIn('body[data-theme="wind-dashboard"]', css)
        self.assertIn("--chart-bg", css)
        self.assertIn("--chart-grid", css)
        self.assertIn("--chart-text", css)
        self.assertIn("#FF8A18", css)
        self.assertIn("风机监控视频风格", css)

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

    def test_investment_profile_settings_and_report_detail_are_exposed(self):
        settings_html = SETTINGS_TEMPLATE.read_text(encoding="utf-8")
        settings_js = SETTINGS_JS.read_text(encoding="utf-8")
        report_detail_html = (ROOT / "templates/report_detail.html").read_text(encoding="utf-8")
        report_detail_js = (ROOT / "static/js/report-detail.js").read_text(encoding="utf-8")

        self.assertIn("投资风格画像", settings_html)
        self.assertIn("investment_style_preset", settings_html)
        self.assertIn("investment_max_single_position_pct", settings_html)
        self.assertIn("investment_allow_left_side", settings_html)
        self.assertIn("套用当前风格模板", settings_html)
        self.assertIn("从交易历史推断", settings_html)
        self.assertIn("maybeApplyInvestmentStylePreset", settings_html)
        self.assertIn("inferInvestmentProfileFromTrades", settings_js)
        self.assertIn("INVESTMENT_STYLE_PRESETS", settings_js)
        self.assertIn("applyInvestmentStylePreset", settings_js)
        self.assertIn("markInvestmentProfileEdited", settings_js)
        self.assertIn("/settings/investment-profile/infer", settings_js)
        self.assertIn("reportInvestmentProfileBody", report_detail_html)
        self.assertIn("renderInvestmentProfile", report_detail_js)
        self.assertIn("风格匹配度", report_detail_js)
        self.assertIn("style_match", report_detail_js)
        self.assertIn("raw.investment_profile", report_detail_js)

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
