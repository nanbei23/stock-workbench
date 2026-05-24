"""Verify migration and syntax"""
import sqlite3
from pathlib import Path

# Check DB tables
db = sqlite3.connect("data/workbench.db")
tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables:", tables)
assert "news_cache" in tables, "news_cache missing!"
print("news_cache columns:", [r[1] for r in db.execute("PRAGMA table_info(news_cache)").fetchall()])
db.close()

# Syntax check both files
import py_compile
py_compile.compile("api/ai_api.py", doraise=True)
print("ai_api.py: OK")
py_compile.compile("scheduler/report_runner.py", doraise=True)
print("report_runner.py: OK")
print("All checks passed!")
