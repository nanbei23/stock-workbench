import unittest
from unittest.mock import AsyncMock, patch

from services import performance_service


class FakePerformanceDb:
    def __init__(self, rows, price_rows=None):
        self.rows = rows
        self.price_rows = price_rows or []
        self.calls = []
        self.close = AsyncMock()

    async def execute_fetchall(self, sql, params=()):
        self.calls.append((sql, params))
        if "FROM daily_pnl" in sql:
            return self.price_rows
        return self.rows


class PerformanceServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_position_plan_performance_aggregates_by_plan(self):
        db = FakePerformanceDb(
            [
                {
                    "plan_id": "plan-a",
                    "title": "最终计划",
                    "stage": "final",
                    "model_strategy": "dual",
                    "context_strategy": "summary_plus_evidence",
                    "adoption_status": "adopted",
                    "confirmed_at": "2026-06-01T10:05:00",
                    "cash_snapshot_json": '{"total_cash": 30000}',
                    "created_at": "2026-06-01T10:00:00",
                    "code": "000001",
                    "name": "平安银行",
                    "action": "BUY",
                    "suggested_amount": 10000,
                    "position_pct": 0.1,
                    "suggested_shares": 1000,
                    "source_report_id": 1,
                    "real_shares": 800,
                    "real_current_price": 10,
                    "real_market_value": 8000,
                    "shadow_shares": 1000,
                    "shadow_market_value": 10000,
                    "pnl_pct": 6.0,
                    "excess_return": 1.5,
                    "tracking_entry_price": 10,
                    "tracking_current_price": 10.6,
                    "tracking_status": "closed",
                },
                {
                    "plan_id": "plan-a",
                    "title": "最终计划",
                    "stage": "final",
                    "model_strategy": "dual",
                    "context_strategy": "summary_plus_evidence",
                    "adoption_status": "adopted",
                    "confirmed_at": "2026-06-01T10:05:00",
                    "cash_snapshot_json": '{"total_cash": 30000}',
                    "created_at": "2026-06-01T10:00:00",
                    "code": "000002",
                    "name": "万科A",
                    "action": "BUY",
                    "suggested_amount": 8000,
                    "position_pct": 0.08,
                    "suggested_shares": 500,
                    "source_report_id": 2,
                    "real_shares": 0,
                    "real_current_price": 16,
                    "real_market_value": 0,
                    "shadow_shares": 500,
                    "shadow_market_value": 8000,
                    "pnl_pct": -2.0,
                    "excess_return": -1.0,
                    "tracking_entry_price": 16,
                    "tracking_current_price": 15.68,
                    "tracking_status": "closed",
                },
                {
                    "plan_id": "plan-b",
                    "title": "初筛计划",
                    "stage": "screening",
                    "model_strategy": "single",
                    "context_strategy": "candidate_screening",
                    "adoption_status": "draft",
                    "confirmed_at": None,
                    "cash_snapshot_json": '{"total_cash": 30000}',
                    "created_at": "2026-05-31T10:00:00",
                    "code": "000003",
                    "name": "国农科技",
                    "action": "HOLD",
                    "suggested_amount": None,
                    "position_pct": None,
                    "suggested_shares": None,
                    "source_report_id": 3,
                    "real_shares": 0,
                    "real_current_price": 0,
                    "real_market_value": 0,
                    "shadow_shares": 0,
                    "shadow_market_value": 0,
                    "pnl_pct": None,
                    "excess_return": None,
                    "tracking_entry_price": None,
                    "tracking_current_price": None,
                    "tracking_status": None,
                },
            ],
            price_rows=[
                {"code": "000001", "date": "2026-06-01", "close_price": 10.0},
                {"code": "000001", "date": "2026-06-02", "close_price": 10.5},
                {"code": "000001", "date": "2026-06-04", "close_price": 10.8},
                {"code": "000002", "date": "2026-06-01", "close_price": 16.0},
                {"code": "000002", "date": "2026-06-02", "close_price": 15.2},
                {"code": "000002", "date": "2026-06-04", "close_price": 15.68},
            ],
        )
        with patch("services.performance_service.get_db", new=AsyncMock(return_value=db)):
            result = await performance_service.position_plan_performance(limit=10)

        self.assertIn("WITH recent_plans", db.calls[0][0])
        self.assertIn("adoption_status = 'adopted'", db.calls[0][0])
        self.assertEqual(db.calls[0][1], (10,))
        self.assertIn("FROM daily_pnl", db.calls[1][0])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["scope"], "adopted_position_plans")
        plan_a = next(item for item in result["plans"] if item["plan_id"] == "plan-a")
        self.assertEqual(plan_a["items"], 2)
        self.assertEqual(plan_a["actionable_items"], 2)
        self.assertEqual(plan_a["tracked"], 2)
        self.assertEqual(plan_a["wins"], 1)
        self.assertEqual(plan_a["avg_pnl_pct"], 2.0)
        self.assertEqual(plan_a["avg_excess_return"], 0.25)
        self.assertEqual(plan_a["win_rate"], 50.0)
        self.assertEqual(plan_a["portfolio_return_pct"], 2.444)
        self.assertEqual(plan_a["portfolio_excess_return"], 0.389)
        self.assertEqual(plan_a["horizon_returns"]["1"], 0.556)
        self.assertEqual(plan_a["horizon_returns"]["3"], 3.556)
        self.assertEqual(plan_a["max_drawdown_pct"], 0.556)
        self.assertEqual(plan_a["allocation"]["suggested_amount"], 18000)
        self.assertEqual(plan_a["deviation"]["evaluated"], 2)
        self.assertEqual(plan_a["deviation"]["underfollowed"], 1)
        self.assertEqual(plan_a["deviation"]["missing_real_position"], 1)
        final_stage = next(item for item in result["by_stage"] if item["stage"] == "final")
        self.assertEqual(final_stage["plans"], 1)
        self.assertEqual(final_stage["tracked"], 1)
        self.assertEqual(final_stage["avg_plan_pnl_pct"], 2.0)
        model_bucket = next(item for item in result["by_model_strategy"] if item["model_strategy"] == "dual")
        self.assertEqual(model_bucket["avg_portfolio_return_pct"], 2.444)
        db.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
