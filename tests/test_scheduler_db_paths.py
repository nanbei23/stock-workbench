import sqlite3
import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

from models import database
from scheduler import ai_engine, anomaly_checker, conditional_order_checker, report_runner


class SchedulerDbPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_paths = {
            ai_engine: ai_engine.DB_PATH,
            anomaly_checker: anomaly_checker.DB_PATH,
            conditional_order_checker: conditional_order_checker.DB_PATH,
            report_runner: report_runner.DB_PATH,
        }
        self.original_env = {
            key: os.environ.get(key)
            for key in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_BASE", "OPENAI_API_BASE")
        }
        ai_engine.DB_PATH = self.db_path
        anomaly_checker.DB_PATH = self.db_path
        conditional_order_checker.DB_PATH = self.db_path
        report_runner.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)

    def tearDown(self):
        for module, path in self.original_paths.items():
            module.DB_PATH = path
        for key, value in self.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def test_anomaly_checker_reads_configured_database_path(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO watchlist (code, name) VALUES (?, ?)", ("000001", "平安银行"))
            db.execute(
                "INSERT INTO portfolio (code, name, total_shares) VALUES (?, ?, ?)",
                ("600519", "贵州茅台", 100),
            )
            db.commit()

        stocks = anomaly_checker._get_all_watchlist_codes()

        self.assertEqual(set(stocks), {("000001", "平安银行"), ("600519", "贵州茅台")})

    def test_report_runner_reads_configured_database_path(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("schedule_open_report", "true"))
            db.commit()

        self.assertEqual(report_runner._get_setting("schedule_open_report"), "true")

    def test_ai_engine_reads_configured_database_path(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("llm_provider", "deepseek"))
            db.execute("INSERT INTO watchlist (code, name) VALUES (?, ?)", ("000001", "平安银行"))
            db.execute(
                "INSERT INTO portfolio (code, name, total_shares, avg_cost) VALUES (?, ?, ?, ?)",
                ("000001", "平安银行", 100, 10.5),
            )
            db.commit()

        self.assertEqual(ai_engine.get_llm_config()["llm_provider"], "deepseek")
        stocks = ai_engine.get_watchlist_and_portfolio()
        self.assertEqual(stocks[0]["code"], "000001")
        self.assertEqual(stocks[0]["total_shares"], 100)

    def test_ai_engine_prefers_base_url_selected_models(self):
        with sqlite3.connect(self.db_path) as db:
            db.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                [
                    ("model_mode", "economy"),
                    ("deep_think_model", "provider-deep"),
                    ("quick_think_model", "provider-quick"),
                    ("llm_model_options", '["provider-deep","provider-quick"]'),
                    ("custom_endpoint", "https://api.example.com/v1"),
                ],
            )
            db.commit()

        config = ai_engine.apply_llm_config_to_ta_config({})

        self.assertEqual(config["deep_think_llm"], "provider-deep")
        self.assertEqual(config["quick_think_llm"], "provider-quick")

    def test_ai_engine_injects_custom_endpoint_for_tradingagents(self):
        with sqlite3.connect(self.db_path) as db:
            db.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?)",
                [
                    ("llm_provider", "openai-compatible"),
                    ("api_key", "sk-test-key"),
                    ("deep_think_model", "provider-deep"),
                    ("quick_think_model", "provider-quick"),
                    ("llm_model_options", '["provider-deep","provider-quick"]'),
                    ("custom_endpoint", "https://api.example.com/v1"),
                ],
            )
            db.commit()

        config = ai_engine.apply_llm_config_to_ta_config({})

        self.assertEqual(config["llm_provider"], "deepseek")
        self.assertEqual(config["backend_url"], "https://api.example.com/v1")
        self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "sk-test-key")
        self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-test-key")
        self.assertEqual(os.environ["DEEPSEEK_API_BASE"], "https://api.example.com/v1")
        self.assertEqual(os.environ["OPENAI_API_BASE"], "https://api.example.com/v1")


class ConditionalOrderCheckerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_path = conditional_order_checker.DB_PATH
        conditional_order_checker.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)

    def tearDown(self):
        conditional_order_checker.DB_PATH = self.original_path
        self.tmp.cleanup()

    async def test_expires_and_triggers_orders_on_configured_database_path(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO conditional_orders
                    (code, name, condition_type, target_price, action, status, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("000001", "平安银行", "price_gte", 10.0, "alert", "pending", "2000-01-01 00:00:00"),
            )
            db.execute(
                """
                INSERT INTO conditional_orders
                    (code, name, condition_type, target_price, action, status, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                ("600519", "贵州茅台", "price_gte", 100.0, "alert", "pending", None),
            )
            db.commit()

        conditional_order_checker._expire_old_orders()
        with patch(
            "data.quote.get_batch_quotes",
            new=AsyncMock(return_value={"600519": {"price": 101.0, "change_pct": 1.0}}),
        ):
            triggered = await conditional_order_checker._check_conditional_orders()

        self.assertEqual([order["code"] for order in triggered], ["600519"])
        with sqlite3.connect(self.db_path) as db:
            rows = {
                row[0]: row[1:]
                for row in db.execute(
                    "SELECT code, status, triggered_at FROM conditional_orders ORDER BY code"
                ).fetchall()
            }

        self.assertEqual(rows["000001"][0], "expired")
        self.assertEqual(rows["600519"][0], "triggered")
        self.assertIsNotNone(rows["600519"][1])


if __name__ == "__main__":
    unittest.main()
