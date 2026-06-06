"""Transient stock selection sets shared between product surfaces."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException

from config import DB_PATH


DEFAULT_TTL_HOURS = 24


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _normalize_code(value: Any) -> str:
    raw = str(value or "").strip().lower()
    raw = re.sub(r"^(sh|sz|bj)", "", raw)
    digits = re.sub(r"\D", "", raw)
    return digits[-6:] if len(digits) >= 6 else ""


def _normalize_codes(values: list[Any] | tuple[Any, ...] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        code = _normalize_code(value)
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(code)
    return result


def _row_to_selection(row: sqlite3.Row) -> dict[str, Any]:
    codes = _loads(row["codes_json"], [])
    filters = _loads(row["filters_json"], {})
    return {
        "selection_id": row["selection_id"],
        "source_page": row["source_page"],
        "source_label": row["source_label"] or "",
        "login_user_id": row["login_user_id"] if "login_user_id" in row.keys() else "admin",
        "codes": codes if isinstance(codes, list) else [],
        "count": len(codes) if isinstance(codes, list) else 0,
        "filters": filters if isinstance(filters, dict) else {},
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
    }


def create_selection_set(payload: dict[str, Any], login_user_id: str = "admin") -> dict[str, Any]:
    codes = _normalize_codes(payload.get("codes") or [])
    if not codes:
        raise HTTPException(status_code=400, detail="请选择至少一只股票")
    filters = payload.get("filters") if isinstance(payload.get("filters"), dict) else {}
    source_page = str(payload.get("source_page") or "unknown").strip()[:40] or "unknown"
    source_label = str(payload.get("source_label") or "").strip()[:120]
    try:
        ttl_hours = max(1, min(int(payload.get("ttl_hours") or DEFAULT_TTL_HOURS), 168))
    except (TypeError, ValueError):
        ttl_hours = DEFAULT_TTL_HOURS
    selection_id = f"sel-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    expires_at = (datetime.now() + timedelta(hours=ttl_hours)).isoformat(timespec="seconds")
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO report_selection_sets
                (selection_id, source_page, source_label, codes_json, filters_json, expires_at, login_user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                selection_id,
                source_page,
                source_label,
                json.dumps(codes, ensure_ascii=False),
                json.dumps(filters, ensure_ascii=False),
                expires_at,
                login_user_id or "admin",
            ),
        )
        conn.commit()
    return get_selection_set(selection_id, login_user_id=login_user_id)


def get_selection_set(selection_id: str, login_user_id: str = "admin") -> dict[str, Any]:
    clean_id = str(selection_id or "").strip()
    if not clean_id:
        raise HTTPException(status_code=404, detail="选择集不存在")
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM report_selection_sets
            WHERE selection_id = ?
              AND COALESCE(login_user_id, 'admin') = ?
              AND (expires_at IS NULL OR expires_at = '' OR datetime(expires_at) >= datetime('now'))
            """,
            (clean_id, login_user_id or "admin"),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="选择集不存在或已过期")
    return _row_to_selection(row)


def delete_selection_set(selection_id: str, login_user_id: str = "admin") -> dict[str, Any]:
    clean_id = str(selection_id or "").strip()
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM report_selection_sets WHERE selection_id = ? AND COALESCE(login_user_id, 'admin') = ?",
            (clean_id, login_user_id or "admin"),
        )
        conn.commit()
    return {"selection_id": clean_id, "deleted": bool(cursor.rowcount)}
