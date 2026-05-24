"""P1 migration: add new columns/tables for P1 gaps."""
import sqlite3
import os

os.chdir('/Users/yuxuanfung/stock-workbench')
conn = sqlite3.connect('data/workbench.db')
c = conn.cursor()

for sql in [
    'ALTER TABLE strategy_params ADD COLUMN buy_prices TEXT DEFAULT "[]"',
]:
    try:
        c.execute(sql)
        print(f"OK: {sql}")
    except Exception as e:
        print(f"SKIP: {sql} ({e})")

c.execute('''CREATE TABLE IF NOT EXISTS pending_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT,
    target_buy_price REAL,
    plan_shares INTEGER DEFAULT 100,
    plan_total_cost REAL,
    reason TEXT,
    strategy_state TEXT DEFAULT "watch",
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)''')
print("OK: pending_positions table")

conn.commit()
conn.close()
print("Migration complete.")
