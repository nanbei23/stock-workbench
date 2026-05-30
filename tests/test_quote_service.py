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


if __name__ == "__main__":
    unittest.main()
