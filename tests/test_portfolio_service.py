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


if __name__ == "__main__":
    unittest.main()
