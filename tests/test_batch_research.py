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

    async def test_snapshot_analysis_uses_saved_snapshot_without_tradingagents(self):
        snapshot = {
            "market": {"quote": {"price": 10.0}},
            "social": {"items": ["ok"]},
            "news": {"items": ["ok"]},
            "fundamentals": {"items": ["ok"]},
            "policy": {"items": ["ok"]},
            "hot_money": {"items": ["ok"]},
            "lockup": {"items": ["ok"]},
        }
        validation = batch_research.validate_snapshot(snapshot)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO stock_data_snapshots
                    (code, name, snapshot_json, validation_json, summary_json, source, run_id)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "000001",
                    "平安银行",
                    json.dumps(snapshot, ensure_ascii=False),
                    json.dumps(validation, ensure_ascii=False),
                    "{}",
                    "test",
                    "run-1",
                ),
            )
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('custom_endpoint', 'https://api.example.com/v1')")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('api_key', 'sk-test')")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('deep_think_model', 'model-deep')")
            conn.commit()

        llm_result = {
            "signal": "BUY",
            "confidence": 0.76,
            "risk_score": 32.5,
            "market_report": "价格结构改善",
            "sentiment_report": "情绪中性",
            "news_report": "无重大负面",
            "fundamentals_report": "基本面稳定",
            "policy_report": "政策无明显冲击",
            "hot_money_report": "资金温和",
            "lockup_report": "解禁风险可控",
            "investment_debate": "多方略占优",
            "risk_debate": "控制仓位",
            "trader_plan": "分批建仓",
            "final_decision": "评级：BUY，置信度 76%，风险评分 32.5",
        }
        with patch("scripts.batch_research.get_batch_quotes", new=AsyncMock(return_value={"000001": {"price": 10.0, "change_pct": 1.0}})), patch(
            "scripts.batch_research._call_snapshot_llm",
            new=AsyncMock(return_value=llm_result),
        ) as call_llm, patch(
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
                dry_run=False,
                skip_recent_days=7,
                output_dir=Path(self.tmp.name),
                analysis_mode="snapshot",
            )

        self.assertEqual(result["mode"], "snapshot_analysis")
        self.assertEqual(result["submitted_count"], 1)
        call_llm.assert_awaited_once()
        start_analysis.assert_not_awaited()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT code, signal, depth, model_mode FROM analysis_reports WHERE code = ?", ("000001",)).fetchone()
        self.assertEqual(row[0], "000001")
        self.assertEqual(row[1], "BUY")
        self.assertEqual(row[2], "snapshot")
        self.assertEqual(row[3], "snapshot_report")

    async def test_recent_reports_are_skipped_for_next_batch_but_kept_in_position_plan(self):
        snapshot = {
            "market": {"quote": {"price": 10.0}},
            "social": {"items": ["ok"]},
            "news": {"items": ["ok"]},
            "fundamentals": {"items": ["ok"]},
            "policy": {"items": ["ok"]},
            "hot_money": {"items": ["ok"]},
            "lockup": {"items": ["ok"]},
        }
        validation = batch_research.validate_snapshot(snapshot)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO stock_data_snapshots
                    (code, name, snapshot_json, validation_json, summary_json, source, run_id)
                VALUES
                    (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "000001",
                    "平安银行",
                    json.dumps(snapshot, ensure_ascii=False),
                    json.dumps(validation, ensure_ascii=False),
                    "{}",
                    "test",
                    "run-1",
                ),
            )
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('custom_endpoint', 'https://api.example.com/v1')")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('api_key', 'sk-test')")
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('deep_think_model', 'model-deep')")
            conn.execute("UPDATE analysis_reports SET signal = 'BUY', confidence = 0.7, risk_score = 35 WHERE code = '600519'")
            conn.commit()

        llm_result = {
            "signal": "BUY",
            "confidence": 0.8,
            "risk_score": 28,
            "final_decision": "评级：BUY",
            "trader_plan": "分批建仓",
        }
        with patch(
            "scripts.batch_research.get_batch_quotes",
            new=AsyncMock(
                return_value={
                    "000001": {"price": 10.0, "change_pct": 1.0},
                    "600519": {"price": 1500.0, "change_pct": 0.5},
                }
            ),
        ), patch("scripts.batch_research._call_snapshot_llm", new=AsyncMock(return_value=llm_result)):
            result = await batch_research.run_batch_research(
                db_path=self.db_path,
                group="默认",
                include_observation=False,
                limit=0,
                top_n=0,
                batch_size=1,
                data_only=False,
                dry_run=False,
                skip_recent_days=7,
                output_dir=Path(self.tmp.name),
                analysis_mode="snapshot",
            )

        self.assertEqual(result["planned_count"], 1)
        self.assertEqual(result["candidates"][0]["code"], "000001")
        self.assertEqual(result["skipped_existing_reports"], 1)
        self.assertEqual(result["position_plan"]["available_reports"], 2)
        self.assertIn("600519", {item["code"] for item in result["position_plan"]["recommendations"]})

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
