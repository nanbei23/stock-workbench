import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import models.database as database
from api.portfolio_api import router as portfolio_router
from services import portfolio_service


class PortfolioServiceTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_pnl_calendar_aggregates_stock_rows(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO daily_pnl (date, code6, pnl, close_price, shares)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("2026-05-01", "000001", 120.5, 10.2, 100),
            )
            db.execute(
                """
                INSERT INTO daily_pnl (date, code6, pnl, close_price, shares)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("2026-05-01", "000002", -20.5, 8.1, 100),
            )
            db.commit()

        result = await portfolio_service.get_pnl_calendar(2026, 5)

        self.assertEqual(result["total_pnl"], 100.0)
        self.assertEqual(result["win_days"], 1)
        self.assertEqual(result["days"][0]["date"], "2026-05-01")
        self.assertEqual(len(result["days"][0]["stocks"]), 2)

    async def test_trading_plan_crud_and_quote_enrichment(self):
        request = SimpleNamespace(
            code="000001",
            name="平安银行",
            direction="buy",
            plan_type="watch",
            target_price=10.0,
            condition_type="price_lte",
            plan_shares=200,
            plan_total_cost=None,
            reason="低吸",
            status="pending",
            expires_at=None,
        )
        created = await portfolio_service.create_trading_plan(request)

        with patch(
            "services.portfolio_service.get_batch_quotes",
            new=AsyncMock(return_value={"000001": {"price": 9.5, "change_pct": -1.2}}),
        ):
            listing = await portfolio_service.get_trading_plans(status="pending")

        self.assertEqual(created["status"], "ok")
        self.assertEqual(listing["count"], 1)
        plan = listing["plans"][0]
        self.assertEqual(plan["plan_total_cost"], 2000.0)
        self.assertEqual(plan["current_price"], 9.5)
        self.assertEqual(plan["distance_pct"], -5.0)

        deleted = await portfolio_service.delete_trading_plan(created["id"])
        self.assertEqual(deleted["id"], created["id"])

    async def test_pending_position_not_found_raises_404(self):
        request = SimpleNamespace(
            code="000001",
            name="平安银行",
            target_buy_price=10.0,
            plan_shares=100,
            plan_total_cost=None,
            reason="低吸",
            strategy_state="watch",
        )

        with self.assertRaises(Exception) as ctx:
            await portfolio_service.update_pending_position(999, request)

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_update_watchlist_can_promote_observation_pool_group(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO watchlist (code, name, group_name) VALUES (?, ?, ?)",
                ("600519", "贵州茅台", "观察池"),
            )
            db.commit()

        request = SimpleNamespace(
            group_name="默认",
            target_buy_price=None,
            target_sell_price=None,
            stop_loss_price=None,
            strategy_state=None,
            notes=None,
        )
        result = await portfolio_service.update_watchlist("600519", request)

        with sqlite3.connect(self.db_path) as db:
            group_name = db.execute(
                "SELECT group_name FROM watchlist WHERE code = ?",
                ("600519",),
            ).fetchone()[0]
        self.assertEqual(result, {"status": "ok", "code": "600519"})
        self.assertEqual(group_name, "默认")

    async def test_get_watchlist_includes_latest_report_signal(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO watchlist (code, name, sort_order) VALUES (?, ?, ?)",
                ("000001", "平安银行", 1),
            )
            db.execute(
                """
                INSERT INTO analysis_reports (code, task_id, signal, created_at)
                VALUES (?, ?, ?, ?)
                """,
                ("000001", "old-report", "SELL", "2026-05-20 09:30:00"),
            )
            db.execute(
                """
                INSERT INTO analysis_reports (code, task_id, signal, created_at)
                VALUES (?, ?, ?, ?)
                """,
                ("000001", "new-report", "BUY", "2026-05-21 09:30:00"),
            )
            db.commit()

        with patch("services.portfolio_service.get_batch_quotes", new=AsyncMock(return_value={})):
            result = await portfolio_service.get_watchlist()

        stock = result["stocks"][0]
        self.assertEqual(stock["last_report_signal"], "BUY")
        self.assertEqual(stock["last_report_id"], 2)
        self.assertEqual(stock["last_report_created_at"], "2026-05-21 09:30:00")

    async def test_remove_watchlist_batch_deletes_selected_codes_only(self):
        with sqlite3.connect(self.db_path) as db:
            db.executemany(
                "INSERT INTO watchlist (code, name, sort_order) VALUES (?, ?, ?)",
                [
                    ("000001", "平安银行", 1),
                    ("000002", "万科A", 2),
                    ("600519", "贵州茅台", 3),
                ],
            )
            db.commit()

        result = await portfolio_service.remove_watchlist_batch(["000001", "600519", "000001", "bad"])

        with sqlite3.connect(self.db_path) as db:
            rows = db.execute("SELECT code FROM watchlist ORDER BY sort_order").fetchall()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["deleted"], 2)
        self.assertEqual(result["codes"], ["000001", "600519"])
        self.assertEqual(rows, [("000002",)])

    async def test_remove_watchlist_batch_rejects_empty_codes(self):
        with self.assertRaises(Exception) as ctx:
            await portfolio_service.remove_watchlist_batch(["", "bad"])

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("请选择", ctx.exception.detail)

    async def test_add_watchlist_validates_quote_and_uses_vendor_name(self):
        request = SimpleNamespace(
            code="600519",
            name="",
            group_name="默认",
            strategy_state="watch",
            target_buy_price=None,
            target_sell_price=None,
            stop_loss_price=None,
            notes="",
        )

        with patch("services.portfolio_service.get_batch_quotes", new=AsyncMock(return_value={"600519": {"name": "贵州茅台", "price": 1500}})):
            result = await portfolio_service.add_to_watchlist(request)

        self.assertEqual(result["stock"]["code"], "600519")
        self.assertEqual(result["stock"]["name"], "贵州茅台")

    async def test_add_watchlist_rejects_unknown_quote_code(self):
        request = SimpleNamespace(
            code="603342",
            name="错误股票",
            group_name="默认",
            strategy_state="watch",
            target_buy_price=None,
            target_sell_price=None,
            stop_loss_price=None,
            notes="",
        )

        with patch("services.portfolio_service.get_batch_quotes", new=AsyncMock(return_value={})):
            with self.assertRaises(Exception) as ctx:
                await portfolio_service.add_to_watchlist(request)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("未找到股票 603342 的实时行情", ctx.exception.detail)

    async def test_add_watchlist_rejects_name_code_mismatch(self):
        request = SimpleNamespace(
            code="600519",
            name="平安银行",
            group_name="默认",
            strategy_state="watch",
            target_buy_price=None,
            target_sell_price=None,
            stop_loss_price=None,
            notes="",
        )

        with patch("services.portfolio_service.get_batch_quotes", new=AsyncMock(return_value={"600519": {"name": "贵州茅台", "price": 1500}})):
            with self.assertRaises(Exception) as ctx:
                await portfolio_service.add_to_watchlist(request)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("股票名称与代码不匹配", ctx.exception.detail)

    async def test_portfolio_overview_defaults_cash_to_zero(self):
        result = await portfolio_service.get_portfolio_overview("default")

        self.assertEqual(result["cash"], 0.0)
        self.assertEqual(result["total_assets"], 0)
        self.assertEqual(result["market_value"], 0)

    async def test_portfolio_overview_uses_configured_cash_balance(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("cash_balance_default", "50000"),
            )
            db.commit()

        result = await portfolio_service.get_portfolio_overview("default")

        self.assertEqual(result["cash"], 50000.0)
        self.assertEqual(result["total_assets"], 50000.0)

    async def test_cash_balance_records_ledger_entry(self):
        result = await portfolio_service.set_cash_balance("default", 12000, "初始入金")
        ledger = await portfolio_service.get_cash_ledger("default")
        overview = await portfolio_service.get_portfolio_overview("default")

        self.assertEqual(result["cash"], 12000.0)
        self.assertEqual(overview["cash"], 12000.0)
        self.assertEqual(overview["cash_source"], "manual")
        self.assertEqual(ledger["count"], 1)
        self.assertEqual(ledger["entries"][0]["amount"], 12000.0)
        self.assertEqual(ledger["entries"][0]["notes"], "初始入金")

    async def test_cash_balance_preserves_three_decimal_places(self):
        result = await portfolio_service.set_cash_balance("default", 12345.678, "三位小数资金")
        ledger = await portfolio_service.get_cash_ledger("default")
        overview = await portfolio_service.get_portfolio_overview("default")

        self.assertEqual(result["cash"], 12345.678)
        self.assertEqual(result["amount"], 12345.678)
        self.assertEqual(overview["cash"], 12345.678)
        self.assertEqual(ledger["entries"][0]["amount"], 12345.678)
        self.assertEqual(ledger["entries"][0]["balance_after"], 12345.678)

    async def test_cash_balance_supports_legacy_settings_table_without_updated_at(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("DROP TABLE settings")
            db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.commit()

        result = await portfolio_service.set_cash_balance("default", 8000, "旧库现金")
        overview = await portfolio_service.get_portfolio_overview("default")

        self.assertEqual(result["cash"], 8000.0)
        self.assertEqual(overview["cash"], 8000.0)
        self.assertEqual(overview["cash_source"], "manual")

    async def test_add_trade_keeps_position_in_selected_account(self):
        request = SimpleNamespace(
            code="000001",
            name="平安银行",
            direction="buy",
            price=10.0,
            shares=100,
            commission=0,
            stamp_tax=0,
            transfer_fee=0,
            notes="账户持仓",
            trade_time=None,
            account_id="account-a",
        )

        with patch("services.portfolio_service.get_batch_quotes", new=AsyncMock(return_value={})):
            await portfolio_service.add_trade(request)
            selected = await portfolio_service.get_portfolio("account-a")
            default = await portfolio_service.get_portfolio("default")

        self.assertEqual(selected["count"], 1)
        self.assertEqual(selected["positions"][0]["code"], "000001")
        self.assertEqual(selected["positions"][0]["total_shares"], 100)
        self.assertEqual(default["count"], 0)

    async def test_add_trade_preserves_three_decimal_asset_numbers(self):
        request = SimpleNamespace(
            code="000001",
            name="平安银行",
            direction="buy",
            price=10.123,
            shares=100.125,
            commission=1.234,
            stamp_tax=0.123,
            transfer_fee=0.012,
            notes="三位小数交易",
            trade_time=None,
            account_id="default",
        )

        with patch("services.portfolio_service.get_batch_quotes", new=AsyncMock(return_value={})):
            await portfolio_service.add_trade(request)
            portfolio = await portfolio_service.get_portfolio("default")

        with sqlite3.connect(self.db_path) as db:
            trade = db.execute("SELECT price, shares, amount, total_cost FROM trades").fetchone()

        self.assertEqual(trade, (10.123, 100.125, 1013.565, 1014.934))
        self.assertEqual(portfolio["positions"][0]["total_shares"], 100.125)
        self.assertEqual(portfolio["positions"][0]["avg_cost"], 10.137)

    async def test_import_watchlist_markdown_parses_name_and_code_lines(self):
        content = """
        # 我的自选股
        - 贵州茅台 600519
        000001 平安银行
        中芯国际+688981
        贵州茅台（600519）
        无代码这一行
        """

        quotes = {
            "600519": {"name": "贵州茅台"},
            "000001": {"name": "平安银行"},
            "688981": {"name": "中芯国际"},
        }
        with patch("services.portfolio_service.get_batch_quotes", new=AsyncMock(return_value=quotes)):
            result = await portfolio_service.import_watchlist_markdown(content)

        self.assertEqual(result["imported"], 3)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(result["invalid"], 2)

        with sqlite3.connect(self.db_path) as db:
            rows = db.execute("SELECT code, name FROM watchlist ORDER BY sort_order").fetchall()

        self.assertEqual(rows, [("600519", "贵州茅台"), ("000001", "平安银行"), ("688981", "中芯国际")])

    async def test_import_watchlist_markdown_parses_table_with_index_name_and_code_columns(self):
        content = """
        | # | 股票名称 | 代码 |
        |---|---------|------|
        | 1 | 深科技 | 000021 |
        | 2 | 中兴通讯 | 000063 |
        | 23 | 创业板ETF | 159915 |
        | 80 | 睿创微纳 | 688002 |
        """

        quotes = {
            "000021": {"name": "深科技"},
            "000063": {"name": "中兴通讯"},
            "159915": {"name": "创业板ETF"},
            "688002": {"name": "睿创微纳"},
        }
        with patch("services.portfolio_service.get_batch_quotes", new=AsyncMock(return_value=quotes)):
            result = await portfolio_service.import_watchlist_markdown(content)

        self.assertEqual(result["imported"], 4)
        self.assertEqual(result["invalid"], 0)

        with sqlite3.connect(self.db_path) as db:
            rows = db.execute("SELECT code, name FROM watchlist ORDER BY sort_order").fetchall()

        self.assertEqual(
            rows,
            [
                ("000021", "深科技"),
                ("000063", "中兴通讯"),
                ("159915", "创业板ETF"),
                ("688002", "睿创微纳"),
            ],
        )

    async def test_import_watchlist_markdown_rejects_unknown_and_mismatched_stocks(self):
        content = """
        错误股票 603342
        贵州茅台 600519
        平安银行 000002
        """
        quotes = {"600519": {"name": "贵州茅台"}, "000002": {"name": "万科A"}}

        with patch("services.portfolio_service.get_batch_quotes", new=AsyncMock(return_value=quotes)):
            result = await portfolio_service.import_watchlist_markdown(content)

        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["invalid"], 2)
        self.assertTrue(any("未找到实时行情" in line for line in result["invalid_lines"]))
        self.assertTrue(any("名称不匹配" in line for line in result["invalid_lines"]))

        with sqlite3.connect(self.db_path) as db:
            rows = db.execute("SELECT code, name FROM watchlist ORDER BY sort_order").fetchall()

        self.assertEqual(rows, [("600519", "贵州茅台")])


class PortfolioApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(portfolio_router, prefix="/api")
        self.client = TestClient(app)

    def test_pnl_calendar_route_uses_service_layer(self):
        with patch(
            "services.portfolio_service.get_pnl_calendar",
            new=AsyncMock(return_value={"year": 2026, "month": 5, "days": []}),
        ) as get_pnl_calendar:
            resp = self.client.get("/api/pnl/calendar?year=2026&month=5")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["year"], 2026)
        get_pnl_calendar.assert_awaited_once_with(2026, 5, None)

    def test_watchlist_markdown_import_route_uses_service_layer(self):
        with patch(
            "services.portfolio_service.import_watchlist_markdown",
            new=AsyncMock(return_value={"status": "ok", "imported": 1}),
        ) as import_watchlist:
            resp = self.client.post(
                "/api/watchlist/import-md",
                json={"content": "贵州茅台 600519", "group_name": "默认"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["imported"], 1)
        import_watchlist.assert_awaited_once_with("贵州茅台 600519", "默认")

    def test_watchlist_add_route_preserves_validation_error_status(self):
        with patch(
            "services.portfolio_service.add_to_watchlist",
            new=AsyncMock(side_effect=portfolio_service.HTTPException(status_code=400, detail="股票名称与代码不匹配")),
        ):
            resp = self.client.post(
                "/api/watchlist",
                json={"code": "600519", "name": "平安银行"},
            )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], "股票名称与代码不匹配")

    def test_watchlist_update_route_accepts_group_name(self):
        with patch(
            "services.portfolio_service.update_watchlist",
            new=AsyncMock(return_value={"status": "ok", "code": "600519"}),
        ) as update_watchlist:
            resp = self.client.put(
                "/api/watchlist/600519",
                json={"group_name": "默认"},
            )

        self.assertEqual(resp.status_code, 200)
        update_watchlist.assert_awaited_once()
        self.assertEqual(update_watchlist.await_args.args[1].group_name, "默认")

    def test_watchlist_batch_delete_route_uses_service_layer(self):
        with patch(
            "services.portfolio_service.remove_watchlist_batch",
            new=AsyncMock(return_value={"status": "ok", "deleted": 2, "codes": ["000001", "600519"]}),
        ) as remove_batch:
            resp = self.client.request(
                "DELETE",
                "/api/watchlist",
                json={"codes": ["000001", "600519"]},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["deleted"], 2)
        remove_batch.assert_awaited_once_with(["000001", "600519"])

    def test_trade_route_accepts_three_decimal_shares(self):
        with patch(
            "services.portfolio_service.add_trade",
            new=AsyncMock(return_value={"status": "ok"}),
        ) as add_trade:
            resp = self.client.post(
                "/api/trades",
                json={
                    "code": "000001",
                    "name": "平安银行",
                    "direction": "buy",
                    "price": 10.123,
                    "shares": 100.125,
                    "commission": 1.234,
                    "stamp_tax": 0.123,
                    "transfer_fee": 0.012,
                },
            )

        self.assertEqual(resp.status_code, 200)
        add_trade.assert_awaited_once()
        req = add_trade.call_args.args[0]
        self.assertEqual(req.price, 10.123)
        self.assertEqual(req.shares, 100.125)
        self.assertEqual(req.commission, 1.234)


if __name__ == "__main__":
    unittest.main()
