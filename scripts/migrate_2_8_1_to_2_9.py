#!/usr/bin/env python3
"""Upgrade an existing 2.8.1 database to the 2.9 schema."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


REQUIRED_COLUMNS: dict[str, set[str]] = {
    "batch_jobs": {
        "worker_id",
        "heartbeat_at",
        "lease_owner",
        "lease_token",
        "lease_until",
        "pause_requested",
        "paused_at",
        "input_snapshot_json",
        "quality_json",
        "post_actions_json",
        "runtime_json",
    },
    "batch_job_items": {
        "locked_snapshot_id",
        "error_type",
        "next_retry_at",
        "lease_owner",
        "lease_token",
        "lease_until",
        "quality_json",
    },
    "batch_job_item_steps": {
        "item_id",
        "job_id",
        "role_key",
        "status",
        "content",
        "error_type",
        "next_retry_at",
        "input_hash",
        "model_config_json",
        "duration_ms",
        "token_usage_json",
        "heartbeat_at",
    },
    "batch_worker_heartbeats": {
        "worker_id",
        "pid",
        "state",
        "model_provider_ids_json",
        "model_tier",
        "last_seen_at",
        "last_loop_at",
        "last_claim_at",
        "current_job_id",
        "current_job_type",
        "current_item_id",
        "current_code",
        "current_stage",
        "last_result_json",
        "error",
    },
    "position_plans": {
        "plan_id",
        "adoption_status",
        "stage",
        "context_strategy",
        "source_report_ids",
        "model_config_json",
        "role_discussion_json",
        "recommendations_json",
        "decision_market_snapshot_json",
        "market_context_captured_at",
        "confirmed_snapshot_json",
    },
    "position_plan_items": {
        "plan_id",
        "code",
        "action",
        "suggested_amount",
        "position_pct",
        "suggested_shares",
        "source_report_id",
    },
    "report_selection_sets": {
        "selection_id",
        "source_page",
        "source_label",
        "codes_json",
        "filters_json",
        "login_user_id",
        "expires_at",
    },
    "watchlist": {
        "code",
        "name",
        "login_user_id",
    },
    "analysis_reports": {
        "code",
        "signal",
        "login_user_id",
    },
}


def _default_db_path() -> Path:
    return Path(os.getenv("WORKBENCH_DB_PATH", ROOT / "data" / "workbench.db")).expanduser().resolve()


def _backup_database(db_path: Path) -> Path | None:
    if not db_path.exists():
        return None
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{db_path.stem}_pre_2_9_{stamp}{db_path.suffix or '.db'}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def _configure_database_path(db_path: Path) -> None:
    os.environ["WORKBENCH_DB_PATH"] = str(db_path)
    import config
    import models.database as database

    config.DB_PATH = db_path
    database.DB_PATH = db_path


def _normalize_running_jobs(db_path: Path) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='batch_jobs'"
        ).fetchone()
        if not row:
            return 0
        cursor = conn.execute(
            """
            UPDATE batch_jobs
            SET status='interrupted',
                error=CASE
                    WHEN error IS NULL OR error = '' THEN '2.9 迁移时发现旧运行任务，已转为可恢复中断'
                    ELSE error || '；2.9 迁移时转为可恢复中断'
                END,
                current_code='',
                worker_id=NULL,
                heartbeat_at=NULL,
                lease_owner=NULL,
                lease_token=NULL,
                lease_until=NULL,
                pause_requested=0,
                updated_at=datetime('now')
            WHERE status='running'
            """
        )
        conn.commit()
        return int(cursor.rowcount or 0)


def verify_schema(db_path: Path) -> dict[str, Any]:
    missing: dict[str, list[str]] = {}
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table, required_columns in REQUIRED_COLUMNS.items():
            if table not in tables:
                missing[table] = sorted(required_columns)
                continue
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            absent = sorted(required_columns - columns)
            if absent:
                missing[table] = absent
        migrations = []
        if "schema_migrations" in tables:
            migrations = [
                {"version": row[0], "name": row[1], "applied_at": row[2]}
                for row in conn.execute(
                    "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
                )
            ]
    return {
        "ok": not missing,
        "missing": missing,
        "migrations": migrations,
    }


def migrate_database(db_path: Path | str | None = None, *, create_backup: bool = True) -> dict[str, Any]:
    target = Path(db_path).expanduser().resolve() if db_path else _default_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    backup_path = _backup_database(target) if create_backup else None

    _configure_database_path(target)
    import models.database as database

    asyncio.run(database.init_db())
    interrupted_jobs = _normalize_running_jobs(target)
    verification = verify_schema(target)
    if not verification["ok"]:
        raise RuntimeError(f"2.9 schema verification failed: {verification['missing']}")
    return {
        "status": "ok",
        "db_path": str(target),
        "backup_path": str(backup_path) if backup_path else "",
        "interrupted_jobs": interrupted_jobs,
        "schema": verification,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Upgrade Stock Workbench database from 2.8.1 to 2.9.")
    parser.add_argument("--db-path", default=str(_default_db_path()), help="SQLite database path")
    parser.add_argument("--no-backup", action="store_true", help="skip pre-migration database copy")
    args = parser.parse_args()

    try:
        result = migrate_database(args.db_path, create_backup=not args.no_backup)
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
