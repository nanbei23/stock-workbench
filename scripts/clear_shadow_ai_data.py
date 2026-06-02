"""Clear AI performance tracking data while preserving reports."""

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


AI_PERFORMANCE_TABLES = (
    "signal_tracking",
    "ai_shadow_orders",
    "ai_shadow_positions",
)

# Backward-compatible name for callers/tests that only knew about shadow data.
SHADOW_AI_TABLES = AI_PERFORMANCE_TABLES


PRESERVED_TABLES = (
    "analysis_reports",
    "watchlist",
    "portfolio",
    "trades",
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


def clear_shadow_ai_data(db_path: Path = DB_PATH, *, apply: bool = False) -> dict[str, Any]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA)
        before = {table: _count_rows(conn, table) for table in AI_PERFORMANCE_TABLES}
        preserved = {table: _count_rows(conn, table) for table in PRESERVED_TABLES}
        if apply:
            with conn:
                for table in AI_PERFORMANCE_TABLES:
                    if _table_exists(conn, table):
                        conn.execute(f"DELETE FROM {table}")
        after = {table: _count_rows(conn, table) for table in AI_PERFORMANCE_TABLES}
    return {
        "db_path": str(db_path),
        "deleted": apply,
        "tables": before,
        "after": after,
        "preserved_tables": preserved,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Clear AI performance tracking, shadow orders, and shadow positions "
            "before re-syncing regenerated reports."
        )
    )
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--apply", action="store_true", help="真正删除数据；不传则只预览将清理的行数")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = clear_shadow_ai_data(args.db, apply=args.apply)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.apply:
        print("预览模式：未删除任何数据。确认后加 --apply 执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
