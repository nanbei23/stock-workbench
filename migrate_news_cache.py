"""Migration: create news_cache table"""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent / "data" / "workbench.db"
db = sqlite3.connect(str(DB))
db.execute("""
CREATE TABLE IF NOT EXISTS news_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code6 TEXT,
    source TEXT,
    title TEXT,
    content TEXT,
    url TEXT,
    sentiment TEXT DEFAULT 'neutral',
    published_at TEXT,
    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")
db.commit()
print("news_cache table created")
db.close()
