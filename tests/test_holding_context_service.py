import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import models.database as database
from services import holding_context_service


class HoldingContextServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    async def test_build_context_for_real_holding(self):
        await database.init_db()
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("cash_balance_default", "200000"))
            db.execute(
                """
                INSERT INTO portfolio (
                    code, name, total_shares, avg_cost, current_price,
                    market_value, unrealized_pnl, unrealized_pnl_pct, account_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("002241", "歌尔股份", 1000, 26.006, 25.5, 25500, -506, -1.946, "default"),
            )
            db.execute(
                """
                INSERT INTO analysis_reports (
                    id, code, task_id, signal, confidence, risk_score, raw_state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (7, "002241", "old", "BUY", 0.72, 33, json.dumps({"research_signal": "BUY"}), "2026-06-03 14:00:00"),
            )
            db.execute(
                """
                INSERT INTO signal_tracking (
                    report_id, code, name, signal, signal_date, entry_price, current_price, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (7, "002241", "歌尔股份", "BUY", "2026-06-03", 26.2, 25.5, "open"),
            )
            db.execute(
                """
                INSERT INTO ai_shadow_positions (
                    code, name, total_shares, avg_cost, current_price, market_value, unrealized_pnl
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("002241", "歌尔股份", 800, 25.8, 25.5, 20400, -240),
            )
            db.commit()

        ctx = await holding_context_service.build_holding_context("002241", account_id="default")

        self.assertTrue(ctx["is_holding"])
        self.assertEqual(ctx["code"], "002241")
        self.assertEqual(ctx["shares"], 1000.0)
        self.assertEqual(ctx["avg_cost"], 26.006)
        self.assertEqual(ctx["current_price"], 25.5)
        self.assertLess(ctx["holding_pnl"], 0)
        self.assertEqual(ctx["cash"], 200000.0)
        self.assertEqual(ctx["last_report"]["signal"], "BUY")
        self.assertEqual(ctx["signal_tracking"]["status"], "open")
        self.assertEqual(ctx["shadow_position"]["total_shares"], 800.0)
        self.assertIn("真实持仓", ctx["prompt_context"])

    async def test_build_context_for_empty_watch_stock(self):
        await database.init_db()

        ctx = await holding_context_service.build_holding_context("000001", account_id="default")

        self.assertFalse(ctx["is_holding"])
        self.assertEqual(ctx["position_action_scope"], "watch_only")
        self.assertIn("当前账户未持仓", ctx["prompt_context"])

    async def test_build_context_uses_default_cash_fallback_for_named_account(self):
        await database.init_db()
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("cash_balance_default", "12345.678"))
            db.commit()

        ctx = await holding_context_service.build_holding_context("000001", account_id="secondary")

        self.assertEqual(ctx["cash"], 12345.678)
        self.assertIn("可用资金: 12345.678", ctx["prompt_context"])

    async def test_build_context_refreshes_quote_when_position_market_value_is_zero(self):
        await database.init_db()
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("cash_balance_default", "100000"))
            db.execute(
                """
                INSERT INTO portfolio (
                    code, name, total_shares, avg_cost, current_price,
                    market_value, unrealized_pnl, unrealized_pnl_pct, account_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("002156", "通富微电", 3100, 71.207, 0, 0, 0, 0, "default"),
            )
            db.commit()

        with patch(
            "services.holding_context_service.get_batch_quotes",
            new=AsyncMock(return_value={"002156": {"price": 71.35, "name": "通富微电"}}),
        ):
            ctx = await holding_context_service.build_holding_context("002156", account_id="default")

        self.assertTrue(ctx["is_holding"])
        self.assertEqual(ctx["current_price"], 71.35)
        self.assertEqual(ctx["market_value"], 221185.0)
        self.assertEqual(ctx["valuation_source"], "realtime_quote")
        self.assertIn("市值来源: realtime_quote", ctx["prompt_context"])
        self.assertIn("不得因行情缺失或市值估算为 0 而当作空仓", ctx["prompt_context"])

    async def test_build_context_keeps_holding_valid_with_cost_fallback_when_quote_missing(self):
        await database.init_db()
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO portfolio (
                    code, name, total_shares, avg_cost, current_price,
                    market_value, unrealized_pnl, unrealized_pnl_pct, account_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("002241", "歌尔股份", 1000, 26.006, 0, 0, 0, 0, "default"),
            )
            db.commit()

        with patch("services.holding_context_service.get_batch_quotes", new=AsyncMock(return_value={})):
            ctx = await holding_context_service.build_holding_context("002241", account_id="default")

        self.assertTrue(ctx["is_holding"])
        self.assertEqual(ctx["market_value"], 26006.0)
        self.assertEqual(ctx["valuation_source"], "cost_fallback")
        self.assertEqual(ctx["holding_pnl"], 0.0)
        self.assertIn("真实持仓: 1000.000 股", ctx["prompt_context"])
