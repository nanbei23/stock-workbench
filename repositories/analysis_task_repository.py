"""Analysis task persistence helpers."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import DB_PATH


def _loads(value, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _dumps(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def row_to_snapshot(row) -> dict:
    data = dict(row)
    payload = _loads(data.get("payload"), {})
    snapshot = {
        "task_id": data.get("task_id"),
        "code": data.get("code"),
        "name": data.get("name") or "",
        "status": data.get("status") or "pending",
        "queue_status": data.get("queue_status"),
        "depth": data.get("depth") or "standard",
        "selected_analysts": _loads(data.get("selected_analysts"), None),
        "debate_rounds": data.get("debate_rounds"),
        "risk_rounds": data.get("risk_rounds"),
        "stages": _loads(data.get("stages"), {}),
        "result": _loads(data.get("result"), None),
        "error": data.get("error"),
        "started_at": data.get("started_at"),
        "completed_at": data.get("completed_at"),
        "elapsed": data.get("elapsed"),
        "token_stats": payload.get("token_stats") if isinstance(payload, dict) else None,
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }
    if isinstance(payload, dict):
        for key in ("depth", "selected_analysts", "debate_rounds", "risk_rounds"):
            if snapshot.get(key) is None:
                snapshot[key] = payload.get(key)
    return snapshot


def persist_task_snapshot(task, queue_status: Optional[str] = None, db_path=DB_PATH):
    """Upsert an observable task snapshot into SQLite."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as db:
        db.execute(
            """
            INSERT INTO analysis_tasks (
                task_id, code, name, status, queue_status, depth,
                selected_analysts, debate_rounds, risk_rounds, stages,
                result, error, started_at, completed_at, elapsed, payload,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(task_id) DO UPDATE SET
                code=excluded.code,
                name=excluded.name,
                status=excluded.status,
                queue_status=excluded.queue_status,
                depth=excluded.depth,
                selected_analysts=excluded.selected_analysts,
                debate_rounds=excluded.debate_rounds,
                risk_rounds=excluded.risk_rounds,
                stages=excluded.stages,
                result=excluded.result,
                error=excluded.error,
                started_at=excluded.started_at,
                completed_at=excluded.completed_at,
                elapsed=excluded.elapsed,
                payload=excluded.payload,
                updated_at=datetime('now')
            """,
            (
                task.task_id,
                task.code,
                task.name,
                task.status,
                queue_status,
                task.depth,
                _dumps(task.selected_analysts),
                task.debate_rounds,
                task.risk_rounds,
                _dumps(task.stages),
                _dumps(task.result),
                task.error,
                task.started_at,
                task.completed_at,
                task.elapsed,
                _dumps(task.dict()),
            ),
        )
        db.commit()


def update_task_status(
    task_id: str,
    status: str,
    error: Optional[str] = None,
    db_path=DB_PATH,
    now: Optional[str] = None,
    queue_status: Optional[str] = None,
):
    """Update status for a persisted task that may not be live in memory."""
    completed_at = now or datetime.now().isoformat()
    path = Path(db_path)
    with sqlite3.connect(str(path)) as db:
        db.execute(
            """
            UPDATE analysis_tasks
            SET status = ?,
                error = COALESCE(?, error),
                queue_status = COALESCE(?, queue_status),
                completed_at = CASE
                    WHEN ? IN ('completed', 'failed', 'timeout', 'cancelled') THEN COALESCE(completed_at, ?)
                    ELSE completed_at
                END,
                updated_at = datetime('now')
            WHERE task_id = ?
            """,
            (status, error, queue_status, status, completed_at, task_id),
        )
        db.commit()


def mark_interrupted(db_path=DB_PATH, now: Optional[str] = None):
    """Mark unfinished persisted tasks as interrupted after process restart."""
    completed_at = now or datetime.now().isoformat()
    path = Path(db_path)
    with sqlite3.connect(str(path)) as db:
        db.execute(
            """
            UPDATE analysis_tasks
            SET status = 'failed',
                queue_status = 'interrupted',
                error = COALESCE(error, '服务重启，任务已中断'),
                completed_at = COALESCE(completed_at, ?),
                updated_at = datetime('now')
            WHERE status IN ('pending', 'running')
               OR queue_status IN ('queued', 'running', 'cancelling')
            """,
            (completed_at,),
        )
        db.commit()


async def get_task(db, task_id: str):
    row = await (
        await db.execute("SELECT * FROM analysis_tasks WHERE task_id = ?", (task_id,))
    ).fetchone()
    return row_to_snapshot(row) if row else None


async def get_latest_active_task(db):
    row = await (
        await db.execute(
            """
            SELECT *
            FROM analysis_tasks
            WHERE status IN ('pending', 'running')
               OR queue_status IN ('queued', 'running', 'cancelling')
            ORDER BY updated_at DESC
            LIMIT 1
            """
        )
    ).fetchone()
    return row_to_snapshot(row) if row else None


async def list_tasks(db, limit: int = 50, status: Optional[str] = None):
    limit = max(1, min(limit, 200))
    params: list[object] = []
    query = "SELECT * FROM analysis_tasks"
    if status:
        query += " WHERE status = ? OR queue_status = ?"
        params.extend([status, status])
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    rows = await db.execute_fetchall(query, params)
    return [row_to_snapshot(row) for row in rows]
