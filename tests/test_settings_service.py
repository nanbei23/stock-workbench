import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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

    def test_bulk_update_keeps_existing_secret_when_mask_placeholder_submitted(self):
        settings_service.update_setting("verification_api_key", "sk-real")

        settings_service.bulk_update_settings({"verification_api_key": "********", "verification_endpoint": "https://api.example.com/v1"})
        settings = settings_service.get_all_settings()

        self.assertEqual(settings["verification_api_key"], "sk-real")
        self.assertEqual(settings["verification_endpoint"], "https://api.example.com/v1")


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

    def test_verification_connection_uses_inline_form_config(self):
        class FakeClient:
            def __init__(self, timeout=None):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, headers=None, json=None):
                self.url = url
                self.headers = headers
                self.json = json
                return SimpleNamespace(
                    status_code=200,
                    json=lambda: {"choices": [{"message": {"content": "pong"}}]},
                    text='{"choices":[]}',
                )

        with patch("services.settings_service.verification_test_config", return_value={"model": "", "endpoint": "", "api_key": ""}), \
             patch("api.settings_api.httpx.AsyncClient", FakeClient):
            resp = self.client.post(
                "/api/settings/test-verification",
                json={"endpoint": "https://api.example.com/v1", "api_key": "sk-inline", "model": "verifier"},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
