import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import models.database as database
import tasks
from api.ai_api import router as ai_router
from services import ai_analysis_service
from tasks import AnalysisTask


class AiAnalysisServiceTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_start_analysis_creates_and_persists_task(self):
        with (
            patch("services.ai_analysis_service.get_stock_name", return_value="平安银行"),
            patch("services.ai_analysis_service.run_with_limits", new=AsyncMock()),
        ):
            result = await ai_analysis_service.start_analysis(
                "000001",
                trade_date="2026-05-29",
                depth="deep",
                selected_analysts=["market"],
            )
            await asyncio.sleep(0)

        self.assertEqual(result["status"], "pending")
        self.assertEqual(len(tasks._tasks), 1)
        task = next(iter(tasks._tasks.values()))
        self.assertEqual(task.code, "000001")
        self.assertEqual(task.depth, "deep")
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT code, name, status, depth FROM analysis_tasks").fetchone()
        self.assertEqual(row, ("000001", "平安银行", "pending", "deep"))

    async def test_start_analysis_reuses_active_task_for_same_code(self):
        tasks._tasks["active"] = AnalysisTask(
            task_id="active",
            code="000001",
            name="平安银行",
            status="running",
        )

        result = await ai_analysis_service.start_analysis("000001")

        self.assertEqual(result["task_id"], "active")
        self.assertEqual(result["status"], "running")

    async def test_cancel_analysis_marks_task_and_cancel_event(self):
        tasks._tasks["task-1"] = AnalysisTask(
            task_id="task-1",
            code="000001",
            name="平安银行",
            status="running",
        )
        tasks._tasks_status["task-1"] = {"status": "running", "cancel": asyncio.Event()}

        result = await ai_analysis_service.cancel_analysis("task-1")

        self.assertEqual(result["status"], "ok")
        self.assertTrue(tasks._tasks_status["task-1"]["cancel"].is_set())
        self.assertEqual(tasks._tasks["task-1"].status, "failed")
        self.assertEqual(tasks._tasks["task-1"].error, "用户取消")

    async def test_trigger_l2_returns_none_when_queue_full(self):
        for i in range(tasks.MAX_CONCURRENT):
            tasks._tasks_status[f"running-{i}"] = {"status": "running", "cancel": asyncio.Event()}
        for i in range(tasks.MAX_QUEUE):
            tasks._tasks_status[f"queued-{i}"] = {"status": "queued", "cancel": asyncio.Event()}

        result = await ai_analysis_service.trigger_l2_for_stock("000001", "2026-05-29")

        self.assertIsNone(result)

    async def test_start_analysis_rejects_when_queue_full(self):
        for i in range(tasks.MAX_CONCURRENT):
            tasks._tasks_status[f"running-{i}"] = {"status": "running", "cancel": asyncio.Event()}
        for i in range(tasks.MAX_QUEUE):
            tasks._tasks_status[f"queued-{i}"] = {"status": "queued", "cancel": asyncio.Event()}

        with self.assertRaises(Exception) as ctx:
            await ai_analysis_service.start_analysis("000001", trade_date="2026-05-29")

        self.assertEqual(ctx.exception.status_code, 429)
        self.assertEqual(tasks._tasks, {})

    async def test_run_with_limits_defaults_to_one_hour_timeout(self):
        captured = {}

        async def fake_wait_for(coro, timeout):
            captured["timeout"] = timeout
            return await coro

        async def work(_task_id):
            return None

        with patch("tasks.asyncio.wait_for", new=fake_wait_for):
            await tasks.run_with_limits("task-timeout-default", work)

        self.assertEqual(captured["timeout"], 3600)
        self.assertEqual(tasks._tasks_status["task-timeout-default"]["status"], "completed")

    async def test_run_with_limits_timeout_message_uses_one_hour(self):
        task = AnalysisTask(
            task_id="task-timeout-message",
            code="000001",
            name="平安银行",
            status="pending",
        )
        tasks._tasks[task.task_id] = task

        async def fake_wait_for(coro, timeout):
            coro.close()
            raise asyncio.TimeoutError()

        async def work(_task_id):
            return None

        with patch("tasks.asyncio.wait_for", new=fake_wait_for):
            await tasks.run_with_limits(task.task_id, work)

        self.assertEqual(tasks._tasks_status[task.task_id]["status"], "timeout")
        self.assertEqual(tasks._tasks[task.task_id].error, "分析超时（1小时）")


class AiAnalysisApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(ai_router, prefix="/api")
        self.client = TestClient(app)

    def test_start_analysis_route_uses_service_layer(self):
        with patch(
            "services.ai_analysis_service.start_analysis",
            new=AsyncMock(return_value={"task_id": "abc123", "status": "pending"}),
        ) as start_analysis:
            resp = self.client.post("/api/ai/analyze/000001", json={"depth": "deep"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["task_id"], "abc123")
        start_analysis.assert_awaited_once()

    def test_generate_cond_order_route_is_removed(self):
        resp = self.client.post(
            "/api/ai/generate-cond-order",
            json={
                "code": "000001",
                "name": "平安银行",
                "action": "buy",
                "price": 10.5,
                "shares": 100,
            },
        )

        self.assertEqual(resp.status_code, 404)

    def test_gbrain_save_route_accepts_json_body(self):
        with patch(
            "api.ai_api.gbrain_api_save",
            new=AsyncMock(return_value={"status": "ok"}),
        ) as save:
            resp = self.client.post(
                "/api/ai/gbrain/save",
                json={"slug": "deep-analysis/000001", "title": "标题", "content": "内容"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")
        save.assert_awaited_once_with("deep-analysis/000001", "标题", "内容")


if __name__ == "__main__":
    unittest.main()
