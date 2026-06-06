import sqlite3
import tempfile
import unittest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import models.database as database
from api.holding_review_api import router as holding_review_router


class HoldingReviewApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)
        asyncio.run(database.init_db())
        app = FastAPI()
        app.include_router(holding_review_router, prefix="/api")
        self.client = TestClient(app)

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_run_and_fetch_holding_review(self):
        import asyncio

        asyncio.run(database.init_db())
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("cash_balance_default", "10000"))
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
                ("000001", "today-holding", "HOLD", 30, "2026-06-04 10:00:00"),
            )
            db.execute(
                """
                INSERT INTO stock_data_snapshots (code, name, snapshot_json, validation_json, summary_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", "{}", "{\"ok\": true}", "{}", "2026-06-04 10:00:00"),
            )
            db.commit()

        with patch(
            "services.portfolio_service.get_batch_quotes",
            new=AsyncMock(return_value={"000001": {"price": 10.5, "change_pct": 5.0, "name": "平安银行"}}),
        ):
            created = self.client.post(
                "/api/holding-reviews/run",
                json={
                    "account_id": "default",
                    "date": "2026-06-04",
                    "include_watchlist_candidates": True,
                    "force_refresh_holdings": False,
                    "refresh_snapshots_for_reports": False,
                },
            )

        self.assertEqual(created.status_code, 200)
        review = created.json()
        self.assertEqual(review["asset_snapshot"]["cash"], 10000.0)
        self.assertEqual(review["holding_count"], 1)

        detail = self.client.get(f"/api/holding-reviews/{review['review_id']}")
        items = self.client.get(f"/api/holding-reviews/{review['review_id']}/items")
        flags = self.client.get(f"/api/holding-reviews/{review['review_id']}/flags")
        markdown = self.client.get(f"/api/holding-reviews/{review['review_id']}/markdown")

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(items.json()["items"][0]["code"], "000001")
        self.assertGreaterEqual(flags.json()["count"], 1)
        self.assertIn("每日 AI 决策报告", markdown.text)

    def test_run_creates_waiting_review_for_auto_finalize_when_report_is_missing(self):
        import asyncio

        asyncio.run(database.init_db())
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
            new=AsyncMock(return_value={"000001": {"price": 10.5, "change_pct": 5.0, "name": "平安银行"}}),
        ), patch(
            "services.batch_report_service.create_research_job",
            new=AsyncMock(return_value={"job_id": "re-api", "job_type": "report_generation", "status": "pending", "total_count": 1}),
        ):
            response = self.client.post(
                "/api/daily-decision-reports/run",
                json={"account_id": "default", "date": "2026-06-04"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "waiting_reports")
        self.assertEqual(payload["batch_job_id"], "re-api")
        self.assertIn("review_id", payload)
        self.assertIn("等待补报告完成", payload["tomorrow_plan_markdown"])
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT status, batch_job_id FROM holding_daily_reviews").fetchone()
        self.assertEqual(row, ("waiting_reports", "re-api"))

    def test_run_forwards_force_report_refresh_options(self):
        async def fake_run_daily_review(**kwargs):
            self.assertTrue(kwargs["force_refresh_holdings"])
            self.assertTrue(kwargs["force_refresh_candidates"])
            self.assertTrue(kwargs["refresh_snapshots_for_reports"])
            self.assertEqual(kwargs["candidate_signal_filters"], ["BUY", "OVERWEIGHT"])
            return {"review_id": "hr-force", "ok": True}

        with patch("api.holding_review_api.holding_review_service.run_daily_review", new=AsyncMock(side_effect=fake_run_daily_review)):
            response = self.client.post(
                "/api/holding-reviews/run",
                json={
                    "account_id": "default",
                    "force_refresh_holdings": True,
                    "force_refresh_candidates": True,
                    "refresh_snapshots_for_reports": True,
                    "candidate_signal_filters": ["BUY", "OVERWEIGHT"],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["review_id"], "hr-force")

    def test_run_defaults_to_refresh_holdings_and_snapshots(self):
        async def fake_run_daily_review(**kwargs):
            self.assertTrue(kwargs["force_refresh_holdings"])
            self.assertFalse(kwargs["force_refresh_candidates"])
            self.assertTrue(kwargs["refresh_snapshots_for_reports"])
            return {"review_id": "hr-default-refresh", "ok": True}

        with patch("api.holding_review_api.holding_review_service.run_daily_review", new=AsyncMock(side_effect=fake_run_daily_review)):
            response = self.client.post(
                "/api/daily-decision-reports/run",
                json={"account_id": "default"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["review_id"], "hr-default-refresh")

    def test_daily_decision_report_aliases_use_same_service_contract(self):
        async def fake_run_daily_review(**kwargs):
            self.assertEqual(kwargs["account_id"], "default")
            return {"review_id": "ddr-1", "title": "每日 AI 决策报告"}

        with patch("api.holding_review_api.holding_review_service.run_daily_review", new=AsyncMock(side_effect=fake_run_daily_review)):
            run_response = self.client.post("/api/daily-decision-reports/run", json={"account_id": "default"})

        self.assertEqual(run_response.status_code, 200)
        self.assertEqual(run_response.json()["review_id"], "ddr-1")

        with patch("api.holding_review_api.holding_review_service.list_reviews", new=AsyncMock(return_value={"reviews": []})) as list_reviews:
            list_response = self.client.get("/api/daily-decision-reports?limit=20")

        self.assertEqual(list_response.status_code, 200)
        list_reviews.assert_awaited_once_with(limit=20, account_id=None)

        review = {"review_id": "ddr-1", "tomorrow_plan_markdown": "# 每日 AI 决策报告"}
        with patch("api.holding_review_api.holding_review_service.get_review", new=AsyncMock(return_value=review)):
            markdown_response = self.client.get("/api/daily-decision-reports/ddr-1/markdown")

        self.assertEqual(markdown_response.status_code, 200)
        self.assertIn("每日 AI 决策报告", markdown_response.text)

    def test_update_daily_decision_item_status(self):
        async def fake_update(review_id, item_id, status):
            self.assertEqual(review_id, "hr-1")
            self.assertEqual(item_id, 9)
            self.assertEqual(status, "watching")
            return {"id": item_id, "review_id": review_id, "decision_status": status}

        with patch(
            "api.holding_review_api.holding_review_service.update_review_item_decision_status",
            new=AsyncMock(side_effect=fake_update),
            create=True,
        ):
            response = self.client.post(
                "/api/daily-decision-reports/hr-1/items/9/status",
                json={"status": "watching"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["decision_status"], "watching")
