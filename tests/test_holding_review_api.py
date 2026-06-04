import sqlite3
import tempfile
import unittest
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
            db.commit()

        with patch(
            "services.portfolio_service.get_batch_quotes",
            new=AsyncMock(return_value={"000001": {"price": 10.5, "change_pct": 5.0, "name": "平安银行"}}),
        ):
            created = self.client.post(
                "/api/holding-reviews/run",
                json={"account_id": "default", "date": "2026-06-04", "include_watchlist_candidates": True},
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
        self.assertIn("所有建议必须基于当前真实仓位和可用资金", markdown.text)
