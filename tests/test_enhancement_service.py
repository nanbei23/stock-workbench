import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi import HTTPException

from models import database
from repositories import settings_repository
from services import enhancement_service


class EnhancementServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_settings_path = settings_repository.DB_PATH
        self.original_database_path = database.DB_PATH
        settings_repository.DB_PATH = self.db_path
        database.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)

    def tearDown(self):
        settings_repository.DB_PATH = self.original_settings_path
        database.DB_PATH = self.original_database_path
        self.tmp.cleanup()

    async def test_model_provider_pool_masks_key_and_applies_to_ai(self):
        saved = enhancement_service.save_model_provider({
            "id": "p1",
            "name": "Example",
            "base_url": "https://api.example.com/v1",
            "api_key": "sk-secret",
            "models": ["fast", "deep"],
            "quick_model": "fast",
            "deep_model": "deep",
            "context_length": "128000",
        })

        listed = enhancement_service.list_model_providers()
        applied = enhancement_service.apply_model_provider("p1", "ai")

        self.assertNotIn("api_key", saved["provider"])
        self.assertTrue(saved["provider"]["has_api_key"])
        self.assertTrue(listed["providers"][0]["has_api_key"])
        self.assertNotIn("api_key", listed["providers"][0])
        self.assertEqual(applied["settings"]["quick_think_model"], "fast")
        self.assertEqual(applied["settings"]["deep_think_model"], "deep")
        self.assertEqual(applied["settings"]["api_key"], "********")

    async def test_model_provider_save_can_apply_to_current_ai_settings(self):
        enhancement_service.save_model_provider({
            "id": "p-ai",
            "name": "Inline AI",
            "base_url": "https://api.inline.com/v1",
            "api_key": "sk-inline",
            "models": ["fast", "deep"],
            "quick_model": "fast",
            "deep_model": "deep",
            "context_length": "256000",
            "apply_to": "ai",
        })

        settings = settings_repository.fetch_settings()

        self.assertEqual(settings["llm_name"], "Inline AI")
        self.assertEqual(settings["custom_endpoint"], "https://api.inline.com/v1")
        self.assertEqual(settings["api_key"], "sk-inline")
        self.assertEqual(settings["quick_think_model"], "fast")
        self.assertEqual(settings["deep_think_model"], "deep")
        self.assertEqual(settings["llm_model_options"], '["fast", "deep"]')

    async def test_model_provider_save_can_apply_to_verification_settings(self):
        enhancement_service.save_model_provider({
            "id": "p-verifier",
            "name": "Inline Verifier",
            "base_url": "https://verify.inline.com/v1",
            "api_key": "sk-verify",
            "models": ["verify-pro"],
            "default_model": "verify-pro",
            "context_length": "128000",
            "apply_to": "verification",
        })

        settings = settings_repository.fetch_settings()

        self.assertEqual(settings["verification_name"], "Inline Verifier")
        self.assertEqual(settings["verification_endpoint"], "https://verify.inline.com/v1")
        self.assertEqual(settings["verification_api_key"], "sk-verify")
        self.assertEqual(settings["verification_model"], "verify-pro")
        self.assertEqual(settings["verification_model_options"], '["verify-pro"]')

    async def test_report_versions_and_compare(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO analysis_reports (code, signal, confidence, risk_score, created_at) VALUES (?, ?, ?, ?, ?)",
                ("000001", "BUY", 70, 30, "2026-01-01 10:00:00"),
            )
            db.execute(
                "INSERT INTO analysis_reports (code, signal, confidence, risk_score, created_at) VALUES (?, ?, ?, ?, ?)",
                ("000001", "SELL", 60, 50, "2026-01-02 10:00:00"),
            )
            db.commit()

        versions = await enhancement_service.report_versions("000001")
        compared = await enhancement_service.compare_reports(1, 2)

        self.assertEqual(versions["count"], 2)
        self.assertTrue(compared["diff"]["signal_changed"])
        self.assertEqual(compared["diff"]["confidence_delta"], -10)

    async def test_condition_backtest_counts_triggers(self):
        with sqlite3.connect(self.db_path) as db:
            db.executemany(
                "INSERT INTO daily_pnl (date, code6, close_price) VALUES (?, ?, ?)",
                [
                    ("2026-01-01", "000001", 10.0),
                    ("2026-01-02", "000001", 9.5),
                    ("2026-01-03", "000001", 11.0),
                ],
            )
            db.commit()

        result = await enhancement_service.condition_backtest({
            "code": "000001",
            "condition_type": "price_lte",
            "target_price": 10,
            "days": 90,
        })

        self.assertEqual(result["trigger_count"], 2)
        self.assertEqual(result["post_trigger_return_pct"], 10.0)

    async def test_data_health_detects_missing_model_list(self):
        result = await enhancement_service.data_health()

        ai_check = next(item for item in result["checks"] if item["key"] == "ai_models")
        self.assertEqual(ai_check["status"], "warning")

    async def test_data_health_detects_and_fixes_portfolio_mismatch(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT OR IGNORE INTO accounts (id, name) VALUES ('default', '默认账户')")
            db.execute(
                """
                INSERT INTO trades (code, name, direction, price, shares, amount, total_cost)
                VALUES ('000001', '平安银行', 'buy', 10, 100, 1000, 1000)
                """
            )
            db.execute(
                """
                INSERT INTO portfolio (code, name, total_shares, available_shares, avg_cost)
                VALUES ('000001', '平安银行', 50, 50, 8)
                """
            )
            db.commit()

        before = await enhancement_service.data_health()
        mismatch = next(item for item in before["checks"] if item["key"] == "portfolio_consistency")

        self.assertEqual(mismatch["status"], "warning")

        fixed = await enhancement_service.fix_data_health()
        after = await enhancement_service.data_health()
        mismatch_after = next(item for item in after["checks"] if item["key"] == "portfolio_consistency")

        self.assertEqual(fixed["portfolio_recalculated"], 1)
        self.assertEqual(mismatch_after["status"], "ok")

    async def test_system_diagnostics_aggregates_panels(self):
        result = await enhancement_service.system_diagnostics()

        self.assertIn("summary", result)
        self.assertIn("health", result)
        self.assertIn("tasks", result)
        self.assertGreaterEqual(result["summary"]["warning_count"], 1)

    async def test_risk_center_flags_concentration_and_stale_quotes(self):
        settings_repository.upsert_settings({
            "risk_max_position_pct": "20",
            "risk_quote_stale_hours": "1",
        })
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT OR IGNORE INTO accounts (id, name) VALUES ('default', '默认账户')")
            db.execute(
                """
                INSERT INTO portfolio (code, name, total_shares, available_shares, avg_cost, current_price, market_value, updated_at)
                VALUES ('000001', '平安银行', 1000, 1000, 10, 10, 10000, datetime('now', '-2 hours'))
                """
            )
            db.commit()

        result = await enhancement_service.risk_center()

        checks = {item["key"]: item for item in result["checks"]}
        self.assertEqual(checks["position_concentration"]["status"], "warning")
        self.assertEqual(checks["quote_freshness"]["status"], "warning")
        self.assertFalse(result["ok"])

    async def test_operations_dashboard_returns_release_and_quality_sections(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT OR IGNORE INTO accounts (id, name) VALUES ('default', '默认账户')")
            db.commit()

        result = await enhancement_service.operations_dashboard()

        self.assertIn("data_trust", result)
        self.assertIn("portfolio", result)
        self.assertIn("risk", result)
        self.assertIn("ai_quality", result)
        self.assertIn("release_ops", result)
        self.assertIn("notifications", result)
        self.assertIn("diagnostics", result)
        self.assertGreaterEqual(result["score"], 0)

    async def test_data_audit_summarizes_health_and_counts(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT OR IGNORE INTO accounts (id, name) VALUES ('default', '默认账户')")
            db.execute("INSERT INTO watchlist (code, name) VALUES ('000001', '平安银行')")
            db.commit()

        result = await enhancement_service.data_audit()

        self.assertIn("summary", result)
        self.assertIn("score", result)
        self.assertEqual(result["summary"]["watchlist_count"], 1)
        self.assertGreaterEqual(result["warning_count"], 1)

    async def test_ai_readiness_blocks_missing_model_config(self):
        result = enhancement_service.ai_readiness()

        self.assertFalse(result["ready"])
        self.assertIn("请先通过 Base URL 获取 AI 模型列表", result["blockers"])

    async def test_ai_readiness_passes_with_complete_config(self):
        settings_repository.upsert_settings({
            "custom_endpoint": "https://api.example.com/v1",
            "api_key": "sk-test",
            "llm_model_options": '["fast","deep"]',
            "quick_think_model": "fast",
            "deep_think_model": "deep",
        })

        result = enhancement_service.ai_readiness()

        self.assertTrue(result["ready"])
        self.assertEqual(result["config"]["model_count"], 2)

    async def test_model_provider_test_requires_selected_model(self):
        enhancement_service.save_model_provider({
            "id": "p2",
            "name": "No model",
            "base_url": "https://api.example.com/v1",
            "models": [],
        })

        with self.assertRaises(HTTPException) as ctx:
            await enhancement_service.test_model_provider("p2")

        self.assertEqual(ctx.exception.status_code, 400)

    async def test_market_regime_returns_actionable_state(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT OR IGNORE INTO accounts (id, name) VALUES ('default', '默认账户')")
            db.execute(
                """
                INSERT INTO daily_pnl (date, code6, total_pnl_pct, total_pnl, total_assets)
                VALUES ('2026-01-02', '', 1.2, 1200, 100000)
                """
            )
            db.commit()

        with patch("services.enhancement_service.quote_service.get_indices", new=AsyncMock(return_value={
            "sh": {"change_pct": 1.0},
            "sz": {"change_pct": 0.6},
            "cyb": {"change_pct": 1.4},
        })), patch("services.enhancement_service.get_market_sentiment", new=AsyncMock(return_value={
            "breadth": {"up": 3200, "down": 1500, "flat": 300, "limit_up": 80, "limit_down": 8, "total": 5000},
            "northbound": {"total_net": 18.5},
        })), patch("services.enhancement_service.get_industry_ranking", new=AsyncMock(return_value=[
            {"name": "半导体", "change_pct": 3.2, "up_count": 60, "down_count": 10, "lead_stock": "芯片样本", "lead_change_pct": 8.5},
        ])):
            result = await enhancement_service.market_regime()

        self.assertIn(result["regime"], {"risk_on", "balanced", "risk_off"})
        self.assertIn("action_bias", result)
        self.assertGreaterEqual(result["score"], 0)
        self.assertEqual(result["source_summary"]["mode"], "live_market_plus_local_risk")

    async def test_market_hotspots_derives_topics_from_news_and_watchlist(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO watchlist (code, name, group_name) VALUES ('688001', '芯片样本', '半导体')")
            db.execute(
                """
                INSERT INTO news_cache (code6, source, title, content, sentiment, cached_at)
                VALUES ('688001', 'test', '半导体芯片产业链升温', '先进封装订单改善', 'positive', datetime('now'))
                """
            )
            db.commit()

        with patch("services.enhancement_service.get_hot_reasons", new=AsyncMock(return_value=[
            {"code": "688001", "name": "芯片样本", "hot_value": 1200000, "change_pct": 6.2},
        ])), patch("services.enhancement_service.get_industry_ranking", new=AsyncMock(return_value=[
            {"name": "半导体", "change_pct": 3.2, "up_count": 60, "down_count": 10, "lead_stock": "芯片样本", "lead_change_pct": 8.5},
        ])), patch("services.enhancement_service.get_market_sentiment", new=AsyncMock(return_value={
            "breadth": {"up": 3000, "down": 1600, "total": 5000},
            "northbound": {"total_net": 12.3},
        })):
            result = await enhancement_service.market_hotspots()

        self.assertGreaterEqual(result["count"], 1)
        names = [item["name"] for item in result["topics"]]
        self.assertIn("半导体", names)
        self.assertEqual(result["source_summary"]["mode"], "live_market_plus_local_research")

    async def test_research_pulse_has_active_phase(self):
        result = enhancement_service.research_pulse()

        self.assertIn("active", result)
        self.assertEqual(len(result["phases"]), 5)

    async def test_strategy_lifecycle_groups_watch_plan_holding_and_exit(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO watchlist (code, name, group_name) VALUES ('000001', '平安银行', '金融')")
            db.execute(
                """
                INSERT INTO trading_plans (code, name, direction, target_price, plan_shares, status)
                VALUES ('000002', '计划样本', 'buy', 10, 100, 'pending')
                """
            )
            db.execute(
                """
                INSERT INTO portfolio (code, name, total_shares, market_value, unrealized_pnl_pct)
                VALUES ('000003', '持仓样本', 100, 1000, -6.5)
                """
            )
            db.execute(
                """
                INSERT INTO signal_tracking (code, name, signal, signal_date, entry_price, exit_price, pnl_pct, status)
                VALUES ('000004', '退出样本', 'BUY', '2026-01-01', 10, 11, 10, 'closed')
                """
            )
            db.commit()

        result = await enhancement_service.strategy_lifecycle()
        columns = {item["key"]: item for item in result["columns"]}

        self.assertGreaterEqual(columns["watching"]["count"], 1)
        self.assertGreaterEqual(columns["planned"]["count"], 1)
        self.assertGreaterEqual(columns["weakening"]["count"], 1)
        self.assertGreaterEqual(columns["exited"]["count"], 1)

    async def test_research_progress_summarizes_recent_tasks(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO analysis_tasks (task_id, code, name, status, stages)
                VALUES ('t1', '000001', '平安银行', 'running', '{"a":{"status":"done"},"b":{"status":"running"}}')
                """
            )
            db.execute(
                """
                INSERT INTO analysis_progress (task_id, code, stage_id, completed_at)
                VALUES ('t1', '000001', 'a', datetime('now'))
                """
            )
            db.commit()

        result = await enhancement_service.research_progress()

        self.assertEqual(result["active_count"], 1)
        self.assertEqual(result["items"][0]["progress_pct"], 50)


if __name__ == "__main__":
    unittest.main()
