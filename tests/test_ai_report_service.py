import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch

import models.database as database
from api.ai_api import router as ai_router
from api.pdf_export import router as pdf_router
from services import ai_report_service


class AiReportServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        self._init_data()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def _init_data(self):
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)
            db.execute("INSERT INTO watchlist (code, name) VALUES (?, ?)", ("600519", "贵州茅台"))
            db.execute(
                """
                INSERT INTO analysis_reports (
                    id, task_id, code, signal, confidence, risk_score,
                    market_report, investment_debate, raw_state, fact_check,
                    bystander_verify, duration_seconds, depth, model_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    "task-1",
                    "600519",
                    "BUY",
                    0.82,
                    0.3,
                    "market text",
                    json.dumps({"bull": "ok"}, ensure_ascii=False),
                    json.dumps({"name": "茅台快照", "signal": "BUY"}, ensure_ascii=False),
                    json.dumps({"stages": {"market": {"accuracy": 100}}}, ensure_ascii=False),
                    json.dumps({"overall_score": 90}, ensure_ascii=False),
                    8.5,
                    "standard",
                    "balanced",
                ),
            )
            db.execute(
                """
                INSERT INTO anomaly_logs (code, name, anomaly_type, description, severity, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                """,
                ("600519", "贵州茅台", "price_surge", "快速上涨", "warning"),
            )
            db.execute(
                """
                INSERT INTO signal_tracking (
                    report_id, code, name, signal, signal_date, entry_price, exit_price, pnl_pct,
                    excess_return, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, "600519", "贵州茅台", "BUY", "2026-05-30", 100, 108, 8.0, 2.0, "closed"),
            )
            db.commit()

    async def test_list_reports_adds_name_and_hides_raw_state(self):
        result = await ai_report_service.list_reports(limit=10)

        self.assertEqual(result["count"], 1)
        report = result["reports"][0]
        self.assertEqual(report["name"], "茅台快照")
        self.assertNotIn("raw_state", report)
        self.assertIn("fact_accuracy", report)
        self.assertIn("hallucinations", report)

    async def test_list_reports_supports_large_report_library_limit(self):
        result = await ai_report_service.list_reports(limit=300, model_mode="balanced")

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["reports"][0]["model_mode"], "balanced")

    async def test_get_report_parses_json_fields(self):
        report = await ai_report_service.get_report(1)

        self.assertEqual(report["name"], "茅台快照")
        self.assertEqual(report["result"]["signal"], "BUY")
        self.assertEqual(report["investment_debate"], {"bull": "ok"})
        self.assertIn("_fact_check", report)
        self.assertEqual(report["_bystander_verify"]["overall_score"], 90)

    async def test_get_anomalies_uses_db_then_memory_fallback(self):
        db_result = await ai_report_service.get_anomalies(limit=10, code="600519", memory_log=[])
        self.assertEqual(db_result["count"], 1)
        self.assertEqual(db_result["anomalies"][0]["message"], "快速上涨")

        mem_result = await ai_report_service.get_anomalies(
            limit=10,
            code="000001",
            memory_log=[{"code": "000001", "message": "memory"}],
        )
        self.assertEqual(mem_result["count"], 1)
        self.assertEqual(mem_result["anomalies"][0]["message"], "memory")

    async def test_clear_anomalies_for_date_deletes_only_target_day(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO anomaly_logs (code, name, anomaly_type, description, severity, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", "price_drop", "昨日异动", "warning", "2026-05-28 10:00:00"),
            )
            db.execute(
                """
                INSERT INTO anomaly_logs (code, name, anomaly_type, description, severity, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("000002", "万科A", "price_surge", "今日异动", "warning", "2026-05-29 10:00:00"),
            )
            db.commit()

        deleted = await ai_report_service.clear_anomalies_for_date("2026-05-28")

        self.assertEqual(deleted, 1)
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute("SELECT code FROM anomaly_logs ORDER BY code").fetchall()
        self.assertEqual([row[0] for row in rows], ["000002", "600519"])

    async def test_quality_summary_groups_by_model_and_signal(self):
        result = await ai_report_service.get_quality_summary(limit=10)

        self.assertEqual(result["best_model_mode"]["model_mode"], "balanced")
        self.assertEqual(result["by_signal"][0]["signal"], "BUY")
        self.assertEqual(result["by_signal"][0]["avg_pnl_pct"], 8.0)


class AiReportApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)
            db.execute("INSERT INTO watchlist (code, name) VALUES (?, ?)", ("000001", "平安银行"))
            db.execute(
                """
                INSERT INTO analysis_reports (
                    id, task_id, code, signal, raw_state, duration_seconds
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    10,
                    "task-10",
                    "000001",
                    "HOLD",
                    json.dumps({"name": "平安快照"}, ensure_ascii=False),
                    2.0,
                ),
            )
            db.commit()
        app = FastAPI()
        app.include_router(ai_router, prefix="/api")
        self.client = TestClient(app)

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_report_routes_use_service_layer(self):
        listing = self.client.get("/api/ai/reports?limit=300")
        detail = self.client.get("/api/ai/reports/10")

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["reports"][0]["name"], "平安快照")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["result"]["name"], "平安快照")


class PdfExportApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)
            db.execute(
                """
                INSERT INTO analysis_reports (
                    id, task_id, code, signal, confidence, risk_score, market_report
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (20, "task-20", "600519", "BUY", 0.8, 0.2, "market text"),
            )
            db.commit()
        app = FastAPI()
        app.include_router(pdf_router, prefix="/api")
        self.client = TestClient(app)

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_pdf_export_uses_report_service_database_path(self):
        with patch("api.pdf_export._generate_pdf_content", return_value=b"%PDF-test"):
            resp = self.client.get("/api/ai/report/20/pdf")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "application/pdf")
        self.assertEqual(resp.content, b"%PDF-test")

    def test_pdf_export_missing_report_returns_404(self):
        resp = self.client.get("/api/ai/report/999/pdf")

        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
