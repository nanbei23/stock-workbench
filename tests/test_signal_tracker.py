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

    def test_sell_signal_profit_when_price_falls(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO signal_tracking
                    (id, report_id, code, name, signal, signal_date, entry_price, current_price, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, 1, "600519", "贵州茅台", "SELL", "2026-05-29", 100.0, 100.0, "open"),
            )
            db.commit()

        self.assertTrue(signal_tracker.close_tracking_manual(1, 90.0))

        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT pnl_pct, excess_return FROM signal_tracking WHERE id=1").fetchone()

        self.assertEqual(row[0], 10.0)
        self.assertEqual(row[1], 10.0)

    def test_stats_filter_by_model_mode(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO analysis_reports (id, code, signal, model_mode, depth)
                VALUES (?, ?, ?, ?, ?)
                """,
                (1, "000001", "BUY", "balanced", "standard"),
            )
            db.execute(
                """
                INSERT INTO analysis_reports (id, code, signal, model_mode, depth)
                VALUES (?, ?, ?, ?, ?)
                """,
                (2, "000002", "BUY", "pro", "deep"),
            )
            db.executemany(
                """
                INSERT INTO signal_tracking
                    (report_id, code, name, signal, signal_date, entry_price, exit_price, pnl_pct, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (1, "000001", "平安银行", "BUY", "2026-05-29", 10.0, 11.0, 10.0, "closed"),
                    (2, "000002", "万科A", "BUY", "2026-05-29", 10.0, 9.0, -10.0, "closed"),
                ],
            )
            db.commit()

        stats = signal_tracker.get_stats(window="all", model_mode="balanced")

        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["closed"], 1)
        self.assertEqual(stats["avg_pnl_pct"], 10.0)


if __name__ == "__main__":
    unittest.main()
