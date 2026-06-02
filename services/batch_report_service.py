"""v2.8 background batch research jobs.

The service intentionally separates three long-running workflows:
- data_prefetch: write seven-layer snapshots to stock_data_snapshots
- report_generation: generate analysis_reports from existing snapshots
- position_plan: generate a position plan from existing reports
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from config import DB_PATH
from scripts import batch_research
from services import position_plan_service


JOB_TYPES = {"data_prefetch", "report_generation", "position_plan"}
TERMINAL_ITEM_STATUS = {"completed", "failed", "skipped", "waiting_snapshot", "cancelled", "quota_paused"}
WAITING_STATUSES = {"waiting_snapshot"}
FAILED_STATUSES = {"failed", "timeout", "cancelled"}
PAUSE_STATUSES = {"pausing", "paused"}
RESUMABLE_ITEM_STATUS = {"pending", "failed", "running", "quota_paused"}
RATE_LIMIT_MARKERS = ("rate limit", "429", "too many requests", "max retries", "proxyerror", "timeout", "限流", "频繁")
NETWORK_MARKERS = ("network", "connection", "connect", "proxyerror", "max retries", "timeout", "read timed out", "网络")
QUOTA_MARKERS = (
    "insufficient_quota",
    "quota_exceeded",
    "quota exceeded",
    "balance exhausted",
    "billing hard limit",
    "daily limit",
    "monthly limit",
    "额度",
    "余额不足",
    "账户余额",
)
CONTEXT_LIMIT_MARKERS = ("context length", "maximum context", "tokens exceed", "上下文")
GUARD_PAUSED_STATUS = "guard_paused"


class ModelQuotaError(RuntimeError):
    def __init__(self, message: str, *, role_key: str = "", model: str = "", resume_after: str = ""):
        super().__init__(message)
        self.role_key = role_key
        self.model = model
        self.resume_after = resume_after


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now_expr() -> str:
    return "datetime('now')"


def _loads(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            continue
    return None


def _load_watchlist_codes(group: str = "all", codes: list[str] | None = None) -> list[dict[str, Any]]:
    if codes:
        placeholders = ",".join("?" for _ in codes)
        with _connect() as conn:
            rows = conn.execute(
                f"""
                SELECT code, name, COALESCE(group_name, '默认') AS group_name, COALESCE(sort_order, 0) AS sort_order
                FROM watchlist
                WHERE code IN ({placeholders})
                """,
                codes,
            ).fetchall()
        by_code = {row["code"]: dict(row) for row in rows}
        return [
            {
                "code": code,
                "name": (by_code.get(code) or {}).get("name") or code,
                "group_name": (by_code.get(code) or {}).get("group_name") or "默认",
                "sort_order": int((by_code.get(code) or {}).get("sort_order") or 0),
            }
            for code in codes
        ]

    params: list[Any] = []
    where = ""
    if group not in {"all", "全部", "*"}:
        where = "WHERE COALESCE(group_name, '默认') = ?"
        params.append(group)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT code, name, COALESCE(group_name, '默认') AS group_name, COALESCE(sort_order, 0) AS sort_order
            FROM watchlist
            {where}
            ORDER BY CASE WHEN COALESCE(group_name, '默认') = '观察池' THEN 1 ELSE 0 END,
                     sort_order ASC,
                     added_at ASC
            """,
            params,
        ).fetchall()
    return [
        {"code": row["code"], "name": row["name"] or row["code"], "group_name": row["group_name"], "sort_order": int(row["sort_order"] or 0)}
        for row in rows
    ]


def _load_report_codes(report_ids: list[int]) -> list[dict[str, Any]]:
    clean_ids = [int(report_id) for report_id in report_ids if int(report_id) > 0]
    if not clean_ids:
        return []
    placeholders = ",".join("?" for _ in clean_ids)
    order_expr = "CASE " + " ".join(f"WHEN ar.id = ? THEN {idx}" for idx, _ in enumerate(clean_ids)) + " END"
    params: list[Any] = clean_ids + clean_ids
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT ar.id AS report_id,
                   ar.code,
                   COALESCE(w.name, ar.code) AS name,
                   COALESCE(w.group_name, '默认') AS group_name,
                   COALESCE(w.sort_order, 0) AS sort_order
            FROM analysis_reports ar
            LEFT JOIN watchlist w ON w.code = ar.code
            WHERE ar.id IN ({placeholders})
            ORDER BY {order_expr}
            """,
            params,
        ).fetchall()
    return [
        {
            "code": row["code"],
            "name": row["name"] or row["code"],
            "group_name": row["group_name"] or "默认",
            "sort_order": int(row["sort_order"] or 0),
            "report_id": int(row["report_id"]),
        }
        for row in rows
    ]


def _stock_candidate(item: dict[str, Any]) -> batch_research.StockCandidate:
    return batch_research.StockCandidate(
        item["code"],
        item.get("name") or item["code"],
        item.get("group_name") or "默认",
        int(item.get("sort_order") or 0),
    )


def _ranked_candidate(item: dict[str, Any]) -> batch_research.RankedCandidate:
    return batch_research.RankedCandidate(
        code=item["code"],
        name=item.get("name") or item["code"],
        group_name=item.get("group_name") or "默认",
        sort_order=int(item.get("sort_order") or 0),
        score=float(item.get("score") or 0),
        quote=item.get("quote") or {},
    )


def _job_name(job_type: str) -> str:
    return {
        "data_prefetch": "七层数据预取",
        "report_generation": "AI报告生成",
        "position_plan": "建仓建议生成",
    }.get(job_type, "批量研究任务")


def _tradingagents_role_call_count(*, debate_rounds: int = 1, risk_rounds: int = 1) -> int:
    return 8 + max(1, int(debate_rounds or 1)) * 2 + 2 + max(1, int(risk_rounds or 1)) * 3 + 1


def preflight_batch_models(
    *,
    job_type: str = "report_generation",
    codes: list[str] | None = None,
    report_ids: list[int] | None = None,
    group: str = "all",
    top_n: int = 0,
    debate_rounds: int = 1,
    risk_rounds: int = 1,
    model_fallback_enabled: bool = True,
    fallback_provider_ids: list[str] | None = None,
    **_extra,
) -> dict[str, Any]:
    clean_report_ids = [int(report_id) for report_id in (report_ids or []) if int(report_id) > 0]
    stocks = _load_report_codes(clean_report_ids) if job_type == "position_plan" and clean_report_ids else _load_watchlist_codes(group, codes or None)
    if top_n > 0:
        stocks = stocks[:top_n]
    primary = batch_research._snapshot_llm_config(DB_PATH, model_tier="deep")
    role_calls = _tradingagents_role_call_count(debate_rounds=debate_rounds, risk_rounds=risk_rounds) if job_type == "report_generation" else 5
    fallback_payload = {"model_fallback_enabled": model_fallback_enabled, "fallback_provider_ids": fallback_provider_ids or []}
    fallbacks = _fallback_model_configs(fallback_payload)
    missing = [key for key in ("base_url", "api_key", "model") if not primary.get(key)]
    status = "ok" if not missing else "warning"
    return {
        "status": status,
        "job_type": job_type,
        "stock_count": len(stocks),
        "role_calls_per_stock": role_calls,
        "estimated_role_calls": len(stocks) * role_calls,
        "primary_ready": not missing,
        "primary_missing": missing,
        "primary_model": primary.get("model", ""),
        "fallback_enabled": bool(model_fallback_enabled),
        "fallback_count": len(fallbacks),
        "fallback_models": [{"profile": item.get("_profile", ""), "model": item.get("model", "")} for item in fallbacks],
        "warnings": [
            *([f"主模型缺少 {', '.join(missing)}"] if missing else []),
            *(["没有可用备用模型，额度耗尽时会进入 quota_paused"] if model_fallback_enabled and not fallbacks else []),
        ],
    }


def _update_row(table: str, key_column: str, key_value: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = _now_expr()
    assignments = []
    params: list[Any] = []
    for key, value in fields.items():
        if value == _now_expr():
            assignments.append(f"{key} = datetime('now')")
        else:
            assignments.append(f"{key} = ?")
            params.append(value)
    params.append(key_value)
    with _connect() as conn:
        conn.execute(f"UPDATE {table} SET {', '.join(assignments)} WHERE {key_column} = ?", params)
        conn.commit()


def _update_job(job_id: str, **fields) -> None:
    _update_row("batch_jobs", "job_id", job_id, **fields)


def _update_item_id(item_id: int, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = _now_expr()
    assignments = []
    params: list[Any] = []
    for key, value in fields.items():
        if value == _now_expr():
            assignments.append(f"{key} = datetime('now')")
        else:
            assignments.append(f"{key} = ?")
            params.append(value)
    params.append(item_id)
    with _connect() as conn:
        conn.execute(f"UPDATE batch_job_items SET {', '.join(assignments)} WHERE id = ?", params)
        conn.commit()


def log_job_event(
    job_id: str,
    level: str,
    event: str,
    message: str = "",
    data: dict[str, Any] | None = None,
    *,
    item_id: int | None = None,
    step_id: int | None = None,
) -> dict[str, Any]:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO batch_job_logs (job_id, item_id, step_id, level, event, message, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, item_id, step_id, level, event, message, json.dumps(data or {}, ensure_ascii=False, default=str)),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM batch_job_logs WHERE id = last_insert_rowid()").fetchone()
    return dict(row)


def get_job_logs(job_id: str, limit: int = 200) -> dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM batch_job_logs
            WHERE job_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (job_id, max(1, min(int(limit or 200), 1000))),
        ).fetchall()
    return {"count": len(rows), "logs": [dict(row) for row in rows]}


def record_job_artifact(
    job_id: str,
    artifact_type: str,
    title: str,
    *,
    path: str = "",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO batch_job_artifacts (job_id, artifact_type, title, path, data_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (job_id, artifact_type, title, path, json.dumps(data or {}, ensure_ascii=False, default=str)),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM batch_job_artifacts WHERE id = last_insert_rowid()").fetchone()
    return dict(row)


def get_job_artifacts(job_id: str) -> dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM batch_job_artifacts
            WHERE job_id = ?
            ORDER BY id DESC
            """,
            (job_id,),
        ).fetchall()
    return {"count": len(rows), "artifacts": [dict(row) for row in rows]}


def touch_job(job_id: str, *, worker_id: str | None = None, current_code: str | None = None) -> None:
    fields: dict[str, Any] = {"heartbeat_at": _now_expr()}
    if worker_id:
        fields["worker_id"] = worker_id
        fields["lease_owner"] = worker_id
        fields["lease_until"] = (datetime.now() + timedelta(minutes=15)).isoformat(timespec="seconds")
    if current_code is not None:
        fields["current_code"] = current_code
    _update_job(job_id, **fields)


def pause_job(job_id: str) -> dict[str, Any]:
    job = get_research_job(job_id)
    if job["status"] in {"completed", "failed", "cancelled", "quota_paused"}:
        return {"job_id": job_id, "status": job["status"], "pause_requested": 0}
    status = "paused" if job["status"] in {"pending", "interrupted"} else "pausing"
    _update_job(job_id, status=status, pause_requested=1, paused_at=_now_expr(), current_code="")
    log_job_event(job_id, "info", "job_pause_requested", "用户请求暂停批量任务", {"from_status": job["status"], "to_status": status})
    return get_research_job(job_id)


def _is_job_pause_requested(job_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT pause_requested, status FROM batch_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return bool(row and (int(row["pause_requested"] or 0) or row["status"] in PAUSE_STATUSES))


def _mark_job_paused(job_id: str) -> None:
    job = get_research_job(job_id)
    if job["status"] == GUARD_PAUSED_STATUS:
        return
    _update_job(job_id, status="paused", pause_requested=1, paused_at=_now_expr(), current_code="")
    log_job_event(job_id, "info", "job_paused", "批量任务已暂停，未开始的股票保持 pending")


def upsert_item_step(
    item_id: int,
    job_id: str,
    role_key: str,
    role_name: str,
    content: str = "",
    *,
    step_order: int = 0,
    status: str = "completed",
    error: str = "",
) -> dict[str, Any]:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO batch_job_item_steps
                (item_id, job_id, role_key, role_name, step_order, status, content, error,
                 heartbeat_at, started_at, completed_at)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'),
                 CASE WHEN ? IN ('completed', 'failed', 'skipped', 'cancelled') THEN datetime('now') ELSE NULL END)
            ON CONFLICT(item_id, role_key) DO UPDATE SET
                role_name=excluded.role_name,
                step_order=excluded.step_order,
                status=excluded.status,
                content=excluded.content,
                error=excluded.error,
                heartbeat_at=datetime('now'),
                started_at=COALESCE(batch_job_item_steps.started_at, excluded.started_at),
                completed_at=excluded.completed_at,
                updated_at=datetime('now')
            """,
            (item_id, job_id, role_key, role_name, step_order, status, content or "", error or "", status),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM batch_job_item_steps WHERE item_id = ? AND role_key = ?",
            (item_id, role_key),
        ).fetchone()
    return dict(row)


def get_research_item_steps(item_id: int) -> dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM batch_job_item_steps
            WHERE item_id = ?
            ORDER BY step_order ASC, id ASC
            """,
            (item_id,),
        ).fetchall()
    return {"count": len(rows), "steps": [dict(row) for row in rows]}


def _step_progress(conn: sqlite3.Connection, item_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not item_ids:
        return {}
    placeholders = ",".join("?" for _ in item_ids)
    rows = conn.execute(
        f"""
        SELECT item_id, role_key, status, error, step_order
        FROM batch_job_item_steps
        WHERE item_id IN ({placeholders})
        ORDER BY item_id, step_order ASC, id ASC
        """,
        item_ids,
    ).fetchall()
    grouped: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(int(row["item_id"]), []).append(row)
    progress = {}
    for item_id, item_rows in grouped.items():
        running = next((row for row in item_rows if row["status"] == "running"), None)
        failed = next((row for row in item_rows if row["status"] in {"failed", "quota_paused"}), None)
        completed = [row for row in item_rows if row["status"] == "completed"]
        current = running or failed or (completed[-1] if completed else None)
        progress[item_id] = {
            "step_total": len(item_rows),
            "step_completed": len(completed),
            "current_step": current["role_key"] if current else "",
            "step_error": failed["error"] if failed else "",
        }
    return progress


def _completed_steps(item_id: int) -> dict[str, dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM batch_job_item_steps
            WHERE item_id = ? AND status = 'completed'
            ORDER BY step_order ASC, id ASC
            """,
            (item_id,),
        ).fetchall()
    return {row["role_key"]: dict(row) for row in rows}


def _snapshot_by_id(snapshot_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT id, code, name, snapshot_json, validation_json, summary_json, created_at
            FROM stock_data_snapshots
            WHERE id = ?
            """,
            (snapshot_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "code": row["code"],
        "name": row["name"],
        "snapshot": _loads(row["snapshot_json"], {}),
        "validation": _loads(row["validation_json"], {}),
        "summary": _loads(row["summary_json"], {}),
        "created_at": row["created_at"],
    }


def _lock_job_snapshots(job_id: str, items: list[dict[str, Any]]) -> None:
    locked: dict[str, Any] = {"locked_at": _iso_now(), "snapshots": {}}
    with _connect() as conn:
        for item in items:
            if item.get("locked_snapshot_id"):
                snapshot = _snapshot_by_id(int(item["locked_snapshot_id"]))
            else:
                snapshot = batch_research._latest_snapshot(DB_PATH, item["code"])
            if not snapshot:
                continue
            locked["snapshots"][item["code"]] = {
                "snapshot_id": snapshot["id"],
                "created_at": snapshot.get("created_at"),
                "validation_ok": bool((snapshot.get("validation") or {}).get("ok")),
            }
            conn.execute(
                """
                UPDATE batch_job_items
                SET locked_snapshot_id = COALESCE(locked_snapshot_id, ?),
                    snapshot_id = COALESCE(snapshot_id, ?),
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (snapshot["id"], snapshot["id"], item["id"]),
            )
        conn.execute(
            """
            UPDATE batch_jobs
            SET input_snapshot_json = ?, updated_at = datetime('now')
            WHERE job_id = ?
            """,
            (json.dumps(locked, ensure_ascii=False, default=str), job_id),
        )
        conn.commit()
    log_job_event(job_id, "info", "input_snapshots_locked", "已锁定本批次输入快照", {"count": len(locked["snapshots"])})


def _runtime_state(job_id: str) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute("SELECT runtime_json FROM batch_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return _loads(row["runtime_json"] if row else "{}", {})


def _save_runtime_state(job_id: str, runtime: dict[str, Any]) -> None:
    _update_job(job_id, runtime_json=json.dumps(runtime, ensure_ascii=False, default=str))


def _is_rate_limit_error(error: str) -> bool:
    lower = (error or "").lower()
    return any(marker in lower for marker in RATE_LIMIT_MARKERS)


def _llm_error_type(error: str) -> str:
    lower = (error or "").lower()
    if any(marker in lower for marker in QUOTA_MARKERS):
        return "quota_exhausted"
    if any(marker in lower for marker in CONTEXT_LIMIT_MARKERS):
        return "context_limit"
    if any(marker in lower for marker in RATE_LIMIT_MARKERS):
        return "rate_limit"
    if any(marker in lower for marker in NETWORK_MARKERS):
        return "network"
    return "unknown"


def _retry_after_seconds(error_type: str, consecutive_failures: int) -> int:
    failures = max(1, int(consecutive_failures or 1))
    if error_type == "quota_exhausted":
        return 3600
    if error_type == "context_limit":
        return 0
    if error_type == "rate_limit":
        return min(1800, 30 * failures)
    if error_type == "network":
        return min(600, 15 * failures)
    return min(300, 10 * failures)


def _load_model_providers() -> list[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'model_providers'").fetchone()
    providers = _loads(row["value"] if row else "[]", [])
    return [provider for provider in providers if isinstance(provider, dict)]


def _provider_to_config(provider: dict[str, Any], *, model: str | None = None) -> dict[str, str]:
    selected_model = model or provider.get("default_model") or provider.get("deep_model") or provider.get("quick_model") or ""
    return {
        "base_url": provider.get("base_url") or "",
        "api_key": provider.get("api_key") or "",
        "model": selected_model,
        "_profile": provider.get("name") or provider.get("id") or selected_model,
        "_provider_id": provider.get("id") or "",
    }


def _fallback_model_configs(payload: dict[str, Any]) -> list[dict[str, str]]:
    if payload.get("model_fallback_enabled") is False:
        return []
    providers = _load_model_providers()
    provider_ids = [str(item) for item in payload.get("fallback_provider_ids") or [] if str(item)]
    if provider_ids:
        order = {provider_id: index for index, provider_id in enumerate(provider_ids)}
        providers = [provider for provider in providers if str(provider.get("id")) in order]
        providers.sort(key=lambda provider: order[str(provider.get("id"))])
    configs: list[dict[str, str]] = []
    seen = set()
    for provider in providers:
        config = _provider_to_config(provider)
        key = (config.get("base_url"), config.get("api_key"), config.get("model"))
        if not all(key) or key in seen:
            continue
        seen.add(key)
        configs.append(config)
    return configs


def _same_model_config(left: dict[str, str], right: dict[str, str]) -> bool:
    return all((left.get(key) or "") == (right.get(key) or "") for key in ("base_url", "api_key", "model"))


def _record_quota_event(
    job_id: str,
    *,
    role_key: str,
    model: str,
    error: str,
    status: str,
    fallback_model: str = "",
    resume_after: str = "",
) -> None:
    runtime = _runtime_state(job_id)
    quota = runtime.setdefault("quota", {})
    events = quota.setdefault("events", [])
    event = {
        "at": _iso_now(),
        "role_key": role_key,
        "model": model,
        "error_type": _llm_error_type(error),
        "error": error,
        "status": status,
        "fallback_model": fallback_model,
        "resume_after": resume_after,
    }
    events.append(event)
    if status == "paused":
        quota["state"] = "exhausted"
        quota["current_role"] = role_key
        quota["model"] = model
        quota["resume_after"] = resume_after
    elif status == "fallback":
        quota["state"] = "fallback_active"
        quota["active_model"] = {"model": fallback_model}
    _save_runtime_state(job_id, runtime)
    log_job_event(job_id, "warning", f"quota_{status}", "模型额度事件", event)


def _mark_quota_paused(
    job_id: str,
    item_id: int,
    role_key: str,
    role_name: str,
    error: str,
    *,
    model: str,
    step_order: int,
    resume_after: str = "",
) -> None:
    upsert_item_step(
        item_id,
        job_id,
        role_key,
        role_name,
        "",
        step_order=step_order,
        status="quota_paused",
        error=error,
    )
    _update_item_id(item_id, status="quota_paused", error=error, completed_at=None)
    _update_job(
        job_id,
        status="quota_paused",
        error=error,
        pause_requested=1,
        paused_at=_now_expr(),
        current_code="",
    )
    _record_quota_event(job_id, role_key=role_key, model=model, error=error, status="paused", resume_after=resume_after)


def _record_runtime_failure(job_id: str, source: str, error: str, *, base_concurrency: int = 1) -> None:
    runtime = _runtime_state(job_id)
    sources = runtime.setdefault("sources", {})
    state = sources.setdefault(source, {"consecutive_failures": 0, "current_concurrency": max(1, base_concurrency)})
    state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
    error_type = _llm_error_type(error)
    retry_after = _retry_after_seconds(error_type, int(state["consecutive_failures"]))
    state["last_failure"] = {
        "at": _iso_now(),
        "error": error,
        "error_type": error_type,
        "retry_after_seconds": retry_after,
    }
    if error_type in {"rate_limit", "network"}:
        cooldown_seconds = retry_after
        state["cooldown_until"] = (datetime.now() + timedelta(seconds=cooldown_seconds)).isoformat(timespec="seconds")
        state["current_concurrency"] = max(1, int(state.get("current_concurrency") or base_concurrency) // 2)
        state["effective_concurrency"] = state["current_concurrency"]
        log_job_event(
            job_id,
            "warning",
            "runtime_cooldown",
            f"{source} 触发冷却",
            {
                "error": error,
                "error_type": error_type,
                "cooldown_seconds": cooldown_seconds,
                "current_concurrency": state["current_concurrency"],
            },
        )
    runtime[source] = state
    _save_runtime_state(job_id, runtime)


def _record_runtime_success(job_id: str, source: str, *, base_concurrency: int = 1) -> None:
    runtime = _runtime_state(job_id)
    sources = runtime.setdefault("sources", {})
    state = sources.setdefault(source, {"consecutive_failures": 0, "current_concurrency": max(1, base_concurrency)})
    state["consecutive_failures"] = 0
    state.pop("cooldown_until", None)
    state["current_concurrency"] = min(max(1, base_concurrency), max(1, int(state.get("current_concurrency") or 1) + 1))
    state["effective_concurrency"] = state["current_concurrency"]
    runtime[source] = state
    _save_runtime_state(job_id, runtime)


def _mark_guard_paused(job_id: str, *, reason: str, error: str = "", data: dict[str, Any] | None = None) -> None:
    runtime = _runtime_state(job_id)
    runtime["guard"] = {
        "state": "paused",
        "reason": reason,
        "error": error,
        "at": _iso_now(),
        **(data or {}),
    }
    _save_runtime_state(job_id, runtime)
    _update_job(
        job_id,
        status=GUARD_PAUSED_STATUS,
        pause_requested=1,
        paused_at=_now_expr(),
        error=reason,
        current_code="",
    )
    log_job_event(job_id, "warning", "guard_paused", reason, {"error": error, **(data or {})})


def _maybe_guard_pause(job_id: str, payload: dict[str, Any], *, error: str = "") -> bool:
    max_consecutive = int(payload.get("max_consecutive_failures") or 0)
    max_failure_rate = float(payload.get("max_failure_rate") or 0)
    min_failure_rate_items = max(1, int(payload.get("min_failure_rate_items") or 5))
    counts = _recount_job(job_id)
    runtime = _runtime_state(job_id)
    guard = runtime.setdefault("guard", {})
    if error:
        guard["consecutive_failures"] = int(guard.get("consecutive_failures") or 0) + 1
        guard["last_error"] = error
    else:
        guard["consecutive_failures"] = 0
    _save_runtime_state(job_id, runtime)
    if max_consecutive and int(guard.get("consecutive_failures") or 0) >= max_consecutive:
        _mark_guard_paused(
            job_id,
            reason="连续失败数达到上限",
            error=error,
            data={"max_consecutive_failures": max_consecutive, "counts": counts},
        )
        return True
    total = max(1, int(counts.get("completed_count", 0)) + int(counts.get("failed_count", 0)) + int(counts.get("skipped_count", 0)))
    failure_rate = int(counts.get("failed_count", 0)) / total
    if max_failure_rate and total >= min_failure_rate_items and failure_rate >= max_failure_rate and int(counts.get("failed_count", 0)) > 0:
        _mark_guard_paused(
            job_id,
            reason="失败率达到上限",
            error=error,
            data={"max_failure_rate": max_failure_rate, "failure_rate": round(failure_rate, 4), "min_failure_rate_items": min_failure_rate_items, "counts": counts},
        )
        return True
    return False


async def _respect_cooldown(job_id: str, source: str) -> None:
    runtime = _runtime_state(job_id)
    state = (runtime.get("sources") or {}).get(source) or {}
    cooldown_until = _parse_dt(state.get("cooldown_until"))
    if not cooldown_until:
        return
    delay = (cooldown_until - datetime.now()).total_seconds()
    if delay > 0:
        await asyncio.sleep(min(delay, 30.0))


def _effective_concurrency(job_id: str, source: str, requested: int) -> int:
    runtime = _runtime_state(job_id)
    state = (runtime.get("sources") or {}).get(source) or {}
    return max(1, min(int(requested or 1), int(state.get("current_concurrency") or requested or 1)))


async def _call_role_with_retries(
    role: dict[str, str],
    prompt: str,
    config: dict[str, str],
    *,
    timeout_seconds: int,
    max_attempts: int = 3,
    backoff_seconds: float = 2.0,
    job_id: str = "",
    payload: dict[str, Any] | None = None,
) -> str:
    attempts = max(1, int(max_attempts or 1))
    delay = max(0.0, float(backoff_seconds or 0))
    last_error: Exception | None = None
    configs = [config]
    for fallback in _fallback_model_configs(payload or {}):
        if not _same_model_config(config, fallback):
            configs.append(fallback)
    config_index = 0
    max_iterations = attempts + max(0, len(configs) - 1)
    for attempt in range(1, max_iterations + 1):
        active_config = configs[min(config_index, len(configs) - 1)]
        try:
            content = await batch_research._call_snapshot_tradingagents_role_llm(
                role,
                prompt,
                active_config,
                timeout_seconds=timeout_seconds,
            )
            if job_id:
                _record_runtime_success(job_id, "llm")
            return content
        except Exception as exc:
            last_error = exc
            error = str(exc)
            error_type = _llm_error_type(error)
            if error_type == "quota_exhausted":
                if config_index + 1 < len(configs):
                    previous = active_config
                    config_index += 1
                    fallback = configs[config_index]
                    if job_id:
                        _record_quota_event(
                            job_id,
                            role_key=role.get("role_key", ""),
                            model=previous.get("model", ""),
                            error=error,
                            status="fallback",
                            fallback_model=fallback.get("model", ""),
                        )
                    continue
                raise ModelQuotaError(error, role_key=role.get("role_key", ""), model=active_config.get("model", ""))
            if job_id:
                _record_runtime_failure(job_id, "llm", error)
            if attempt >= max_iterations:
                break
            await asyncio.sleep(min(delay * (2 ** (attempt - 1)), 30.0))
    raise last_error or RuntimeError("角色模型调用失败")


async def _run_snapshot_tradingagents_graph_with_steps(
    *,
    item: dict[str, Any],
    ranked: batch_research.RankedCandidate,
    snapshot: dict[str, Any],
    config: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    item_id = int(item["id"])
    job_id = payload.get("job_id") or item["job_id"]
    state = batch_research._initial_snapshot_agent_state(ranked, snapshot)
    role_discussion: list[dict[str, str]] = []
    completed = _completed_steps(item_id)
    max_attempts = int(payload.get("role_retry_attempts") or 3)
    backoff_seconds = float(payload.get("role_retry_backoff_seconds") or 2)
    step_order = 0

    async def call_role(
        role_key: str,
        role_name: str,
        output_key: str,
        role_goal: str,
        *,
        step_key: str | None = None,
        step_name: str | None = None,
    ) -> str:
        nonlocal step_order
        step_order += 1
        persisted_key = step_key or role_key
        persisted_name = step_name or role_name
        cached = completed.get(persisted_key)
        if cached and cached.get("content"):
            content = cached["content"]
            role_discussion.append(
                {
                    "role_key": role_key,
                    "step_key": persisted_key,
                    "role_name": role_name,
                    "output_key": output_key,
                    "content": content,
                }
            )
            return content
        role = {"role_key": role_key, "role_name": role_name, "role_goal": role_goal, "output_key": output_key}
        upsert_item_step(
            item_id,
            job_id,
            persisted_key,
            persisted_name,
            "",
            step_order=step_order,
            status="running",
            error="",
        )
        touch_job(job_id)
        log_job_event(job_id, "info", "role_started", f"{persisted_name} 开始", {"role_key": persisted_key}, item_id=item_id)
        prompt = batch_research._snapshot_tradingagents_state_prompt(
            ranked,
            snapshot,
            role_key=role_key,
            role_name=role_name,
            role_goal=role_goal,
            output_key=output_key,
            state=state,
        )
        try:
            content = await _call_role_with_retries(
                role,
                prompt,
                config,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
                backoff_seconds=backoff_seconds,
                job_id=job_id,
                payload=payload,
            )
        except ModelQuotaError as exc:
            _mark_quota_paused(
                job_id,
                item_id,
                persisted_key,
                persisted_name,
                str(exc),
                model=exc.model or config.get("model", ""),
                step_order=step_order,
                resume_after=exc.resume_after,
            )
            raise
        except Exception as exc:
            upsert_item_step(
                item_id,
                job_id,
                persisted_key,
                persisted_name,
                "",
                step_order=step_order,
                status="failed",
                error=str(exc),
            )
            log_job_event(job_id, "error", "role_failed", f"{persisted_name} 失败", {"role_key": persisted_key, "error": str(exc)}, item_id=item_id)
            raise
        upsert_item_step(
            item_id,
            job_id,
            persisted_key,
            persisted_name,
            content,
            step_order=step_order,
            status="completed",
            error="",
        )
        log_job_event(job_id, "info", "role_completed", f"{persisted_name} 完成", {"role_key": persisted_key}, item_id=item_id)
        role_discussion.append(
            {
                "role_key": role_key,
                "step_key": persisted_key,
                "role_name": role_name,
                "output_key": output_key,
                "content": content,
            }
        )
        return content

    for role_key, role_name, output_key, role_goal in batch_research.SNAPSHOT_TRADINGAGENTS_ROLES[:7]:
        state[output_key] = await call_role(role_key, role_name, output_key, role_goal)

    quality = batch_research.SNAPSHOT_TRADINGAGENTS_ROLES[7]
    state["data_quality_summary"] = await call_role(*quality)

    bull = batch_research.SNAPSHOT_TRADINGAGENTS_ROLES[8]
    bear = batch_research.SNAPSHOT_TRADINGAGENTS_ROLES[9]
    debate_rounds = max(1, int(payload.get("debate_rounds") or 1))
    for round_index in range(1, debate_rounds + 1):
        bull_step_key = bull[0] if debate_rounds == 1 else f"{bull[0]}_r{round_index}"
        bear_step_key = bear[0] if debate_rounds == 1 else f"{bear[0]}_r{round_index}"
        bull_content = await call_role(
            *bull,
            step_key=bull_step_key,
            step_name=bull[1] if debate_rounds == 1 else f"{bull[1]} 第{round_index}轮",
        )
        batch_research._append_investment_debate(state, speaker="Bull Analyst", content=bull_content)
        bear_content = await call_role(
            *bear,
            step_key=bear_step_key,
            step_name=bear[1] if debate_rounds == 1 else f"{bear[1]} 第{round_index}轮",
        )
        batch_research._append_investment_debate(state, speaker="Bear Analyst", content=bear_content)

    research_manager = batch_research.SNAPSHOT_TRADINGAGENTS_ROLES[10]
    state["investment_plan"] = await call_role(*research_manager)
    state["investment_debate_state"]["judge_decision"] = state["investment_plan"]
    state["investment_debate_state"]["current_response"] = state["investment_plan"]

    trader = batch_research.SNAPSHOT_TRADINGAGENTS_ROLES[11]
    state["trader_investment_plan"] = await call_role(*trader)

    risk_roles = batch_research.SNAPSHOT_TRADINGAGENTS_ROLES[12:15]
    risk_speakers = {
        "aggressive_risk": "Aggressive",
        "conservative_risk": "Conservative",
        "neutral_risk": "Neutral",
    }
    risk_rounds = max(1, int(payload.get("risk_rounds") or 1))
    for round_index in range(1, risk_rounds + 1):
        for role_key, role_name, output_key, role_goal in risk_roles:
            step_key = role_key if risk_rounds == 1 else f"{role_key}_r{round_index}"
            step_name = role_name if risk_rounds == 1 else f"{role_name} 第{round_index}轮"
            content = await call_role(role_key, role_name, output_key, role_goal, step_key=step_key, step_name=step_name)
            batch_research._append_risk_debate(state, speaker=risk_speakers[role_key], content=content)

    portfolio_manager = batch_research.SNAPSHOT_TRADINGAGENTS_ROLES[15]
    state["final_trade_decision"] = await call_role(*portfolio_manager)
    state["risk_debate_state"]["judge_decision"] = state["final_trade_decision"]
    state["risk_debate_state"]["latest_speaker"] = "Judge"
    return batch_research._snapshot_tradingagents_result(role_discussion, state=state)


def _insert_job(job_id: str, job_type: str, payload: dict[str, Any], stocks: list[dict[str, Any]]) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO batch_jobs
                (job_id, job_type, name, status, total_count, payload_json)
            VALUES
                (?, ?, ?, 'pending', ?, ?)
            """,
            (
                job_id,
                job_type,
                payload.get("name") or _job_name(job_type),
                len(stocks),
                json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )
        conn.executemany(
            """
            INSERT INTO batch_job_items (job_id, code, name, status)
            VALUES (?, ?, ?, 'pending')
            """,
            [(job_id, stock["code"], stock.get("name") or stock["code"]) for stock in stocks],
        )
        conn.commit()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def _recount_job(job_id: str) -> dict[str, int]:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_count,
                SUM(CASE WHEN status IN ('failed', 'timeout', 'cancelled') THEN 1 ELSE 0 END) AS failed_count,
                SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) AS skipped_count,
                SUM(CASE WHEN status = 'waiting_snapshot' THEN 1 ELSE 0 END) AS waiting_count,
                SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running_count
            FROM batch_job_items
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
    counts = {
        "completed_count": int(row["completed_count"] or 0),
        "failed_count": int(row["failed_count"] or 0),
        "skipped_count": int(row["skipped_count"] or 0),
        "waiting_count": int(row["waiting_count"] or 0),
        "running_count": int(row["running_count"] or 0),
    }
    _update_job(job_id, **counts)
    return counts


def get_research_job(job_id: str) -> dict[str, Any]:
    with _connect() as conn:
        job = _row_to_dict(conn.execute("SELECT * FROM batch_jobs WHERE job_id = ?", (job_id,)).fetchone())
        if not job:
            raise HTTPException(404, "批量研究任务不存在")
        items = conn.execute(
            "SELECT * FROM batch_job_items WHERE job_id = ? ORDER BY id ASC",
            (job_id,),
        ).fetchall()
        progress = _step_progress(conn, [int(item["id"]) for item in items])
    job["items"] = [{**dict(item), **progress.get(int(item["id"]), {})} for item in items]
    return job


def list_research_jobs(limit: int = 50, status: str | None = None, job_type: str | None = None) -> dict[str, Any]:
    params: list[Any] = []
    where = ["1=1"]
    if status:
        where.append("status = ?")
        params.append(status)
    if job_type:
        where.append("job_type = ?")
        params.append(job_type)
    params.append(max(1, min(limit, 200)))
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM batch_jobs
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return {"count": len(rows), "jobs": [dict(row) for row in rows]}


def claim_next_job(*, worker_id: str, lease_seconds: int = 300) -> dict[str, Any] | None:
    """Atomically claim one runnable job for an independent worker."""
    worker = worker_id or f"worker-{os.getpid()}"
    lease = max(30, int(lease_seconds or 300))
    token = uuid.uuid4().hex
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT job_id
            FROM batch_jobs
            WHERE COALESCE(pause_requested, 0) = 0
              AND (
                status IN ('pending', 'interrupted')
                OR (
                  status = 'running'
                  AND lease_until IS NOT NULL
                  AND datetime(lease_until) < datetime('now')
                )
              )
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            conn.commit()
            return None
        job_id = row["job_id"]
        cursor = conn.execute(
            """
            UPDATE batch_jobs
            SET status='running',
                worker_id=?,
                heartbeat_at=datetime('now'),
                lease_owner=?,
                lease_token=?,
                lease_until=datetime('now', ?),
                completed_at=NULL,
                updated_at=datetime('now')
            WHERE job_id = ?
              AND COALESCE(pause_requested, 0) = 0
              AND (
                status IN ('pending', 'interrupted')
                OR (
                  status = 'running'
                  AND lease_until IS NOT NULL
                  AND datetime(lease_until) < datetime('now')
                )
              )
            """,
            (worker, worker, token, f"+{lease} seconds", job_id),
        )
        if cursor.rowcount != 1:
            conn.commit()
            return None
        payload_row = conn.execute("SELECT payload_json FROM batch_jobs WHERE job_id = ?", (job_id,)).fetchone()
        payload = _loads(payload_row["payload_json"] if payload_row else "{}", {})
        payload["worker_id"] = worker
        payload["lease_token"] = token
        conn.execute(
            "UPDATE batch_jobs SET payload_json = ?, updated_at=datetime('now') WHERE job_id = ?",
            (json.dumps(payload, ensure_ascii=False, default=str), job_id),
        )
        conn.commit()
    runtime = _runtime_state(job_id)
    runtime["worker"] = {
        "lease_owner": worker,
        "lease_token": token,
        "lease_seconds": lease,
        "claimed_at": _iso_now(),
    }
    _save_runtime_state(job_id, runtime)
    log_job_event(job_id, "info", "job_claimed", "worker 已领取批量任务", {"worker_id": worker, "lease_seconds": lease})
    return get_research_job(job_id)


def refresh_job_lease(job_id: str, *, worker_id: str, lease_token: str = "", lease_seconds: int = 300) -> None:
    lease = max(30, int(lease_seconds or 300))
    with _connect() as conn:
        conn.execute(
            """
            UPDATE batch_jobs
            SET heartbeat_at=datetime('now'),
                lease_until=datetime('now', ?),
                updated_at=datetime('now')
            WHERE job_id = ?
              AND (lease_owner IS NULL OR lease_owner = ?)
              AND (? = '' OR lease_token = ?)
            """,
            (f"+{lease} seconds", job_id, worker_id, lease_token or "", lease_token or ""),
        )
        conn.commit()


def mark_interrupted_jobs() -> int:
    with _connect() as conn:
        running = conn.execute("SELECT job_id FROM batch_jobs WHERE status = 'running'").fetchall()
        job_ids = [row["job_id"] for row in running]
        for job_id in job_ids:
            conn.execute(
                """
                UPDATE batch_job_items
                SET status='failed',
                    error=CASE WHEN error IS NULL OR error='' THEN '服务重启或进程中断' ELSE error END,
                    completed_at=datetime('now'),
                    updated_at=datetime('now')
                WHERE job_id = ? AND status = 'running'
                """,
                (job_id,),
            )
            conn.execute(
                """
                UPDATE batch_job_item_steps
                SET status='failed',
                    error=CASE WHEN error IS NULL OR error='' THEN '服务重启或进程中断' ELSE error END,
                    completed_at=datetime('now'),
                    updated_at=datetime('now')
                WHERE job_id = ? AND status = 'running'
                """,
                (job_id,),
            )
            conn.execute(
                """
                UPDATE batch_jobs
                SET status='interrupted',
                    error='服务重启或进程中断',
                    current_code='',
                    completed_at=datetime('now'),
                    updated_at=datetime('now')
                WHERE job_id = ?
                """,
                (job_id,),
            )
        conn.commit()
    for job_id in job_ids:
        _recount_job(job_id)
    return len(job_ids)


def mark_stalled_jobs(*, stale_minutes: int = 15) -> int:
    threshold = f"-{max(1, int(stale_minutes or 15))} minutes"
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT job_id
            FROM batch_jobs
            WHERE status = 'running'
              AND (
                heartbeat_at IS NULL
                OR datetime(heartbeat_at) < datetime('now', ?)
              )
            """,
            (threshold,),
        ).fetchall()
        job_ids = [row["job_id"] for row in rows]
        for job_id in job_ids:
            conn.execute(
                """
                UPDATE batch_job_items
                SET status='failed',
                    error=CASE WHEN error IS NULL OR error='' THEN '心跳超时，任务可能已卡死' ELSE error END,
                    completed_at=datetime('now'),
                    updated_at=datetime('now')
                WHERE job_id = ? AND status = 'running'
                """,
                (job_id,),
            )
            conn.execute(
                """
                UPDATE batch_job_item_steps
                SET status='failed',
                    error=CASE WHEN error IS NULL OR error='' THEN '心跳超时，角色步骤可能已卡死' ELSE error END,
                    completed_at=datetime('now'),
                    updated_at=datetime('now')
                WHERE job_id = ? AND status = 'running'
                """,
                (job_id,),
            )
            conn.execute(
                """
                UPDATE batch_jobs
                SET status='interrupted',
                    error='心跳超时，任务已进入可恢复状态',
                    current_code='',
                    lease_owner=NULL,
                    lease_token=NULL,
                    lease_until=NULL,
                    completed_at=datetime('now'),
                    updated_at=datetime('now')
                WHERE job_id = ?
                """,
                (job_id,),
            )
        conn.commit()
    for job_id in job_ids:
        _recount_job(job_id)
        log_job_event(job_id, "warning", "job_stalled", "心跳超时，任务已转为 interrupted", {"stale_minutes": stale_minutes})
    return len(job_ids)


def get_worker_status(*, stale_minutes: int = 15) -> dict[str, Any]:
    now = datetime.now(UTC).replace(tzinfo=None)
    threshold = now - timedelta(minutes=max(1, int(stale_minutes or 15)))
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT worker_id, lease_owner, status, job_id, job_type, current_code, heartbeat_at, lease_until, updated_at
            FROM batch_jobs
            WHERE worker_id IS NOT NULL OR lease_owner IS NOT NULL
            ORDER BY COALESCE(heartbeat_at, updated_at, created_at) DESC
            LIMIT 200
            """
        ).fetchall()
    by_worker: dict[str, dict[str, Any]] = {}
    for row in rows:
        worker_id = row["worker_id"] or row["lease_owner"] or "unknown"
        heartbeat = _parse_dt(row["heartbeat_at"])
        lease_until = _parse_dt(row["lease_until"])
        state = "offline"
        if heartbeat and heartbeat >= threshold and (not lease_until or lease_until >= now):
            state = "online"
        elif row["status"] == "running":
            state = "stale"
        current = by_worker.get(worker_id)
        record = {
            "worker_id": worker_id,
            "state": state,
            "job_id": row["job_id"],
            "job_type": row["job_type"],
            "job_status": row["status"],
            "current_code": row["current_code"],
            "heartbeat_at": row["heartbeat_at"],
            "lease_until": row["lease_until"],
        }
        if not current or (record["state"] == "online" and current["state"] != "online"):
            by_worker[worker_id] = record
    workers = list(by_worker.values())
    summary = {
        "total": len(workers),
        "online": sum(1 for worker in workers if worker["state"] == "online"),
        "stale": sum(1 for worker in workers if worker["state"] == "stale"),
        "offline": sum(1 for worker in workers if worker["state"] == "offline"),
    }
    return {"summary": summary, "workers": workers}


def get_research_items(job_id: str, status: str | None = None) -> dict[str, Any]:
    params: list[Any] = [job_id]
    where = "WHERE job_id = ?"
    if status:
        where += " AND status = ?"
        params.append(status)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM batch_job_items {where} ORDER BY id ASC",
            params,
        ).fetchall()
        progress = _step_progress(conn, [int(row["id"]) for row in rows])
    return {"count": len(rows), "items": [{**dict(row), **progress.get(int(row["id"]), {})} for row in rows]}


def _is_job_cancelled(job_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT status FROM batch_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return bool(row and row["status"] == "cancelled")


def cancel_job(job_id: str) -> dict[str, Any]:
    job = get_research_job(job_id)
    if job["status"] in {"completed", "failed", "cancelled"}:
        return {"job_id": job_id, "status": job["status"], "cancelled_items": 0}
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE batch_job_items
            SET status='cancelled',
                error=CASE WHEN error IS NULL OR error='' THEN '用户取消批量任务' ELSE error END,
                completed_at=datetime('now'),
                updated_at=datetime('now')
            WHERE job_id = ?
              AND status NOT IN ('completed', 'skipped')
            """,
            (job_id,),
        )
        conn.execute(
            """
            UPDATE batch_job_item_steps
            SET status='cancelled',
                error=CASE WHEN error IS NULL OR error='' THEN '用户取消批量任务' ELSE error END,
                completed_at=datetime('now'),
                updated_at=datetime('now')
            WHERE job_id = ?
              AND status NOT IN ('completed', 'skipped')
            """,
            (job_id,),
        )
        conn.execute(
            """
            UPDATE batch_jobs
            SET status='cancelled',
                error='用户取消批量任务',
                current_code='',
                completed_at=datetime('now'),
                updated_at=datetime('now')
            WHERE job_id = ?
            """,
            (job_id,),
        )
        conn.commit()
        cancelled_items = cursor.rowcount
    _recount_job(job_id)
    _update_job(job_id, status="cancelled", error="用户取消批量任务", current_code="", completed_at=_now_expr())
    return {"job_id": job_id, "status": "cancelled", "cancelled_items": cancelled_items}


def _schedule_job(job_id: str) -> None:
    asyncio.create_task(run_research_job(job_id))


async def create_research_job(
    *,
    job_type: str,
    codes: list[str] | None = None,
    report_ids: list[int] | None = None,
    group: str = "all",
    top_n: int = 0,
    skip_recent_days: int = 30,
    refresh_snapshots: bool = False,
    snapshot_concurrency: int = 3,
    analysis_mode: str = "snapshot-tradingagents",
    analysis_concurrency: int = 1,
    snapshot_model_tier: str = "deep",
    plan_top_n: int = 10,
    multi_role: bool = False,
    stage: str = "final",
    parent_plan_id: str | None = None,
    context_strategy: str = "auto",
    model_strategy: str = "single",
    role_models: dict[str, Any] | None = None,
    title: str | None = None,
    trade_date: str | None = None,
    output_dir: Path | str | None = None,
    auto_start: bool = True,
    **extra,
) -> dict[str, Any]:
    if job_type not in JOB_TYPES:
        raise HTTPException(400, f"未知批量任务类型: {job_type}")
    clean_report_ids = [int(report_id) for report_id in (report_ids or []) if int(report_id) > 0]
    stocks = _load_report_codes(clean_report_ids) if job_type == "position_plan" and clean_report_ids else _load_watchlist_codes(group, codes or None)
    if top_n > 0:
        stocks = stocks[:top_n]
    if not stocks:
        raise HTTPException(400, "没有可执行的股票")
    job_id = f"{job_type[:2]}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:6]}"
    max_consecutive_failures = int(extra.pop("max_consecutive_failures", 5) or 0)
    max_failure_rate = float(extra.pop("max_failure_rate", 0.25) or 0)
    min_failure_rate_items = int(extra.pop("min_failure_rate_items", 5) or 1)
    payload = {
        "job_type": job_type,
        "group": group,
        "codes": [stock["code"] for stock in stocks],
        "report_ids": clean_report_ids,
        "top_n": top_n,
        "skip_recent_days": skip_recent_days,
        "refresh_snapshots": refresh_snapshots,
        "snapshot_concurrency": max(1, snapshot_concurrency),
        "analysis_mode": analysis_mode,
        "analysis_concurrency": max(1, analysis_concurrency),
        "snapshot_model_tier": snapshot_model_tier,
        "plan_top_n": plan_top_n,
        "multi_role": multi_role,
        "stage": stage,
        "parent_plan_id": parent_plan_id,
        "context_strategy": context_strategy,
        "model_strategy": model_strategy,
        "role_models": role_models or {},
        "title": title,
        "trade_date": trade_date or date.today().isoformat(),
        "output_dir": str(output_dir) if output_dir else str(Path("data") / "batch_research"),
        "max_consecutive_failures": max_consecutive_failures,
        "max_failure_rate": max_failure_rate,
        "min_failure_rate_items": min_failure_rate_items,
        **extra,
    }
    _insert_job(job_id, job_type, payload, stocks)
    log_job_event(
        job_id,
        "info",
        "job_created",
        f"{_job_name(job_type)} 已创建",
        {"total_count": len(stocks), "job_type": job_type, "codes": [stock["code"] for stock in stocks]},
    )
    if auto_start:
        _schedule_job(job_id)
    return {"job_id": job_id, "job_type": job_type, "status": "pending", "total_count": len(stocks)}


def _load_job_for_run(job_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    job = get_research_job(job_id)
    payload = json.loads(job.get("payload_json") or "{}")
    return job, job["items"], payload


async def _run_data_prefetch_item(item: dict[str, Any], payload: dict[str, Any]) -> None:
    stock = batch_research.StockCandidate(item["code"], item.get("name") or item["code"], "默认", 0)
    if not payload.get("refresh_snapshots") and batch_research._has_complete_snapshot(DB_PATH, item["code"]):
        snapshot = batch_research._latest_snapshot(DB_PATH, item["code"])
        _update_item_id(item["id"], status="skipped", snapshot_id=snapshot["id"] if snapshot else None, completed_at=_now_expr(), error="")
        return
    _update_item_id(item["id"], status="running", started_at=_now_expr(), error="")
    snapshot = await batch_research.fetch_seven_layer_snapshot(stock, trade_date=payload.get("trade_date"))
    saved = batch_research.save_data_snapshot(DB_PATH, stock, snapshot, run_id=payload.get("job_id") or "batch-ui")
    status = "completed" if saved.get("ok") else "failed"
    error = "" if saved.get("ok") else json.dumps(saved.get("validation"), ensure_ascii=False)
    _update_item_id(item["id"], status=status, snapshot_id=saved.get("id"), completed_at=_now_expr(), error=error)


async def _run_report_item(item: dict[str, Any], payload: dict[str, Any], recent_codes: set[str], config: dict[str, str]) -> None:
    code = item["code"]
    if code in recent_codes:
        with _connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM analysis_reports
                WHERE code = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (code,),
            ).fetchone()
        _update_item_id(item["id"], status="skipped", report_id=row["id"] if row else None, completed_at=_now_expr(), error="")
        return
    snapshot = _snapshot_by_id(int(item["locked_snapshot_id"])) if item.get("locked_snapshot_id") else batch_research._latest_snapshot(DB_PATH, code)
    if not snapshot or not (snapshot.get("validation") or {}).get("ok"):
        _update_item_id(item["id"], status="waiting_snapshot", completed_at=_now_expr(), error="缺少完整七层快照")
        log_job_event(payload.get("job_id") or item["job_id"], "warning", "snapshot_missing", "缺少完整七层快照", {"code": code}, item_id=item["id"])
        return
    _update_item_id(item["id"], status="running", snapshot_id=snapshot["id"], locked_snapshot_id=snapshot["id"], started_at=_now_expr(), error="")
    log_job_event(payload.get("job_id") or item["job_id"], "info", "item_started", f"{code} 开始生成报告", {"snapshot_id": snapshot["id"]}, item_id=item["id"])
    ranked = batch_research.RankedCandidate(code, item.get("name") or code, "默认", 0, 0.0, {})
    analysis_mode = payload.get("analysis_mode") or "snapshot-tradingagents"
    timeout_seconds = int(payload.get("timeout_seconds") or 1800)
    started = datetime.now()
    if analysis_mode == "snapshot-tradingagents":
        result = await _run_snapshot_tradingagents_graph_with_steps(
            item=item,
            ranked=ranked,
            snapshot=snapshot,
            config=config,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        report_source = "snapshot_tradingagents"
        depth = "snapshot_tradingagents"
        model_mode = "snapshot_tradingagents"
    elif analysis_mode == "snapshot-debate":
        role_discussion = []
        for role_key, role_name, role_goal in batch_research.SNAPSHOT_DEBATE_ROLES:
            role = {"role_key": role_key, "role_name": role_name, "role_goal": role_goal}
            prompt = batch_research._snapshot_debate_prompt(
                ranked,
                snapshot,
                role_name=role_name,
                role_goal=role_goal,
                previous_discussion=role_discussion,
            )
            content = await batch_research._call_snapshot_debate_role_llm(role, prompt, config, timeout_seconds=timeout_seconds)
            role_discussion.append({"role_key": role_key, "role_name": role_name, "content": content})
        result = batch_research._snapshot_debate_result(role_discussion)
        report_source = "snapshot_debate"
        depth = "snapshot_debate"
        model_mode = "snapshot_debate"
    else:
        result = await batch_research._call_snapshot_llm(
            batch_research._snapshot_prompt(ranked, snapshot),
            config,
            timeout_seconds=timeout_seconds,
        )
        report_source = "snapshot_report"
        depth = "snapshot"
        model_mode = "snapshot_report"
    report_id = batch_research._save_snapshot_report(
        DB_PATH,
        ranked,
        result,
        snapshot,
        run_id=payload.get("job_id") or "batch-ui",
        duration_seconds=(datetime.now() - started).total_seconds(),
        model=config.get("model", ""),
        report_source=report_source,
        depth=depth,
        model_mode=model_mode,
    )
    _update_item_id(item["id"], status="completed", report_id=report_id, completed_at=_now_expr(), error="")
    log_job_event(payload.get("job_id") or item["job_id"], "info", "item_completed", f"{code} 报告生成完成", {"report_id": report_id}, item_id=item["id"])


async def _run_position_plan(job_id: str, items: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    stocks = [batch_research.StockCandidate(item["code"], item.get("name") or item["code"], "默认", 0) for item in items]
    output_dir = Path(payload.get("output_dir") or Path("data") / "batch_research")
    if payload.get("multi_role"):
        config = batch_research._snapshot_llm_config(DB_PATH, model_tier=payload.get("snapshot_model_tier") or "deep")
        plan = await batch_research.build_multi_role_position_plan(
            DB_PATH,
            stocks,
            report_ids=payload.get("report_ids") or [],
            top_n=int(payload.get("plan_top_n") or 10),
            config=config,
            timeout_seconds=int(payload.get("timeout_seconds") or 1800),
            context_strategy=payload.get("context_strategy") or "auto",
            model_strategy=payload.get("model_strategy") or "single",
            role_models=payload.get("role_models") or {},
        )
    else:
        plan = batch_research.build_position_plan(DB_PATH, stocks, top_n=int(payload.get("plan_top_n") or 10))
    outputs = batch_research.write_position_plan(output_dir, plan)
    persisted = position_plan_service.persist_position_plan(
        plan,
        db_path=DB_PATH,
        batch_job_id=job_id,
        payload=payload,
        outputs=outputs,
    )
    reports = {item["code"]: item for item in plan.get("recommendations", []) if item.get("report_id")}
    for item in items:
        report = reports.get(item["code"])
        if report:
            _update_item_id(item["id"], status="completed", report_id=report.get("report_id"), completed_at=_now_expr())
        else:
            _update_item_id(item["id"], status="waiting_snapshot", error="缺少分析报告", completed_at=_now_expr())
    return {"plan": plan, "outputs": outputs, "position_plan": {"plan_id": persisted["plan_id"], "id": persisted["id"]}}


def run_batch_quality_check(job_id: str) -> dict[str, Any]:
    job = get_research_job(job_id)
    report_ids = [int(item["report_id"]) for item in job["items"] if item.get("report_id")]
    reports: dict[int, sqlite3.Row] = {}
    if report_ids:
        placeholders = ",".join("?" for _ in report_ids)
        with _connect() as conn:
            rows = conn.execute(
                f"""
                SELECT id, code, signal, confidence, risk_score, final_decision, trader_plan, raw_state
                FROM analysis_reports
                WHERE id IN ({placeholders})
                """,
                report_ids,
            ).fetchall()
        reports = {int(row["id"]): row for row in rows}
    missing_report_ids = 0
    empty_final_decision = 0
    invalid_signals = 0
    short_reports = 0
    duplicate_codes: dict[str, int] = {}
    seen_codes: dict[str, int] = {}
    for item in job["items"]:
        code = item["code"]
        seen_codes[code] = seen_codes.get(code, 0) + 1
        if item["status"] != "completed":
            continue
        report = reports.get(int(item["report_id"] or 0))
        if not report:
            missing_report_ids += 1
            continue
        signal = str(report["signal"] or "").upper()
        if signal not in {"STRONG_BUY", "BUY", "OVERWEIGHT", "HOLD", "UNDERWEIGHT", "SELL", "STRONG_SELL"}:
            invalid_signals += 1
        final_text = str(report["final_decision"] or "")
        if not final_text.strip():
            empty_final_decision += 1
        if len(final_text + str(report["trader_plan"] or "")) < 20:
            short_reports += 1
    duplicate_codes = {code: count for code, count in seen_codes.items() if count > 1}
    quality = {
        "job_id": job_id,
        "checked_at": _iso_now(),
        "total_items": int(job.get("total_count") or len(job["items"])),
        "completed_reports": len(report_ids),
        "failed_items": int(job.get("failed_count") or 0),
        "waiting_items": int(job.get("waiting_count") or 0),
        "missing_report_ids": missing_report_ids,
        "invalid_signals": invalid_signals,
        "empty_final_decision": empty_final_decision,
        "short_reports": short_reports,
        "duplicate_codes": duplicate_codes,
        "score": max(0, 100 - missing_report_ids * 20 - invalid_signals * 10 - empty_final_decision * 20 - short_reports * 5),
    }
    _update_job(job_id, quality_json=json.dumps(quality, ensure_ascii=False, default=str))
    record_job_artifact(job_id, "quality_json", "批次质量巡检", data=quality)
    log_job_event(job_id, "info", "quality_checked", "批次质量巡检完成", quality)
    return quality


def _write_post_batch_artifacts(job_id: str, payload: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    job = get_research_job(job_id)
    output_dir = Path(payload.get("output_dir") or Path("data") / "batch_research")
    output_dir.mkdir(parents=True, exist_ok=True)
    failure_items = [item for item in job["items"] if item["status"] in FAILED_STATUSES or item["status"] == "waiting_snapshot"]
    summary_path = output_dir / f"{job_id}_batch_summary.md"
    failure_path = output_dir / f"{job_id}_failures.json"
    summary_lines = [
        f"# 批量任务摘要 {job_id}",
        "",
        f"- 类型：{job['job_type']}",
        f"- 状态：{job['status']}",
        f"- 总数：{job['total_count']}",
        f"- 完成：{counts.get('completed_count', 0)}",
        f"- 跳过：{counts.get('skipped_count', 0)}",
        f"- 失败：{counts.get('failed_count', 0)}",
        f"- 待数据：{counts.get('waiting_count', 0)}",
        "",
        "## 失败清单",
        "",
    ]
    if failure_items:
        summary_lines.extend(f"- {item['name'] or item['code']} {item['code']}：{item['status']} {item.get('error') or ''}" for item in failure_items)
    else:
        summary_lines.append("- 无")
    summary_path.write_text("\n".join(summary_lines), encoding="utf-8")
    failure_path.write_text(json.dumps(failure_items, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    actions = {
        "generated_at": _iso_now(),
        "summary_markdown": str(summary_path),
        "failure_json": str(failure_path),
        "next_actions": [
            "查看批次质量评分",
            "重试失败股票",
            "基于完整报告生成建仓计划",
        ],
    }
    record_job_artifact(job_id, "summary_markdown", "批次摘要", path=str(summary_path), data={"counts": counts})
    record_job_artifact(job_id, "failure_json", "失败清单", path=str(failure_path), data={"count": len(failure_items)})
    _update_job(job_id, post_actions_json=json.dumps(actions, ensure_ascii=False, default=str))
    log_job_event(job_id, "info", "post_actions_generated", "批次后处理产物已生成", actions)
    return actions


async def run_research_job(job_id: str) -> dict[str, Any]:
    job, items, payload = _load_job_for_run(job_id)
    payload["job_id"] = job_id
    job_type = job["job_type"]
    if job.get("status") == "cancelled":
        return get_research_job(job_id)
    if job.get("status") == "paused" and int(job.get("pause_requested") or 0):
        return get_research_job(job_id)
    worker_id = payload.get("worker_id") or f"web-{os.getpid()}"
    _update_job(job_id, status="running", pause_requested=0, worker_id=worker_id, heartbeat_at=_now_expr(), started_at=job.get("started_at") or _now_expr(), error="", current_code="")
    log_job_event(job_id, "info", "job_started", "批量任务开始执行", {"worker_id": worker_id, "job_type": job_type})
    result: dict[str, Any] = {}
    try:
        runnable_items = [item for item in items if item["status"] in RESUMABLE_ITEM_STATUS]
        if job_type == "position_plan":
            result = await _run_position_plan(job_id, items, payload)
        elif job_type == "data_prefetch":
            requested_concurrency = max(1, int(payload.get("snapshot_concurrency") or 3))
            semaphore = asyncio.Semaphore(_effective_concurrency(job_id, "snapshot", requested_concurrency))

            async def run_one(item: dict[str, Any]) -> None:
                async with semaphore:
                    if _is_job_cancelled(job_id):
                        if item["status"] not in TERMINAL_ITEM_STATUS:
                            _update_item_id(item["id"], status="cancelled", error="用户取消批量任务", completed_at=_now_expr())
                        return
                    if _is_job_pause_requested(job_id):
                        _mark_job_paused(job_id)
                        return
                    await _respect_cooldown(job_id, "snapshot")
                    touch_job(job_id, worker_id=worker_id, current_code=item["code"])
                    try:
                        log_job_event(job_id, "info", "item_started", f"{item['code']} 开始预取七层数据", item_id=item["id"])
                        await _run_data_prefetch_item(item, payload)
                        _record_runtime_success(job_id, "snapshot", base_concurrency=requested_concurrency)
                    except Exception as exc:
                        _record_runtime_failure(job_id, "snapshot", str(exc), base_concurrency=requested_concurrency)
                        log_job_event(job_id, "error", "item_failed", f"{item['code']} 七层数据预取失败", {"error": str(exc)}, item_id=item["id"])
                        _update_item_id(item["id"], status="failed", error=str(exc), error_type=_llm_error_type(str(exc)), completed_at=_now_expr())
                        _maybe_guard_pause(job_id, payload, error=str(exc))
                    finally:
                        _recount_job(job_id)

            await asyncio.gather(*(run_one(item) for item in runnable_items))
        elif job_type == "report_generation":
            recent_codes = batch_research.recent_report_codes(DB_PATH, int(payload.get("skip_recent_days") or 30))
            config = batch_research._snapshot_llm_config(DB_PATH, model_tier=payload.get("snapshot_model_tier") or "deep")
            _lock_job_snapshots(job_id, items)
            job, items, payload = _load_job_for_run(job_id)
            payload["job_id"] = job_id
            runnable_items = [item for item in items if item["status"] in RESUMABLE_ITEM_STATUS]
            requested_concurrency = max(1, int(payload.get("analysis_concurrency") or 1))
            semaphore = asyncio.Semaphore(_effective_concurrency(job_id, "llm", requested_concurrency))

            async def run_one(item: dict[str, Any]) -> None:
                async with semaphore:
                    if _is_job_cancelled(job_id):
                        if item["status"] not in TERMINAL_ITEM_STATUS:
                            _update_item_id(item["id"], status="cancelled", error="用户取消批量任务", completed_at=_now_expr())
                        return
                    if _is_job_pause_requested(job_id):
                        _mark_job_paused(job_id)
                        return
                    await _respect_cooldown(job_id, "llm")
                    touch_job(job_id, worker_id=worker_id, current_code=item["code"])
                    try:
                        await _run_report_item(item, payload, recent_codes, config)
                    except ModelQuotaError as exc:
                        log_job_event(job_id, "warning", "item_quota_paused", f"{item['code']} 因模型额度暂停", {"error": str(exc)}, item_id=item["id"])
                    except Exception as exc:
                        _record_runtime_failure(job_id, "llm", str(exc), base_concurrency=requested_concurrency)
                        log_job_event(job_id, "error", "item_failed", f"{item['code']} 报告生成失败", {"error": str(exc)}, item_id=item["id"])
                        _update_item_id(item["id"], status="failed", error=str(exc), error_type=_llm_error_type(str(exc)), completed_at=_now_expr())
                        _maybe_guard_pause(job_id, payload, error=str(exc))
                    finally:
                        _recount_job(job_id)

            await asyncio.gather(*(run_one(item) for item in runnable_items))
        counts = _recount_job(job_id)
        refreshed = get_research_job(job_id)
        if refreshed["status"] == "quota_paused":
            return refreshed
        if refreshed["status"] == GUARD_PAUSED_STATUS:
            return refreshed
        if refreshed["status"] == "paused" or int(refreshed.get("pause_requested") or 0):
            return refreshed
        if _is_job_cancelled(job_id):
            _update_job(job_id, status="cancelled", completed_at=_now_expr(), current_code="")
            return get_research_job(job_id)
        final_status = "failed" if counts["failed_count"] and not counts["completed_count"] and not counts["skipped_count"] else "completed"
        if counts["waiting_count"] and not counts["completed_count"] and not counts["skipped_count"]:
            final_status = "failed"
        quality: dict[str, Any] = {}
        if job_type == "report_generation":
            quality = run_batch_quality_check(job_id)
        post_actions = _write_post_batch_artifacts(job_id, payload, counts)
        _update_job(
            job_id,
            status=final_status,
            completed_at=_now_expr(),
            current_code="",
            result_json=json.dumps({"counts": counts, "quality": quality, "post_actions": post_actions, **result}, ensure_ascii=False, default=str),
        )
        log_job_event(job_id, "info", "job_completed", "批量任务执行结束", {"status": final_status, "counts": counts})
    except Exception as exc:
        log_job_event(job_id, "error", "job_failed", "批量任务执行异常", {"error": str(exc)})
        _update_job(job_id, status="failed", error=str(exc), completed_at=_now_expr(), current_code="")
    return get_research_job(job_id)


async def resume_job(job_id: str) -> dict[str, Any]:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE batch_job_items
            SET status='pending', error='', updated_at=datetime('now')
            WHERE job_id = ? AND status = 'quota_paused'
            """,
            (job_id,),
        )
        conn.execute(
            """
            UPDATE batch_job_item_steps
            SET status='pending', error='', completed_at=NULL, updated_at=datetime('now')
            WHERE job_id = ? AND status = 'quota_paused'
            """,
            (job_id,),
        )
        conn.commit()
    runtime = _runtime_state(job_id)
    if "quota" in runtime:
        runtime["quota"]["state"] = "resumed"
    if "guard" in runtime:
        runtime["guard"]["state"] = "resumed"
        runtime["guard"]["resumed_at"] = _iso_now()
        runtime["guard"]["consecutive_failures"] = 0
    _save_runtime_state(job_id, runtime)
    _update_job(job_id, status="pending", pause_requested=0, paused_at=None, error="", current_code="")
    log_job_event(job_id, "info", "job_resumed", "批量任务继续执行")
    _schedule_job(job_id)
    return get_research_job(job_id)


async def retry_failed(job_id: str, *, auto_start: bool = True) -> dict[str, Any]:
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE batch_job_items
            SET status='pending', error='', retry_count=COALESCE(retry_count, 0) + 1, updated_at=datetime('now')
            WHERE job_id = ? AND status IN ('failed', 'timeout', 'cancelled', 'waiting_snapshot', 'quota_paused')
            """,
            (job_id,),
        )
        step_cursor = conn.execute(
            """
            UPDATE batch_job_item_steps
            SET status='pending',
                error='',
                retry_count=COALESCE(retry_count, 0) + 1,
                started_at=NULL,
                completed_at=NULL,
                updated_at=datetime('now')
            WHERE job_id = ?
              AND status IN ('failed', 'timeout', 'cancelled', 'quota_paused')
            """,
            (job_id,),
        )
        conn.commit()
        reset_count = cursor.rowcount + step_cursor.rowcount
    _recount_job(job_id)
    if auto_start and reset_count:
        _schedule_job(job_id)
    return {"job_id": job_id, "reset_count": reset_count, "status": "pending" if reset_count else get_research_job(job_id)["status"]}


async def run_worker_once(*, worker_id: str | None = None, stale_minutes: int = 15) -> dict[str, Any]:
    worker = worker_id or f"worker-{os.getpid()}"
    stalled = mark_stalled_jobs(stale_minutes=stale_minutes)
    claimed = claim_next_job(worker_id=worker, lease_seconds=max(60, int(stale_minutes or 15) * 60))
    if not claimed:
        return {"worker_id": worker, "ran": False, "stalled": stalled}
    job_id = claimed["job_id"]
    await run_research_job(job_id)
    return {"worker_id": worker, "ran": True, "job_id": job_id, "stalled": stalled}


# Compatibility wrappers for the old /api/batch-reports surface.
async def create_batch_report_job(**kwargs) -> dict[str, Any]:
    return await create_research_job(job_type="report_generation", **kwargs)


def get_job(job_id: str) -> dict[str, Any]:
    return get_research_job(job_id)


def list_jobs(limit: int = 50, status: str | None = None) -> dict[str, Any]:
    return list_research_jobs(limit=limit, status=status, job_type="report_generation")


async def run_batch_report_job(job_id: str, **_kwargs) -> dict[str, Any]:
    return await run_research_job(job_id)
