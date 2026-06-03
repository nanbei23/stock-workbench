import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from models import database
from services import quote_service


class QuoteServiceTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_get_batch_validates_codes(self):
        with self.assertRaises(HTTPException) as ctx:
            await quote_service.get_batch(" , ")

        self.assertEqual(ctx.exception.status_code, 400)

    async def test_get_quote_enriches_position_pnl(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO portfolio (code, name, total_shares, avg_cost) VALUES (?, ?, ?, ?)",
                ("000001", "平安银行", 100, 10.0),
            )
            db.commit()

        with patch(
            "services.quote_service.get_realtime_quote",
            new=AsyncMock(return_value={"code": "000001", "price": 11.0, "prev_close": 10.5}),
        ):
            result = await quote_service.get_quote("000001")

        self.assertEqual(result["avg_cost"], 10.0)
        self.assertEqual(result["total_shares"], 100)
        self.assertEqual(result["unrealized_pnl"], 100.0)
        self.assertEqual(result["daily_pnl"], 50.0)

    async def test_get_quote_raises_404_when_empty(self):
        with patch("services.quote_service.get_realtime_quote", new=AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException) as ctx:
                await quote_service.get_quote("000001")

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_get_kline_data_falls_back_and_reports_quality(self):
        fallback_rows = [
            {"date": f"2026-06-{day:02d}", "open": 10, "high": 11, "low": 9.8, "close": 10.5, "volume": 100 + day, "amount": 1000 + day}
            for day in range(1, 26)
        ]
        with patch("services.quote_service.get_kline", return_value=[]), patch(
            "services.quote_service.get_tencent_history_kline",
            return_value=fallback_rows,
        ), patch(
            "services.quote_service.get_kline_with_ma",
            new=AsyncMock(return_value={"kline": []}),
        ):
            result = await quote_service.get_kline_data("000001", "day", 120)

        self.assertEqual(result["source"], "tencent_history")
        self.assertEqual(result["fallback_source"], "tencent_history")
        self.assertEqual(result["count"], 25)
        self.assertEqual(result["quality"]["score"], 100)
        self.assertEqual(result["quality"]["issues"], [])
        self.assertIn("mootdx", result["quality"]["source_attempts"][0]["source"])
        self.assertEqual(result["quality"]["source_attempts"][1]["source"], "tencent_history")

    async def test_get_kline_data_reports_invalid_primary_rows(self):
        rows = [
            {"date": "2026-06-01", "open": 10, "high": 9, "low": 9.8, "close": 10.5, "volume": 100, "amount": 1000},
        ]
        with patch("services.quote_service.get_kline", return_value=rows):
            result = await quote_service.get_kline_data("000001", "day", 120)

        self.assertEqual(result["source"], "mootdx")
        self.assertLess(result["quality"]["score"], 100)
        self.assertTrue(any("invalid_ohlc" in issue for issue in result["quality"]["issues"]))


if __name__ == "__main__":
    unittest.main()
