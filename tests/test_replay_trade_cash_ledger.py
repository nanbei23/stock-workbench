import sqlite3
import tempfile
import unittest
from pathlib import Path

from models.database import SCHEMA
from scripts import replay_trade_cash_ledger


class ReplayTradeCashLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "workbench.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            conn.execute("INSERT INTO settings (key, value) VALUES ('cash_balance_default', '100000')")
            conn.executemany(
                """
                INSERT INTO trades
                    (code, name, direction, price, shares, amount, commission, stamp_tax, transfer_fee, total_cost, trade_time, account_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("000001", "平安银行", "buy", 10, 100, 1000, 1, 0, 0.1, 1001.1, "2026-06-04 09:30:00", "default"),
                    ("000001", "平安银行", "sell", 11, 40, 440, 1, 0.22, 0.044, 438.736, "2026-06-04 10:30:00", "default"),
                ],
            )
            conn.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def test_replay_is_dry_run_by_default(self):
        result = replay_trade_cash_ledger.replay(self.db_path, "default", "2026-06-04", None, False)

        with sqlite3.connect(self.db_path) as conn:
            cash = conn.execute("SELECT value FROM settings WHERE key='cash_balance_default'").fetchone()[0]
            ledger_count = conn.execute("SELECT COUNT(*) FROM cash_ledger").fetchone()[0]

        self.assertEqual(result["ending_cash"], 99437.636)
        self.assertEqual(cash, "100000")
        self.assertEqual(ledger_count, 0)

    def test_replay_apply_writes_cash_and_ledger_entries(self):
        result = replay_trade_cash_ledger.replay(self.db_path, "default", "2026-06-04", None, True)

        with sqlite3.connect(self.db_path) as conn:
            cash = float(conn.execute("SELECT value FROM settings WHERE key='cash_balance_default'").fetchone()[0])
            rows = conn.execute("SELECT direction, amount, balance_after, source FROM cash_ledger ORDER BY id").fetchall()

        self.assertEqual(result["ending_cash"], 99437.636)
        self.assertEqual(cash, 99437.636)
        self.assertEqual(rows[0], ("trade_buy", -1001.1, 98998.9, "trade_replay"))
        self.assertEqual(rows[1], ("trade_sell", 438.736, 99437.636, "trade_replay"))


if __name__ == "__main__":
    unittest.main()
