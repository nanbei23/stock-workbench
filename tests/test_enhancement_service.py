import sqlite3
import tempfile
import unittest
from pathlib import Path
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

    async def test_system_diagnostics_aggregates_panels(self):
        result = await enhancement_service.system_diagnostics()

        self.assertIn("summary", result)
        self.assertIn("health", result)
        self.assertIn("tasks", result)
        self.assertGreaterEqual(result["summary"]["warning_count"], 1)

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


if __name__ == "__main__":
    unittest.main()
