import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

import models.database as database
from api.settings_api import router as settings_router
from repositories import settings_repository
from services import investment_profile_service, market_permission_service, settings_service


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

    def test_trading_market_permissions_default_all_enabled(self):
        result = settings_service.get_all_settings()

        self.assertEqual(result["trade_market_main"], "true")
        self.assertEqual(result["trade_market_gem"], "true")
        self.assertEqual(result["trade_market_star"], "true")
        self.assertEqual(result["trade_market_bse"], "true")

    def test_daily_decision_schedule_defaults_are_available(self):
        result = settings_service.get_all_settings()

        self.assertEqual(result["daily_decision_auto_enabled"], "false")
        self.assertEqual(result["daily_decision_auto_time"], "15:20")
        self.assertEqual(result["daily_decision_candidate_mode"], "holdings_only")
        self.assertEqual(result["daily_decision_candidate_group"], "每日决策候选")
        self.assertEqual(result["daily_decision_force_refresh_holdings"], "true")
        self.assertEqual(result["daily_decision_refresh_snapshots"], "true")

    def test_bulk_update_provider_references_shadow_legacy_model_fields_server_side(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO model_providers
                    (id, name, base_url, api_key, models_json, quick_model, deep_model, default_model, embedding_model, embedding_dimensions, usage_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "provider-main",
                    "Provider Main",
                    "https://provider-main.example.com/v1",
                    "sk-main",
                    json.dumps(["provider-fast", "provider-deep", "provider-default", "provider-embed"], ensure_ascii=False),
                    "provider-fast",
                    "provider-deep",
                    "provider-default",
                    "provider-embed",
                    1536,
                    json.dumps(["ai", "verification", "embedding"], ensure_ascii=False),
                ),
            )
            db.commit()

        settings_service.bulk_update_settings(
            {
                "ai_primary_provider_id": "provider-main",
                "ai_quick_model": "stale-fast-override",
                "ai_deep_model": "stale-deep-override",
                "verification_provider_id": "provider-main",
                "verification_model": "stale-verification-override",
                "embedding_provider_id": "provider-main",
                "embedding_model": "stale-embedding-override",
                "api_key": "********",
                "verification_api_key": "********",
                "embedding_api_key": "********",
            }
        )

        saved = settings_repository.fetch_settings()

        self.assertEqual(saved["custom_endpoint"], "https://provider-main.example.com/v1")
        self.assertEqual(saved["api_key"], "sk-main")
        self.assertEqual(saved["quick_think_model"], "provider-fast")
        self.assertEqual(saved["deep_think_model"], "provider-deep")
        self.assertEqual(saved["verification_endpoint"], "https://provider-main.example.com/v1")
        self.assertEqual(saved["verification_api_key"], "sk-main")
        self.assertEqual(saved["verification_model"], "provider-default")
        self.assertEqual(saved["embedding_endpoint"], "https://provider-main.example.com/v1")
        self.assertEqual(saved["embedding_api_key"], "sk-main")
        self.assertEqual(saved["embedding_model"], "provider-embed")

    def test_investment_profile_defaults_are_available(self):
        result = settings_service.get_all_settings()

        self.assertEqual(result["investment_style_preset"], "balanced")
        self.assertEqual(result["investment_max_single_position_pct"], "15")
        self.assertEqual(result["investment_min_cash_pct"], "5")
        self.assertEqual(result["investment_allow_left_side"], "false")
        self.assertIn("右侧确认", result["investment_entry_preference"])
        self.assertIn("investment_entry_required_conditions", result)
        self.assertIn("investment_buy_veto_rules", result)
        self.assertIn("investment_position_sizing_discipline", result)
        self.assertIn("investment_max_sector_position_pct", result)
        self.assertIn("investment_max_total_position_pct", result)
        self.assertIn("investment_max_single_trade_loss_pct", result)

    def test_investment_profile_context_includes_version_and_execution_contract(self):
        profile = investment_profile_service.investment_profile_snapshot({
            "investment_style_preset": "aggressive",
            "investment_max_single_position_pct": "40",
            "investment_entry_preference": "右侧突破",
            "investment_exit_discipline": "破位退出",
        })

        self.assertEqual(profile["version"], "investment-profile-v2")
        self.assertIn("风格匹配度", profile["context"])
        self.assertIn("试仓条件", profile["context"])
        self.assertIn("加仓条件", profile["context"])
        self.assertIn("放弃条件", profile["context"])
        self.assertIn("style_match", profile["output_contract"])

    def test_investment_profile_strategy_context_is_config_driven(self):
        profile = investment_profile_service.investment_profile_snapshot({
            "investment_style_preset": "custom",
            "investment_entry_strategy_name": "自定义回踩买入",
            "investment_entry_required_conditions": "必须满足 A 和 B",
            "investment_entry_supporting_conditions": "至少满足 C",
            "investment_buy_veto_rules": "触发 D 禁止买入",
            "investment_position_sizing_discipline": "分三批试仓",
            "investment_add_position_discipline": "只在二次确认后加仓",
            "investment_max_single_position_pct": "30",
            "investment_max_sector_position_pct": "50",
            "investment_max_total_position_pct": "85",
            "investment_max_single_trade_loss_pct": "3",
            "investment_initial_entry_fraction": "0.333",
        })

        self.assertEqual(profile["entry_strategy_name"], "自定义回踩买入")
        self.assertEqual(profile["max_sector_position_pct"], "50")
        self.assertEqual(profile["max_total_position_pct"], "85")
        self.assertEqual(profile["max_single_trade_loss_pct"], "3")
        self.assertIn("自定义回踩买入", profile["context"])
        self.assertIn("必须满足 A 和 B", profile["context"])
        self.assertIn("触发 D 禁止买入", profile["context"])
        self.assertIn("单一交易最大亏损：3%", profile["context"])
        self.assertIn("strategy_checklist", profile["output_contract"])

    def test_infer_investment_profile_from_trade_history_prefers_aggressive_when_concentrated(self):
        with sqlite3.connect(self.db_path) as db:
            db.executemany(
                """
                INSERT INTO trades
                    (code, name, direction, price, shares, amount, trade_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("000001", "平安银行", "buy", 10, 1000, 10000, "2026-01-01 09:30:00"),
                    ("000001", "平安银行", "sell", 12, 1000, 12000, "2026-01-03 09:30:00"),
                    ("300502", "新易盛", "buy", 40, 3000, 120000, "2026-01-04 09:30:00"),
                    ("300502", "新易盛", "sell", 45, 1000, 45000, "2026-01-05 09:30:00"),
                ],
            )
            db.commit()

        inferred = investment_profile_service.infer_profile_from_trade_history(self.db_path)

        self.assertEqual(inferred["suggested_settings"]["investment_style_preset"], "aggressive")
        self.assertEqual(inferred["metrics"]["trade_count"], 4)
        self.assertGreaterEqual(float(inferred["suggested_settings"]["investment_max_single_position_pct"]), 30)
        self.assertIn("交易历史推断", inferred["summary"])

    def test_style_match_assessment_falls_back_to_profile_constraints(self):
        profile = investment_profile_service.investment_profile_snapshot({
            "investment_style_preset": "aggressive",
            "investment_allow_left_side": "false",
        })

        assessment = investment_profile_service.style_match_assessment(
            {
                "signal": "BUY",
                "confidence": 0.72,
                "risk_score": 45,
                "final_decision": "放量突破后买入，跌破支撑止损。",
                "trader_plan": "试仓后加仓。",
            },
            profile,
        )

        self.assertEqual(assessment["profile_version"], "investment-profile-v2")
        self.assertGreaterEqual(assessment["match_score"], 70)
        self.assertIn("进攻型", assessment["style_label"])

    def test_market_permission_classifier_and_filter_respect_settings(self):
        settings = {
            "trade_market_main": "true",
            "trade_market_gem": "true",
            "trade_market_star": "false",
            "trade_market_bse": "false",
        }

        self.assertEqual(market_permission_service.classify_stock_market("688498")["key"], "star")
        self.assertEqual(market_permission_service.classify_stock_market("300502")["key"], "gem")
        self.assertEqual(market_permission_service.classify_stock_market("600519")["key"], "main")
        self.assertEqual(market_permission_service.classify_stock_market("830799")["key"], "bse")

        filtered, excluded = market_permission_service.filter_allowed_stocks(
            [
                {"code": "688498", "name": "源杰科技"},
                {"code": "300502", "name": "新易盛"},
                {"code": "600519", "name": "贵州茅台"},
                {"code": "830799", "name": "艾融软件"},
            ],
            settings=settings,
        )

        self.assertEqual([item["code"] for item in filtered], ["300502", "600519"])
        self.assertEqual([item["market_key"] for item in excluded], ["star", "bse"])

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

    def test_create_backup_file_copies_full_sqlite_database(self):
        settings_service.update_setting("model_mode", "balanced")
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO batch_jobs (job_id, name, job_type, status, total_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("job-full", "完整批量任务", "report_generation", "completed", 1),
            )
            db.commit()

        result = settings_service.create_backup_file()
        backup_path = Path(result["path"])

        self.assertEqual(result["backup_type"], "sqlite")
        self.assertTrue(result["filename"].endswith(".db"))
        self.assertTrue(backup_path.exists())
        with sqlite3.connect(backup_path) as backup:
            self.assertEqual(
                backup.execute("SELECT value FROM settings WHERE key='model_mode'").fetchone()[0],
                "balanced",
            )
            self.assertEqual(
                backup.execute("SELECT name FROM batch_jobs WHERE job_id='job-full'").fetchone()[0],
                "完整批量任务",
            )

    def test_restore_latest_backup_replaces_database_file(self):
        settings_service.update_setting("model_mode", "before")
        backup = settings_service.create_backup_file()
        settings_service.update_setting("model_mode", "after")
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("transient_only", "yes"))
            db.commit()

        result = settings_service.restore_latest_backup()

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["restored_type"], "sqlite")
        self.assertEqual(result["filename"], Path(backup["path"]).name)
        self.assertTrue(Path(result["pre_restore_backup_path"]).exists())
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(
                db.execute("SELECT value FROM settings WHERE key='model_mode'").fetchone()[0],
                "before",
            )
            self.assertIsNone(db.execute("SELECT value FROM settings WHERE key='transient_only'").fetchone())

    def test_restore_uploaded_database_file_replaces_current_database(self):
        settings_service.update_setting("model_mode", "current")
        upload_path = Path(self.tmp.name) / "uploaded.db"
        with sqlite3.connect(upload_path) as db:
            db.executescript(database.SCHEMA)
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("model_mode", "uploaded"))
            db.execute(
                """
                INSERT INTO batch_jobs (job_id, name, job_type, status, total_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("job-upload", "上传数据库任务", "report_generation", "completed", 1),
            )
            db.commit()

        result = settings_service.restore_uploaded_database_file(upload_path, original_filename="uploaded.db")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["restored_type"], "sqlite")
        self.assertEqual(result["filename"], "uploaded.db")
        self.assertTrue(Path(result["pre_restore_backup_path"]).exists())
        self.assertTrue(result["restart_required"])
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(
                db.execute("SELECT value FROM settings WHERE key='model_mode'").fetchone()[0],
                "uploaded",
            )
            self.assertEqual(
                db.execute("SELECT name FROM batch_jobs WHERE job_id='job-upload'").fetchone()[0],
                "上传数据库任务",
            )

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

    def test_fetch_models_reports_connect_error_detail(self):
        class FakeClient:
            def __init__(self, timeout=None):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, headers=None):
                raise httpx.ConnectError("nodename nor servname provided")

        with patch("api.settings_api.httpx.AsyncClient", FakeClient):
            resp = self.client.post(
                "/api/settings/fetch-models",
                json={"endpoint": "https://token-plan-cn.xiaomimimo.com/v1", "api_key": "sk-test"},
            )

        self.assertEqual(resp.status_code, 400)
        detail = resp.json()["detail"]
        self.assertIn("nodename nor servname provided", detail)
        self.assertIn("代理", detail)

    def test_fetch_models_rejects_empty_model_payload(self):
        class FakeClient:
            def __init__(self, timeout=None):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, headers=None):
                return SimpleNamespace(status_code=200, json=lambda: {"data": []}, text='{"data":[]}')

        with patch("api.settings_api.httpx.AsyncClient", FakeClient):
            resp = self.client.post(
                "/api/settings/fetch-models",
                json={"endpoint": "https://api.example.com/v1", "api_key": "sk-test"},
            )

        self.assertEqual(resp.status_code, 502)
        self.assertIn("未返回模型", resp.json()["detail"])

    def test_restore_uploaded_database_route_accepts_db_file(self):
        seen = {}

        def fake_restore(path, *, original_filename):
            seen["exists_during_call"] = Path(path).exists()
            seen["path"] = Path(path)
            return {"status": "ok", "filename": original_filename, "restart_required": True}

        with patch(
            "services.settings_service.restore_uploaded_database_file",
            side_effect=fake_restore,
        ) as restore:
            resp = self.client.post(
                "/api/settings/backup/restore-upload",
                files={"file": ("restore.db", b"SQLite bytes", "application/vnd.sqlite3")},
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["filename"], "restore.db")
        restore.assert_called_once()
        self.assertTrue(seen["exists_during_call"])
        self.assertFalse(seen["path"].exists())


if __name__ == "__main__":
    unittest.main()
