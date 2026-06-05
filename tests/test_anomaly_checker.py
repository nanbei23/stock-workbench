import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from models import database
from scheduler import anomaly_checker


class AnomalyCheckerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = anomaly_checker.DB_PATH
        anomaly_checker.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)
            db.execute("INSERT INTO watchlist (code, name) VALUES (?, ?)", ("002463", "沪电股份"))
            db.commit()

    def tearDown(self):
        anomaly_checker.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    async def test_repeated_same_day_price_anomaly_is_not_logged_every_minute(self):
        quote = {"002463": {"price": 133.22, "change_pct": -5.4, "volume": 0}}
        with patch("data.quote.get_batch_quotes", new=AsyncMock(return_value=quote)):
            first = await anomaly_checker._check_anomalies()
            second = await anomaly_checker._check_anomalies()

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        with sqlite3.connect(self.db_path) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM anomaly_logs WHERE code = ? AND anomaly_type = ?",
                ("002463", "跌幅异动"),
            ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
