import sqlite3
import tempfile
import unittest
from pathlib import Path

from models import conditional_order, news_manager, portfolio, settings, watchlist


class ModelHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        self._patch_db_paths(self.db_path)
        self._init_schema()

    def tearDown(self):
        self.tmp.cleanup()

    def _patch_db_paths(self, db_path):
        self.original_paths = {
            watchlist: watchlist.DB_PATH,
            portfolio: portfolio.DB_PATH,
            settings: settings.DB_PATH,
            news_manager: news_manager.DB_PATH,
            conditional_order: conditional_order.DB_PATH,
        }
        for module in self.original_paths:
            module.DB_PATH = db_path

    def _init_schema(self):
        with sqlite3.connect(self.db_path) as db:
            db.executescript(
                """
                CREATE TABLE watchlist (
                    code TEXT PRIMARY KEY,
                    name TEXT,
                    sort_order INTEGER DEFAULT 0,
                    strategy_state TEXT DEFAULT 'watch',
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    price REAL NOT NULL,
                    shares REAL NOT NULL,
                    amount REAL NOT NULL,
                    total_cost REAL DEFAULT 0,
                    notes TEXT,
                    trade_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE daily_pnl (
                    date TEXT NOT NULL,
                    code6 TEXT DEFAULT '',
                    pnl REAL,
                    close_price REAL,
                    shares REAL,
                    PRIMARY KEY (date, code6)
                );
                CREATE TABLE settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE news_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code6 TEXT,
                    source TEXT,
                    title TEXT,
                    content TEXT,
                    url TEXT,
                    sentiment TEXT DEFAULT 'neutral',
                    published_at TEXT,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE conditional_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL,
                    name TEXT,
                    condition_type TEXT NOT NULL,
                    target_price REAL NOT NULL,
                    action TEXT NOT NULL,
                    shares REAL,
                    status TEXT DEFAULT 'pending',
                    triggered_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,
                    notes TEXT
                );
                """
            )

    def test_watchlist_uses_current_schema(self):
        watchlist.add_stock("600519", "贵州茅台")
        watchlist.add_stock("000001", "平安银行")
        watchlist.reorder(["000001", "600519"])
        watchlist.set_pending("000001", True)

        rows = watchlist.get_all()
        self.assertEqual([row["code"] for row in rows], ["000001", "600519"])
        self.assertEqual(watchlist.get_stock("000001")["strategy_state"], "near_buy")

    def test_portfolio_summary_uses_code_and_direction_columns(self):
        portfolio.record_trade("600519", "buy", 100.0, 100, "first")
        portfolio.record_trade("600519", "buy", 80.0, 100, "add")
        portfolio.record_trade("600519", "sell", 120.0, 50, "trim")

        summary = portfolio.get_position_summary("600519")
        self.assertEqual(summary["shares"], 150)
        self.assertEqual(summary["avg_price"], 90.0)
        self.assertEqual(summary["min_buy_price"], 80.0)
        self.assertEqual(summary["last_buy_price"], 80.0)

    def test_settings_round_trip(self):
        settings.set_setting("quick_think_model", "deepseek-chat")
        self.assertEqual(settings.get_setting("quick_think_model"), "deepseek-chat")
        self.assertEqual(settings.get_all_settings(), {"quick_think_model": "deepseek-chat"})

    def test_news_manager_deduplicates_by_url(self):
        item = {
            "source": "test",
            "title": "标题",
            "content": "正文",
            "url": "https://example.com/news/1",
            "sentiment": "positive",
            "published_at": "2026-05-29",
        }
        news_manager.save_news("600519", [item, item])
        news = news_manager.get_news("600519")
        self.assertEqual(len(news), 1)
        self.assertEqual(news_manager.get_sentiment_summary("600519")["overall"], "positive")

    def test_conditional_order_uses_pending_current_schema(self):
        order_id = conditional_order.create_order(
            "600519", "price_gte", 100.0, action="sell", action_shares=100
        )
        active = conditional_order.get_active_orders("600519")
        self.assertEqual(active[0]["id"], order_id)

        triggered = conditional_order.check_orders({"600519": {"price": 101.0, "change_pct": 1.0}})
        self.assertEqual(len(triggered), 1)
        conditional_order.update_status(order_id, "triggered")
        self.assertEqual(conditional_order.get_active_orders("600519"), [])


if __name__ == "__main__":
    unittest.main()
