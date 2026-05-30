import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import models.database as database
from api.settings_api import router as settings_router
from repositories import settings_repository
from services import settings_service


class SettingsServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = settings_repository.DB_PATH
        settings_repository.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)

    def tearDown(self):
        settings_repository.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def test_defaults_are_merged_with_saved_settings(self):
        settings_service.update_setting("refresh_interval", 15)

        result = settings_service.get_all_settings()

        self.assertEqual(result["refresh_interval"], "15")
        self.assertIn("quick_think_model", result)

    def test_reset_restores_defaults(self):
        settings_service.update_setting("quick_think_model", "custom-model")

        result = settings_service.reset_settings()
        settings = settings_service.get_all_settings()

        self.assertEqual(result["reset"], len(settings_service.DEFAULTS))
        self.assertEqual(settings["quick_think_model"], "")

    def test_export_payload_contains_core_tables(self):
        settings_service.update_setting("model_mode", "balanced")

        content, filename = settings_service.export_payload()
        payload = json.loads(content)

        self.assertIn("stock-workbench-backup-", filename)
        self.assertEqual(payload["settings"]["model_mode"], "balanced")
        self.assertIn("watchlist", payload)

    def test_poll_notifications_sorts_recent_items(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO anomaly_logs (code, name, anomaly_type, description, severity, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                """,
                ("000001", "平安银行", "price_surge", "快速上涨", "warning"),
            )
            db.commit()

        result = settings_service.poll_notifications()

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["notifications"][0]["type"], "anomaly")

    def test_onboarding_status_tracks_pending_steps_and_completion(self):
        result = settings_service.onboarding_status()

        self.assertFalse(result["completed"])
        self.assertGreaterEqual(result["pending_count"], 1)

        completed = settings_service.complete_onboarding()
        result = settings_service.onboarding_status()

        self.assertTrue(completed["completed"])
        self.assertTrue(result["completed"])


class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(settings_router, prefix="/api")
        self.client = TestClient(app)

    def test_get_setting_route_uses_service_layer(self):
        with patch(
            "services.settings_service.get_setting",
            return_value={"key": "refresh_interval", "value": "30"},
        ) as get_setting:
            resp = self.client.get("/api/settings/refresh_interval")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["value"], "30")
        get_setting.assert_called_once_with("refresh_interval")


if __name__ == "__main__":
    unittest.main()
