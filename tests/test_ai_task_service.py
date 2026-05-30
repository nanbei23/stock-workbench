import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import models.database as database
import tasks
from api.ai_api import router as ai_router
from scheduler.ta_bridge import PIPELINE_STAGES
from services import ai_task_service
from tasks import AnalysisTask


class AiTaskServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        self.original_tasks_db_path = tasks.DB_PATH
        database.DB_PATH = self.db_path
        tasks.DB_PATH = self.db_path
        tasks._tasks.clear()
        tasks._tasks_status.clear()
        tasks._queue.clear()
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        tasks.DB_PATH = self.original_tasks_db_path
        tasks._tasks.clear()
        tasks._tasks_status.clear()
        tasks._queue.clear()
        self.tmp.cleanup()

    async def test_status_falls_back_to_persisted_task(self):
        task = AnalysisTask(
            task_id="task-db-1",
            code="600519",
            name="贵州茅台",
            status="completed",
            elapsed=12.5,
            result={"signal": "buy"},
            stages={"market": {"status": "completed", "report": "ok"}},
        )
        await tasks.persist_task(task, "completed")
        tasks._tasks.clear()

        status = await ai_task_service.get_analysis_status("task-db-1", PIPELINE_STAGES)
        result = await ai_task_service.get_analysis_result("task-db-1")

        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["progress"], f"1/{len(PIPELINE_STAGES)}")
        self.assertEqual(status["stages"]["market"]["name"], "技术分析")
        self.assertEqual(result["result"], {"signal": "buy"})

    async def test_active_task_falls_back_to_persisted_pending_task(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO analysis_tasks (
                    task_id, code, name, status, queue_status, stages, depth, selected_analysts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "task-pending",
                    "000001",
                    "平安银行",
                    "pending",
                    "queued",
                    json.dumps({"market": {"status": "pending"}}, ensure_ascii=False),
                    "deep",
                    json.dumps(["market"], ensure_ascii=False),
                ),
            )
            db.commit()

        active = await ai_task_service.get_active_task(PIPELINE_STAGES)

        self.assertEqual(active["task_id"], "task-pending")
        self.assertEqual(active["status"], "pending")
        self.assertEqual(active["depth"], "deep")
        self.assertEqual(active["selected_analysts"], ["market"])


class AiTaskApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        self.original_tasks_db_path = tasks.DB_PATH
        database.DB_PATH = self.db_path
        tasks.DB_PATH = self.db_path
        tasks._tasks.clear()
        tasks._tasks_status.clear()
        tasks._queue.clear()
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)
            db.execute(
                """
                INSERT INTO analysis_tasks (
                    task_id, code, name, status, error, stages, result, elapsed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "task-failed",
                    "600000",
                    "浦发银行",
                    "failed",
                    "provider failed",
                    json.dumps({"market": {"status": "completed"}}, ensure_ascii=False),
                    json.dumps({"signal": "hold"}, ensure_ascii=False),
                    3.0,
                ),
            )
            db.commit()
        app = FastAPI()
        app.include_router(ai_router, prefix="/api")
        self.client = TestClient(app)

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        tasks.DB_PATH = self.original_tasks_db_path
        tasks._tasks.clear()
        tasks._tasks_status.clear()
        tasks._queue.clear()
        self.tmp.cleanup()

    def test_status_route_uses_persisted_task_when_memory_empty(self):
        resp = self.client.get("/api/ai/analyze/task-failed/status")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error"], "provider failed")
        self.assertEqual(body["code"], "600000")

    def test_result_route_uses_persisted_task_when_memory_empty(self):
        resp = self.client.get("/api/ai/analyze/task-failed/result")

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error"], "provider failed")


if __name__ == "__main__":
    unittest.main()
