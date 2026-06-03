import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import models.database as database
from api.hermes_api import router as hermes_router
from repositories import settings_repository
from services import hermes_console_service


class HermesConsoleServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        self.original_settings_path = settings_repository.DB_PATH
        database.DB_PATH = self.db_path
        settings_repository.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)
            db.execute("INSERT OR IGNORE INTO accounts (id, name) VALUES ('default', '默认账户')")
            db.commit()
        self.quote_patcher = patch(
            "services.portfolio_service.get_batch_quotes",
            new=AsyncMock(return_value={
                "000001": {"name": "平安银行", "price": 10.5},
                "600519": {"name": "贵州茅台", "price": 1500.0},
            }),
        )
        self.quote_patcher.start()

    def tearDown(self):
        self.quote_patcher.stop()
        database.DB_PATH = self.original_db_path
        settings_repository.DB_PATH = self.original_settings_path
        hermes_console_service._DRAFTS.clear()
        self.tmp.cleanup()

    async def _make_llm_multi_step_plan(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT OR REPLACE INTO portfolio (code, name, total_shares, available_shares, avg_cost)
                VALUES ('000001', '平安银行', 500, 400, 10.25)
                """
            )
            db.commit()

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"plan":{"title":"茅台观察计划","steps":['
                                    '{"title":"先查平安银行持仓","action":"query_position","code":"000001","name":"平安银行"},'
                                    '{"title":"加入茅台自选","tool":"add_watchlist","args":{"code":"600519","name":"贵州茅台"}},'
                                    '{"title":"创建茅台条件单","tool":"create_conditional_order","args":{"code":"600519","name":"贵州茅台",'
                                    '"trade_action":"buy","condition_type":"price_lte","target_price":1680,"shares":100}}'
                                    ']}}'
                                )
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
            return await hermes_console_service.handle_message("查平安银行持仓，把茅台加自选，并低于1680建100股买入条件单")

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
        self.assertEqual(draft["risk_level"], "medium")
        self.assertEqual(draft["impact_preview"]["status"], "ready")
        self.assertIn("增加到 200 股", draft["impact_preview"]["summary"])
        await hermes_console_service.confirm_draft(parsed["session_id"], draft["id"])

        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT total_shares, avg_cost FROM portfolio WHERE code='000001'").fetchone()
        self.assertEqual(row[0], 200)
        self.assertEqual(row[1], 10.5)

    async def test_undo_last_tool_run_reverts_recent_trade(self):
        parsed = await hermes_console_service.handle_message("买入 000001 平安银行 2手 成交价 10.5")
        draft = parsed["draft"]
        await hermes_console_service.confirm_draft(parsed["session_id"], draft["id"])

        result = await hermes_console_service.undo_last_tool_run(parsed["session_id"])

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["tool"], "record_trade")
        with sqlite3.connect(self.db_path) as db:
            trade_count = db.execute("SELECT COUNT(*) FROM trades WHERE code='000001'").fetchone()[0]
            position = db.execute("SELECT total_shares FROM portfolio WHERE code='000001'").fetchone()
            undo_run = db.execute("SELECT status FROM hermes_tool_runs WHERE status='undone'").fetchone()
        self.assertEqual(trade_count, 0)
        self.assertIsNone(position)
        self.assertEqual(undo_run[0], "undone")

    async def test_disabled_hermes_tool_blocks_draft_execution(self):
        settings_repository.upsert_settings({"hermes_tool_policy": '{"record_trade":"disabled"}'})

        parsed = await hermes_console_service.handle_message("买入 000001 平安银行 2手 成交价 10.5")
        draft = parsed["draft"]

        self.assertFalse(draft["executable"])
        self.assertIn("record_trade", " ".join(draft["blockers"]))

        with self.assertRaises(HTTPException) as ctx:
            await hermes_console_service.confirm_draft(parsed["session_id"], draft["id"])

        self.assertEqual(ctx.exception.status_code, 400)

    async def test_confirm_recovers_unexecuted_draft_from_history_after_restart(self):
        parsed = await hermes_console_service.handle_message("新增 600519 贵州茅台 到自选")
        draft_id = parsed["draft"]["id"]
        hermes_console_service._DRAFTS.clear()

        result = await hermes_console_service.confirm_draft(parsed["session_id"], draft_id)

        self.assertEqual(result["status"], "ok")
        with sqlite3.connect(self.db_path) as db:
            stock = db.execute("SELECT code, name FROM watchlist WHERE code='600519'").fetchone()
            audit = db.execute(
                "SELECT status FROM hermes_tool_runs WHERE session_id=? AND draft_id=?",
                (parsed["session_id"], draft_id),
            ).fetchone()
        self.assertEqual(stock, ("600519", "贵州茅台"))
        self.assertEqual(audit[0], "ok")

    async def test_confirm_rejects_duplicate_draft_execution(self):
        parsed = await hermes_console_service.handle_message("新增 600519 贵州茅台 到自选")
        draft_id = parsed["draft"]["id"]
        await hermes_console_service.confirm_draft(parsed["session_id"], draft_id)

        with self.assertRaises(HTTPException) as ctx:
            await hermes_console_service.confirm_draft(parsed["session_id"], draft_id)

        self.assertEqual(ctx.exception.status_code, 409)
        with sqlite3.connect(self.db_path) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM hermes_tool_runs WHERE session_id=? AND draft_id=? AND status='ok'",
                (parsed["session_id"], draft_id),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    async def test_cancel_draft_persists_and_blocks_later_confirmation(self):
        parsed = await hermes_console_service.handle_message("新增 600519 贵州茅台 到自选")
        draft_id = parsed["draft"]["id"]

        result = await hermes_console_service.cancel_draft(parsed["session_id"], draft_id)

        self.assertEqual(result["status"], "cancelled")
        with sqlite3.connect(self.db_path) as db:
            stock = db.execute("SELECT code FROM watchlist WHERE code='600519'").fetchone()
            audit = db.execute(
                "SELECT status FROM hermes_tool_runs WHERE session_id=? AND draft_id=?",
                (parsed["session_id"], draft_id),
            ).fetchone()
        self.assertIsNone(stock)
        self.assertEqual(audit[0], "cancelled")

        with self.assertRaises(HTTPException) as ctx:
            await hermes_console_service.confirm_draft(parsed["session_id"], draft_id)
        self.assertEqual(ctx.exception.status_code, 409)

    async def test_cancel_recovers_draft_from_history_after_restart(self):
        parsed = await hermes_console_service.handle_message("新增 600519 贵州茅台 到自选")
        draft_id = parsed["draft"]["id"]
        hermes_console_service._DRAFTS.clear()

        result = await hermes_console_service.cancel_draft(parsed["session_id"], draft_id)

        self.assertEqual(result["status"], "cancelled")
        runs = await hermes_console_service.list_tool_runs(parsed["session_id"])
        self.assertEqual(runs["runs"][0]["status"], "cancelled")

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

    async def test_llm_tool_call_generates_auditable_draft_and_confirm_writes(self):
        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"tool":"record_trade","args":{"code":"000001","name":"平安银行",'
                                    '"direction":"buy","shares":200,"price":10.5},'
                                    '"confidence":0.92,"reason":"用户明确要求买入两手"}'
                                )
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
            parsed = await hermes_console_service.handle_message("帮我买入平安银行两手，成交价十块五")

        draft = parsed["draft"]
        self.assertEqual(parsed["parser"], "llm")
        self.assertEqual(draft["tool_call"]["tool"], "record_trade")
        self.assertEqual(draft["tool_call"]["args"]["shares"], 200)

        await hermes_console_service.confirm_draft(parsed["session_id"], draft["id"])

        with sqlite3.connect(self.db_path) as db:
            trade = db.execute("SELECT code, direction, shares, price FROM trades WHERE code='000001'").fetchone()
            audit_count = db.execute(
                "SELECT COUNT(*) FROM hermes_tool_runs WHERE session_id=?",
                (parsed["session_id"],),
            ).fetchone()[0]
            audit = db.execute(
                "SELECT tool, status, args_json FROM hermes_tool_runs WHERE session_id=? AND status='ok'",
                (parsed["session_id"],),
            ).fetchone()
        self.assertEqual(trade, ("000001", "buy", 200, 10.5))
        self.assertEqual(audit_count, 1)
        self.assertEqual(audit[0], "record_trade")
        self.assertEqual(audit[1], "ok")
        self.assertIn('"shares": 200', audit[2])

        runs = await hermes_console_service.list_tool_runs(parsed["session_id"])
        self.assertEqual(runs["count"], 1)
        self.assertEqual(runs["runs"][0]["tool"], "record_trade")
        self.assertEqual(runs["runs"][0]["args"]["shares"], 200)

    async def test_llm_plan_generates_multi_step_draft_and_confirms_write_steps(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO portfolio (code, name, total_shares, available_shares, avg_cost)
                VALUES ('000001', '平安银行', 500, 400, 10.25)
                """
            )
            db.commit()

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"plan":{"title":"茅台观察计划","steps":['
                                    '{"title":"先查平安银行持仓","action":"query_position","code":"000001","name":"平安银行"},'
                                    '{"title":"加入茅台自选","tool":"add_watchlist","args":{"code":"600519","name":"贵州茅台"}},'
                                    '{"title":"创建茅台条件单","tool":"create_conditional_order","args":{"code":"600519","name":"贵州茅台",'
                                    '"trade_action":"buy","condition_type":"price_lte","target_price":1680,"shares":100}}'
                                    ']}}'
                                )
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
            parsed = await hermes_console_service.handle_message("查平安银行持仓，把茅台加自选，并低于1680建100股买入条件单")

        draft = parsed["draft"]
        self.assertEqual(draft["action"], "multi_step_plan")
        self.assertTrue(draft["executable"])
        self.assertEqual(len(draft["plan_steps"]), 3)
        self.assertEqual(draft["plan_steps"][0]["kind"], "read")
        self.assertIn("500 股", draft["plan_steps"][0]["summary"])
        self.assertEqual(draft["plan_steps"][1]["tool_call"]["tool"], "add_watchlist")
        self.assertEqual(draft["plan_steps"][1]["impact_preview"]["status"], "ready")
        self.assertIn("默认自选", draft["plan_steps"][1]["impact_preview"]["summary"])
        self.assertEqual(draft["plan_steps"][2]["impact_preview"]["status"], "ready")
        self.assertIn("待执行条件单", draft["plan_steps"][2]["impact_preview"]["summary"])

        result = await hermes_console_service.confirm_draft(parsed["session_id"], draft["id"])

        self.assertEqual(result["status"], "ok")
        with sqlite3.connect(self.db_path) as db:
            watch = db.execute("SELECT code, name FROM watchlist WHERE code='600519'").fetchone()
            order = db.execute("SELECT code, action, shares, target_price FROM conditional_orders WHERE code='600519'").fetchone()
            runs = db.execute(
                "SELECT draft_id, tool, status FROM hermes_tool_runs WHERE session_id=? ORDER BY id",
                (parsed["session_id"],),
            ).fetchall()
        self.assertEqual(watch, ("600519", "贵州茅台"))
        self.assertEqual(order, ("600519", "buy", 100, 1680.0))
        self.assertEqual(len(runs), 2)
        self.assertTrue(runs[0][0].endswith(":step-2"))
        self.assertEqual(runs[0][1:], ("add_watchlist", "ok"))
        self.assertTrue(runs[1][0].endswith(":step-3"))
        self.assertEqual(runs[1][1:], ("create_conditional_order", "ok"))

    async def test_llm_plan_persists_task_timeline(self):
        parsed = await self._make_llm_multi_step_plan()
        draft = parsed["draft"]

        with sqlite3.connect(self.db_path) as db:
            task = db.execute(
                "SELECT session_id, status, title FROM hermes_tasks WHERE task_id=?",
                (draft["id"],),
            ).fetchone()
            steps = db.execute(
                "SELECT step_id, kind, status FROM hermes_task_steps WHERE task_id=? ORDER BY position",
                (draft["id"],),
            ).fetchall()

        self.assertEqual(task, (parsed["session_id"], "waiting_confirm", "茅台观察计划"))
        self.assertEqual(
            steps,
            [
                ("step-1", "read", "done"),
                ("step-2", "write", "ready"),
                ("step-3", "write", "ready"),
            ],
        )

    async def test_list_tasks_returns_persisted_task_with_steps(self):
        parsed = await self._make_llm_multi_step_plan()
        draft = parsed["draft"]

        listing = await hermes_console_service.list_tasks(limit=10)
        task = next(item for item in listing["tasks"] if item["task_id"] == draft["id"])

        self.assertEqual(task["session_id"], parsed["session_id"])
        self.assertEqual(task["status"], "waiting_confirm")
        self.assertEqual(task["write_total"], 2)
        self.assertEqual(task["write_done"], 0)
        self.assertEqual([step["step_id"] for step in task["steps"]], ["step-1", "step-2", "step-3"])

    async def test_confirm_plan_step_keeps_plan_active_for_remaining_steps(self):
        parsed = await self._make_llm_multi_step_plan()
        draft = parsed["draft"]

        first = await hermes_console_service.confirm_plan_step(parsed["session_id"], draft["id"], "step-2")
        second = await hermes_console_service.confirm_plan_step(parsed["session_id"], draft["id"], "step-3")

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "ok")
        with sqlite3.connect(self.db_path) as db:
            watch = db.execute("SELECT code, name FROM watchlist WHERE code='600519'").fetchone()
            order = db.execute("SELECT code, action, shares, target_price FROM conditional_orders WHERE code='600519'").fetchone()
            task_status = db.execute("SELECT status FROM hermes_tasks WHERE task_id=?", (draft["id"],)).fetchone()[0]
            step_statuses = db.execute(
                "SELECT step_id, status FROM hermes_task_steps WHERE task_id=? ORDER BY position",
                (draft["id"],),
            ).fetchall()

        self.assertEqual(watch, ("600519", "贵州茅台"))
        self.assertEqual(order, ("600519", "buy", 100, 1680.0))
        self.assertEqual(task_status, "ok")
        self.assertEqual(step_statuses, [("step-1", "done"), ("step-2", "ok"), ("step-3", "ok")])

    async def test_skip_plan_step_updates_timeline_without_writing(self):
        parsed = await self._make_llm_multi_step_plan()
        draft = parsed["draft"]

        result = await hermes_console_service.skip_plan_step(parsed["session_id"], draft["id"], "step-3")

        self.assertEqual(result["status"], "skipped")
        with sqlite3.connect(self.db_path) as db:
            order = db.execute("SELECT code FROM conditional_orders WHERE code='600519'").fetchone()
            step_status = db.execute(
                "SELECT status FROM hermes_task_steps WHERE task_id=? AND step_id='step-3'",
                (draft["id"],),
            ).fetchone()[0]
        self.assertIsNone(order)
        self.assertEqual(step_status, "skipped")

    async def test_common_stock_alias_fills_missing_code(self):
        parsed = await hermes_console_service.handle_message("帮我把平安银行今天买两手，十块五附近记到账上")

        self.assertEqual(parsed["parser"], "rules")
        self.assertEqual(parsed["draft"]["payload"]["code"], "000001")
        self.assertEqual(parsed["draft"]["payload"]["name"], "平安银行")
        self.assertTrue(parsed["draft"]["executable"])

    async def test_ai_search_completion_fills_missing_stock_code_before_confirmation(self):
        async def fake_search(query):
            return {"code": "300750", "name": "宁德时代"}

        with patch("services.hermes_console_service._ai_search_stock_candidate", new=fake_search):
            parsed = await hermes_console_service.handle_message("宁王买 100 股 成交价 200")

        draft = parsed["draft"]
        self.assertTrue(draft["executable"])
        self.assertEqual(draft["payload"]["code"], "300750")
        self.assertEqual(draft["payload"]["name"], "宁德时代")
        self.assertIn("AI 搜索补全股票", draft["completion_sources"][0])
        self.assertIn("补齐能查到的信息", parsed["answer"])

    async def test_quote_completion_fills_missing_trade_price_before_confirmation(self):
        async def fake_quote(code):
            return {"code": code, "name": "平安银行", "price": 10.23}

        with patch("services.hermes_console_service._quote_for_completion", new=fake_quote):
            parsed = await hermes_console_service.handle_message("买入 000001 平安银行 2手")

        draft = parsed["draft"]
        self.assertTrue(draft["executable"])
        self.assertEqual(draft["payload"]["price"], 10.23)
        self.assertIn("行情补全成交价参考", draft["completion_sources"][0])


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

    def test_cancel_route_uses_service(self):
        app = FastAPI()
        app.include_router(hermes_router, prefix="/api")
        client = TestClient(app)

        async def fake_cancel(session_id, draft_id):
            return {"session_id": session_id, "draft_id": draft_id, "status": "cancelled"}

        original = hermes_console_service.cancel_draft
        hermes_console_service.cancel_draft = fake_cancel
        try:
            resp = client.post("/api/hermes/cancel", json={"session_id": "s1", "draft_id": "d1"})
        finally:
            hermes_console_service.cancel_draft = original

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "cancelled")

    def test_step_confirm_route_uses_service(self):
        app = FastAPI()
        app.include_router(hermes_router, prefix="/api")
        client = TestClient(app)

        async def fake_confirm_step(session_id, draft_id, step_id):
            return {"session_id": session_id, "draft_id": draft_id, "step_id": step_id, "status": "ok"}

        original = hermes_console_service.confirm_plan_step
        hermes_console_service.confirm_plan_step = fake_confirm_step
        try:
            resp = client.post("/api/hermes/step/confirm", json={"session_id": "s1", "draft_id": "d1", "step_id": "step-2"})
        finally:
            hermes_console_service.confirm_plan_step = original

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["step_id"], "step-2")

    def test_step_skip_route_uses_service(self):
        app = FastAPI()
        app.include_router(hermes_router, prefix="/api")
        client = TestClient(app)

        async def fake_skip_step(session_id, draft_id, step_id):
            return {"session_id": session_id, "draft_id": draft_id, "step_id": step_id, "status": "skipped"}

        original = hermes_console_service.skip_plan_step
        hermes_console_service.skip_plan_step = fake_skip_step
        try:
            resp = client.post("/api/hermes/step/skip", json={"session_id": "s1", "draft_id": "d1", "step_id": "step-3"})
        finally:
            hermes_console_service.skip_plan_step = original

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "skipped")

    def test_tool_runs_route_uses_service(self):
        app = FastAPI()
        app.include_router(hermes_router, prefix="/api")
        client = TestClient(app)

        async def fake_runs(session_id, limit=30):
            return {"session_id": session_id, "count": 1, "runs": [{"tool": "record_trade", "status": "ok"}]}

        original = hermes_console_service.list_tool_runs
        hermes_console_service.list_tool_runs = fake_runs
        try:
            resp = client.get("/api/hermes/session/s1/tool-runs?limit=10")
        finally:
            hermes_console_service.list_tool_runs = original

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["runs"][0]["tool"], "record_trade")


if __name__ == "__main__":
    unittest.main()
