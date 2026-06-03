import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from models.database import SCHEMA
from services import position_plan_service


class PositionPlanServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('cash_balance_default', '253375.680')")
            conn.execute(
                """
                INSERT INTO portfolio
                    (code, name, total_shares, available_shares, avg_cost, current_price, market_value, account_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", 100.123, 100.123, 10.123, 11.234, 1124.691, "default"),
            )
            conn.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def test_persist_position_plan_keeps_research_asset_and_items(self):
        plan = {
            "cash": 253375.68,
            "candidate_count": 2,
            "selected_count": 1,
            "selected_report_ids": [10, 11],
            "context_strategy": "candidate_screening",
            "model_strategy": "dual",
            "summary": "建议轻仓试探平安银行。",
            "risk_controls": ["总仓位不超过 10%"],
            "role_discussion": [{"role_key": "chair", "role_name": "最终裁决", "content": "通过"}],
            "recommendations": [
                {
                    "code": "000001",
                    "name": "平安银行",
                    "action": "buy",
                    "suggested_amount": 12000.1234,
                    "position_pct": 4.7374,
                    "confidence": 0.81234,
                    "risk_score": 33.3334,
                    "reason": "报告完整且风险可控",
                    "entry_plan": "分两批",
                    "report_id": 10,
                }
            ],
        }

        saved = position_plan_service.persist_position_plan(
            plan,
            db_path=self.db_path,
            batch_job_id="po-job-1",
            payload={
                "stage": "screening",
                "model_strategy": "dual",
                "context_strategy": "candidate_screening",
                "title": "125进30 初筛",
            },
        )

        self.assertEqual(saved["stage"], "screening")
        self.assertEqual(saved["context_strategy"], "candidate_screening")
        self.assertEqual(saved["model_strategy"], "dual")
        self.assertEqual(saved["source_report_ids"], [10, 11])
        self.assertEqual(saved["cash_snapshot_json"]["total_cash"], 253375.68)
        self.assertEqual(saved["portfolio_snapshot_json"]["position_count"], 1)

        detail = position_plan_service.get_position_plan(saved["plan_id"], db_path=self.db_path)
        self.assertEqual(len(detail["items"]), 1)
        self.assertEqual(detail["items"][0]["source_report_id"], 10)
        self.assertEqual(detail["items"][0]["suggested_amount"], 12000.123)
        self.assertEqual(detail["items"][0]["position_pct"], 4.737)
        self.assertEqual(detail["recommendations_json"][0]["code"], "000001")
        self.assertEqual(detail["adoption_status"], "draft")
        self.assertIsNone(detail["confirmed_at"])

    def test_adopt_final_position_plan_locks_snapshot_and_supersedes_previous(self):
        base_plan = {
            "candidate_count": 1,
            "selected_count": 1,
            "selected_report_ids": [10],
            "summary": "最终建仓计划",
            "recommendations": [
                {
                    "code": "000001",
                    "name": "平安银行",
                    "action": "buy",
                    "suggested_amount": 12000,
                    "position_pct": 4,
                    "report_id": 10,
                }
            ],
        }
        first = position_plan_service.persist_position_plan(
            base_plan,
            db_path=self.db_path,
            payload={"stage": "final", "title": "最终计划 A"},
        )
        second = position_plan_service.persist_position_plan(
            {**base_plan, "selected_report_ids": [11]},
            db_path=self.db_path,
            payload={"stage": "final", "title": "最终计划 B"},
        )

        adopted_first = position_plan_service.adopt_position_plan(first["plan_id"], db_path=self.db_path)
        self.assertEqual(adopted_first["adoption_status"], "adopted")
        self.assertIsNotNone(adopted_first["confirmed_at"])
        self.assertEqual(adopted_first["confirmed_snapshot_json"]["cash"]["total_cash"], 253375.68)
        self.assertEqual(adopted_first["confirmed_snapshot_json"]["portfolio"]["position_count"], 1)

        adopted_second = position_plan_service.adopt_position_plan(second["plan_id"], db_path=self.db_path)
        old_first = position_plan_service.get_position_plan(first["plan_id"], db_path=self.db_path)
        self.assertEqual(adopted_second["adoption_status"], "adopted")
        self.assertEqual(old_first["adoption_status"], "superseded")

    def test_only_final_position_plan_can_be_adopted(self):
        plan = position_plan_service.persist_position_plan(
            {"recommendations": []},
            db_path=self.db_path,
            payload={"stage": "screening", "title": "初筛计划"},
        )

        with self.assertRaises(Exception) as ctx:
            position_plan_service.adopt_position_plan(plan["plan_id"], db_path=self.db_path)

        self.assertIn("最终建仓", str(ctx.exception))

    def test_abandon_draft_position_plan_marks_it_not_actionable(self):
        plan = position_plan_service.persist_position_plan(
            {"recommendations": [], "summary": "候选计划"},
            db_path=self.db_path,
            payload={"stage": "screening", "title": "待确认候选计划"},
        )

        abandoned = position_plan_service.abandon_position_plan(plan["plan_id"], db_path=self.db_path)
        detail = position_plan_service.get_position_plan(plan["plan_id"], db_path=self.db_path)

        self.assertEqual(abandoned["status"], "abandoned")
        self.assertEqual(abandoned["adoption_status"], "abandoned")
        self.assertEqual(detail["status"], "abandoned")
        self.assertEqual(detail["adoption_status"], "abandoned")

    def test_adopted_position_plan_cannot_be_abandoned(self):
        plan = position_plan_service.persist_position_plan(
            {"recommendations": [], "summary": "最终计划"},
            db_path=self.db_path,
            payload={"stage": "final", "title": "最终计划"},
        )
        position_plan_service.adopt_position_plan(plan["plan_id"], db_path=self.db_path)

        with self.assertRaises(Exception) as ctx:
            position_plan_service.abandon_position_plan(plan["plan_id"], db_path=self.db_path)

        self.assertIn("已采纳", str(ctx.exception))

    def test_list_data_snapshots_returns_validation_summary_without_full_payload(self):
        snapshot = {"market": {"quote": {"price": 10}}, "social": {}, "news": {}, "fundamentals": {}, "policy": {}, "hot_money": {}, "lockup": {}}
        validation = {"ok": True, "checked_layers": ["market", "social"]}
        summary = {"total_bytes": 1234, "layers": ["market", "social"]}
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO stock_data_snapshots
                    (code, name, snapshot_json, validation_json, summary_json, run_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", json.dumps(snapshot), json.dumps(validation), json.dumps(summary), "run-1"),
            )
            conn.commit()

        data = position_plan_service.list_data_snapshots(limit=10, code="000001", ok=True, db_path=self.db_path)

        self.assertEqual(data["count"], 1)
        self.assertNotIn("snapshot_json", data["snapshots"][0])
        self.assertTrue(data["snapshots"][0]["ok"])
        self.assertEqual(data["snapshots"][0]["summary"]["total_bytes"], 1234)
        self.assertEqual(data["summary"]["total"], 1)
        self.assertEqual(data["summary"]["complete"], 1)
        self.assertEqual(data["summary"]["complete_rate"], 100.0)

    def test_list_data_snapshots_summarizes_missing_layers(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO stock_data_snapshots
                    (code, name, snapshot_json, validation_json, summary_json, run_id)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    ("000001", "平安银行", "{}", json.dumps({"ok": False, "missing_layers": ["news", "policy"]}), "{}", "run-1"),
                    ("000002", "万科A", "{}", json.dumps({"ok": False, "missing_layers": ["news"]}), "{}", "run-1"),
                ],
            )
            conn.commit()

        data = position_plan_service.list_data_snapshots(limit=10, db_path=self.db_path)

        self.assertEqual(data["summary"]["total"], 2)
        self.assertEqual(data["summary"]["incomplete"], 2)
        self.assertEqual(data["summary"]["missing_layers"]["news"], 2)
        self.assertEqual(data["summary"]["missing_layers"]["policy"], 1)


if __name__ == "__main__":
    unittest.main()
