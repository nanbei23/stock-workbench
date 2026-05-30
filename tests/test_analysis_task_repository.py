import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from models import database
from repositories import analysis_task_repository
from tasks import AnalysisTask


class AnalysisTaskRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)

    def tearDown(self):
        self.tmp.cleanup()

    def test_persist_task_snapshot_upserts_serialized_payload(self):
        task = AnalysisTask(
            task_id="task-1",
            code="000001",
            name="平安银行",
            status="running",
            stages={"market": {"status": "completed"}},
            result={"signal": "hold"},
            depth="deep",
            selected_analysts=["market"],
        )
        task.token_stats = {"total_tokens": 100}

        analysis_task_repository.persist_task_snapshot(task, "running", self.db_path)

        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                """
                SELECT code, status, queue_status, selected_analysts, stages, payload
                FROM analysis_tasks
                WHERE task_id = ?
                """,
                ("task-1",),
            ).fetchone()

        self.assertEqual(row[0], "000001")
        self.assertEqual(row[1], "running")
        self.assertEqual(row[2], "running")
        self.assertEqual(json.loads(row[3]), ["market"])
        self.assertEqual(json.loads(row[4])["market"]["status"], "completed")
        self.assertEqual(json.loads(row[5])["token_stats"], {"total_tokens": 100})

    def test_update_task_status_sets_terminal_completed_at(self):
        task = AnalysisTask("task-2", "600000", "浦发银行", status="running")
        analysis_task_repository.persist_task_snapshot(task, "running", self.db_path)

        analysis_task_repository.update_task_status(
            "task-2",
            "failed",
            "provider failed",
            self.db_path,
            now="2026-05-29T12:00:00",
        )

        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT status, error, completed_at FROM analysis_tasks WHERE task_id = ?",
                ("task-2",),
            ).fetchone()

        self.assertEqual(row, ("failed", "provider failed", "2026-05-29T12:00:00"))

    def test_mark_interrupted_marks_unfinished_tasks_only(self):
        running = AnalysisTask("task-running", "000001", "平安银行", status="running")
        done = AnalysisTask("task-done", "600000", "浦发银行", status="completed")
        analysis_task_repository.persist_task_snapshot(running, "running", self.db_path)
        analysis_task_repository.persist_task_snapshot(done, "completed", self.db_path)

        analysis_task_repository.mark_interrupted(self.db_path, now="2026-05-29T12:30:00")

        with sqlite3.connect(self.db_path) as db:
            rows = {
                row[0]: row[1:]
                for row in db.execute(
                    """
                    SELECT task_id, status, queue_status, error, completed_at
                    FROM analysis_tasks
                    ORDER BY task_id
                    """
                ).fetchall()
            }

        self.assertEqual(
            rows["task-running"],
            ("failed", "interrupted", "服务重启，任务已中断", "2026-05-29T12:30:00"),
        )
        self.assertEqual(rows["task-done"], ("completed", "completed", None, None))


if __name__ == "__main__":
    unittest.main()
