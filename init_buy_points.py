import sqlite3
from pathlib import Path

db_path = Path(__file__).parent / "data" / "workbench.db"
db = sqlite3.connect(str(db_path))
db.execute("""
CREATE TABLE IF NOT EXISTS buy_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    price REAL NOT NULL,
    shares INTEGER DEFAULT 0,
    reason TEXT,
    status TEXT DEFAULT "pending",
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
db.commit()
db.close()
print("buy_points table created/verified")
