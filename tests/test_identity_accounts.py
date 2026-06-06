import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import models.database as database
from api.auth_api import router as auth_router
from api.ai_api import router as ai_router
from api.enhancement_api import router as enhancement_router
from api.hermes_api import router as hermes_router
from api.performance_api import router as performance_router
from api.holding_review_api import router as holding_review_router
from api.portfolio_api import router as portfolio_router
from api.position_plan_api import router as position_plan_router
from services import auth_service


ROOT = Path(__file__).resolve().parents[1]


class IdentityAccountTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        with sqlite3.connect(self.db_path) as db:
            db.executescript(database.SCHEMA)

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    async def test_identity_migration_creates_admin_and_default_securities_account(self):
        db = await database.get_db()
        try:
            await database.ensure_identity_tables(db)
            user = await db.execute_fetchall("SELECT * FROM login_users WHERE id = 'admin'")
            account = await db.execute_fetchall(
                "SELECT * FROM securities_accounts WHERE id = 'default' AND login_user_id = 'admin'"
            )
        finally:
            await db.close()

        self.assertEqual(len(user), 1)
        self.assertEqual(user[0]["username"], "admin")
        self.assertTrue(auth_service.verify_password("123456", user[0]["password_hash"]))
        self.assertEqual(user[0]["must_change_credentials"], 1)
        self.assertEqual(len(account), 1)
        self.assertEqual(account[0]["name"], "默认账户")

    async def test_current_login_user_skips_identity_migration_when_schema_is_ready(self):
        await database.init_db()

        with patch("services.auth_service.ensure_identity_tables", new=AsyncMock()) as ensure:
            user = await auth_service.current_login_user()

        self.assertEqual(user["id"], "admin")
        ensure.assert_not_awaited()

    async def test_legacy_local_owner_data_is_migrated_to_admin(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO login_users (id, username, password_hash, display_name) VALUES (?, ?, ?, ?)",
                ("local_owner", "local_owner", auth_service.hash_password(""), "本机账户"),
            )
            db.execute(
                "INSERT INTO securities_accounts (id, login_user_id, name) VALUES (?, ?, ?)",
                ("legacy-account", "local_owner", "历史账户"),
            )
            db.execute("INSERT INTO watchlist (code, name, login_user_id) VALUES ('000001', '平安银行', 'local_owner')")
            db.execute("INSERT INTO analysis_reports (code, signal, raw_state, login_user_id) VALUES ('000001', 'BUY', '{}', 'local_owner')")
            db.commit()

        await database.init_db()

        with sqlite3.connect(self.db_path) as db:
            users = db.execute("SELECT id, username FROM login_users ORDER BY id").fetchall()
            securities_owner = db.execute("SELECT login_user_id FROM securities_accounts WHERE id='legacy-account'").fetchone()[0]
            watch_owner = db.execute("SELECT login_user_id FROM watchlist WHERE code='000001'").fetchone()[0]
            report_owner = db.execute("SELECT login_user_id FROM analysis_reports WHERE code='000001'").fetchone()[0]

        self.assertIn(("admin", "admin"), users)
        self.assertNotIn(("local_owner", "local_owner"), users)
        self.assertEqual(securities_owner, "admin")
        self.assertEqual(watch_owner, "admin")
        self.assertEqual(report_owner, "admin")

    async def test_admin_login_requires_profile_update_and_clears_after_change(self):
        await database.init_db()
        app = FastAPI()
        app.include_router(auth_router, prefix="/api")
        with TestClient(app) as client:
            login = client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
            self.assertEqual(login.status_code, 200)
            self.assertTrue(login.json()["user"]["must_change_credentials"])

            weak = client.put("/api/auth/profile", json={"username": "admin", "password": "123456"})
            self.assertEqual(weak.status_code, 400)

            update = client.put(
                "/api/auth/profile",
                json={"username": "owner", "display_name": "Owner", "password": "new-pass-123"},
            )
            self.assertEqual(update.status_code, 200)
            self.assertEqual(update.json()["user"]["username"], "owner")
            self.assertFalse(update.json()["user"]["must_change_credentials"])

            client.post("/api/auth/logout")
            old_login = client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
            new_login = client.post("/api/auth/login", json={"username": "owner", "password": "new-pass-123"})

        self.assertEqual(old_login.status_code, 401)
        self.assertEqual(new_login.status_code, 200)
        self.assertFalse(new_login.json()["user"]["must_change_credentials"])

    async def test_auth_session_and_account_listing_are_scoped_to_login_user(self):
        await database.init_db()
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO login_users (id, username, password_hash, display_name)
                VALUES (?, ?, ?, ?)
                """,
                ("user-b", "userb", auth_service.hash_password("pw-b"), "User B"),
            )
            db.execute(
                """
                INSERT INTO securities_accounts (id, login_user_id, name, broker)
                VALUES (?, ?, ?, ?)
                """,
                ("b-account", "user-b", "B 账户", "测试券商"),
            )
            db.commit()

        app = FastAPI()
        app.include_router(auth_router, prefix="/api")
        app.include_router(portfolio_router, prefix="/api")
        with TestClient(app) as client:
            login = client.post("/api/auth/login", json={"username": "userb", "password": "pw-b"})
            self.assertEqual(login.status_code, 200)
            self.assertEqual(login.json()["user"]["id"], "user-b")

            session = client.get("/api/auth/session")
            self.assertEqual(session.status_code, 200)
            self.assertEqual(session.json()["user"]["id"], "user-b")

            accounts = client.get("/api/accounts")
            self.assertEqual(accounts.status_code, 200)
            self.assertEqual([item["id"] for item in accounts.json()["accounts"]], ["b-account"])

    async def test_login_user_cannot_read_another_users_securities_account(self):
        await database.init_db()
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                """
                INSERT INTO login_users (id, username, password_hash, display_name)
                VALUES (?, ?, ?, ?)
                """,
                ("user-b", "userb", auth_service.hash_password("pw-b"), "User B"),
            )
            db.execute(
                """
                INSERT INTO securities_accounts (id, login_user_id, name, broker)
                VALUES (?, ?, ?, ?)
                """,
                ("b-account", "user-b", "B 账户", "测试券商"),
            )
            db.commit()

        app = FastAPI()
        app.include_router(auth_router, prefix="/api")
        app.include_router(portfolio_router, prefix="/api")
        with TestClient(app) as client:
            client.post("/api/auth/login", json={"username": "userb", "password": "pw-b"})
            resp = client.get("/api/portfolio?account_id=default")

        self.assertEqual(resp.status_code, 403)
        self.assertIn("证券账户不属于当前登录账户", resp.json()["detail"])


class IdentityGapClosureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self.original_db_path = database.DB_PATH
        database.DB_PATH = self.db_path
        import asyncio
        asyncio.run(database.init_db())

    def tearDown(self):
        database.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def _add_second_user(self):
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO login_users (id, username, password_hash, display_name) VALUES (?, ?, ?, ?)",
                ("user-b", "userb", auth_service.hash_password("pw-b"), "User B"),
            )
            db.execute(
                "INSERT INTO securities_accounts (id, login_user_id, name, broker) VALUES (?, ?, ?, ?)",
                ("b-account", "user-b", "B 账户", "测试券商"),
            )
            db.commit()

    def _client_as_user_b(self):
        app = FastAPI()
        app.include_router(auth_router, prefix="/api")
        app.include_router(portfolio_router, prefix="/api")
        app.include_router(holding_review_router, prefix="/api")
        app.include_router(position_plan_router, prefix="/api")
        client = TestClient(app)
        client.post("/api/auth/login", json={"username": "userb", "password": "pw-b"})
        return client

    def test_login_page_and_logout_contract_exist(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        base_html = (ROOT / "templates/base.html").read_text(encoding="utf-8")
        app_js = (ROOT / "static/js/app.js").read_text(encoding="utf-8")
        login_html = (ROOT / "templates/login.html").read_text(encoding="utf-8")

        self.assertIn('@app.get("/login"', app_source)
        self.assertIn("loginUserBadge", base_html)
        self.assertIn("logoutLoginUser", app_js)
        self.assertIn("/api/auth/login", login_html)
        self.assertIn("登录账户", login_html)

    def test_remaining_account_action_endpoints_reject_foreign_account(self):
        self._add_second_user()
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "INSERT INTO trades (code, name, direction, price, shares, amount, trade_time, account_id) VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?)",
                ("000001", "平安银行", "buy", 10, 100, 1000, "default"),
            )
            db.execute(
                "INSERT INTO holding_daily_reviews (review_id, date, account_id, status) VALUES (?, date('now'), ?, ?)",
                ("review-default", "default", "completed"),
            )
            db.commit()

        with self._client_as_user_b() as client:
            cases = [
                client.delete("/api/trades/1?account_id=default"),
                client.get("/api/trade-memories/candidates?account_id=default"),
                client.get("/api/daily-decision-reports?account_id=default"),
                client.get("/api/position-plans?account_id=default"),
            ]

        self.assertEqual([resp.status_code for resp in cases], [403, 403, 403, 403])

    def test_watchlist_and_reports_are_scoped_to_login_user(self):
        self._add_second_user()
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO watchlist (code, name, login_user_id) VALUES ('000001', '平安银行', 'admin')")
            db.execute("INSERT INTO watchlist (code, name, login_user_id) VALUES ('000002', '万科A', 'user-b')")
            db.execute("INSERT INTO analysis_reports (code, signal, raw_state, login_user_id) VALUES ('000001', 'BUY', '{}', 'admin')")
            db.execute("INSERT INTO analysis_reports (code, signal, raw_state, login_user_id) VALUES ('000002', 'SELL', '{}', 'user-b')")
            db.commit()

        app = FastAPI()
        app.include_router(auth_router, prefix="/api")
        app.include_router(portfolio_router, prefix="/api")
        app.include_router(ai_router, prefix="/api")
        with TestClient(app) as client:
            client.post("/api/auth/login", json={"username": "userb", "password": "pw-b"})
            watchlist = client.get("/api/watchlist")
            reports = client.get("/api/ai/reports?limit=20")

        self.assertEqual(watchlist.status_code, 200)
        self.assertEqual([row["code"] for row in watchlist.json()["stocks"]], ["000002"])
        self.assertEqual(reports.status_code, 200)
        self.assertEqual([row["code"] for row in reports.json()["reports"]], ["000002"])

    def test_two_login_users_can_watch_same_stock_code(self):
        self._add_second_user()
        with sqlite3.connect(self.db_path) as db:
            db.execute("INSERT INTO watchlist (code, name, login_user_id) VALUES ('000001', '平安银行', 'admin')")
            db.execute("INSERT INTO watchlist (code, name, login_user_id) VALUES ('000001', '平安银行', 'user-b')")
            db.commit()

            rows = db.execute(
                "SELECT code, login_user_id FROM watchlist WHERE code='000001' ORDER BY login_user_id"
            ).fetchall()

        self.assertEqual(rows, [("000001", "admin"), ("000001", "user-b")])

    def test_account_management_compatibility_and_health_contracts(self):
        app = FastAPI()
        app.include_router(auth_router, prefix="/api")
        app.include_router(portfolio_router, prefix="/api")
        app.include_router(enhancement_router, prefix="/api")
        with TestClient(app) as client:
            update = client.put("/api/accounts/default", json={"name": "主账户", "broker": "方正证券", "notes": "主用"})
            accounts = client.get("/api/accounts")
            delete_default = client.delete("/api/accounts/default")
            with sqlite3.connect(self.db_path) as db:
                db.execute("INSERT INTO portfolio (code, name, total_shares, account_id) VALUES ('000003', '测试', 100, 'missing-account')")
                db.commit()
            health = client.get("/api/data-health")

        self.assertEqual(update.status_code, 200)
        self.assertEqual(accounts.json()["accounts"][0]["name"], "主账户")
        self.assertEqual(delete_default.status_code, 400)
        self.assertIn("默认证券账户不能删除", delete_default.json()["detail"])
        self.assertEqual(health.status_code, 200)
        identity = next(item for item in health.json()["checks"] if item["key"] == "identity_integrity")
        self.assertEqual(identity["details"][0]["account_id"], "missing-account")

        database_source = (ROOT / "models/database.py").read_text(encoding="utf-8")
        auth_source = (ROOT / "services/auth_service.py").read_text(encoding="utf-8")
        account_html = (ROOT / "templates/account.html").read_text(encoding="utf-8")
        account_js = (ROOT / "static/js/account.js").read_text(encoding="utf-8")
        settings_html = (ROOT / "templates/settings.html").read_text(encoding="utf-8")
        self.assertIn("portfolio_securities_view", database_source)
        self.assertIn("account_id AS securities_account_id", database_source)
        self.assertIn("physical account_id columns are securities account ids", auth_source)
        self.assertIn("accountProfileForm", account_html)
        self.assertIn("securitiesAccountForm", account_html)
        self.assertIn("saveAccountProfile", account_js)
        self.assertIn("deleteSecuritiesAccount", account_js)
        self.assertNotIn("loginUserManagementPanel", settings_html)
        self.assertNotIn("securitiesAccountForm", settings_html)
        self.assertIn("登录账户完整性", settings_html)

    def test_login_user_management_creates_and_archives_user_with_default_account(self):
        app = FastAPI()
        app.include_router(auth_router, prefix="/api")
        app.include_router(portfolio_router, prefix="/api")
        with TestClient(app) as client:
            create = client.post(
                "/api/auth/users",
                json={"username": "userc", "password": "pw-c", "display_name": "User C"},
            )
            users = client.get("/api/auth/users")
            archive = client.delete(f"/api/auth/users/{create.json()['user']['id']}")

        self.assertEqual(create.status_code, 200)
        self.assertEqual(create.json()["user"]["username"], "userc")
        self.assertTrue(create.json()["default_securities_account"]["id"].startswith(create.json()["user"]["id"]))
        self.assertIn("userc", [item["username"] for item in users.json()["users"]])
        self.assertEqual(archive.status_code, 200)
        with sqlite3.connect(self.db_path) as db:
            user_status = db.execute("SELECT status FROM login_users WHERE username='userc'").fetchone()[0]
            account_status = db.execute(
                "SELECT status FROM securities_accounts WHERE login_user_id=?",
                (create.json()["user"]["id"],),
            ).fetchone()[0]
        self.assertEqual(user_status, "archived")
        self.assertEqual(account_status, "archived")

    def test_hermes_writes_use_current_login_user_and_selected_securities_account(self):
        self._add_second_user()
        app = FastAPI()
        app.include_router(auth_router, prefix="/api")
        app.include_router(hermes_router, prefix="/api")
        with TestClient(app) as client:
            client.post("/api/auth/login", json={"username": "userb", "password": "pw-b"})
            parsed = client.post(
                "/api/hermes/message",
                json={"message": "新增 600519 贵州茅台 到自选", "account_id": "b-account"},
            )
            self.assertEqual(parsed.status_code, 200)
            draft = parsed.json()["draft"]
            confirmed = client.post(
                "/api/hermes/confirm",
                json={"session_id": parsed.json()["session_id"], "draft_id": draft["id"], "account_id": "b-account"},
            )

        self.assertEqual(confirmed.status_code, 200)
        with sqlite3.connect(self.db_path) as db:
            row = db.execute("SELECT code, login_user_id FROM watchlist WHERE code='600519'").fetchone()
        self.assertEqual(row, ("600519", "user-b"))

    def test_performance_overview_rejects_foreign_account_and_passes_owned_scope(self):
        self._add_second_user()
        app = FastAPI()
        app.include_router(auth_router, prefix="/api")
        app.include_router(performance_router, prefix="/api")

        calls = []

        async def fake_overview(**kwargs):
            calls.append(kwargs)
            return {"filters": {}, "scope": kwargs}

        from services import performance_service

        original = performance_service.overview
        performance_service.overview = fake_overview
        try:
            with TestClient(app) as client:
                client.post("/api/auth/login", json={"username": "userb", "password": "pw-b"})
                reject = client.get("/api/performance/overview?account_id=default")
                ok = client.get("/api/performance/overview?account_id=b-account")
        finally:
            performance_service.overview = original

        self.assertEqual(reject.status_code, 403)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(calls[0]["login_user_id"], "user-b")
        self.assertEqual(calls[0]["account_id"], "b-account")

    def test_remaining_gap_static_contracts_are_account_scoped(self):
        batch_research = (ROOT / "scripts/batch_research.py").read_text(encoding="utf-8")
        ta_bridge = (ROOT / "scheduler/ta_bridge.py").read_text(encoding="utf-8")
        report_runner = (ROOT / "scheduler/report_runner.py").read_text(encoding="utf-8")
        performance_service = (ROOT / "services/performance_service.py").read_text(encoding="utf-8")
        performance_api = (ROOT / "api/performance_api.py").read_text(encoding="utf-8")
        hermes_api = (ROOT / "api/hermes_api.py").read_text(encoding="utf-8")
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        base_html = (ROOT / "templates/base.html").read_text(encoding="utf-8")
        account_html = (ROOT / "templates/account.html").read_text(encoding="utf-8")
        account_js = (ROOT / "static/js/account.js").read_text(encoding="utf-8")

        self.assertIn("login_user_id", batch_research)
        self.assertIn("login_user_id", ta_bridge)
        self.assertIn("login_user_id", report_runner)
        self.assertIn("account_id: str | None = Query", performance_api)
        self.assertIn("position_plan_performance(limit=limit, account_id=account_id)", performance_service)
        self.assertIn("require_login_user", hermes_api)
        self.assertIn('@app.get("/account"', app_source)
        self.assertIn("账户管理", base_html)
        self.assertIn("账户资料", account_html)
        self.assertIn("证券账户", account_html)
        self.assertIn("saveLoginUser", account_js)
