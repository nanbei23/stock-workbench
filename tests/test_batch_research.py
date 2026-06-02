import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from models.database import SCHEMA
from scripts import batch_research


class BatchResearchScriptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            conn.executemany(
                "INSERT INTO watchlist (code, name, group_name, sort_order) VALUES (?, ?, ?, ?)",
                [
                    ("000001", "平安银行", "默认", 1),
                    ("600519", "贵州茅台", "默认", 2),
                    ("000063", "中兴通讯", "观察池", 3),
                ],
            )
            conn.execute(
                "INSERT INTO analysis_reports (code, task_id, signal, created_at) VALUES (?, ?, ?, datetime('now'))",
                ("600519", "old-task", "HOLD"),
            )
            conn.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_candidates_filters_group_and_recent_reports(self):
        candidates = batch_research.load_candidates(self.db_path, group="默认", skip_recent_days=7)

        self.assertEqual([item.code for item in candidates], ["000001"])

    def test_rank_candidates_prioritizes_self_selected_and_positive_change(self):
        stocks = [
            batch_research.StockCandidate("000001", "平安银行", "默认", 1),
            batch_research.StockCandidate("000063", "中兴通讯", "观察池", 2),
        ]
        quotes = {
            "000001": {"price": 10.0, "change_pct": 1.2, "amount": 900000000},
            "000063": {"price": 20.0, "change_pct": 5.0, "amount": 2000000000},
        }

        ranked = batch_research.rank_candidates(stocks, quotes, top_n=2)

        self.assertEqual(ranked[0].code, "000001")
        self.assertGreater(ranked[0].score, ranked[1].score)

    async def test_dry_run_builds_plan_without_submitting_ai_tasks(self):
        with patch("scripts.batch_research.get_batch_quotes", new=AsyncMock(return_value={"000001": {"price": 10.0, "change_pct": 1.0}})), patch(
            "services.ai_analysis_service.start_analysis",
            new=AsyncMock(),
        ) as start_analysis:
            result = await batch_research.run_batch_research(
                db_path=self.db_path,
                group="默认",
                include_observation=False,
                limit=5,
                top_n=1,
                batch_size=1,
                data_only=False,
                dry_run=True,
                skip_recent_days=7,
                output_dir=Path(self.tmp.name),
            )

        self.assertEqual(result["planned_count"], 1)
        self.assertEqual(result["submitted_count"], 0)
        start_analysis.assert_not_awaited()

    async def test_data_only_prewarms_quotes_without_submitting_ai_tasks(self):
        snapshot = {
            "market": {"quote": {"price": 10.0}},
            "social": {"items": ["ok"]},
            "news": {"items": ["ok"]},
            "fundamentals": {"items": ["ok"]},
            "policy": {"items": ["ok"]},
            "hot_money": {"items": ["ok"]},
            "lockup": {"items": ["ok"]},
        }
        with patch("scripts.batch_research.get_batch_quotes", new=AsyncMock(return_value={"000001": {"price": 10.0, "change_pct": 1.0}})), patch(
            "scripts.batch_research.fetch_seven_layer_snapshot",
            new=AsyncMock(return_value=snapshot),
        ) as fetch_snapshot, patch(
            "services.ai_analysis_service.start_analysis",
            new=AsyncMock(),
        ) as start_analysis:
            result = await batch_research.run_batch_research(
                db_path=self.db_path,
                group="默认",
                include_observation=False,
                limit=5,
                top_n=1,
                batch_size=1,
                data_only=True,
                dry_run=False,
                skip_recent_days=7,
                output_dir=Path(self.tmp.name),
            )

        self.assertEqual(result["mode"], "data_only")
        self.assertEqual(result["snapshots"]["saved"], 1)
        self.assertEqual(result["submitted_count"], 0)
        fetch_snapshot.assert_awaited_once()
        start_analysis.assert_not_awaited()

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT code, snapshot_json, validation_json FROM stock_data_snapshots").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "000001")
        self.assertEqual(json.loads(rows[0][1])["market"]["quote"]["price"], 10.0)
        self.assertTrue(json.loads(rows[0][2])["ok"])

    def test_snapshot_validation_marks_missing_layers(self):
        validation = batch_research.validate_snapshot({"market": {"quote": {"price": 10}}})

        self.assertFalse(validation["ok"])
        self.assertIn("social", validation["missing_layers"])

    def test_build_position_plan_uses_latest_reports_and_cash(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('cash_balance_default', '253375.680')")
            conn.execute(
                """
                INSERT INTO analysis_reports
                    (code, task_id, signal, confidence, risk_score, final_decision, trader_plan, created_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, datetime('now', '-1 minute'))
                """,
                ("000001", "report-buy", "BUY", 0.82, 24.5, "建议分批建仓", "回撤买入，目标 12.500",),
            )
            conn.execute(
                """
                INSERT INTO analysis_reports
                    (code, task_id, signal, confidence, risk_score, final_decision, trader_plan, created_at)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("000063", "report-hold", "HOLD", 0.62, 58.0, "等待确认", "暂不建仓",),
            )
            conn.commit()

        plan = batch_research.build_position_plan(
            self.db_path,
            [
                batch_research.StockCandidate("000001", "平安银行", "默认", 1),
                batch_research.StockCandidate("000063", "中兴通讯", "观察池", 2),
            ],
            top_n=5,
        )

        self.assertEqual(plan["cash"], 253375.68)
        self.assertEqual(plan["available_reports"], 2)
        self.assertEqual(plan["recommendations"][0]["code"], "000001")
        self.assertGreater(plan["recommendations"][0]["suggested_amount"], 0)
        self.assertEqual(plan["recommendations"][1]["action"], "watch")


if __name__ == "__main__":
    unittest.main()
