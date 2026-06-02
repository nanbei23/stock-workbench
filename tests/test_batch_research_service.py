import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import models.database as database
from api.batch_report_api import router as batch_report_router
from services import batch_report_service


COMPLETE_SNAPSHOT = {
    "market": {"quote": {"price": 10.0}},
    "social": {"items": ["ok"]},
    "news": {"items": ["ok"]},
    "fundamentals": {"items": ["ok"]},
    "policy": {"items": ["ok"]},
    "hot_money": {"items": ["ok"]},
    "lockup": {"items": ["ok"]},
}
COMPLETE_VALIDATION = {"ok": True, "missing_layers": [], "empty_layers": [], "layer_errors": {}}


class BatchResearchServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = batch_report_service.DB_PATH
        batch_report_service.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)
            db.executemany(
                "INSERT INTO watchlist (code, name, group_name, sort_order) VALUES (?, ?, ?, ?)",
                [
                    ("000001", "平安银行", "默认", 1),
                    ("000002", "万科A", "默认", 2),
                    ("600519", "贵州茅台", "默认", 3),
                ],
            )
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('custom_endpoint', 'https://api.example.com/v1')")
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('api_key', 'sk-test')")
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('deep_think_model', 'model-deep')")
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('cash_balance_default', '253375.680')")
            db.commit()

    def tearDown(self):
        batch_report_service.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def _insert_snapshot(self, code="000001", name="平安银行"):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO stock_data_snapshots
                    (code, name, snapshot_json, validation_json, summary_json, source, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    code,
                    name,
                    json.dumps(COMPLETE_SNAPSHOT, ensure_ascii=False),
                    json.dumps(COMPLETE_VALIDATION, ensure_ascii=False),
                    "{}",
                    "test",
                    "run-1",
                ),
            )
            db.commit()

    async def test_data_prefetch_job_skips_existing_complete_snapshot(self):
        self._insert_snapshot("000001", "平安银行")
        with patch("services.batch_report_service.batch_research.fetch_seven_layer_snapshot", new=AsyncMock(return_value=COMPLETE_SNAPSHOT)) as fetch:
            created = await batch_report_service.create_research_job(
                job_type="data_prefetch",
                codes=["000001", "000002"],
                auto_start=False,
            )
            await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        statuses = {item["code"]: item["status"] for item in job["items"]}
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["skipped_count"], 1)
        self.assertEqual(job["completed_count"], 1)
        self.assertEqual(statuses["000001"], "skipped")
        self.assertEqual(statuses["000002"], "completed")
        fetch.assert_awaited_once()

    async def test_report_generation_skips_recent_report_and_waits_for_snapshot(self):
        self._insert_snapshot("000001", "平安银行")
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO analysis_reports (code, task_id, signal, confidence, risk_score, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
                ("600519", "old-report", "BUY", 0.7, 30),
            )
            db.commit()

        llm_result = {
            "signal": "BUY",
            "confidence": 0.8,
            "risk_score": 28,
            "final_decision": "评级：BUY",
            "trader_plan": "分批建仓",
        }
        with patch("services.batch_report_service.batch_research._call_snapshot_llm", new=AsyncMock(return_value=llm_result)) as call_llm:
            created = await batch_report_service.create_research_job(
                job_type="report_generation",
                codes=["000001", "000002", "600519"],
                skip_recent_days=30,
                auto_start=False,
            )
            await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        statuses = {item["code"]: item["status"] for item in job["items"]}
        self.assertEqual(job["completed_count"], 1)
        self.assertEqual(job["skipped_count"], 1)
        self.assertEqual(job["waiting_count"], 1)
        self.assertEqual(statuses["000001"], "completed")
        self.assertEqual(statuses["000002"], "waiting_snapshot")
        self.assertEqual(statuses["600519"], "skipped")
        call_llm.assert_awaited_once()

    async def test_position_plan_job_uses_existing_reports_without_generating_reports(self):
        with sqlite3.connect(self.db_path) as db:
            db.executemany(
                """
                INSERT INTO analysis_reports
                    (code, task_id, signal, confidence, risk_score, final_decision, trader_plan, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                [
                    ("000001", "r1", "BUY", 0.8, 24, "建议建仓", "分批"),
                    ("600519", "r2", "HOLD", 0.6, 55, "等待", "暂不建仓"),
                ],
            )
            db.commit()

        created = await batch_report_service.create_research_job(
            job_type="position_plan",
            codes=["000001", "000002", "600519"],
            auto_start=False,
            output_dir=Path(self.tmp.name),
        )
        await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        result = json.loads(job["result_json"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual(result["plan"]["available_reports"], 2)
        self.assertEqual(result["plan"]["missing_reports"], 1)
        self.assertTrue(result["outputs"]["markdown"].endswith(".md"))

    async def test_position_plan_job_runs_multi_role_discussion_from_selected_full_reports(self):
        with sqlite3.connect(self.db_path) as db:
            db.executemany(
                """
                INSERT INTO analysis_reports
                    (code, task_id, signal, confidence, risk_score,
                     market_report, sentiment_report, news_report, fundamentals_report,
                     investment_debate, risk_debate, final_decision, trader_plan, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                [
                    (
                        "000001",
                        "r1",
                        "BUY",
                        0.82,
                        24,
                        "平安银行市场结构改善",
                        "情绪稳定",
                        "新闻无重大负面",
                        "基本面稳健",
                        "多方认为赔率改善",
                        "保守派要求控制仓位",
                        "最终建议分批买入",
                        "回踩分批建仓",
                    ),
                    (
                        "600519",
                        "r2",
                        "HOLD",
                        0.61,
                        55,
                        "贵州茅台趋势震荡",
                        "情绪分歧",
                        "新闻偏中性",
                        "基本面高质量但估值压力",
                        "多空分歧较大",
                        "风险经理提示估值风险",
                        "最终建议观察",
                        "等待突破再考虑",
                    ),
                ],
            )
            report_ids = [row[0] for row in db.execute("SELECT id FROM analysis_reports ORDER BY id ASC").fetchall()]
            db.commit()

        role_outputs = [
            "组合经理：优先小仓位配置平安银行，贵州茅台观察。",
            "风控经理：单票仓位不超过 10%，保留现金。",
            "交易员：分两批执行，设置失效条件。",
            "反方审查：警惕银行顺周期和白酒估值压力。",
            json.dumps(
                {
                    "summary": "组合级讨论后建议轻仓试探平安银行，贵州茅台暂缓。",
                    "actions": [
                        {"code": "000001", "action": "buy", "suggested_amount": 12000.123, "position_pct": 4.737, "reason": "报告完整且多角色共识较高"},
                        {"code": "600519", "action": "watch", "suggested_amount": 0, "position_pct": 0, "reason": "估值风险仍需等待"},
                    ],
                    "risk_controls": ["总仓位先控制在 10% 以内"],
                },
                ensure_ascii=False,
            ),
        ]

        with patch(
            "services.batch_report_service.batch_research._call_position_plan_role_llm",
            new=AsyncMock(side_effect=role_outputs),
            create=True,
        ) as call_role:
            created = await batch_report_service.create_research_job(
                job_type="position_plan",
                report_ids=report_ids,
                multi_role=True,
                auto_start=False,
                output_dir=Path(self.tmp.name),
            )
            await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        result = json.loads(job["result_json"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual([item["code"] for item in job["items"]], ["000001", "600519"])
        self.assertEqual(call_role.await_count, 5)
        first_prompt = call_role.await_args_list[0].args[1]
        self.assertIn("平安银行市场结构改善", first_prompt)
        self.assertIn("贵州茅台趋势震荡", first_prompt)
        self.assertTrue(result["plan"]["multi_role"])
        self.assertEqual(len(result["plan"]["role_discussion"]), 5)
        self.assertEqual(result["plan"]["recommendations"][0]["suggested_amount"], 12000.123)
        self.assertIn("multi_role_position_plan", result["outputs"]["markdown"])

    async def test_retry_failed_resets_only_failed_items(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001", "000002"],
            auto_start=False,
        )
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE batch_job_items SET status='failed', error='boom' WHERE code='000001'")
            db.execute("UPDATE batch_job_items SET status='completed', report_id=123 WHERE code='000002'")
            db.commit()

        result = await batch_report_service.retry_failed(created["job_id"], auto_start=False)
        job = batch_report_service.get_research_job(created["job_id"])
        statuses = {item["code"]: item["status"] for item in job["items"]}
        self.assertEqual(result["reset_count"], 1)
        self.assertEqual(statuses["000001"], "pending")
        self.assertEqual(statuses["000002"], "completed")

    async def test_mark_interrupted_jobs_recovers_stale_running_state(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001"],
            auto_start=False,
        )
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE batch_jobs SET status='running', current_code='000001' WHERE job_id=?", (created["job_id"],))
            db.execute("UPDATE batch_job_items SET status='running' WHERE job_id=?", (created["job_id"],))
            db.commit()

        changed = batch_report_service.mark_interrupted_jobs()
        job = batch_report_service.get_research_job(created["job_id"])

        self.assertEqual(changed, 1)
        self.assertEqual(job["status"], "interrupted")
        self.assertEqual(job["items"][0]["status"], "failed")


class BatchResearchApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(batch_report_router, prefix="/api")
        self.client = TestClient(app)

    def test_create_batch_research_route_uses_service_layer(self):
        with patch(
            "services.batch_report_service.create_research_job",
            new=AsyncMock(return_value={"job_id": "job-1", "status": "pending", "total_count": 2}),
        ) as create_job:
            resp = self.client.post("/api/batch-research/jobs", json={"job_type": "report_generation", "codes": ["000001", "000002"]})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["job_id"], "job-1")
        create_job.assert_awaited_once()

    def test_create_position_plan_route_forwards_selected_report_ids(self):
        with patch(
            "services.batch_report_service.create_research_job",
            new=AsyncMock(return_value={"job_id": "job-1", "status": "pending", "total_count": 2}),
        ) as create_job:
            resp = self.client.post(
                "/api/batch-research/jobs",
                json={"job_type": "position_plan", "report_ids": [10, 11], "multi_role": True},
            )

        self.assertEqual(resp.status_code, 200)
        create_job.assert_awaited_once()
        self.assertEqual(create_job.await_args.kwargs["report_ids"], [10, 11])
        self.assertTrue(create_job.await_args.kwargs["multi_role"])

    def test_batch_reports_route_is_compatibility_wrapper(self):
        with patch(
            "services.batch_report_service.create_research_job",
            new=AsyncMock(return_value={"job_id": "job-1", "status": "pending", "total_count": 2}),
        ) as create_job:
            resp = self.client.post("/api/batch-reports", json={"codes": ["000001", "000002"]})

        self.assertEqual(resp.status_code, 200)
        create_job.assert_awaited_once()
        self.assertEqual(create_job.await_args.kwargs["job_type"], "report_generation")


if __name__ == "__main__":
    unittest.main()
