import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import models.database as database
from services import holding_review_service


class HoldingReviewServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)
        self.market_indices_patch = patch("services.holding_review_service.get_index_quotes", return_value=[])
        self.market_sentiment_patch = patch(
            "services.holding_review_service.get_market_sentiment",
            new=AsyncMock(return_value={"breadth": {}, "northbound": {}}),
        )
        self.report_job_patch = patch(
            "services.batch_report_service.create_research_job",
            new=AsyncMock(return_value={"job_id": "re-test", "job_type": "report_generation", "status": "pending", "total_count": 1}),
        )
        self.market_indices_patch.start()
        self.market_sentiment_patch.start()
        self.report_job_patch.start()

    def tearDown(self):
        self.report_job_patch.stop()
        self.market_sentiment_patch.stop()
        self.market_indices_patch.stop()
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    async def test_run_review_persists_account_context_and_holding_flags(self):
        await database.init_db()
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("cash_balance_default", "9000"),
            )
            db.execute(
                """
                INSERT INTO portfolio (code, name, total_shares, avg_cost, account_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", 100, 10.0, "default"),
            )
            db.execute(
                """
                INSERT INTO analysis_reports (code, task_id, signal, risk_score, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("000001", "r1", "SELL", 80, "2026-06-04 09:30:00"),
            )
            db.commit()

        with patch(
            "services.portfolio_service.get_batch_quotes",
            new=AsyncMock(return_value={"000001": {"price": 11.0, "change_pct": 10.0, "name": "平安银行"}}),
        ):
            review = await holding_review_service.run_daily_review(account_id="default", date_text="2026-06-04", wait_for_report_refresh=False)

        self.assertEqual(review["status"], "completed")
        self.assertEqual(review["holding_count"], 1)
        self.assertEqual(review["asset_snapshot"]["cash"], 9000.0)
        self.assertEqual(review["asset_snapshot"]["market_value"], 1100.0)
        self.assertEqual(review["asset_snapshot"]["total_assets"], 10100.0)
        self.assertEqual(review["asset_snapshot"]["cash_pct"], 89.109)
        self.assertEqual(review["asset_snapshot"]["position_usage_pct"], 10.891)
        self.assertIn("所有建议必须基于当前真实仓位和可用资金", review["tomorrow_plan_markdown"])
        roles = review["tomorrow_plan"]["role_discussion"]
        self.assertEqual([role["role"] for role in roles], ["持仓经理", "风控经理", "交易员/最终执行"])
        self.assertIn("三角色讨论", review["tomorrow_plan_markdown"])
        self.assertIn("持仓经理", review["tomorrow_plan_markdown"])

        flags = await holding_review_service.get_review_flags(review["review_id"])
        flag_types = {flag["flag_type"] for flag in flags["flags"]}
        self.assertIn("limit_up", flag_types)
        self.assertIn("signal_conflict", flag_types)

        saved_review = await holding_review_service.get_review(review["review_id"])
        saved_roles = saved_review["tomorrow_plan"]["role_discussion"]
        self.assertEqual(saved_roles[1]["role"], "风控经理")
        self.assertIn("信号冲突", saved_roles[1]["view"])

    async def test_run_review_builds_tomorrow_trading_battle_plan_context(self):
        await database.init_db()
        snapshot = {
            "market": {"quote": {"price": 11.0, "change_pct": 6.5}, "indicators": {"ma20": 10.2}},
            "news": {"items": [{"title": "订单增长"}]},
            "fundamentals": {"balance_sheet": {"asset_liability_ratio": 45.1}},
            "hot_money": {"main_net_inflow": 1234.5},
        }
        validation = {"ok": True, "missing_layers": [], "empty_layers": [], "layer_errors": {}}
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("cash_balance_default", "9000"))
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("investment_style_preset", "aggressive"))
            db.execute(
                """
                INSERT INTO portfolio (code, name, total_shares, avg_cost, account_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", 100, 10.0, "default"),
            )
            db.execute(
                "INSERT INTO stock_data_snapshots (code, name, snapshot_json, validation_json, summary_json) VALUES (?, ?, ?, ?, ?)",
                ("000001", "平安银行", json.dumps(snapshot), json.dumps(validation), json.dumps({"total_bytes": 2048})),
            )
            db.commit()

        async def fake_create_job(**kwargs):
            self.assertEqual(kwargs["job_type"], "report_generation")
            self.assertEqual(kwargs["codes"], ["000001"])
            self.assertEqual(kwargs["skip_recent_days"], 0)
            self.assertTrue(kwargs["auto_start"])
            return {"job_id": "re-补报告", "job_type": "report_generation", "status": "pending", "total_count": 1}

        with patch(
            "services.portfolio_service.get_batch_quotes",
            new=AsyncMock(return_value={"000001": {"price": 11.0, "change_pct": 6.5, "name": "平安银行"}}),
        ), patch(
            "services.batch_report_service.create_research_job",
            new=AsyncMock(side_effect=fake_create_job),
        ), patch(
            "services.holding_review_service.get_index_quotes",
            return_value=[{"code": "sh000001", "name": "上证指数", "price": 3100, "change_pct": 0.5}],
            create=True,
        ), patch(
            "services.holding_review_service.get_market_sentiment",
            new=AsyncMock(return_value={"breadth": {"up": 3000, "down": 1800, "limit_up": 60, "limit_down": 8, "total": 5200}, "northbound": {"total": 12.3}}),
            create=True,
        ):
            review = await holding_review_service.run_daily_review(account_id="default", date_text="2026-06-04", wait_for_report_refresh=False)

        plan = review["tomorrow_plan"]
        self.assertEqual(plan["title"], "明日交易作战计划")
        self.assertEqual(plan["report_refresh_job"]["job_id"], "re-补报告")
        self.assertEqual(plan["report_refresh_job"]["codes"], ["000001"])
        self.assertEqual(plan["investment_profile"]["preset"], "aggressive")
        self.assertEqual(plan["layer_context"]["items"][0]["code"], "000001")
        self.assertIn("news", plan["layer_context"]["items"][0]["available_layers"])
        self.assertEqual(plan["market_context"]["indices"][0]["name"], "上证指数")
        self.assertIn("holding_management", plan["battle_plan"])
        self.assertIn("offensive_candidates", plan["battle_plan"])
        self.assertIn("do_not_touch", plan["battle_plan"])
        self.assertIn("trigger_conditions", plan["battle_plan"])
        self.assertIn("明日交易作战计划", review["tomorrow_plan_markdown"])
        self.assertIn("用户投资风格", review["tomorrow_plan_markdown"])
        self.assertIn("七层快照摘要", review["tomorrow_plan_markdown"])
        self.assertIn("大盘与板块环境", review["tomorrow_plan_markdown"])

        saved_review = await holding_review_service.get_review(review["review_id"])
        self.assertEqual(saved_review["tomorrow_plan"]["report_refresh_job"]["job_id"], "re-补报告")

    async def test_run_review_only_includes_selected_watchlist_candidates_without_mixing_holdings(self):
        await database.init_db()
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                ("cash_balance_default", "5000"),
            )
            db.execute(
                """
                INSERT INTO portfolio (code, name, total_shares, avg_cost, account_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", 100, 10.0, "default"),
            )
            db.execute(
                "INSERT INTO watchlist (code, name, group_name, sort_order) VALUES (?, ?, ?, ?)",
                ("000002", "万科A", "默认", 1),
            )
            db.execute(
                "INSERT INTO watchlist (code, name, group_name, sort_order) VALUES (?, ?, ?, ?)",
                ("000003", "观察股", "观察池", 2),
            )
            db.execute(
                """
                INSERT INTO analysis_reports (code, task_id, signal, risk_score, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("000002", "r2", "BUY", 30, "2026-06-04 10:00:00"),
            )
            db.commit()

        with patch(
            "services.portfolio_service.get_batch_quotes",
            new=AsyncMock(
                return_value={
                    "000001": {"price": 10.0, "change_pct": 0.0, "name": "平安银行"},
                    "000002": {"price": 20.0, "change_pct": 2.0, "name": "万科A"},
                    "000003": {"price": 8.0, "change_pct": 1.0, "name": "观察股"},
                }
            ),
        ):
            review = await holding_review_service.run_daily_review(
                account_id="default",
                date_text="2026-06-04",
                include_watchlist_candidates=True,
                include_observation_pool=False,
                candidate_codes=["000002"],
            )

        self.assertEqual(review["holding_count"], 1)
        self.assertEqual(review["candidate_count"], 1)
        self.assertEqual([item["code"] for item in review["candidate_context"]["items"]], ["000002"])

        items = await holding_review_service.get_review_items(review["review_id"])
        by_code = {item["code"]: item for item in items["items"]}
        self.assertEqual(by_code["000001"]["item_type"], "holding")
        self.assertEqual(by_code["000002"]["item_type"], "candidate")
        self.assertNotIn("000003", by_code)

        with sqlite3.connect(self.db_path) as db:
            row = db.execute(
                "SELECT candidate_context_json FROM holding_daily_reviews WHERE review_id = ?",
                (review["review_id"],),
            ).fetchone()
        saved = json.loads(row[0])
        self.assertEqual(saved["scope"], "selected")
        self.assertEqual(saved["items"][0]["latest_signal"], "BUY")

    async def test_run_review_does_not_include_all_watchlist_when_no_candidates_are_selected(self):
        await database.init_db()
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO portfolio (code, name, total_shares, avg_cost, account_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", 100, 10.0, "default"),
            )
            db.execute(
                "INSERT INTO watchlist (code, name, group_name, sort_order) VALUES (?, ?, ?, ?)",
                ("000002", "万科A", "默认", 1),
            )
            db.commit()

        with patch(
            "services.portfolio_service.get_batch_quotes",
            new=AsyncMock(return_value={"000001": {"price": 10.0, "change_pct": 0.0, "name": "平安银行"}}),
        ):
            review = await holding_review_service.run_daily_review(
                account_id="default",
                date_text="2026-06-04",
                include_watchlist_candidates=True,
                include_observation_pool=True,
            )

        self.assertEqual(review["candidate_count"], 0)
        self.assertEqual(review["candidate_context"]["scope"], "none")

    async def test_run_review_can_force_refresh_holding_and_selected_candidate_reports(self):
        await database.init_db()
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("cash_balance_default", "5000"))
            db.execute(
                """
                INSERT INTO portfolio (code, name, total_shares, avg_cost, account_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", 100, 10.0, "default"),
            )
            db.execute(
                "INSERT INTO watchlist (code, name, group_name, sort_order) VALUES (?, ?, ?, ?)",
                ("000002", "万科A", "默认", 1),
            )
            db.executemany(
                """
                INSERT INTO analysis_reports (code, task_id, signal, risk_score, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    ("000001", "today-holding", "HOLD", 30, "2026-06-04 10:00:00"),
                    ("000002", "today-candidate", "BUY", 25, "2026-06-04 10:00:00"),
                ],
            )
            db.commit()

        async def fake_create_job(**kwargs):
            self.assertEqual(kwargs["codes"], ["000001", "000002"])
            self.assertEqual(kwargs["skip_recent_days"], 0)
            self.assertTrue(kwargs["refresh_snapshots"])
            self.assertEqual(kwargs["analysis_mode"], "snapshot-tradingagents")
            return {"job_id": "re-force", "job_type": "report_generation", "status": "pending", "total_count": 2}

        with patch(
            "services.portfolio_service.get_batch_quotes",
            new=AsyncMock(
                return_value={
                    "000001": {"price": 10.0, "change_pct": 0.0, "name": "平安银行"},
                    "000002": {"price": 20.0, "change_pct": 1.0, "name": "万科A"},
                }
            ),
        ), patch(
            "services.batch_report_service.create_research_job",
            new=AsyncMock(side_effect=fake_create_job),
        ):
            review = await holding_review_service.run_daily_review(
                account_id="default",
                date_text="2026-06-04",
                include_watchlist_candidates=True,
                candidate_codes=["000002"],
                force_refresh_holdings=True,
                force_refresh_candidates=True,
                refresh_snapshots_for_reports=True,
            )

        self.assertEqual(review["rerun_report_codes"], ["000001", "000002"])
        self.assertEqual(review["tomorrow_plan"]["report_refresh_job"]["job_id"], "re-force")
        self.assertEqual(review["status"], "waiting_reports")
        self.assertTrue(review["tomorrow_plan"]["report_refresh_policy"]["force_refresh_holdings"])
        self.assertTrue(review["tomorrow_plan"]["report_refresh_policy"]["force_refresh_candidates"])
        self.assertTrue(review["tomorrow_plan"]["report_refresh_policy"]["refresh_snapshots"])
        self.assertFalse(review["tomorrow_plan"]["report_refresh_policy"]["plan_uses_refreshed_reports"])
        self.assertIn("等待补报告完成", review["tomorrow_plan_markdown"])

        items = await holding_review_service.get_review_items(review["review_id"])
        by_code = {item["code"]: item for item in items["items"]}
        self.assertEqual(by_code["000001"]["needs_report"], 1)
        self.assertEqual(by_code["000002"]["needs_report"], 1)
        self.assertIn("持有", by_code["000001"]["reason"])
        self.assertIn("已强制加入补报告队列", by_code["000001"]["reason"])

    async def test_run_review_waits_for_forced_reports_then_finalizes_with_latest_reports(self):
        await database.init_db()
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("cash_balance_default", "5000"))
            db.execute(
                """
                INSERT INTO portfolio (code, name, total_shares, avg_cost, account_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", 100, 10.0, "default"),
            )
            db.execute(
                """
                INSERT INTO analysis_reports (code, task_id, signal, risk_score, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("000001", "old-holding", "HOLD", 30, "2026-06-04 10:00:00"),
            )
            db.execute(
                """
                INSERT INTO batch_jobs (job_id, job_type, status, total_count, completed_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("re-force", "report_generation", "pending", 1, 0),
            )
            db.commit()

        async def fake_create_job(**kwargs):
            return {"job_id": "re-force", "job_type": "report_generation", "status": "pending", "total_count": 1}

        with patch(
            "services.portfolio_service.get_batch_quotes",
            new=AsyncMock(return_value={"000001": {"price": 10.0, "change_pct": 0.0, "name": "平安银行"}}),
        ), patch(
            "services.batch_report_service.create_research_job",
            new=AsyncMock(side_effect=fake_create_job),
        ):
            pending = await holding_review_service.run_daily_review(
                account_id="default",
                date_text="2026-06-04",
                force_refresh_holdings=True,
            )

        self.assertEqual(pending["status"], "waiting_reports")
        self.assertEqual(pending["batch_job_id"], "re-force")
        self.assertIn("等待补报告完成", pending["tomorrow_plan_markdown"])
        self.assertFalse(pending["tomorrow_plan"]["report_refresh_policy"]["plan_uses_refreshed_reports"])

        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO analysis_reports (code, task_id, signal, risk_score, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("000001", "new-holding", "SELL", 90, "2026-06-04 11:00:00"),
            )
            db.execute(
                """
                UPDATE batch_jobs
                SET status='completed', completed_count=1, completed_at=datetime('now')
                WHERE job_id='re-force'
                """
            )
            db.commit()

        with patch(
            "services.portfolio_service.get_batch_quotes",
            new=AsyncMock(return_value={"000001": {"price": 9.5, "change_pct": -1.0, "name": "平安银行"}}),
        ):
            result = await holding_review_service.finalize_waiting_reviews_for_batch_job("re-force")

        self.assertEqual(result["finalized"], 1)
        saved = await holding_review_service.get_review(pending["review_id"])
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["review_id"], pending["review_id"])
        self.assertTrue(saved["tomorrow_plan"]["report_refresh_policy"]["plan_uses_refreshed_reports"])
        items = await holding_review_service.get_review_items(pending["review_id"])
        self.assertEqual(items["items"][0]["latest_signal"], "SELL")

    async def test_list_reviews_reconciles_waiting_review_after_completed_refresh_job(self):
        await database.init_db()
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("cash_balance_default", "5000"))
            db.execute(
                """
                INSERT INTO portfolio (code, name, total_shares, avg_cost, account_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", 100, 10.0, "default"),
            )
            db.execute(
                """
                INSERT INTO batch_jobs (job_id, job_type, status, total_count, completed_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("re-force", "report_generation", "pending", 1, 0),
            )
            db.commit()

        async def fake_create_job(**kwargs):
            return {"job_id": "re-force", "job_type": "report_generation", "status": "pending", "total_count": 1}

        with patch(
            "services.portfolio_service.get_batch_quotes",
            new=AsyncMock(return_value={"000001": {"price": 10.0, "change_pct": 0.0, "name": "平安银行"}}),
        ), patch(
            "services.batch_report_service.create_research_job",
            new=AsyncMock(side_effect=fake_create_job),
        ):
            pending = await holding_review_service.run_daily_review(
                account_id="default",
                date_text="2026-06-04",
                force_refresh_holdings=True,
            )

        self.assertEqual(pending["status"], "waiting_reports")

        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO analysis_reports (code, task_id, signal, risk_score, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("000001", "new-holding", "SELL", 90, "2026-06-04 11:00:00"),
            )
            db.execute(
                """
                UPDATE batch_jobs
                SET status='completed', completed_count=1, completed_at=datetime('now')
                WHERE job_id='re-force'
                """
            )
            db.commit()

        with patch(
            "services.portfolio_service.get_batch_quotes",
            new=AsyncMock(return_value={"000001": {"price": 9.5, "change_pct": -1.0, "name": "平安银行"}}),
        ):
            reviews = await holding_review_service.list_reviews(limit=1)

        self.assertEqual(reviews["reviews"][0]["review_id"], pending["review_id"])
        self.assertEqual(reviews["reviews"][0]["status"], "completed")
        self.assertTrue(reviews["reviews"][0]["tomorrow_plan"]["report_refresh_policy"]["plan_uses_refreshed_reports"])
        self.assertNotIn("等待补报告完成", reviews["reviews"][0]["tomorrow_plan_markdown"])

    async def test_run_review_marks_failed_when_report_refresh_job_cannot_be_created(self):
        await database.init_db()
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO portfolio (code, name, total_shares, avg_cost, account_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", 100, 10.0, "default"),
            )
            db.commit()

        with patch(
            "services.portfolio_service.get_batch_quotes",
            new=AsyncMock(return_value={"000001": {"price": 10.0, "change_pct": 0.0, "name": "平安银行"}}),
        ), patch(
            "services.batch_report_service.create_research_job",
            new=AsyncMock(side_effect=RuntimeError("worker unavailable")),
        ):
            review = await holding_review_service.run_daily_review(
                account_id="default",
                date_text="2026-06-04",
                force_refresh_holdings=True,
            )

        self.assertEqual(review["status"], "report_refresh_failed")
        self.assertIn("worker unavailable", review["error"])
        self.assertEqual(review["tomorrow_plan"]["battle_plan"], {})
        self.assertIn("未生成最终作战计划", review["tomorrow_plan_markdown"])
