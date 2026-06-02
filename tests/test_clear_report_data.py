import sqlite3
import tempfile
import unittest
from pathlib import Path

from models.database import SCHEMA
from scripts import clear_report_data


class ClearReportDataScriptTests(unittest.TestCase):
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
                "INSERT INTO stock_data_snapshots (code, name, snapshot_json) VALUES (?, ?, ?)",
                ("000001", "平安银行", "{}"),
            )
            conn.execute(
                "INSERT INTO analysis_reports (code, task_id, signal) VALUES (?, ?, ?)",
                ("000001", "task-1", "BUY"),
            )
            conn.execute(
                "INSERT INTO analysis_tasks (task_id, code, name, status) VALUES (?, ?, ?, ?)",
                ("task-1", "000001", "平安银行", "completed"),
            )
            conn.execute(
                "INSERT INTO analysis_progress (task_id, code, stage_id, report_text) VALUES (?, ?, ?, ?)",
                ("task-1", "000001", "market", "report"),
            )
            conn.execute(
                """
                INSERT INTO signal_tracking
                    (report_id, code, name, signal, signal_date, entry_price)
                VALUES
                    (?, ?, ?, ?, ?, ?)
                """,
                (1, "000001", "平安银行", "BUY", "2026-06-02", 10.0),
            )
            conn.execute(
                "INSERT INTO batch_report_jobs (job_id, name) VALUES (?, ?)",
                ("job-1", "report job"),
            )
            conn.execute(
                "INSERT INTO batch_report_items (job_id, code, status) VALUES (?, ?, ?)",
                ("job-1", "000001", "completed"),
            )
            conn.execute(
                "INSERT INTO batch_jobs (job_id, job_type, name) VALUES (?, ?, ?)",
                ("old-job-1", "research", "old report job"),
            )
            conn.execute(
                "INSERT INTO batch_job_items (job_id, code, status) VALUES (?, ?, ?)",
                ("old-job-1", "000001", "completed"),
            )
            conn.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def _count(self, table):
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_dry_run_reports_counts_without_deleting_rows(self):
        summary = clear_report_data.clear_report_data(self.db_path, apply=False)

        self.assertEqual(summary["deleted"], False)
        self.assertEqual(summary["tables"]["analysis_reports"], 1)
        self.assertEqual(self._count("analysis_reports"), 1)
        self.assertEqual(self._count("analysis_tasks"), 1)
        self.assertEqual(self._count("signal_tracking"), 1)
        self.assertEqual(self._count("stock_data_snapshots"), 1)
        self.assertEqual(self._count("watchlist"), 1)

    def test_apply_deletes_report_tables_and_preserves_snapshots_and_watchlist(self):
        summary = clear_report_data.clear_report_data(self.db_path, apply=True)

        self.assertEqual(summary["deleted"], True)
        for table in clear_report_data.REPORT_TABLES:
            self.assertEqual(self._count(table), 0, table)
        self.assertEqual(self._count("stock_data_snapshots"), 1)
        self.assertEqual(self._count("watchlist"), 1)


if __name__ == "__main__":
    unittest.main()
