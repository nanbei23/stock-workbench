import sqlite3
import tempfile
import unittest
from pathlib import Path

import models.database as database
from services import execution_review_service


class ExecutionReviewServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    async def asyncSetUp(self):
        await database.init_db()

    async def test_daily_decision_items_bind_real_trades_and_classify_execution(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO holding_daily_reviews
                    (review_id, date, account_id, status, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("hr-1", "2026-06-05", "default", "completed", "2026-06-05 16:00:00"),
            )
            db.executemany(
                """
                INSERT INTO holding_review_items
                    (review_id, date, account_id, item_type, code, name, decision_action, decision_status, suggested_amount)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("hr-1", "2026-06-05", "default", "candidate", "000001", "平安银行", "add", "not_executed", 10000),
                    ("hr-1", "2026-06-05", "default", "holding", "000002", "万科A", "sell", "not_executed", 8000),
                    ("hr-1", "2026-06-05", "default", "candidate", "000003", "国农科技", "forbid_buy", "not_executed", 0),
                ],
            )
            db.executemany(
                """
                INSERT INTO trades
                    (code, name, direction, price, shares, amount, total_cost, trade_time, account_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("000001", "平安银行", "buy", 15, 1000, 15000, 15001, "2026-06-06 09:35:00", "default"),
                    ("000003", "国农科技", "buy", 10, 100, 1000, 1001, "2026-06-06 10:20:00", "default"),
                ],
            )
            db.commit()

        result = await execution_review_service.daily_decision_execution(review_id="hr-1")
        rows = {row["code"]: row for row in result["rows"]}

        self.assertEqual(result["scope"], "daily_decision_execution")
        self.assertEqual(rows["000001"]["execution"]["classification"], "over_executed")
        self.assertEqual(rows["000001"]["execution"]["matched_trade_ids"], [1])
        self.assertEqual(rows["000001"]["execution"]["matched_buy_amount"], 15000)
        self.assertEqual(rows["000002"]["execution"]["classification"], "not_executed")
        self.assertEqual(rows["000003"]["execution"]["classification"], "violated")
        self.assertEqual(result["summary"]["by_classification"]["over_executed"], 1)
        self.assertEqual(result["summary"]["by_classification"]["not_executed"], 1)
        self.assertEqual(result["summary"]["by_classification"]["violated"], 1)

    async def test_position_plan_items_bind_only_adopted_items_to_real_trades(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO position_plans
                    (plan_id, title, status, stage, adoption_status, confirmed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("pp-1", "最终组合研究", "active", "final", "partially_adopted", "2026-06-05 16:10:00", "2026-06-05 16:00:00"),
            )
            db.executemany(
                """
                INSERT INTO position_plan_items
                    (plan_id, code, name, action, suggested_amount, position_pct, adoption_status, adopted_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("pp-1", "000001", "平安银行", "buy", 12000, 4.0, "adopted", "2026-06-05 16:20:00"),
                    ("pp-1", "000002", "万科A", "buy", 8000, 3.0, "ignored", None),
                ],
            )
            db.execute(
                """
                INSERT INTO trades
                    (code, name, direction, price, shares, amount, total_cost, trade_time, account_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", "buy", 12, 500, 6000, 6001, "2026-06-06 09:45:00", "default"),
            )
            db.commit()

        result = await execution_review_service.position_plan_execution(plan_id="pp-1")

        self.assertEqual(result["scope"], "position_plan_execution")
        self.assertEqual(len(result["rows"]), 1)
        self.assertEqual(result["rows"][0]["code"], "000001")
        self.assertEqual(result["rows"][0]["execution"]["classification"], "partial_executed")
        self.assertEqual(result["summary"]["adopted_items"], 1)
        self.assertEqual(result["summary"]["ignored_items"], 0)

    async def test_trade_amount_falls_back_to_total_cost_then_price_shares(self):
        total_cost_result = execution_review_service.classify_execution(
            "buy",
            1000,
            [{"direction": "buy", "amount": 0, "total_cost": 1100, "price": 11, "shares": 100, "id": 9}],
        )
        computed_amount_result = execution_review_service.classify_execution(
            "sell",
            800,
            [{"direction": "sell", "amount": None, "total_cost": 0, "price": 8, "shares": 100, "id": 10}],
        )

        self.assertEqual(total_cost_result["classification"], "full_executed")
        self.assertEqual(total_cost_result["matched_buy_amount"], 1100)
        self.assertEqual(computed_amount_result["classification"], "full_executed")
        self.assertEqual(computed_amount_result["matched_sell_amount"], 800)


if __name__ == "__main__":
    unittest.main()
