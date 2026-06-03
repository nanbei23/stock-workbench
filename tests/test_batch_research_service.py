import json
import os
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
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('quick_think_model', 'model-quick')")
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

    async def test_data_prefetch_job_reports_snapshot_validation_summary(self):
        invalid_snapshot = {
            "market": {
                "quote": {
                    "ok": False,
                    "error": "No quote data found for A-stock '603342'; code may be invalid",
                    "payload": {},
                }
            },
            "social": {"items": ["ok"]},
            "news": {"items": ["ok"]},
            "fundamentals": {"items": ["ok"]},
            "policy": {"items": ["ok"]},
            "hot_money": {"items": ["ok"]},
            "lockup": {"items": ["ok"]},
        }
        with patch("services.batch_report_service.batch_research.fetch_seven_layer_snapshot", new=AsyncMock(return_value=invalid_snapshot)):
            created = await batch_report_service.create_research_job(
                job_type="data_prefetch",
                codes=["000002"],
                auto_start=False,
            )
            await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        item = job["items"][0]
        self.assertEqual(item["status"], "failed")
        self.assertIn("market: quote: No quote data found", item["error"])

    async def test_batch_research_rejects_implicit_all_watchlist_without_selection(self):
        with self.assertRaises(Exception) as ctx:
            await batch_report_service.create_research_job(
                job_type="report_generation",
                auto_start=False,
            )

        self.assertIn("请先选择股票", str(ctx.exception))

    async def test_batch_research_all_watchlist_requires_explicit_allow_all(self):
        created = await batch_report_service.create_research_job(
            job_type="data_prefetch",
            allow_all=True,
            auto_start=False,
        )

        job = batch_report_service.get_research_job(created["job_id"])
        self.assertEqual(job["total_count"], 3)
        self.assertEqual(sorted(item["code"] for item in job["items"]), ["000001", "000002", "600519"])

    async def test_batch_research_respects_trade_market_permissions(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO watchlist (code, name, group_name, sort_order) VALUES (?, ?, ?, ?)",
                ("688498", "源杰科技", "默认", 4),
            )
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('trade_market_star', 'false')")
            db.commit()

        preflight = batch_report_service.preflight_batch_models(
            job_type="data_prefetch",
            codes=["000001", "688498"],
        )
        created = await batch_report_service.create_research_job(
            job_type="data_prefetch",
            codes=["000001", "688498"],
            auto_start=False,
        )
        job = batch_report_service.get_research_job(created["job_id"])

        self.assertEqual(preflight["stock_count"], 1)
        self.assertEqual(preflight["excluded_by_permission_count"], 1)
        self.assertEqual(job["total_count"], 1)
        self.assertEqual(job["items"][0]["code"], "000001")

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
                analysis_mode="snapshot",
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

    async def test_report_generation_skip_recent_zero_forces_reanalysis(self):
        self._insert_snapshot("000001", "平安银行")
        self._insert_snapshot("600519", "贵州茅台")
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
                codes=["000001", "600519"],
                skip_recent_days=0,
                analysis_mode="snapshot",
                auto_start=False,
            )
            await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        statuses = {item["code"]: item["status"] for item in job["items"]}
        self.assertEqual(job["completed_count"], 2)
        self.assertEqual(job["skipped_count"], 0)
        self.assertEqual(statuses["600519"], "completed")
        self.assertEqual(call_llm.await_count, 2)

    async def test_report_generation_default_uses_snapshot_tradingagents(self):
        self._insert_snapshot("000001", "平安银行")
        role_outputs = [f"角色输出{i}" for i in range(15)] + [
            json.dumps(
                {
                    "signal": "BUY",
                    "confidence": 0.78,
                    "risk_score": 36,
                    "trader_plan": "分批建仓",
                    "final_decision": "BUY，风险可控",
                },
                ensure_ascii=False,
            )
        ]
        with patch(
            "services.batch_report_service.batch_research._call_snapshot_tradingagents_role_llm",
            new=AsyncMock(side_effect=role_outputs),
            create=True,
        ) as call_role, patch(
            "services.batch_report_service.batch_research._call_snapshot_llm",
            new=AsyncMock(return_value={}),
        ) as call_single:
            created = await batch_report_service.create_research_job(
                job_type="report_generation",
                codes=["000001"],
                skip_recent_days=0,
                auto_start=False,
            )
            await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        self.assertEqual(job["completed_count"], 1)
        self.assertEqual(call_role.await_count, 16)
        call_single.assert_not_awaited()
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT depth, model_mode, raw_state FROM analysis_reports WHERE code='000001'").fetchone()
        self.assertEqual(row[0], "standard")
        self.assertEqual(row[1], "balanced")
        self.assertIn("snapshot_tradingagents_state", json.loads(row[2]))

    async def test_report_generation_preserves_selected_depth_and_model_mode(self):
        self._insert_snapshot("000001", "平安银行")
        llm_result = {
            "signal": "BUY",
            "confidence": 0.8,
            "risk_score": 28,
            "final_decision": "评级：BUY",
            "trader_plan": "分批建仓",
        }
        with patch("services.batch_report_service.batch_research._call_snapshot_llm", new=AsyncMock(return_value=llm_result)):
            created = await batch_report_service.create_research_job(
                job_type="report_generation",
                codes=["000001"],
                skip_recent_days=0,
                analysis_mode="snapshot",
                analysis_depth="quick",
                model_mode="economy",
                snapshot_model_tier="quick",
                auto_start=False,
            )
            await batch_report_service.run_research_job(created["job_id"])

        with sqlite3.connect(self.db_path) as db:
            payload_row = db.execute("SELECT payload_json FROM batch_jobs WHERE job_id = ?", (created["job_id"],)).fetchone()
            row = db.execute("SELECT depth, model_mode, raw_state FROM analysis_reports WHERE code='000001'").fetchone()
        payload = json.loads(payload_row[0])
        self.assertEqual(payload["analysis_depth"], "quick")
        self.assertEqual(payload["model_mode"], "economy")
        self.assertEqual(payload["snapshot_model_tier"], "quick")
        self.assertEqual(row[0], "quick")
        self.assertEqual(row[1], "economy")
        self.assertEqual(json.loads(row[2])["model"], "model-quick")

    async def test_worker_model_provider_pool_overrides_primary_model(self):
        self._insert_snapshot("000001", "平安银行")
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('model_providers', ?)",
                (
                    json.dumps(
                        [
                            {
                                "id": "fast",
                                "name": "Fast Pool",
                                "base_url": "https://fast.example.com/v1",
                                "api_key": "sk-fast",
                                "default_model": "fast-default",
                                "quick_model": "fast-quick",
                                "deep_model": "fast-deep",
                            }
                        ],
                        ensure_ascii=False,
                    ),
                ),
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
                codes=["000001"],
                skip_recent_days=0,
                analysis_mode="snapshot",
                auto_start=False,
            )
            await batch_report_service.run_research_job(
                created["job_id"],
                worker_id="fast-worker",
                worker_model_provider_ids=["fast"],
                worker_model_tier="quick",
            )

        self.assertEqual(call_llm.await_args.args[1]["model"], "fast-quick")
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT raw_state FROM analysis_reports WHERE code='000001'").fetchone()
        self.assertEqual(json.loads(row[0])["model"], "fast-quick")

    async def test_snapshot_tradingagents_resume_uses_completed_role_steps(self):
        self._insert_snapshot("000001", "平安银行")
        with patch(
            "services.batch_report_service.batch_research._call_snapshot_tradingagents_role_llm",
            new=AsyncMock(
                side_effect=[
                    "质量门控通过",
                    "多头支持轻仓",
                    "空头提示回撤",
                    "研究经理建议 Overweight",
                    "交易员建议分批",
                    "激进风控同意小仓位",
                    "保守风控要求止损",
                    "中性风控建议控制总仓",
                    json.dumps(
                        {
                            "signal": "BUY",
                            "confidence": 0.78,
                            "risk_score": 36,
                            "trader_plan": "分批建仓",
                            "final_decision": "BUY，风险可控",
                        },
                        ensure_ascii=False,
                    ),
                ]
            ),
            create=True,
        ) as call_role:
            created = await batch_report_service.create_research_job(
                job_type="report_generation",
                codes=["000001"],
                skip_recent_days=0,
                auto_start=False,
            )
            job = batch_report_service.get_research_job(created["job_id"])
            item_id = job["items"][0]["id"]
            for index, role_key in enumerate(
                [
                    "market_analyst",
                    "social_analyst",
                    "news_analyst",
                    "fundamentals_analyst",
                    "policy_analyst",
                    "hot_money_analyst",
                    "lockup_analyst",
                ],
                start=1,
            ):
                batch_report_service.upsert_item_step(
                    item_id,
                    created["job_id"],
                    role_key,
                    role_key,
                    f"已有{role_key}",
                    step_order=index,
                    status="completed",
                )
            await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        self.assertEqual(job["completed_count"], 1)
        self.assertEqual(call_role.await_count, 9)
        self.assertEqual(job["items"][0]["step_total"], 16)
        self.assertEqual(job["items"][0]["step_completed"], 16)
        self.assertEqual(job["items"][0]["current_step"], "portfolio_manager")
        steps = batch_report_service.get_research_item_steps(job["items"][0]["id"])
        self.assertEqual(steps["count"], 16)
        self.assertEqual(steps["steps"][0]["role_key"], "market_analyst")
        self.assertEqual(steps["steps"][0]["content"], "已有market_analyst")

    async def test_snapshot_tradingagents_multi_rounds_keep_each_role_step(self):
        self._insert_snapshot("000001", "平安银行")
        role_outputs = [f"角色输出{i}" for i in range(20)] + [
            json.dumps(
                {
                    "signal": "OVERWEIGHT",
                    "confidence": 0.66,
                    "risk_score": 42,
                    "trader_plan": "两轮辩论后小仓位观察",
                    "final_decision": "OVERWEIGHT，等待回踩确认",
                },
                ensure_ascii=False,
            )
        ]
        with patch(
            "services.batch_report_service.batch_research._call_snapshot_tradingagents_role_llm",
            new=AsyncMock(side_effect=role_outputs),
            create=True,
        ) as call_role:
            created = await batch_report_service.create_research_job(
                job_type="report_generation",
                codes=["000001"],
                skip_recent_days=0,
                debate_rounds=2,
                risk_rounds=2,
                role_retry_backoff_seconds=0,
                auto_start=False,
            )
            await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        steps = batch_report_service.get_research_item_steps(job["items"][0]["id"])
        role_keys = [step["role_key"] for step in steps["steps"]]

        self.assertEqual(job["completed_count"], 1)
        self.assertEqual(call_role.await_count, 21)
        self.assertEqual(steps["count"], 21)
        self.assertIn("bull_researcher_r2", role_keys)
        self.assertIn("bear_researcher_r2", role_keys)
        self.assertIn("aggressive_risk_r2", role_keys)
        self.assertIn("neutral_risk_r2", role_keys)
        self.assertEqual(job["items"][0]["step_completed"], 21)

    async def test_snapshot_tradingagents_retries_transient_role_failure(self):
        self._insert_snapshot("000001", "平安银行")
        role_outputs = [Exception("Eastmoney rate limited")] + [f"角色输出{i}" for i in range(15)] + [
            json.dumps(
                {
                    "signal": "BUY",
                    "confidence": 0.72,
                    "risk_score": 33,
                    "trader_plan": "限流重试后继续生成",
                    "final_decision": "BUY，重试后完成",
                },
                ensure_ascii=False,
            )
        ]
        with patch(
            "services.batch_report_service.batch_research._call_snapshot_tradingagents_role_llm",
            new=AsyncMock(side_effect=role_outputs),
            create=True,
        ) as call_role:
            created = await batch_report_service.create_research_job(
                job_type="report_generation",
                codes=["000001"],
                skip_recent_days=0,
                role_retry_attempts=2,
                role_retry_backoff_seconds=0,
                auto_start=False,
            )
            await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        steps = batch_report_service.get_research_item_steps(job["items"][0]["id"])

        self.assertEqual(job["completed_count"], 1)
        self.assertEqual(call_role.await_count, 17)
        self.assertEqual(steps["steps"][0]["status"], "completed")
        self.assertEqual(steps["steps"][0]["error"], "")

    async def test_quota_exhaustion_falls_back_to_backup_model(self):
        self._insert_snapshot("000001", "平安银行")
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('model_providers', ?)",
                (
                    json.dumps(
                        [
                            {
                                "id": "backup",
                                "name": "Backup",
                                "base_url": "https://backup.example.com/v1",
                                "api_key": "sk-backup",
                                "models": ["backup-model"],
                                "default_model": "backup-model",
                                "deep_model": "backup-model",
                                "quick_model": "backup-model",
                            }
                        ],
                        ensure_ascii=False,
                    ),
                ),
            )
            db.commit()
        role_outputs = [Exception("insufficient_quota: balance exhausted")] + [f"角色输出{i}" for i in range(15)] + [
            json.dumps(
                {
                    "signal": "BUY",
                    "confidence": 0.72,
                    "risk_score": 33,
                    "trader_plan": "备用模型完成",
                    "final_decision": "BUY，备用模型完成",
                },
                ensure_ascii=False,
            )
        ]
        with patch(
            "services.batch_report_service.batch_research._call_snapshot_tradingagents_role_llm",
            new=AsyncMock(side_effect=role_outputs),
            create=True,
        ) as call_role:
            created = await batch_report_service.create_research_job(
                job_type="report_generation",
                codes=["000001"],
                skip_recent_days=0,
                role_retry_attempts=2,
                role_retry_backoff_seconds=0,
                model_fallback_enabled=True,
                fallback_provider_ids=["backup"],
                auto_start=False,
            )
            await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        runtime = json.loads(job["runtime_json"])
        configs = [call.args[2] for call in call_role.await_args_list]

        self.assertEqual(job["status"], "completed")
        self.assertEqual(configs[1]["model"], "backup-model")
        self.assertEqual(runtime["quota"]["events"][0]["status"], "fallback")
        self.assertEqual(runtime["quota"]["active_model"]["model"], "backup-model")

    async def test_quota_exhaustion_without_fallback_pauses_at_current_role(self):
        self._insert_snapshot("000001", "平安银行")
        with patch(
            "services.batch_report_service.batch_research._call_snapshot_tradingagents_role_llm",
            new=AsyncMock(side_effect=[Exception("quota_exceeded: daily limit reached")]),
            create=True,
        ) as call_role:
            created = await batch_report_service.create_research_job(
                job_type="report_generation",
                codes=["000001"],
                skip_recent_days=0,
                role_retry_attempts=1,
                role_retry_backoff_seconds=0,
                model_fallback_enabled=False,
                auto_start=False,
            )
            await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        steps = batch_report_service.get_research_item_steps(job["items"][0]["id"])
        runtime = json.loads(job["runtime_json"])

        self.assertEqual(call_role.await_count, 1)
        self.assertEqual(job["status"], "quota_paused")
        self.assertEqual(job["items"][0]["status"], "quota_paused")
        self.assertEqual(steps["steps"][0]["status"], "quota_paused")
        self.assertIn("daily limit", steps["steps"][0]["error"])
        self.assertEqual(runtime["quota"]["state"], "exhausted")
        self.assertEqual(runtime["quota"]["current_role"], "market_analyst")

    async def test_failed_stock_does_not_block_following_report_items(self):
        self._insert_snapshot("000001", "平安银行")
        self._insert_snapshot("000002", "万科A")
        role_outputs = [Exception("first stock failed")] + [f"角色输出{i}" for i in range(15)] + [
            json.dumps(
                {
                    "signal": "HOLD",
                    "confidence": 0.55,
                    "risk_score": 48,
                    "trader_plan": "等待确认",
                    "final_decision": "HOLD，第二只股票正常完成",
                },
                ensure_ascii=False,
            )
        ]
        with patch(
            "services.batch_report_service.batch_research._call_snapshot_tradingagents_role_llm",
            new=AsyncMock(side_effect=role_outputs),
            create=True,
        ) as call_role:
            created = await batch_report_service.create_research_job(
                job_type="report_generation",
                codes=["000001", "000002"],
                skip_recent_days=0,
                role_retry_attempts=1,
                role_retry_backoff_seconds=0,
                analysis_concurrency=1,
                auto_start=False,
            )
            await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        statuses = {item["code"]: item["status"] for item in job["items"]}

        self.assertEqual(job["completed_count"], 1)
        self.assertEqual(job["failed_count"], 1)
        self.assertEqual(statuses["000001"], "failed")
        self.assertEqual(statuses["000002"], "completed")
        self.assertEqual(call_role.await_count, 17)

    async def test_failed_role_step_can_retry_without_rerunning_completed_roles(self):
        self._insert_snapshot("000001", "平安银行")
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001"],
            skip_recent_days=0,
            auto_start=False,
        )
        job = batch_report_service.get_research_job(created["job_id"])
        item_id = job["items"][0]["id"]
        batch_report_service.upsert_item_step(
            item_id,
            created["job_id"],
            "market_analyst",
            "市场/技术分析师",
            "已有市场报告",
            step_order=1,
            status="completed",
        )
        batch_report_service.upsert_item_step(
            item_id,
            created["job_id"],
            "social_analyst",
            "情绪分析师",
            "",
            step_order=2,
            status="failed",
            error="timeout",
        )

        result = await batch_report_service.retry_failed(created["job_id"], auto_start=False)
        steps = batch_report_service.get_research_item_steps(item_id)

        self.assertEqual(result["reset_count"], 1)
        by_role = {step["role_key"]: step for step in steps["steps"]}
        self.assertEqual(by_role["market_analyst"]["status"], "completed")
        self.assertEqual(by_role["social_analyst"]["status"], "pending")
        self.assertEqual(by_role["social_analyst"]["retry_count"], 1)

    async def test_pause_job_stops_scheduling_new_items_after_current_item(self):
        self._insert_snapshot("000001", "平安银行")
        self._insert_snapshot("000002", "万科A")
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001", "000002"],
            skip_recent_days=0,
            auto_start=False,
        )

        batch_report_service.pause_job(created["job_id"])
        await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        statuses = {item["code"]: item["status"] for item in job["items"]}
        self.assertEqual(job["status"], "paused")
        self.assertTrue(job["pause_requested"])
        self.assertEqual(statuses["000001"], "pending")
        self.assertEqual(statuses["000002"], "pending")

    async def test_job_heartbeat_watchdog_marks_stalled_running_steps(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001"],
            auto_start=False,
        )
        job = batch_report_service.get_research_job(created["job_id"])
        item_id = job["items"][0]["id"]
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                UPDATE batch_jobs
                SET status='running', heartbeat_at=datetime('now', '-30 minutes')
                WHERE job_id=?
                """,
                (created["job_id"],),
            )
            db.execute(
                """
                UPDATE batch_job_items
                SET status='running'
                WHERE id=?
                """,
                (item_id,),
            )
            db.commit()
        batch_report_service.upsert_item_step(
            item_id,
            created["job_id"],
            "market_analyst",
            "市场/技术分析师",
            "",
            step_order=1,
            status="running",
        )

        changed = batch_report_service.mark_stalled_jobs(stale_minutes=10)
        job = batch_report_service.get_research_job(created["job_id"])
        steps = batch_report_service.get_research_item_steps(item_id)

        self.assertEqual(changed, 1)
        self.assertEqual(job["status"], "interrupted")
        self.assertEqual(job["items"][0]["status"], "failed")
        self.assertEqual(steps["steps"][0]["status"], "failed")
        self.assertIn("心跳超时", steps["steps"][0]["error"])

    async def test_worker_claim_uses_lease_to_prevent_double_pickup(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001"],
            auto_start=False,
        )

        first = batch_report_service.claim_next_job(worker_id="worker-a", lease_seconds=60)
        second = batch_report_service.claim_next_job(worker_id="worker-b", lease_seconds=60, cooperative=False)

        self.assertEqual(first["job_id"], created["job_id"])
        self.assertIsNone(second)
        job = batch_report_service.get_research_job(created["job_id"])
        runtime = json.loads(job["runtime_json"])
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["lease_owner"], "worker-a")
        self.assertTrue(job["lease_token"])
        self.assertEqual(runtime["worker"]["lease_owner"], "worker-a")

    async def test_position_plan_is_left_pending_for_worker_pool_by_default(self):
        with patch("services.batch_report_service._schedule_job") as schedule:
            created = await batch_report_service.create_research_job(
                job_type="position_plan",
                codes=["000001", "000002"],
            )

        job = batch_report_service.get_research_job(created["job_id"])

        schedule.assert_not_called()
        self.assertEqual(job["status"], "pending")
        self.assertFalse(job["worker_id"])

    async def test_running_position_plan_without_valid_lease_can_be_reclaimed(self):
        created = await batch_report_service.create_research_job(
            job_type="position_plan",
            codes=["000001", "000002"],
            auto_start=False,
        )
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                UPDATE batch_jobs
                SET status='running',
                    worker_id='web-85050',
                    heartbeat_at=datetime('now', '-20 minutes'),
                    lease_owner=NULL,
                    lease_token=NULL,
                    lease_until=NULL
                WHERE job_id=?
                """,
                (created["job_id"],),
            )
            db.commit()

        reclaimed = batch_report_service.claim_next_job(worker_id="worker-b", lease_seconds=60)
        job = batch_report_service.get_research_job(created["job_id"])

        self.assertEqual(reclaimed["job_id"], created["job_id"])
        self.assertEqual(job["lease_owner"], "worker-b")
        self.assertEqual(job["worker_id"], "worker-b")

    async def test_retry_failed_position_plan_stays_pending_for_worker_pool(self):
        created = await batch_report_service.create_research_job(
            job_type="position_plan",
            codes=["000001", "000002"],
            auto_start=False,
        )
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                UPDATE batch_job_items
                SET status='failed', error='quota exhausted'
                WHERE job_id=? AND code='000001'
                """,
                (created["job_id"],),
            )
            db.execute(
                """
                UPDATE batch_jobs
                SET status='failed', failed_count=1, error='quota exhausted'
                WHERE job_id=?
                """,
                (created["job_id"],),
            )
            db.commit()

        with patch("services.batch_report_service._schedule_job") as schedule:
            result = await batch_report_service.retry_failed(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])

        schedule.assert_not_called()
        self.assertEqual(result["status"], "pending")
        self.assertEqual(job["status"], "pending")

    async def test_expired_lease_can_be_reclaimed_by_another_worker(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001"],
            auto_start=False,
        )
        batch_report_service.claim_next_job(worker_id="worker-a", lease_seconds=60)
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "UPDATE batch_jobs SET lease_until=datetime('now', '-1 minute') WHERE job_id=?",
                (created["job_id"],),
            )
            db.commit()

        reclaimed = batch_report_service.claim_next_job(worker_id="worker-b", lease_seconds=60)

        self.assertEqual(reclaimed["job_id"], created["job_id"])
        self.assertEqual(batch_report_service.get_research_job(created["job_id"])["lease_owner"], "worker-b")

    async def test_worker_can_join_running_report_job_with_pending_items(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001", "000002"],
            auto_start=False,
        )

        first = batch_report_service.claim_next_job(worker_id="worker-a", lease_seconds=60)
        joined = batch_report_service.claim_next_job(worker_id="worker-b", lease_seconds=60)

        self.assertEqual(first["job_id"], created["job_id"])
        self.assertEqual(joined["job_id"], created["job_id"])
        runtime = json.loads(batch_report_service.get_research_job(created["job_id"])["runtime_json"])
        self.assertIn("worker-b", runtime["cooperative_workers"])

    async def test_item_leases_split_work_between_workers(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001", "000002"],
            auto_start=False,
        )

        first = batch_report_service._claim_runnable_items(
            created["job_id"],
            worker_id="worker-a",
            lease_token="lease-a",
            limit=1,
        )
        second = batch_report_service._claim_runnable_items(
            created["job_id"],
            worker_id="worker-b",
            lease_token="lease-b",
            limit=1,
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first[0]["id"], second[0]["id"])
        items = batch_report_service.get_research_items(created["job_id"])["items"]
        leases = {item["code"]: item["lease_owner"] for item in items}
        self.assertEqual(set(leases.values()), {"worker-a", "worker-b"})

    async def test_finalize_lock_can_only_be_claimed_once(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001"],
            auto_start=False,
        )

        first = batch_report_service._try_claim_finalize(created["job_id"])
        second = batch_report_service._try_claim_finalize(created["job_id"])
        batch_report_service._mark_finalize_completed(created["job_id"], status="completed")
        runtime = json.loads(batch_report_service.get_research_job(created["job_id"])["runtime_json"])

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(runtime["finalize"]["state"], "completed")

    async def test_guard_pause_stops_batch_after_consecutive_failures(self):
        self._insert_snapshot("000001", "平安银行")
        self._insert_snapshot("000002", "万科A")
        with patch(
            "services.batch_report_service.batch_research._call_snapshot_tradingagents_role_llm",
            new=AsyncMock(side_effect=[Exception("network timeout"), Exception("network timeout")]),
            create=True,
        ):
            created = await batch_report_service.create_research_job(
                job_type="report_generation",
                codes=["000001", "000002", "600519"],
                skip_recent_days=0,
                role_retry_attempts=1,
                role_retry_backoff_seconds=0,
                max_consecutive_failures=1,
                auto_start=False,
            )
            await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        runtime = json.loads(job["runtime_json"])
        statuses = {item["code"]: item["status"] for item in job["items"]}

        self.assertEqual(job["status"], "guard_paused")
        self.assertEqual(runtime["guard"]["reason"], "连续失败数达到上限")
        self.assertEqual(statuses["000001"], "failed")
        self.assertEqual(statuses["000002"], "pending")

    async def test_runtime_failure_records_error_type_and_next_retry_hint(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001"],
            auto_start=False,
        )

        batch_report_service._record_runtime_failure(
            created["job_id"],
            "llm",
            "429 rate limit exceeded",
            base_concurrency=3,
        )

        runtime = json.loads(batch_report_service.get_research_job(created["job_id"])["runtime_json"])
        failure = runtime["llm"]["last_failure"]
        self.assertEqual(failure["error_type"], "rate_limit")
        self.assertGreaterEqual(failure["retry_after_seconds"], 30)
        self.assertLessEqual(runtime["llm"]["effective_concurrency"], 2)

    async def test_worker_status_reports_online_and_stale_workers(self):
        fresh = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001"],
            auto_start=False,
        )
        stale = await batch_report_service.create_research_job(
            job_type="data_prefetch",
            codes=["000002"],
            auto_start=False,
        )
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                UPDATE batch_jobs
                SET status='running', worker_id='fresh-worker', heartbeat_at=datetime('now'), lease_owner='fresh-worker',
                    lease_until=datetime('now', '+5 minutes')
                WHERE job_id=?
                """,
                (fresh["job_id"],),
            )
            db.execute(
                """
                UPDATE batch_jobs
                SET status='running', worker_id='stale-worker', heartbeat_at=datetime('now', '-40 minutes'), lease_owner='stale-worker',
                    lease_until=datetime('now', '-20 minutes')
                WHERE job_id=?
                """,
                (stale["job_id"],),
            )
            db.commit()

        status = batch_report_service.get_worker_status(stale_minutes=15)
        by_id = {row["worker_id"]: row for row in status["workers"]}

        self.assertEqual(by_id["fresh-worker"]["state"], "online")
        self.assertEqual(by_id["stale-worker"]["state"], "stale")
        self.assertEqual(status["summary"]["online"], 1)
        self.assertEqual(status["summary"]["stale"], 1)

    async def test_reclaim_stale_workers_requeues_running_items(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001", "000002"],
            auto_start=False,
        )
        with sqlite3.connect(self.db_path) as db:
            item_id = db.execute(
                "SELECT id FROM batch_job_items WHERE job_id=? AND code='000001'",
                (created["job_id"],),
            ).fetchone()[0]
            db.execute(
                """
                UPDATE batch_jobs
                SET status='running', worker_id='worker-stale', heartbeat_at=datetime('now', '-40 minutes'),
                    lease_owner='worker-stale', lease_until=datetime('now', '-20 minutes'), current_code='000001'
                WHERE job_id=?
                """,
                (created["job_id"],),
            )
            db.execute(
                """
                UPDATE batch_job_items
                SET status='running', lease_owner='worker-stale', lease_token='old-token',
                    lease_until=datetime('now', '-20 minutes')
                WHERE id=?
                """,
                (item_id,),
            )
            db.execute(
                """
                INSERT INTO batch_worker_heartbeats
                    (worker_id, state, last_seen_at, current_job_id, current_item_id, current_code)
                VALUES
                    ('worker-stale', 'running', datetime('now', '-40 minutes'), ?, ?, '000001')
                """,
                (created["job_id"], item_id),
            )
            db.commit()

        result = batch_report_service.reclaim_stale_workers(stale_minutes=15)
        job = batch_report_service.get_research_job(created["job_id"])
        reclaimed_item = next(item for item in job["items"] if item["code"] == "000001")

        self.assertEqual(result["workers"], ["worker-stale"])
        self.assertEqual(result["reclaimed_items"], 1)
        self.assertEqual(job["status"], "pending")
        self.assertEqual(reclaimed_item["status"], "pending")
        self.assertIsNone(reclaimed_item["lease_owner"])

    async def test_launchd_worker_scripts_exist_and_reference_project_root(self):
        root = Path(__file__).resolve().parents[1]
        scripts = [
            root / "scripts" / "worker_install_launchd.sh",
            root / "scripts" / "worker_start.sh",
            root / "scripts" / "worker_stop.sh",
            root / "scripts" / "worker_status.sh",
            root / "scripts" / "worker_logs.sh",
        ]
        for script in scripts:
            self.assertTrue(script.exists(), f"missing {script.name}")
            self.assertTrue(os.access(script, os.X_OK), f"{script.name} is not executable")
            self.assertIn("stock-workbench-local", script.read_text())

    async def test_job_logs_are_written_for_lifecycle_events(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001"],
            auto_start=False,
        )
        batch_report_service.log_job_event(created["job_id"], "info", "custom_event", "测试日志", {"x": 1})

        logs = batch_report_service.get_job_logs(created["job_id"])
        events = [row["event"] for row in logs["logs"]]

        self.assertIn("job_created", events)
        self.assertIn("custom_event", events)
        self.assertEqual(logs["logs"][0]["job_id"], created["job_id"])

    async def test_report_job_locks_snapshot_ids_for_reproducibility(self):
        self._insert_snapshot("000001", "平安银行")
        role_outputs = [f"角色输出{i}" for i in range(15)] + [
            json.dumps(
                {
                    "signal": "BUY",
                    "confidence": 0.7,
                    "risk_score": 30,
                    "trader_plan": "使用锁定快照",
                    "final_decision": "BUY",
                },
                ensure_ascii=False,
            )
        ]
        with patch(
            "services.batch_report_service.batch_research._call_snapshot_tradingagents_role_llm",
            new=AsyncMock(side_effect=role_outputs),
            create=True,
        ):
            created = await batch_report_service.create_research_job(
                job_type="report_generation",
                codes=["000001"],
                skip_recent_days=0,
                auto_start=False,
            )
            with sqlite3.connect(self.db_path) as db:
                snapshot_id = db.execute("SELECT id FROM stock_data_snapshots WHERE code='000001'").fetchone()[0]
            await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        locked = json.loads(job["input_snapshot_json"])
        self.assertEqual(job["items"][0]["locked_snapshot_id"], snapshot_id)
        self.assertEqual(locked["snapshots"]["000001"]["snapshot_id"], snapshot_id)

    async def test_completed_report_job_writes_quality_and_post_action_artifacts(self):
        self._insert_snapshot("000001", "平安银行")
        role_outputs = [f"角色输出{i}" for i in range(15)] + [
            json.dumps(
                {
                    "signal": "BUY",
                    "confidence": 0.71,
                    "risk_score": 35,
                    "trader_plan": "批次后处理",
                    "final_decision": "BUY，生成完整报告用于 QA",
                },
                ensure_ascii=False,
            )
        ]
        with patch(
            "services.batch_report_service.batch_research._call_snapshot_tradingagents_role_llm",
            new=AsyncMock(side_effect=role_outputs),
            create=True,
        ):
            created = await batch_report_service.create_research_job(
                job_type="report_generation",
                codes=["000001"],
                skip_recent_days=0,
                output_dir=Path(self.tmp.name),
                auto_start=False,
            )
            await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        quality = json.loads(job["quality_json"])
        post_actions = json.loads(job["post_actions_json"])
        artifacts = batch_report_service.get_job_artifacts(created["job_id"])

        self.assertEqual(quality["total_items"], 1)
        self.assertEqual(quality["completed_reports"], 1)
        self.assertEqual(quality["missing_report_ids"], 0)
        self.assertTrue(post_actions["summary_markdown"].endswith(".md"))
        self.assertGreaterEqual(artifacts["count"], 2)

    async def test_batch_analysis_summarizes_signals_industries_breadth_and_market(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO batch_jobs (job_id, job_type, name, status, total_count, completed_count)
                VALUES ('job-analysis', 'report_generation', '测试批量分析', 'completed', 3, 3)
                """
            )
            snapshots = [
                ("000001", "平安银行", 1.234),
                ("000002", "万科A", -2.345),
                ("600519", "贵州茅台", 0.000),
            ]
            for code, name, change_pct in snapshots:
                cursor = db.execute(
                    """
                    INSERT INTO stock_data_snapshots
                        (code, name, snapshot_json, validation_json, summary_json, source, run_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        code,
                        name,
                        json.dumps({"market": {"quote": {"price": 10.0, "change_pct": change_pct}}}, ensure_ascii=False),
                        json.dumps(COMPLETE_VALIDATION, ensure_ascii=False),
                        "{}",
                        "test",
                        "job-analysis",
                    ),
                )
                report = db.execute(
                    """
                    INSERT INTO analysis_reports
                        (code, task_id, signal, confidence, risk_score, final_decision, trader_plan, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (
                        code,
                        f"report-{code}",
                        {"000001": "BUY", "000002": "SELL", "600519": "HOLD"}[code],
                        {"000001": 0.8, "000002": 0.7, "600519": 0.5}[code],
                        {"000001": 25, "000002": 70, "600519": 50}[code],
                        "批量分析样本",
                        "执行计划",
                    ),
                )
                db.execute(
                    """
                    INSERT INTO batch_job_items
                        (job_id, code, name, status, snapshot_id, report_id)
                    VALUES (?, ?, ?, 'completed', ?, ?)
                    """,
                    ("job-analysis", code, name, cursor.lastrowid, report.lastrowid),
                )
            db.commit()

        with patch(
            "services.batch_report_service.quote_service.get_indices",
            new=AsyncMock(return_value={
                "sh": {"name": "上证指数", "change_pct": 0.5},
                "sz": {"name": "深证成指", "change_pct": -0.2},
            }),
        ):
            result = await batch_report_service.get_batch_analysis("job-analysis")

        self.assertEqual(result["overview"]["total"], 3)
        self.assertEqual(result["signal_distribution"]["BUY"], 1)
        self.assertEqual(result["signal_distribution"]["SELL"], 1)
        self.assertEqual(result["breadth"]["up"], 1)
        self.assertEqual(result["breadth"]["down"], 1)
        self.assertIn("默认", result["industry_groups"])
        self.assertEqual(result["market"]["indices_count"], 2)
        self.assertTrue(result["observations"])

    async def test_model_preflight_estimates_calls_and_fallback_capacity(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('model_providers', ?)",
                (
                    json.dumps(
                        [
                            {
                                "id": "backup",
                                "name": "Backup",
                                "base_url": "https://backup.example.com/v1",
                                "api_key": "sk-backup",
                                "models": ["backup-model"],
                                "default_model": "backup-model",
                            }
                        ],
                        ensure_ascii=False,
                    ),
                ),
            )
            db.commit()

        result = batch_report_service.preflight_batch_models(
            job_type="report_generation",
            codes=["000001", "000002"],
            debate_rounds=2,
            risk_rounds=2,
            model_fallback_enabled=True,
            fallback_provider_ids=["backup"],
        )

        self.assertEqual(result["stock_count"], 2)
        self.assertEqual(result["role_calls_per_stock"], 21)
        self.assertEqual(result["estimated_role_calls"], 42)
        self.assertTrue(result["primary_ready"])
        self.assertEqual(result["fallback_count"], 1)
        self.assertEqual(result["status"], "ok")

    async def test_preflight_estimates_worker_throughput_duration_and_model_pool_risk(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('model_providers', ?)",
                (
                    json.dumps(
                        [
                            {"id": "mimo", "name": "MiMo", "base_url": "https://mimo.example/v1", "api_key": "sk", "deep_model": "mimo-deep"},
                            {"id": "backup", "name": "Backup", "base_url": "", "api_key": "", "deep_model": "backup-deep"},
                        ],
                        ensure_ascii=False,
                    ),
                ),
            )
            db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('batch_worker_pool', ?)",
                (
                    json.dumps(
                        [
                            {"id": "w1", "name": "Worker 1", "enabled": True, "provider_ids": ["mimo"], "model_tier": "deep"},
                            {"id": "w2", "name": "Worker 2", "enabled": True, "provider_ids": ["backup"], "model_tier": "deep"},
                            {"id": "w3", "name": "Worker 3", "enabled": False, "provider_ids": ["mimo"], "model_tier": "quick"},
                        ],
                        ensure_ascii=False,
                    ),
                ),
            )
            db.commit()

        result = batch_report_service.preflight_batch_models(
            job_type="report_generation",
            codes=["000001", "000002", "600519"],
            debate_rounds=2,
            risk_rounds=2,
            allowed_worker_ids=["w1", "w2"],
            primary_provider_ids=["mimo"],
            model_fallback_enabled=True,
            fallback_provider_ids=["backup"],
        )

        self.assertEqual(result["worker_count"], 2)
        self.assertEqual(result["enabled_worker_count"], 2)
        self.assertEqual(result["selected_worker_ids"], ["w1", "w2"])
        self.assertEqual(result["estimated_role_calls"], 63)
        self.assertGreater(result["throughput"]["role_calls_per_hour"], 0)
        self.assertIn("duration_range_hours", result)
        self.assertTrue(any(risk["provider_id"] == "backup" for risk in result["model_pool_risks"]))

    async def test_create_job_persists_task_level_worker_and_model_strategy(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001", "000002"],
            allowed_worker_ids=["worker-a"],
            primary_provider_ids=["mimo"],
            fallback_provider_ids=["backup"],
            model_fallback_enabled=True,
            quota_exhausted_action="switch_model",
            auto_start=False,
        )

        job = batch_report_service.get_research_job(created["job_id"])
        payload = json.loads(job["payload_json"])
        self.assertEqual(payload["allowed_worker_ids"], ["worker-a"])
        self.assertEqual(payload["primary_provider_ids"], ["mimo"])
        self.assertEqual(payload["fallback_provider_ids"], ["backup"])
        self.assertEqual(payload["quota_exhausted_action"], "switch_model")

    async def test_allowed_worker_job_stays_pending_for_background_worker(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001"],
            allowed_worker_ids=["worker-a"],
        )

        job = batch_report_service.get_research_job(created["job_id"])
        self.assertEqual(job["status"], "pending")
        self.assertEqual(json.loads(job["payload_json"])["allowed_worker_ids"], ["worker-a"])

    async def test_claim_next_job_respects_allowed_worker_ids(self):
        first = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001"],
            allowed_worker_ids=["worker-a"],
            auto_start=False,
        )
        second = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000002"],
            allowed_worker_ids=[],
            auto_start=False,
        )

        claimed_b = batch_report_service.claim_next_job(worker_id="worker-b", cooperative=False)
        self.assertEqual(claimed_b["job_id"], second["job_id"])
        claimed_a = batch_report_service.claim_next_job(worker_id="worker-a", cooperative=False)
        self.assertEqual(claimed_a["job_id"], first["job_id"])

    async def test_retry_failed_can_filter_error_type_and_override_model_pool(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001", "000002"],
            auto_start=False,
        )
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE batch_job_items SET status='failed', error_type='network', error='net' WHERE code='000001'")
            db.execute("UPDATE batch_job_items SET status='failed', error_type='quota_exhausted', error='quota' WHERE code='000002'")
            db.commit()

        result = await batch_report_service.retry_failed(
            created["job_id"],
            error_type="network",
            model_provider_ids=["backup"],
            model_tier="quick",
            auto_start=False,
        )
        job = batch_report_service.get_research_job(created["job_id"])
        statuses = {item["code"]: item["status"] for item in job["items"]}
        payload = json.loads(job["payload_json"])

        self.assertEqual(result["reset_count"], 1)
        self.assertEqual(statuses["000001"], "pending")
        self.assertEqual(statuses["000002"], "failed")
        self.assertEqual(payload["primary_provider_ids"], ["backup"])
        self.assertEqual(payload["snapshot_model_tier"], "quick")

    async def test_worker_status_includes_configured_model_pool_counts_and_runtime_model(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001", "000002"],
            auto_start=False,
        )
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('model_providers', ?)",
                (
                    json.dumps(
                        [
                            {"id": "mimo", "name": "MiMo", "base_url": "https://mimo.example/v1", "api_key": "sk", "deep_model": "mimo-deep"},
                            {"id": "backup", "name": "Backup", "base_url": "https://backup.example/v1", "api_key": "sk", "deep_model": "backup-deep"},
                        ],
                        ensure_ascii=False,
                    ),
                ),
            )
            db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('batch_worker_pool', ?)",
                (
                    json.dumps(
                        [{"id": "worker-a", "name": "Worker A", "enabled": True, "provider_ids": ["mimo", "backup"], "model_tier": "deep"}],
                        ensure_ascii=False,
                    ),
                ),
            )
            db.execute(
                """
                UPDATE batch_jobs
                SET status='running', worker_id='worker-a', lease_owner='worker-a',
                    current_code='000001', heartbeat_at=datetime('now'), lease_until=datetime('now', '+10 minutes'),
                    runtime_json=?
                WHERE job_id=?
                """,
                (
                    json.dumps({"quota": {"active_model": {"model": "backup-deep"}, "events": [{"fallback_model": "backup-deep"}]}}),
                    created["job_id"],
                ),
            )
            db.execute("UPDATE batch_job_items SET status='completed' WHERE code='000001'")
            db.execute("UPDATE batch_job_items SET status='failed', error_type='network' WHERE code='000002'")
            db.commit()

        status = batch_report_service.get_worker_status()
        worker = next(item for item in status["workers"] if item["worker_id"] == "worker-a")

        self.assertEqual(worker["state"], "running")
        self.assertEqual(worker["current_code"], "000001")
        self.assertEqual(worker["counts"]["completed"], 1)
        self.assertEqual(worker["counts"]["failed"], 1)
        self.assertEqual(worker["model_pool"][0]["provider_id"], "mimo")
        self.assertEqual(worker["current_model"], "backup-deep")
        self.assertEqual(worker["fallback_model"], "backup-deep")

    async def test_worker_status_marks_configured_workers_without_heartbeat_as_not_started(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('batch_worker_pool', ?)",
                (
                    json.dumps(
                        [
                            {"id": "worker-1", "name": "Worker 1", "enabled": True, "provider_ids": ["mimo"], "model_tier": "deep"},
                            {"id": "worker-2", "name": "Worker 2", "enabled": True, "provider_ids": ["mimo"], "model_tier": "deep"},
                            {"id": "worker-3", "name": "Worker 3", "enabled": True, "provider_ids": ["backup"], "model_tier": "deep"},
                        ],
                        ensure_ascii=False,
                    ),
                ),
            )
            db.commit()

        status = batch_report_service.get_worker_status()
        by_id = {row["worker_id"]: row for row in status["workers"]}

        self.assertEqual(by_id["worker-1"]["state"], "not_started")
        self.assertEqual(by_id["worker-2"]["state"], "not_started")
        self.assertEqual(by_id["worker-3"]["state"], "not_started")
        self.assertEqual(status["summary"]["idle"], 0)
        self.assertEqual(status["summary"]["not_started"], 3)
        self.assertEqual(status["summary"]["online"], 0)

    async def test_worker_status_uses_independent_heartbeat_for_online_idle_workers(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES ('batch_worker_pool', ?)",
                (
                    json.dumps(
                        [{"id": "worker-1", "name": "Worker 1", "enabled": True, "provider_ids": ["mimo"], "model_tier": "deep"}],
                        ensure_ascii=False,
                    ),
                ),
            )
            db.commit()

        batch_report_service.record_worker_heartbeat(
            "worker-1",
            state="idle",
            model_provider_ids=["mimo"],
            model_tier="deep",
            last_result={"ran": False},
        )
        status = batch_report_service.get_worker_status()
        worker = next(item for item in status["workers"] if item["worker_id"] == "worker-1")

        self.assertEqual(worker["state"], "idle")
        self.assertTrue(worker["heartbeat_at"])
        self.assertEqual(worker["pid"], os.getpid())
        self.assertEqual(worker["last_result_json"]["ran"], False)
        self.assertEqual(status["summary"]["idle"], 1)
        self.assertEqual(status["summary"]["not_started"], 0)

    async def test_worker_status_reports_position_plan_stage_from_heartbeat(self):
        created = await batch_report_service.create_research_job(
            job_type="position_plan",
            codes=["000001", "000002"],
            auto_start=False,
        )
        batch_report_service.record_worker_heartbeat(
            "worker-plan",
            state="running",
            model_provider_ids=["mimo"],
            model_tier="deep",
            current_job_id=created["job_id"],
            current_job_type="position_plan",
            current_stage="组合级多角色讨论",
        )
        status = batch_report_service.get_worker_status()
        worker = next(item for item in status["workers"] if item["worker_id"] == "worker-plan")

        self.assertEqual(worker["state"], "running")
        self.assertEqual(worker["job_id"], created["job_id"])
        self.assertEqual(worker["job_type"], "position_plan")
        self.assertEqual(worker["current_stage"], "组合级多角色讨论")
        self.assertEqual(worker["counts"]["pending"], 2)

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

        with patch(
            "services.batch_report_service.batch_research.collect_position_plan_market_context",
            new=AsyncMock(return_value={"captured_at": "2026-06-03T10:15:00", "status": "ok", "items": {}, "summary": []}),
        ):
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
        self.assertTrue(result["position_plan"]["plan_id"].startswith("pp-"))

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
            "services.batch_report_service.batch_research.collect_position_plan_market_context",
            new=AsyncMock(return_value={"captured_at": "2026-06-03T10:15:00", "status": "ok", "items": {}, "summary": []}),
        ), patch(
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

    async def test_position_plan_job_adds_decision_market_context_to_prompt_and_storage(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('cash_balance_default', '253375.680')")
            db.execute(
                """
                INSERT INTO portfolio
                    (code, name, total_shares, available_shares, avg_cost, current_price, market_value, unrealized_pnl, unrealized_pnl_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", 1000, 1000, 10.000, 11.000, 11000.000, 1000.000, 10.000),
            )
            db.executemany(
                """
                INSERT INTO analysis_reports
                    (code, task_id, signal, confidence, risk_score,
                     market_report, final_decision, trader_plan, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                [
                    ("000001", "r1", "BUY", 0.82, 24, "旧报告价格 10.000", "建议分批买入", "回踩买入"),
                    ("600519", "r2", "HOLD", 0.61, 55, "旧报告震荡", "暂缓", "等待突破"),
                ],
            )
            report_ids = [row[0] for row in db.execute("SELECT id FROM analysis_reports ORDER BY id ASC").fetchall()]
            db.commit()

        market_context = {
            "captured_at": "2026-06-03T10:15:00",
            "status": "partial",
            "items": {
                "000001": {
                    "status": "ok",
                    "quote": {"code": "000001", "name": "平安银行", "price": 11.23, "change_pct": 2.345},
                    "kline_summary": {"day": {"count": 60, "last_close": 11.23, "return_5d_pct": 3.456}},
                },
                "600519": {"status": "failed", "error": "timeout"},
            },
            "summary": [
                {"code": "000001", "price": 11.23, "change_pct": 2.345, "status": "ok"},
                {"code": "600519", "status": "failed", "error": "timeout"},
            ],
        }
        role_outputs = [
            "组合经理：参考实时行情后仅小仓位。",
            "风控经理：实时校准后保留现金。",
            "交易员：等待分时回落。",
            "反方审查：行情过热。",
            json.dumps(
                {
                    "summary": "实时行情校准后只观察。",
                    "actions": [{"code": "000001", "action": "watch", "suggested_amount": 0, "position_pct": 0, "reason": "当前涨幅偏高"}],
                    "risk_controls": ["缺失实时行情的股票不直接执行"],
                },
                ensure_ascii=False,
            ),
        ]

        with patch(
            "services.batch_report_service.batch_research.collect_position_plan_market_context",
            new=AsyncMock(return_value=market_context),
            create=True,
        ) as collect_context, patch(
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

        collect_context.assert_awaited_once()
        first_prompt = call_role.await_args_list[0].args[1]
        self.assertIn("决策实时行情快照", first_prompt)
        self.assertIn("11.230", first_prompt)
        self.assertIn("缺失实时行情", first_prompt)
        self.assertIn("当前组合与资金快照", first_prompt)
        self.assertIn("可用现金：253375.680", first_prompt)
        self.assertIn("平安银行", first_prompt)
        self.assertIn("1000.000", first_prompt)
        self.assertIn("建仓建议必须是调仓建议，不允许默认全仓重建", first_prompt)

        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            row = db.execute("SELECT decision_market_snapshot_json, market_context_captured_at FROM position_plans").fetchone()
        saved_context = json.loads(row["decision_market_snapshot_json"])
        self.assertEqual(saved_context["items"]["000001"]["quote"]["price"], 11.23)
        self.assertEqual(row["market_context_captured_at"], "2026-06-03T10:15:00")

    async def test_position_plan_auto_generates_today_reports_for_current_holdings(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO portfolio
                    (code, name, total_shares, available_shares, avg_cost, current_price, market_value)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("000002", "万科A", 2000, 2000, 8.000, 8.500, 17000.000),
            )
            db.execute(
                """
                INSERT INTO analysis_reports
                    (code, task_id, signal, confidence, risk_score, final_decision, trader_plan, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                ("000001", "selected-report", "BUY", 0.82, 24, "建议建仓", "分批"),
            )
            selected_report_id = db.execute("SELECT id FROM analysis_reports WHERE code='000001'").fetchone()[0]
            db.commit()

        async def fake_report_item(item, payload, recent_codes, config):
            with sqlite3.connect(self.db_path) as db:
                cursor = db.execute(
                    """
                    INSERT INTO analysis_reports
                        (code, task_id, signal, confidence, risk_score, final_decision, trader_plan, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    """,
                    (item["code"], f"auto-holding-{item['code']}", "HOLD", 0.7, 40, "持仓股补充日报告", "保留观察"),
                )
                report_id = cursor.lastrowid
                db.commit()
            batch_report_service._update_item_id(item["id"], status="completed", report_id=report_id, completed_at=batch_report_service._now_expr(), error="")

        with patch(
            "services.batch_report_service.batch_research.collect_position_plan_market_context",
            new=AsyncMock(return_value={"captured_at": "2026-06-03T10:15:00", "status": "ok", "items": {}, "summary": []}),
        ), patch(
            "services.batch_report_service._run_data_prefetch_item",
            new=AsyncMock(),
        ), patch(
            "services.batch_report_service._run_report_item",
            new=AsyncMock(side_effect=fake_report_item),
        ) as run_report:
            created = await batch_report_service.create_research_job(
                job_type="position_plan",
                report_ids=[selected_report_id],
                auto_start=False,
                output_dir=Path(self.tmp.name),
            )
            await batch_report_service.run_research_job(created["job_id"])

        job = batch_report_service.get_research_job(created["job_id"])
        result = json.loads(job["result_json"])
        codes = {item["code"] for item in result["plan"]["recommendations"]}
        self.assertEqual(run_report.await_count, 1)
        self.assertIn("000002", codes)
        self.assertEqual(result["plan"]["auto_holding_reports"]["generated_count"], 1)
        self.assertIn("000002", result["plan"]["auto_holding_reports"]["generated_codes"])

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

    async def test_cancel_job_cancels_unfinished_items_and_prevents_resume_without_retry(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001", "000002", "600519"],
            auto_start=False,
        )
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE batch_jobs SET status='running' WHERE job_id=?", (created["job_id"],))
            db.execute("UPDATE batch_job_items SET status='running' WHERE code='000001'")
            db.execute("UPDATE batch_job_items SET status='completed', report_id=123 WHERE code='000002'")
            db.execute("UPDATE batch_job_items SET status='pending' WHERE code='600519'")
            db.commit()

        result = batch_report_service.cancel_job(created["job_id"])
        job = batch_report_service.get_research_job(created["job_id"])
        statuses = {item["code"]: item["status"] for item in job["items"]}

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(statuses["000001"], "cancelled")
        self.assertEqual(statuses["000002"], "completed")
        self.assertEqual(statuses["600519"], "cancelled")

    async def test_manual_complete_stops_remaining_work_but_keeps_it_resumable(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001", "000002", "600519"],
            auto_start=False,
        )
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE batch_jobs SET status='running', current_code='000001' WHERE job_id=?", (created["job_id"],))
            db.execute("UPDATE batch_job_items SET status='running' WHERE code='000001'")
            db.execute("UPDATE batch_job_items SET status='completed', report_id=123 WHERE code='000002'")
            db.execute("UPDATE batch_job_items SET status='pending' WHERE code='600519'")
            db.commit()

        result = batch_report_service.manual_complete_job(created["job_id"])
        job = batch_report_service.get_research_job(created["job_id"])
        statuses = {item["code"]: item["status"] for item in job["items"]}

        self.assertEqual(result["status"], "manual_completed")
        self.assertEqual(job["status"], "manual_completed")
        self.assertEqual(statuses["000001"], "pending")
        self.assertEqual(statuses["000002"], "completed")
        self.assertEqual(statuses["600519"], "pending")
        self.assertEqual(job["completed_count"], 1)
        self.assertEqual(job["current_code"], "")

    async def test_resume_manual_completed_job_requeues_remaining_items(self):
        created = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=["000001", "000002"],
            auto_start=False,
        )
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE batch_jobs SET status='manual_completed', completed_at=datetime('now') WHERE job_id=?", (created["job_id"],))
            db.execute("UPDATE batch_job_items SET status='completed', report_id=123 WHERE code='000001'")
            db.execute("UPDATE batch_job_items SET status='pending' WHERE code='000002'")
            db.commit()

        result = await batch_report_service.resume_job(created["job_id"])
        job = batch_report_service.get_research_job(created["job_id"])
        statuses = {item["code"]: item["status"] for item in job["items"]}

        self.assertEqual(result["status"], "pending")
        self.assertEqual(job["status"], "pending")
        self.assertIsNone(job["completed_at"])
        self.assertEqual(statuses["000001"], "completed")
        self.assertEqual(statuses["000002"], "pending")

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
        job = batch_report_service.get_research_job(created["job_id"])
        batch_report_service.upsert_item_step(
            job["items"][0]["id"],
            created["job_id"],
            "market_analyst",
            "市场/技术分析师",
            "",
            step_order=1,
            status="running",
        )

        changed = batch_report_service.mark_interrupted_jobs()
        job = batch_report_service.get_research_job(created["job_id"])
        steps = batch_report_service.get_research_item_steps(job["items"][0]["id"])

        self.assertEqual(changed, 1)
        self.assertEqual(job["status"], "interrupted")
        self.assertEqual(job["items"][0]["status"], "failed")
        self.assertEqual(steps["steps"][0]["status"], "failed")
        self.assertEqual(steps["steps"][0]["error"], "服务重启或进程中断")


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

    def test_get_batch_research_item_steps_route(self):
        with patch(
            "services.batch_report_service.get_research_item_steps",
            return_value={"count": 1, "steps": [{"item_id": 7, "role_key": "market_analyst", "status": "completed"}]},
        ) as get_steps:
            resp = self.client.get("/api/batch-research/items/7/steps")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["steps"][0]["role_key"], "market_analyst")
        get_steps.assert_called_once_with(7)

    def test_pause_resume_logs_and_artifact_routes(self):
        with patch("services.batch_report_service.pause_job", return_value={"job_id": "job-1", "status": "paused"}) as pause_job:
            pause_resp = self.client.post("/api/batch-research/jobs/job-1/pause")
        with patch("services.batch_report_service.manual_complete_job", return_value={"job_id": "job-1", "status": "manual_completed"}) as manual_complete_job:
            complete_resp = self.client.post("/api/batch-research/jobs/job-1/manual-complete")
        with patch("services.batch_report_service.get_job_logs", return_value={"count": 0, "logs": []}) as get_logs:
            logs_resp = self.client.get("/api/batch-research/jobs/job-1/logs")
        with patch("services.batch_report_service.get_job_artifacts", return_value={"count": 0, "artifacts": []}) as get_artifacts:
            artifacts_resp = self.client.get("/api/batch-research/jobs/job-1/artifacts")

        self.assertEqual(pause_resp.status_code, 200)
        self.assertEqual(complete_resp.status_code, 200)
        self.assertEqual(logs_resp.status_code, 200)
        self.assertEqual(artifacts_resp.status_code, 200)
        pause_job.assert_called_once_with("job-1")
        manual_complete_job.assert_called_once_with("job-1")
        get_logs.assert_called_once_with("job-1", limit=200)
        get_artifacts.assert_called_once_with("job-1")

    def test_batch_analysis_route_uses_service_layer(self):
        with patch(
            "services.batch_report_service.get_batch_analysis",
            new=AsyncMock(return_value={"job_id": "job-1", "overview": {}}),
        ) as get_analysis:
            resp = self.client.get("/api/batch-research/jobs/job-1/analysis")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["job_id"], "job-1")
        get_analysis.assert_awaited_once_with("job-1")

    def test_preflight_route_uses_service_layer(self):
        with patch(
            "services.batch_report_service.preflight_batch_models",
            return_value={"status": "ok", "estimated_role_calls": 16},
        ) as preflight:
            resp = self.client.post("/api/batch-research/preflight", json={"job_type": "report_generation", "codes": ["000001"]})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["estimated_role_calls"], 16)
        preflight.assert_called_once()

    def test_worker_status_route_uses_service_layer(self):
        with patch(
            "services.batch_report_service.get_worker_status",
            return_value={"summary": {"online": 1, "stale": 0}, "workers": [{"worker_id": "w1", "state": "online"}]},
        ) as get_status:
            resp = self.client.get("/api/batch-research/workers?stale_minutes=20")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["workers"][0]["worker_id"], "w1")
        get_status.assert_called_once_with(stale_minutes=20)


class BatchWorkerScriptTests(unittest.TestCase):
    def test_worker_script_exists_for_independent_long_running_jobs(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "run_batch_worker.py"
        self.assertTrue(script.exists())
        content = script.read_text(encoding="utf-8")
        self.assertIn("run_worker_once", content)


if __name__ == "__main__":
    unittest.main()
