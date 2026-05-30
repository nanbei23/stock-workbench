import sqlite3
import tempfile
import unittest
from pathlib import Path

from models import database
from scheduler import signal_tracker


class SignalTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = signal_tracker.DB_PATH
        signal_tracker.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)

    def tearDown(self):
        signal_tracker.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_get_open_tracking_codes_returns_distinct_open_codes(self):
        with sqlite3.connect(self.db_path) as db:
            rows = [
                (1, "000001", "平安银行", "BUY", "2026-05-29", 10.0, "open"),
                (2, "000001", "平安银行", "BUY", "2026-05-29", 10.0, "open"),
                (3, "600519", "贵州茅台", "SELL", "2026-05-29", 1600.0, "closed"),
                (4, "300750", "宁德时代", "HOLD", "2026-05-29", 200.0, "open"),
            ]
            db.executemany(
                """
                INSERT INTO signal_tracking
                    (report_id, code, name, signal, signal_date, entry_price, status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            db.commit()

        self.assertEqual(signal_tracker.get_open_tracking_codes(), ["000001", "300750"])


if __name__ == "__main__":
    unittest.main()
