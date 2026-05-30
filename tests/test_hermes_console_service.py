import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import models.database as database
from api.hermes_api import router as hermes_router
from services import hermes_console_service


class HermesConsoleServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)
            db.execute("INSERT OR IGNORE INTO accounts (id, name) VALUES ('default', '默认账户')")
            db.commit()

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        hermes_console_service._DRAFTS.clear()
        self.tmp.cleanup()

    async def test_add_watchlist_requires_confirmation_then_writes(self):
        parsed = await hermes_console_service.handle_message("新增 600519 贵州茅台 到自选")

        self.assertIn("draft", parsed)
        draft = parsed["draft"]
        self.assertTrue(draft["executable"])
        self.assertEqual(draft["action"], "add_watchlist")

        result = await hermes_console_service.confirm_draft(parsed["session_id"], draft["id"])

        self.assertEqual(result["status"], "ok")
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT code, name FROM watchlist WHERE code='600519'").fetchone()
        self.assertEqual(row[0], "600519")
        self.assertEqual(row[1], "贵州茅台")

    async def test_query_position_answers_without_draft(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO portfolio (code, name, total_shares, available_shares, avg_cost)
                VALUES ('000001', '平安银行', 500, 400, 10.25)
                """
            )
            db.commit()

        result = await hermes_console_service.handle_message("平安银行今天持仓多少")

        self.assertNotIn("draft", result)
        self.assertIn("500 股", result["answer"])
        self.assertEqual(result["result"]["code"], "000001")

    async def test_record_trade_draft_confirmation_updates_portfolio(self):
        parsed = await hermes_console_service.handle_message("买入 000001 平安银行 2手 成交价 10.5")
        draft = parsed["draft"]

        self.assertEqual(draft["payload"]["shares"], 200)
        self.assertEqual(draft["payload"]["price"], 10.5)
        await hermes_console_service.confirm_draft(parsed["session_id"], draft["id"])

        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT total_shares, avg_cost FROM portfolio WHERE code='000001'").fetchone()
        self.assertEqual(row[0], 200)
        self.assertEqual(row[1], 10.5)

    async def test_list_sessions_summarizes_history(self):
        first = await hermes_console_service.handle_message("查询 000001 持仓")
        second = await hermes_console_service.handle_message("新增 600519 贵州茅台 到自选")

        listing = await hermes_console_service.list_sessions()
        session_ids = [item["session_id"] for item in listing["sessions"]]

        self.assertIn(first["session_id"], session_ids)
        self.assertIn(second["session_id"], session_ids)
        draft_session = next(item for item in listing["sessions"] if item["session_id"] == second["session_id"])
        self.assertEqual(draft_session["draft_count"], 1)
        self.assertEqual(draft_session["last_draft"]["action"], "add_watchlist")

    async def test_list_sessions_keeps_older_sessions_when_latest_is_chatty(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO hermes_console_events (session_id, role, message) VALUES (?, ?, ?)",
                ("older", "user", "查询 000001 持仓"),
            )
            db.executemany(
                "INSERT INTO hermes_console_events (session_id, role, message) VALUES (?, ?, ?)",
                [("chatty", "assistant", f"消息 {idx}") for idx in range(20)],
            )
            db.commit()

        listing = await hermes_console_service.list_sessions(limit=2)
        session_ids = [item["session_id"] for item in listing["sessions"]]

        self.assertEqual(session_ids, ["chatty", "older"])
        self.assertEqual(listing["sessions"][0]["message_count"], 20)

    async def test_llm_parser_generates_intent_before_rule_fallback(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": '{"action":"record_trade","code":"000001","name":"平安银行","direction":"buy","shares":200,"price":10.5}'
                            }
                        }
                    ]
                }

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def post(self, *args, **kwargs):
                return FakeResponse()

        async def fake_settings():
            return {
                "custom_endpoint": "https://example.com/v1",
                "api_key": "sk-test",
                "quick_think_model": "test-model",
            }

        with patch("services.hermes_console_service._llm_settings", new=fake_settings), patch(
            "services.hermes_console_service.httpx.AsyncClient", new=FakeClient
        ):
            parsed = await hermes_console_service.handle_message("帮我把平安银行今天买两手，十块五附近")

        self.assertEqual(parsed["parser"], "llm")
        self.assertEqual(parsed["draft"]["parser"], "llm")
        self.assertEqual(parsed["draft"]["action"], "record_trade")
        self.assertEqual(parsed["draft"]["payload"]["shares"], 200)
        self.assertEqual(parsed["draft"]["payload"]["price"], 10.5)

    async def test_common_stock_alias_fills_missing_code(self):
        parsed = await hermes_console_service.handle_message("帮我把平安银行今天买两手，十块五附近记到账上")

        self.assertEqual(parsed["parser"], "rules")
        self.assertEqual(parsed["draft"]["payload"]["code"], "000001")
        self.assertEqual(parsed["draft"]["payload"]["name"], "平安银行")
        self.assertTrue(parsed["draft"]["executable"])


class HermesConsoleApiTests(unittest.TestCase):
    def test_message_route_uses_service(self):
        app = FastAPI()
        app.include_router(hermes_router, prefix="/api")
        client = TestClient(app)

        async def fake_handle(message, session_id=None):
            return {"session_id": session_id or "s1", "answer": f"ok:{message}"}

        original = hermes_console_service.handle_message
        hermes_console_service.handle_message = fake_handle
        try:
            resp = client.post("/api/hermes/message", json={"message": "查询 000001 持仓"})
        finally:
            hermes_console_service.handle_message = original

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["answer"], "ok:查询 000001 持仓")

    def test_sessions_route_uses_service(self):
        app = FastAPI()
        app.include_router(hermes_router, prefix="/api")
        client = TestClient(app)

        async def fake_list(limit=50):
            return {"count": 1, "sessions": [{"session_id": "s1", "title": "测试"}]}

        original = hermes_console_service.list_sessions
        hermes_console_service.list_sessions = fake_list
        try:
            resp = client.get("/api/hermes/sessions?limit=10")
        finally:
            hermes_console_service.list_sessions = original

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["sessions"][0]["session_id"], "s1")


if __name__ == "__main__":
    unittest.main()
