import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from models import database
from services import strategy_service


class StrategyServiceTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_params_round_trip(self):
        params = SimpleNamespace(
            budget=10000,
            entry_price=10,
            drop_pct=3,
            add_mult=1,
            bounce_pct=5,
            sell_pct=50,
            lot_size=100,
            target_profit_pct=6,
            low_water_manual=None,
            buy_prices="[10,9.7]",
        )

        saved = await strategy_service.update_params("000001", params)
        loaded = await strategy_service.get_params("000001")

        self.assertEqual(saved, {"ok": True})
        self.assertEqual(loaded["data"]["code6"], "000001")
        self.assertEqual(loaded["data"]["budget"], 10000)
        self.assertEqual(loaded["data"]["target_profit_pct"], 6)

    async def test_get_state_combines_quote_and_triggers(self):
        with (
            patch("services.strategy_service.get_realtime_quote", new=AsyncMock(return_value={"price": 9.8})),
            patch("services.strategy_service.calc_next_triggers", return_value={"next_buy_price": 10, "next_sell_price": 12}),
        ):
            result = await strategy_service.get_state("000001")

        self.assertEqual(result["ok"], True)
        self.assertEqual(result["data"]["current_price"], 9.8)
        self.assertEqual(result["data"]["state"], "buy")


if __name__ == "__main__":
    unittest.main()
