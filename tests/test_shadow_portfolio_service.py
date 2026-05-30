import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import models.database as database
from api.shadow_api import router as shadow_router
from services import shadow_portfolio_service


class ShadowPortfolioServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    async def test_sync_reports_creates_filled_shadow_order_and_position(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO analysis_reports (id, code, signal, confidence, risk_score, raw_state)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    "000001",
                    "BUY",
                    0.82,
                    0.2,
                    json.dumps({"name": "平安银行", "entry_price": 10.5, "target_price": 12}, ensure_ascii=False),
                ),
            )
            db.commit()

        with patch("services.shadow_portfolio_service.get_batch_quotes", new=AsyncMock(return_value={"000001": {"price": 11.0}})):
            synced = await shadow_portfolio_service.sync_reports()
            positions = await shadow_portfolio_service.list_positions()

        self.assertEqual(synced["created"], 1)
        self.assertEqual(positions["count"], 1)
        pos = positions["positions"][0]
        self.assertEqual(pos["code"], "000001")
        self.assertEqual(pos["total_shares"], 200)
        self.assertAlmostEqual(pos["avg_cost"], 10.5033, places=4)

    async def test_comparison_keeps_real_and_shadow_separate(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO ai_shadow_orders (
                    report_id, code, name, action, signal, suggested_price, fill_price,
                    shares, confidence, risk_score, status, filled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (1, "000001", "平安银行", "buy", "BUY", 10, 10, 100, 0.8, 0.2, "filled"),
            )
            db.execute(
                """
                INSERT INTO portfolio (code, name, total_shares, available_shares, avg_cost)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", 200, 200, 9.0),
            )
            db.commit()

        with patch("services.shadow_portfolio_service.get_batch_quotes", new=AsyncMock(return_value={"000001": {"price": 11.0}})):
            result = await shadow_portfolio_service.comparison()

        self.assertEqual(result["count"], 1)
        row = result["rows"][0]
        self.assertEqual(row["shadow_shares"], 100)
        self.assertEqual(row["real_shares"], 200)
        self.assertEqual(row["share_gap"], -100)
        self.assertEqual(row["pnl_gap"], -300.31)

    async def test_calibration_groups_signal_and_confidence_returns(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO analysis_reports (id, code, signal, confidence, risk_score, raw_state)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (1, "000001", "BUY", 0.82, 0.2, json.dumps({"name": "平安银行"}, ensure_ascii=False)),
            )
            db.execute(
                """
                INSERT INTO analysis_reports (id, code, signal, confidence, risk_score, raw_state)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (2, "000002", "SELL", 0.6, 0.3, json.dumps({"name": "万科A"}, ensure_ascii=False)),
            )
            db.executemany(
                """
                INSERT INTO ai_shadow_orders (
                    report_id, code, name, action, signal, suggested_price, fill_price,
                    shares, confidence, risk_score, status, filled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                [
                    (1, "000001", "平安银行", "buy", "BUY", 10, 10, 100, 0.82, 0.2, "filled"),
                    (2, "000002", "万科A", "sell", "SELL", 20, 20, 100, 0.6, 0.3, "filled"),
                ],
            )
            db.commit()

        quotes = {"000001": {"price": 11.0}, "000002": {"price": 18.0}}
        with patch("services.shadow_portfolio_service.get_batch_quotes", new=AsyncMock(return_value=quotes)):
            result = await shadow_portfolio_service.calibration()

        self.assertEqual(result["summary"]["evaluated"], 2)
        self.assertEqual(result["summary"]["hit_rate"], 100.0)
        self.assertEqual(result["summary"]["avg_return_pct"], 10.0)
        self.assertEqual({item["key"] for item in result["by_signal"]}, {"BUY", "SELL"})
        self.assertEqual({item["key"] for item in result["by_confidence"]}, {"medium", "high"})
        self.assertEqual(result["top_wins"][0]["directional_return_pct"], 10.0)


class ShadowPortfolioApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(shadow_router, prefix="/api")
        self.client = TestClient(app)

    def test_summary_route_uses_service_layer(self):
        with patch(
            "services.shadow_portfolio_service.summary",
            new=AsyncMock(return_value={"orders": {"total": 1}, "positions": {}, "comparison": {}}),
        ) as summary:
            resp = self.client.get("/api/shadow/summary")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["orders"]["total"], 1)
        summary.assert_awaited_once()

    def test_sync_route_passes_limit(self):
        with patch(
            "services.shadow_portfolio_service.sync_reports",
            new=AsyncMock(return_value={"created": 2}),
        ) as sync_reports:
            resp = self.client.post("/api/shadow/sync-reports?limit=25")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["created"], 2)
        sync_reports.assert_awaited_once_with(25)

    def test_calibration_route_passes_limit(self):
        with patch(
            "services.shadow_portfolio_service.calibration",
            new=AsyncMock(return_value={"summary": {"evaluated": 2}}),
        ) as calibration:
            resp = self.client.get("/api/shadow/calibration?limit=50")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["summary"]["evaluated"], 2)
        calibration.assert_awaited_once_with(50, "all", None, None)


if __name__ == "__main__":
    unittest.main()
