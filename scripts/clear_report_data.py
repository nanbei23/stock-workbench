"""Clear generated AI report/task data while preserving market snapshots."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.database import DB_PATH, SCHEMA


REPORT_TABLES = (
    "analysis_progress",
    "analysis_reports",
    "analysis_tasks",
    "signal_tracking",
    "batch_report_items",
    "batch_report_jobs",
    "batch_job_items",
    "batch_jobs",
)


PRESERVED_TABLES = (
    "stock_data_snapshots",
    "watchlist",
    "portfolio",
    "trades",
    "settings",
)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _count_rows(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def clear_report_data(db_path: Path = DB_PATH, *, apply: bool = False) -> dict[str, Any]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        before = {table: _count_rows(conn, table) for table in REPORT_TABLES}
        preserved = {table: _count_rows(conn, table) for table in PRESERVED_TABLES}
        if apply:
            with conn:
                for table in REPORT_TABLES:
                    if _table_exists(conn, table):
                        conn.execute(f"DELETE FROM {table}")
        after = {table: _count_rows(conn, table) for table in REPORT_TABLES}
    return {
        "db_path": str(db_path),
        "deleted": apply,
        "tables": before,
        "after": after,
        "preserved_tables": preserved,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Clear generated AI report/task data before regenerating TradingAgents reports."
    )
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--apply", action="store_true", help="真正删除数据；不传则只预览将清理的行数")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = clear_report_data(args.db, apply=args.apply)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.apply:
        print("预览模式：未删除任何数据。确认后加 --apply 执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
