import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import models.database as database
from api.ai_api import router as ai_router
from repositories import ai_fact_repository
from services import ai_fact_service


class AiFactServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = ai_fact_repository.DB_PATH
        ai_fact_repository.DB_PATH = self.db_path
        self._init_data()

    def tearDown(self):
        ai_fact_repository.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def _init_data(self):
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)
            db.execute(
                """
                INSERT INTO analysis_reports (
                    id, task_id, code, signal, confidence, risk_score,
                    market_report, sentiment_report, final_decision,
                    raw_state, fact_check, duration_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    "task-1",
                    "600519",
                    "BUY",
                    0.8,
                    0.2,
                    "现价 10.5 元，PE 12 倍",
                    "情绪稳定",
                    "建议关注",
                    json.dumps({"target_price": 20.0}, ensure_ascii=False),
                    json.dumps(
                        {
                            "stages": {"market": {"accuracy": 100}},
                            "overall_accuracy": 100,
                        },
                        ensure_ascii=False,
                    ),
                    5.0,
                ),
            )
            db.commit()

    async def test_get_fact_check_returns_stored_stage_ledger(self):
        result = await ai_fact_service.get_fact_check(1)

        self.assertEqual(result["overall_accuracy"], 100)
        self.assertEqual(result["stages"]["market"]["accuracy"], 100)

    async def test_bystander_verify_skips_without_api_key(self):
        with patch(
            "services.ai_fact_service._get_verify_settings",
            return_value={
                "api_key": "",
                "api_url": "http://example.test/chat/completions",
                "verify_key": "",
                "verify_model": "mimo-v2.5-pro",
            },
        ):
            result = await ai_fact_service.bystander_verify(1)

        self.assertEqual(result["status"], "skipped")
        self.assertIn("未配置API密钥", result["error"])

    def test_extract_numerical_claims_finds_market_metrics(self):
        claims = ai_fact_service.extract_numerical_claims("现价 10.5 元，PE 12 倍，市值 3000 亿")

        keywords = {claim["keyword"] for claim in claims}
        self.assertIn("现价", keywords)
        self.assertIn("PE", keywords)
        self.assertIn("总市值", keywords)


class AiFactApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = ai_fact_repository.DB_PATH
        ai_fact_repository.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)
            db.execute(
                """
                INSERT INTO analysis_reports (id, task_id, code, fact_check)
                VALUES (?, ?, ?, ?)
                """,
                (
                    10,
                    "task-10",
                    "000001",
                    json.dumps(
                        {
                            "stages": {"market": {"accuracy": 90}},
                            "overall_accuracy": 90,
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            db.commit()
        app = FastAPI()
        app.include_router(ai_router, prefix="/api")
        self.client = TestClient(app)

    def tearDown(self):
        ai_fact_repository.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_fact_check_route_uses_service_layer(self):
        resp = self.client.get("/api/ai/reports/10/fact-check")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["stages"]["market"]["accuracy"], 90)


if __name__ == "__main__":
    unittest.main()
