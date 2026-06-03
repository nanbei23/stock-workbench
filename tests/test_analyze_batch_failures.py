import sqlite3
import tempfile
import unittest
from pathlib import Path

from models.database import SCHEMA
from scripts import analyze_batch_failures


class AnalyzeBatchFailuresScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            conn.execute(
                """
                INSERT INTO batch_jobs
                    (job_id, job_type, name, status, total_count, completed_count, failed_count, skipped_count, waiting_count, created_at)
                VALUES
                    ('old-job', 'report_generation', 'old', 'completed', 1, 1, 0, 0, 0, '2026-06-01 10:00:00'),
                    ('job-125', 'report_generation', '125 reports', 'completed', 4, 2, 2, 0, 0, '2026-06-03 09:00:00')
                """
            )
            conn.execute(
                """
                INSERT INTO batch_job_items
                    (id, job_id, code, name, status, error, error_type, retry_count, report_id, created_at)
                VALUES
                    (1, 'job-125', '000001', '平安银行', 'completed', '', '', 0, 101, '2026-06-03 09:00:01'),
                    (2, 'job-125', '300620', '光库科技', 'failed', 'ProxyError Max retries exceeded push2.eastmoney.com', 'network', 2, NULL, '2026-06-03 09:00:02'),
                    (3, 'job-125', '688498', '源杰科技', 'quota_paused', 'quota exceeded for mimo-v2.5-pro', 'quota_exhausted', 1, NULL, '2026-06-03 09:00:03'),
                    (4, 'job-125', '601138', '工业富联', 'completed', '', '', 0, 102, '2026-06-03 09:00:04')
                """
            )
            conn.execute(
                """
                INSERT INTO batch_job_item_steps
                    (item_id, job_id, role_key, role_name, step_order, status, error, error_type, duration_ms, retry_count)
                VALUES
                    (2, 'job-125', 'market', '市场分析师', 1, 'failed', 'eastmoney connection reset', 'network', 120000, 2),
                    (3, 'job-125', 'trader', '交易员', 7, 'quota_paused', 'quota exceeded', 'quota_exhausted', 30000, 1)
                """
            )
            conn.execute(
                """
                INSERT INTO batch_job_logs
                    (job_id, item_id, level, event, message, data_json, created_at)
                VALUES
                    ('job-125', 2, 'error', 'role_failed', '市场分析师失败', '{"provider":"mimo"}', '2026-06-03 09:05:00'),
                    ('job-125', 3, 'warning', 'quota_paused', '额度耗尽，等待切换模型', '{}', '2026-06-03 09:06:00')
                """
            )
            conn.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def test_analyze_latest_batch_job_groups_failures_and_logs(self):
        report = analyze_batch_failures.analyze_batch_failures(self.db_path)

        self.assertEqual(report["job"]["job_id"], "job-125")
        self.assertEqual(report["summary"]["total"], 4)
        self.assertEqual(report["summary"]["failed_like"], 2)
        self.assertEqual(report["status_counts"]["completed"], 2)
        self.assertEqual(report["failure_groups"]["network"]["count"], 1)
        self.assertEqual(report["failure_groups"]["quota_exhausted"]["count"], 1)
        self.assertEqual(report["step_failure_groups"]["market"]["count"], 1)
        self.assertEqual(report["step_failure_groups"]["trader"]["count"], 1)
        self.assertEqual(len(report["logs"]), 2)

    def test_markdown_report_contains_actionable_failure_summary(self):
        report = analyze_batch_failures.analyze_batch_failures(self.db_path, job_id="job-125")
        markdown = analyze_batch_failures.render_markdown(report)

        self.assertIn("# 批量任务失败诊断", markdown)
        self.assertIn("job-125", markdown)
        self.assertIn("network", markdown)
        self.assertIn("quota_exhausted", markdown)
        self.assertIn("300620", markdown)
        self.assertIn("688498", markdown)
        self.assertIn("市场分析师", markdown)


if __name__ == "__main__":
    unittest.main()
