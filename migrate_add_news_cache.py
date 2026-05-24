"""Migration: create news_cache table in existing DB"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "workbench.db"

def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
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
    conn.commit()
    
    # Verify
    tables = [t[0] for t in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    print("Tables:", tables)
    assert "news_cache" in tables, "news_cache not found!"
    
    cols = [c[1] for c in conn.execute("PRAGMA table_info(news_cache)").fetchall()]
    print("news_cache columns:", cols)
    print("Migration successful!")
    conn.close()

if __name__ == "__main__":
    migrate()
