import sqlite3
import tempfile
import unittest
from pathlib import Path

from models.database import SCHEMA
from scripts import clear_shadow_ai_data


class ClearShadowAiDataScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                "INSERT INTO watchlist (code, name, group_name) VALUES (?, ?, ?)",
                ("000001", "平安银行", "默认"),
            )
            conn.execute(
                "INSERT INTO analysis_reports (id, code, task_id, signal) VALUES (?, ?, ?, ?)",
                (1, "000001", "task-1", "BUY"),
            )
            conn.execute(
                """
                INSERT INTO signal_tracking
                    (report_id, code, name, signal, signal_date, entry_price)
                VALUES
                    (?, ?, ?, ?, ?, ?)
                """,
                (1, "000001", "平安银行", "BUY", "2026-06-03", 10.0),
            )
            conn.execute(
                """
                INSERT INTO ai_shadow_orders
                    (report_id, code, name, action, signal, fill_price, shares, status)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (1, "000001", "平安银行", "buy", "BUY", 10.0, 100, "filled"),
            )
            conn.execute(
                """
                INSERT INTO ai_shadow_positions
                    (code, name, total_shares, avg_cost, market_value)
                VALUES
                    (?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", 100, 10.0, 1000.0),
            )
            conn.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def _count(self, table):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_dry_run_reports_counts_without_deleting_rows(self):
        summary = clear_shadow_ai_data.clear_shadow_ai_data(self.db_path, apply=False)

        self.assertEqual(summary["deleted"], False)
        self.assertEqual(summary["tables"]["signal_tracking"], 1)
        self.assertEqual(summary["tables"]["ai_shadow_orders"], 1)
        self.assertEqual(summary["tables"]["ai_shadow_positions"], 1)
        self.assertEqual(self._count("signal_tracking"), 1)
        self.assertEqual(self._count("ai_shadow_orders"), 1)
        self.assertEqual(self._count("ai_shadow_positions"), 1)
        self.assertEqual(self._count("analysis_reports"), 1)
        self.assertEqual(self._count("watchlist"), 1)

    def test_apply_deletes_shadow_tables_and_preserves_reports_and_watchlist(self):
        summary = clear_shadow_ai_data.clear_shadow_ai_data(self.db_path, apply=True)

        self.assertEqual(summary["deleted"], True)
        for table in clear_shadow_ai_data.AI_PERFORMANCE_TABLES:
            self.assertEqual(self._count(table), 0, table)
        self.assertEqual(self._count("analysis_reports"), 1)
        self.assertEqual(self._count("watchlist"), 1)


if __name__ == "__main__":
    unittest.main()
