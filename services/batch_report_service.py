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
from services import market_permission_service, quote_service, settings_service
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
MANUAL_COMPLETED_STATUS = "manual_completed"
ERROR_TYPE_LABELS = {
    "quota_exhausted": "额度失败",
    "network": "网络失败",
    "rate_limit": "限流失败",
    "context_limit": "上下文过长",
    "snapshot_incomplete": "快照不完整",
    "json_parse": "模型 JSON 输出失败",
    "role_failure": "单角色失败",
    "unknown": "未知失败",
}


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


def record_worker_heartbeat(
    worker_id: str,
    *,
    state: str = "idle",
    model_provider_ids: list[str] | None = None,
    model_tier: str | None = None,
    current_job_id: str | None = None,
    current_job_type: str | None = None,
    current_item_id: int | None = None,
    current_code: str | None = None,
    current_stage: str | None = None,
    last_result: dict[str, Any] | None = None,
    error: str = "",
    mark_claim: bool = False,
) -> dict[str, Any]:
    worker = str(worker_id or f"worker-{os.getpid()}").strip() or f"worker-{os.getpid()}"
    providers = [str(item).strip() for item in (model_provider_ids or []) if str(item).strip()]
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO batch_worker_heartbeats
                (worker_id, pid, state, model_provider_ids_json, model_tier,
                 last_seen_at, last_loop_at, last_claim_at, current_job_id,
                 current_job_type, current_item_id, current_code, current_stage,
                 last_result_json, error)
            VALUES
                (?, ?, ?, ?, ?, datetime('now'), datetime('now'),
                 CASE WHEN ? THEN datetime('now') ELSE NULL END,
                 ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(worker_id) DO UPDATE SET
                pid=excluded.pid,
                state=excluded.state,
                model_provider_ids_json=excluded.model_provider_ids_json,
                model_tier=excluded.model_tier,
                last_seen_at=datetime('now'),
                last_loop_at=datetime('now'),
                last_claim_at=CASE WHEN ? THEN datetime('now') ELSE batch_worker_heartbeats.last_claim_at END,
                current_job_id=excluded.current_job_id,
                current_job_type=excluded.current_job_type,
                current_item_id=excluded.current_item_id,
                current_code=excluded.current_code,
                current_stage=excluded.current_stage,
                last_result_json=excluded.last_result_json,
                error=excluded.error,
                updated_at=datetime('now')
            """,
            (
                worker,
                os.getpid(),
                state,
                json.dumps(providers, ensure_ascii=False),
                model_tier or "",
                1 if mark_claim else 0,
                current_job_id or "",
                current_job_type or "",
                current_item_id,
                current_code or "",
                current_stage or "",
                json.dumps(last_result or {}, ensure_ascii=False, default=str),
                error or "",
                1 if mark_claim else 0,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM batch_worker_heartbeats WHERE worker_id=?", (worker,)).fetchone()
    return dict(row)


def _clean_str_list(values: list[Any] | None) -> list[str]:
    return [str(item).strip() for item in (values or []) if str(item).strip()]


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


def _trading_permission_settings() -> dict[str, Any]:
    settings = dict(settings_service.DEFAULTS)
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT key, value FROM settings
                WHERE key IN ('trade_market_main', 'trade_market_gem', 'trade_market_star', 'trade_market_bse')
                """
            ).fetchall()
        settings.update({row["key"]: row["value"] for row in rows})
    except Exception:
        pass
    return settings


def _filter_stocks_by_trading_permissions(stocks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return market_permission_service.filter_allowed_stocks(stocks, settings=_trading_permission_settings())


def _excluded_market_summary(excluded: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in excluded:
        label = str(item.get("market_label") or item.get("market_key") or "未知")
        summary[label] = summary.get(label, 0) + 1
    return summary


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
    allowed_worker_ids: list[str] | None = None,
    primary_provider_ids: list[str] | None = None,
    **extra,
) -> dict[str, Any]:
    clean_report_ids = [int(report_id) for report_id in (report_ids or []) if int(report_id) > 0]
    stocks = _load_report_codes(clean_report_ids) if job_type == "position_plan" and clean_report_ids else _load_watchlist_codes(group, codes or None)
    stocks, excluded_by_permission = _filter_stocks_by_trading_permissions(stocks)
    if job_type == "position_plan" and clean_report_ids:
        clean_report_ids = [int(stock["report_id"]) for stock in stocks if stock.get("report_id")]
    if top_n > 0:
        stocks = stocks[:top_n]
    primary = batch_research._snapshot_llm_config(DB_PATH, model_tier="deep")
    role_calls = _tradingagents_role_call_count(debate_rounds=debate_rounds, risk_rounds=risk_rounds) if job_type == "report_generation" else 5
    fallback_payload = {"model_fallback_enabled": model_fallback_enabled, "fallback_provider_ids": fallback_provider_ids or []}
    fallbacks = _fallback_model_configs(fallback_payload)
    missing = [key for key in ("base_url", "api_key", "model") if not primary.get(key)]
    status = "ok" if not missing else "warning"
    all_workers, selected_workers, selected_worker_ids = _worker_pool_for_payload({"allowed_worker_ids": allowed_worker_ids or []})
    worker_count = len(selected_workers) if selected_workers else 1
    role_calls = int(role_calls)
    estimated_calls = len(stocks) * role_calls
    depth = str(extra.get("analysis_depth") or "standard")
    mode = str(extra.get("model_mode") or "balanced")
    base_seconds = 50 if depth == "quick" else 80 if mode == "economy" else 120 if depth == "standard" else 170
    if job_type == "position_plan":
        base_seconds = 90
    role_calls_per_hour = max(1, int(worker_count * 3600 / base_seconds))
    low_hours = round(estimated_calls / role_calls_per_hour * 0.85, 2) if estimated_calls else 0.0
    high_hours = round(estimated_calls / role_calls_per_hour * 1.35, 2) if estimated_calls else 0.0
    configured_provider_ids = set(primary_provider_ids or [])
    configured_provider_ids.update(fallback_provider_ids or [])
    for worker in selected_workers:
        configured_provider_ids.update(worker.get("provider_ids") or [])
    provider_pool = _public_provider_pool(list(configured_provider_ids), model_tier=extra.get("snapshot_model_tier") or "deep")
    model_pool_risks = [
        {
            "provider_id": item["provider_id"],
            "name": item["name"],
            "model": item["model"],
            "risk": "missing_config",
            "message": f"{item['name']} 缺少 {', '.join(item['missing'])}",
        }
        for item in provider_pool
        if not item.get("ready")
    ]
    if estimated_calls >= 1000 and not fallbacks:
        model_pool_risks.append(
            {
                "provider_id": "fallback",
                "name": "备用模型",
                "model": "",
                "risk": "no_fallback",
                "message": "预计调用量较高，但没有可用备用模型池",
            }
        )
    recommendations = []
    if estimated_calls >= 1500:
        recommendations.append("预计调用量较高，建议启用多 worker、多模型池或降低辩论轮数。")
    if high_hours >= 6:
        recommendations.append("预计为 6 小时以上长任务，建议使用后台守护 worker 并确认断点续跑。")
    return {
        "status": status,
        "job_type": job_type,
        "stock_count": len(stocks),
        "excluded_by_permission_count": len(excluded_by_permission),
        "excluded_by_permission": _excluded_market_summary(excluded_by_permission),
        "role_calls_per_stock": role_calls,
        "estimated_role_calls": estimated_calls,
        "primary_ready": not missing,
        "primary_missing": missing,
        "primary_model": primary.get("model", ""),
        "fallback_enabled": bool(model_fallback_enabled),
        "fallback_count": len(fallbacks),
        "fallback_models": [{"profile": item.get("_profile", ""), "model": item.get("model", "")} for item in fallbacks],
        "enabled_worker_count": len([worker for worker in all_workers if worker.get("enabled")]),
        "worker_count": worker_count,
        "selected_worker_ids": selected_worker_ids,
        "throughput": {
            "role_calls_per_hour": role_calls_per_hour,
            "worker_count": worker_count,
            "seconds_per_role_call": base_seconds,
        },
        "duration_range_hours": {"low": low_hours, "high": high_hours},
        "estimated_duration_text": f"{low_hours:g}-{high_hours:g} 小时" if high_hours else "0 小时",
        "model_pool_risks": model_pool_risks,
        "recommendations": recommendations,
        "warnings": [
            *([f"主模型缺少 {', '.join(missing)}"] if missing else []),
            *([f"交易权限已排除 {len(excluded_by_permission)} 只标的: {_excluded_market_summary(excluded_by_permission)}"] if excluded_by_permission else []),
            *(["没有可用备用模型，额度耗尽时会进入 quota_paused"] if model_fallback_enabled and not fallbacks else []),
            *[item["message"] for item in model_pool_risks],
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
        job = get_research_job(job_id)
        record_worker_heartbeat(
            worker_id,
            state="running",
            current_job_id=job_id,
            current_job_type=job.get("job_type") or "",
            current_code=current_code or "",
            current_stage="处理股票" if current_code else "任务运行中",
        )
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
    model_config: dict[str, Any] | None = None,
    duration_ms: int | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO batch_job_item_steps
                (item_id, job_id, role_key, role_name, step_order, status, content, error,
                 error_type, model_config_json, duration_ms, heartbeat_at, started_at, completed_at)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'),
                 CASE WHEN ? IN ('completed', 'failed', 'skipped', 'cancelled') THEN datetime('now') ELSE NULL END)
            ON CONFLICT(item_id, role_key) DO UPDATE SET
                role_name=excluded.role_name,
                step_order=excluded.step_order,
                status=excluded.status,
                content=excluded.content,
                error=excluded.error,
                error_type=excluded.error_type,
                model_config_json=excluded.model_config_json,
                duration_ms=excluded.duration_ms,
                heartbeat_at=datetime('now'),
                started_at=COALESCE(batch_job_item_steps.started_at, excluded.started_at),
                completed_at=excluded.completed_at,
                updated_at=datetime('now')
            """,
            (
                item_id,
                job_id,
                role_key,
                role_name,
                step_order,
                status,
                content or "",
                error or "",
                error_type or _classify_item_error(status, error or ""),
                json.dumps(model_config or {}, ensure_ascii=False, default=str),
                duration_ms,
                status,
            ),
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


def _claim_runnable_items(
    job_id: str,
    *,
    worker_id: str,
    lease_token: str,
    limit: int,
    lease_seconds: int = 900,
) -> list[dict[str, Any]]:
    limit = max(1, int(limit or 1))
    lease = max(60, int(lease_seconds or 900))
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT *
            FROM batch_job_items
            WHERE job_id = ?
              AND (
                status IN ('pending', 'quota_paused')
                OR (
                  status = 'running'
                  AND lease_until IS NOT NULL
                  AND datetime(lease_until) < datetime('now')
                )
              )
            ORDER BY id ASC
            LIMIT ?
            """,
            (job_id, limit),
        ).fetchall()
        item_ids = [int(row["id"]) for row in rows]
        if item_ids:
            placeholders = ",".join("?" for _ in item_ids)
            conn.execute(
                f"""
                UPDATE batch_job_items
                SET status='running',
                    lease_owner=?,
                    lease_token=?,
                    lease_until=datetime('now', ?),
                    started_at=COALESCE(started_at, datetime('now')),
                    completed_at=NULL,
                    error='',
                    updated_at=datetime('now')
                WHERE id IN ({placeholders})
                """,
                [worker_id, lease_token, f"+{lease} seconds", *item_ids],
            )
        conn.commit()
    return [dict(row) for row in rows]


def _unfinished_item_count(job_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM batch_job_items
            WHERE job_id = ?
              AND status NOT IN ('completed', 'failed', 'skipped', 'waiting_snapshot', 'cancelled')
            """,
            (job_id,),
        ).fetchone()
    return int(row["count"] or 0)


def _try_claim_finalize(job_id: str) -> bool:
    token = f"finalize-{uuid.uuid4().hex}"
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT runtime_json FROM batch_jobs WHERE job_id = ?", (job_id,)).fetchone()
        runtime = _loads(row["runtime_json"] if row else "{}", {})
        if runtime.get("finalize", {}).get("state") in {"running", "completed"}:
            conn.commit()
            return False
        runtime["finalize"] = {"state": "running", "token": token, "started_at": _iso_now()}
        conn.execute(
            "UPDATE batch_jobs SET runtime_json = ?, updated_at=datetime('now') WHERE job_id = ?",
            (json.dumps(runtime, ensure_ascii=False, default=str), job_id),
        )
        conn.commit()
    return True


def _mark_finalize_completed(job_id: str, *, status: str) -> None:
    runtime = _runtime_state(job_id)
    finalize = runtime.setdefault("finalize", {})
    finalize["state"] = "completed"
    finalize["status"] = status
    finalize["completed_at"] = _iso_now()
    _save_runtime_state(job_id, runtime)


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


def _classify_item_error(status: str, error: str, error_type: str | None = None) -> str:
    if error_type:
        return error_type
    lower = (error or "").lower()
    if status == "waiting_snapshot" or "快照" in error or "snapshot" in lower:
        return "snapshot_incomplete"
    if "json" in lower or "parse" in lower or "解析" in error:
        return "json_parse"
    if "角色" in error or "role" in lower:
        return "role_failure"
    return _llm_error_type(error)


def get_failure_groups(job_id: str) -> dict[str, Any]:
    with _connect() as conn:
        item_rows = conn.execute(
            """
            SELECT id, code, name, status, error, error_type
            FROM batch_job_items
            WHERE job_id = ?
              AND status IN ('failed', 'timeout', 'cancelled', 'waiting_snapshot', 'quota_paused')
            ORDER BY id ASC
            """,
            (job_id,),
        ).fetchall()
        step_rows = conn.execute(
            """
            SELECT item_id, role_key, role_name, status, error, error_type
            FROM batch_job_item_steps
            WHERE job_id = ?
              AND status IN ('failed', 'timeout', 'cancelled', 'quota_paused')
            ORDER BY item_id ASC, step_order ASC
            """,
            (job_id,),
        ).fetchall()
    groups: dict[str, dict[str, Any]] = {}
    for row in item_rows:
        kind = _classify_item_error(row["status"], row["error"] or "", row["error_type"])
        group = groups.setdefault(kind, {"error_type": kind, "label": ERROR_TYPE_LABELS.get(kind, kind), "count": 0, "items": []})
        group["count"] += 1
        group["items"].append({"item_id": row["id"], "code": row["code"], "name": row["name"], "status": row["status"], "error": row["error"] or ""})
    for row in step_rows:
        kind = _classify_item_error(row["status"], row["error"] or "", row["error_type"])
        group = groups.setdefault(kind, {"error_type": kind, "label": ERROR_TYPE_LABELS.get(kind, kind), "count": 0, "items": []})
        group.setdefault("steps", []).append({"item_id": row["item_id"], "role_key": row["role_key"], "role_name": row["role_name"], "error": row["error"] or ""})
    return {"job_id": job_id, "groups": sorted(groups.values(), key=lambda item: item["count"], reverse=True)}


def get_runtime_stats(job_id: str) -> dict[str, Any]:
    with _connect() as conn:
        item_rows = conn.execute(
            """
            SELECT id, code, name, status, started_at, completed_at
            FROM batch_job_items
            WHERE job_id = ?
            ORDER BY id ASC
            """,
            (job_id,),
        ).fetchall()
        step_rows = conn.execute(
            """
            SELECT item_id, role_key, role_name, status, model_config_json, duration_ms, started_at, completed_at, error_type
            FROM batch_job_item_steps
            WHERE job_id = ?
            ORDER BY item_id ASC, step_order ASC, id ASC
            """,
            (job_id,),
        ).fetchall()
        log_rows = conn.execute(
            """
            SELECT event, data_json, created_at
            FROM batch_job_logs
            WHERE job_id = ?
            ORDER BY id ASC
            """,
            (job_id,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in item_rows:
        started = _parse_dt(row["started_at"])
        completed = _parse_dt(row["completed_at"])
        duration_ms = int((completed - started).total_seconds() * 1000) if started and completed else 0
        items.append({"item_id": row["id"], "code": row["code"], "name": row["name"], "status": row["status"], "duration_ms": duration_ms})
    role_stats: dict[str, dict[str, Any]] = {}
    model_stats: dict[str, dict[str, Any]] = {}
    for row in step_rows:
        model_config = _loads(row["model_config_json"], {})
        model = model_config.get("model") or ""
        duration_ms = int(row["duration_ms"] or 0)
        if not duration_ms:
            started = _parse_dt(row["started_at"])
            completed = _parse_dt(row["completed_at"])
            duration_ms = int((completed - started).total_seconds() * 1000) if started and completed else 0
        role = role_stats.setdefault(row["role_key"], {"role_key": row["role_key"], "role_name": row["role_name"], "count": 0, "failed": 0, "duration_ms": 0})
        role["count"] += 1
        role["duration_ms"] += duration_ms
        if row["status"] != "completed":
            role["failed"] += 1
        if model:
            model_item = model_stats.setdefault(model, {"model": model, "count": 0, "failed": 0, "duration_ms": 0})
            model_item["count"] += 1
            model_item["duration_ms"] += duration_ms
            if row["status"] != "completed":
                model_item["failed"] += 1
    fallback_events = []
    for row in log_rows:
        if "fallback" not in (row["event"] or ""):
            continue
        data = _loads(row["data_json"], {})
        fallback_events.append({"event": row["event"], "created_at": row["created_at"], **data})
    slowest_items = sorted(items, key=lambda item: item["duration_ms"], reverse=True)[:10]
    return {
        "job_id": job_id,
        "items": items,
        "slowest_items": slowest_items,
        "roles": list(role_stats.values()),
        "models": list(model_stats.values()),
        "fallback_events": fallback_events,
    }


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


def _provider_to_config(provider: dict[str, Any], *, model: str | None = None, model_tier: str | None = None) -> dict[str, str]:
    if model:
        selected_model = model
    elif model_tier == "quick":
        selected_model = provider.get("quick_model") or provider.get("default_model") or provider.get("deep_model") or ""
    elif model_tier == "deep":
        selected_model = provider.get("deep_model") or provider.get("default_model") or provider.get("quick_model") or ""
    else:
        selected_model = provider.get("default_model") or provider.get("deep_model") or provider.get("quick_model") or ""
    return {
        "base_url": provider.get("base_url") or "",
        "api_key": provider.get("api_key") or "",
        "model": selected_model,
        "_profile": provider.get("name") or provider.get("id") or selected_model,
        "_provider_id": provider.get("id") or "",
    }


def _provider_configs_by_ids(provider_ids: list[str], *, model_tier: str | None = None) -> list[dict[str, str]]:
    if not provider_ids:
        return []
    providers = _load_model_providers()
    order = {str(provider_id): index for index, provider_id in enumerate(provider_ids)}
    selected = [provider for provider in providers if str(provider.get("id")) in order]
    selected.sort(key=lambda provider: order[str(provider.get("id"))])
    configs: list[dict[str, str]] = []
    seen = set()
    for provider in selected:
        config = _provider_to_config(provider, model_tier=model_tier)
        key = (config.get("base_url"), config.get("api_key"), config.get("model"))
        if not all(key) or key in seen:
            continue
        seen.add(key)
        configs.append(config)
    return configs


def _load_worker_pool_config() -> list[dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'batch_worker_pool'").fetchone()
    workers = _loads(row["value"] if row else "[]", [])
    normalized: list[dict[str, Any]] = []
    for index, worker in enumerate(workers if isinstance(workers, list) else []):
        if not isinstance(worker, dict):
            continue
        worker_id = str(worker.get("id") or worker.get("name") or f"worker-{index + 1}").strip()
        normalized.append(
            {
                "id": worker_id or f"worker-{index + 1}",
                "name": worker.get("name") or worker_id or f"Worker {index + 1}",
                "enabled": bool(worker.get("enabled", True)),
                "provider_ids": _clean_str_list(worker.get("provider_ids") or []),
                "model_tier": worker.get("model_tier") if worker.get("model_tier") in {"quick", "deep"} else "deep",
                "sleep_seconds": int(worker.get("sleep_seconds") or 5),
                "stale_minutes": int(worker.get("stale_minutes") or 15),
            }
        )
    return normalized


def _public_provider_pool(provider_ids: list[str], *, model_tier: str = "deep") -> list[dict[str, Any]]:
    providers = _load_model_providers()
    provider_by_id = {str(provider.get("id")): provider for provider in providers}
    output: list[dict[str, Any]] = []
    for provider_id in provider_ids:
        provider = provider_by_id.get(str(provider_id))
        if not provider:
            output.append({"provider_id": provider_id, "name": provider_id, "model": "", "ready": False, "missing": ["provider"]})
            continue
        config = _provider_to_config(provider, model_tier=model_tier)
        missing = [key for key in ("base_url", "api_key", "model") if not config.get(key)]
        output.append(
            {
                "provider_id": provider_id,
                "name": provider.get("name") or provider_id,
                "model": config.get("model") or "",
                "model_tier": model_tier,
                "ready": not missing,
                "missing": missing,
            }
        )
    return output


def _worker_pool_for_payload(payload: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    payload = payload or {}
    workers = _load_worker_pool_config()
    enabled = [worker for worker in workers if worker.get("enabled")]
    allowed = set(_clean_str_list(payload.get("allowed_worker_ids") or []))
    selected = [worker for worker in enabled if not allowed or worker["id"] in allowed]
    return workers, selected, [worker["id"] for worker in selected]


def _primary_model_config(payload: dict[str, Any], *, model_tier: str) -> dict[str, str]:
    provider_ids = [str(item) for item in (payload.get("_worker_model_provider_ids") or payload.get("primary_provider_ids") or []) if str(item)]
    provider_configs = _provider_configs_by_ids(provider_ids, model_tier=model_tier)
    if provider_configs:
        return provider_configs[0]
    return batch_research._snapshot_llm_config(DB_PATH, model_tier=model_tier)


def _fallback_model_configs(payload: dict[str, Any]) -> list[dict[str, str]]:
    if payload.get("model_fallback_enabled") is False:
        return []
    if payload.get("quota_exhausted_action") == "pause":
        return []
    providers = _load_model_providers()
    provider_ids = [str(item) for item in payload.get("fallback_provider_ids") or [] if str(item)]
    if not provider_ids:
        worker_provider_ids = [str(item) for item in payload.get("_worker_model_provider_ids") or [] if str(item)]
        provider_ids = worker_provider_ids[1:] if len(worker_provider_ids) > 1 else []
    if provider_ids:
        order = {provider_id: index for index, provider_id in enumerate(provider_ids)}
        providers = [provider for provider in providers if str(provider.get("id")) in order]
        providers.sort(key=lambda provider: order[str(provider.get("id"))])
    configs: list[dict[str, str]] = []
    seen = set()
    for provider in providers:
        config = _provider_to_config(provider, model_tier=payload.get("_worker_model_tier") or payload.get("snapshot_model_tier") or "deep")
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
    _update_item_id(item_id, status="quota_paused", error=error, error_type=_classify_item_error("quota_paused", error), completed_at=None)
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
            model_config={"model": config.get("model", ""), "profile": config.get("_profile", ""), "provider_id": config.get("_provider_id", "")},
        )
        touch_job(job_id)
        log_job_event(
            job_id,
            "info",
            "role_started",
            f"{persisted_name} 开始",
            {"role_key": persisted_key, "model": config.get("model", ""), "provider_id": config.get("_provider_id", "")},
            item_id=item_id,
        )
        prompt = batch_research._snapshot_tradingagents_state_prompt(
            ranked,
            snapshot,
            role_key=role_key,
            role_name=role_name,
            role_goal=role_goal,
            output_key=output_key,
            state=state,
        )
        role_started_at = datetime.now()
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
                error_type=_classify_item_error("failed", str(exc)),
                model_config={"model": config.get("model", ""), "profile": config.get("_profile", ""), "provider_id": config.get("_provider_id", "")},
                duration_ms=int((datetime.now() - role_started_at).total_seconds() * 1000),
            )
            log_job_event(
                job_id,
                "error",
                "role_failed",
                f"{persisted_name} 失败",
                {"role_key": persisted_key, "error": str(exc), "error_type": _classify_item_error("failed", str(exc)), "model": config.get("model", "")},
                item_id=item_id,
            )
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
            model_config={"model": config.get("model", ""), "profile": config.get("_profile", ""), "provider_id": config.get("_provider_id", "")},
            duration_ms=int((datetime.now() - role_started_at).total_seconds() * 1000),
        )
        log_job_event(
            job_id,
            "info",
            "role_completed",
            f"{persisted_name} 完成",
            {
                "role_key": persisted_key,
                "model": config.get("model", ""),
                "provider_id": config.get("_provider_id", ""),
                "duration_ms": int((datetime.now() - role_started_at).total_seconds() * 1000),
            },
            item_id=item_id,
        )
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


def claim_next_job(*, worker_id: str, lease_seconds: int = 300, cooperative: bool = True) -> dict[str, Any] | None:
    """Atomically claim one runnable job for an independent worker."""
    worker = worker_id or f"worker-{os.getpid()}"
    lease = max(30, int(lease_seconds or 300))
    token = uuid.uuid4().hex
    joined_running = False
    worker_filter_sql = """
      AND (
        json_extract(COALESCE(payload_json, '{}'), '$.allowed_worker_ids') IS NULL
        OR json_array_length(json_extract(COALESCE(payload_json, '{}'), '$.allowed_worker_ids')) = 0
        OR EXISTS (
          SELECT 1
          FROM json_each(json_extract(COALESCE(payload_json, '{}'), '$.allowed_worker_ids'))
          WHERE value = ?
        )
      )
    """
    worker_filter_bj_sql = """
      AND (
        json_extract(COALESCE(bj.payload_json, '{}'), '$.allowed_worker_ids') IS NULL
        OR json_array_length(json_extract(COALESCE(bj.payload_json, '{}'), '$.allowed_worker_ids')) = 0
        OR EXISTS (
          SELECT 1
          FROM json_each(json_extract(COALESCE(bj.payload_json, '{}'), '$.allowed_worker_ids'))
          WHERE value = ?
        )
      )
    """
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            f"""
            SELECT job_id
            FROM batch_jobs
            WHERE COALESCE(pause_requested, 0) = 0
              {worker_filter_sql}
              AND (
                status IN ('pending', 'interrupted')
                OR (
                  status = 'running'
                  AND (
                    (
                      lease_until IS NOT NULL
                      AND datetime(lease_until) < datetime('now')
                    )
                    OR (
                      lease_until IS NULL
                      AND (
                        heartbeat_at IS NULL
                        OR datetime(heartbeat_at) < datetime('now', ?)
                      )
                    )
                  )
                )
              )
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (worker, f"-{lease} seconds"),
        ).fetchone()
        if not row and cooperative:
            row = conn.execute(
                f"""
                SELECT bj.job_id
                FROM batch_jobs bj
                WHERE bj.job_type = 'report_generation'
                  AND bj.status = 'running'
                  AND COALESCE(bj.pause_requested, 0) = 0
                  {worker_filter_bj_sql}
                  AND EXISTS (
                    SELECT 1
                    FROM batch_job_items bi
                    WHERE bi.job_id = bj.job_id
                      AND (
                        bi.status IN ('pending', 'quota_paused')
                        OR (
                          bi.status = 'running'
                          AND bi.lease_until IS NOT NULL
                          AND datetime(bi.lease_until) < datetime('now')
                        )
                      )
                  )
                ORDER BY bj.created_at ASC
                LIMIT 1
                """,
                (worker,),
            ).fetchone()
            joined_running = bool(row)
        if not row:
            conn.commit()
            return None
        job_id = row["job_id"]
        if not joined_running:
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
                      AND (
                        (
                          lease_until IS NOT NULL
                          AND datetime(lease_until) < datetime('now')
                        )
                        OR (
                          lease_until IS NULL
                          AND (
                            heartbeat_at IS NULL
                            OR datetime(heartbeat_at) < datetime('now', ?)
                          )
                        )
                      )
                    )
                  )
                """,
                (worker, worker, token, f"+{lease} seconds", job_id, f"-{lease} seconds"),
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
    if joined_running:
        cooperative_workers = runtime.setdefault("cooperative_workers", {})
        cooperative_workers[worker] = {"joined_at": _iso_now(), "lease_token": token, "lease_seconds": lease}
    _save_runtime_state(job_id, runtime)
    log_job_event(
        job_id,
        "info",
        "job_joined" if joined_running else "job_claimed",
        "worker 已加入运行中的批量任务" if joined_running else "worker 已领取批量任务",
        {"worker_id": worker, "lease_seconds": lease, "cooperative": joined_running},
    )
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
    configured_workers = {worker["id"]: worker for worker in _load_worker_pool_config()}
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT worker_id, lease_owner, status, job_id, job_type, current_code, heartbeat_at, lease_until, updated_at,
                   payload_json, runtime_json
            FROM batch_jobs
            WHERE worker_id IS NOT NULL OR lease_owner IS NOT NULL
            ORDER BY COALESCE(heartbeat_at, updated_at, created_at) DESC
            LIMIT 200
            """
        ).fetchall()
        item_rows = conn.execute(
            """
            SELECT job_id,
                   SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                   SUM(CASE WHEN status IN ('failed', 'timeout', 'cancelled') THEN 1 ELSE 0 END) AS failed,
                   SUM(CASE WHEN status = 'waiting_snapshot' THEN 1 ELSE 0 END) AS waiting,
                   SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running,
                   SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending
            FROM batch_job_items
            GROUP BY job_id
            """
        ).fetchall()
        has_worker_heartbeats = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='batch_worker_heartbeats'"
        ).fetchone()
        heartbeat_rows = conn.execute(
            """
            SELECT *
            FROM batch_worker_heartbeats
            ORDER BY COALESCE(last_seen_at, updated_at, created_at) DESC
            LIMIT 500
            """
        ).fetchall() if has_worker_heartbeats else []
    counts_by_job = {row["job_id"]: {key: int(row[key] or 0) for key in ("completed", "failed", "waiting", "running", "pending")} for row in item_rows}
    by_worker: dict[str, dict[str, Any]] = {}
    for row in rows:
        worker_id = row["worker_id"] or row["lease_owner"] or "unknown"
        heartbeat = _parse_dt(row["heartbeat_at"])
        lease_until = _parse_dt(row["lease_until"])
        state = "offline"
        if heartbeat and heartbeat >= threshold and (not lease_until or lease_until >= now):
            state = "running" if row["status"] == "running" and row["current_code"] else "online"
        elif row["status"] == "running":
            state = "stale"
        runtime = _loads(row["runtime_json"], {})
        quota = runtime.get("quota") if isinstance(runtime, dict) else {}
        active_model = quota.get("active_model") if isinstance(quota, dict) else {}
        latest_event = (quota.get("events") or [])[-1] if isinstance(quota, dict) and quota.get("events") else {}
        config = configured_workers.get(worker_id, {})
        provider_ids = config.get("provider_ids") or _clean_str_list((_loads(row["payload_json"], {}) or {}).get("_worker_model_provider_ids"))
        model_tier = config.get("model_tier") or (_loads(row["payload_json"], {}) or {}).get("_worker_model_tier") or "deep"
        current = by_worker.get(worker_id)
        record = {
            "worker_id": worker_id,
            "name": config.get("name") or worker_id,
            "enabled": config.get("enabled", True),
            "state": state,
            "pid": "",
            "job_id": row["job_id"],
            "job_type": row["job_type"],
            "job_status": row["status"],
            "current_code": row["current_code"],
            "current_stage": "",
            "heartbeat_at": row["heartbeat_at"],
            "last_loop_at": "",
            "last_claim_at": "",
            "lease_until": row["lease_until"],
            "counts": counts_by_job.get(row["job_id"], {"completed": 0, "failed": 0, "waiting": 0, "running": 0, "pending": 0}),
            "model_pool": _public_provider_pool(provider_ids, model_tier=model_tier),
            "current_model": (active_model or {}).get("model") or quota.get("model", "") if isinstance(quota, dict) else "",
            "fallback_model": latest_event.get("fallback_model", "") if isinstance(latest_event, dict) else "",
            "last_result_json": {},
            "error": "",
            "runtime": runtime,
        }
        if not current or (record["state"] in {"running", "online"} and current["state"] not in {"running", "online"}):
            by_worker[worker_id] = record
    for row in heartbeat_rows:
        worker_id = row["worker_id"] or "unknown"
        heartbeat = _parse_dt(row["last_seen_at"])
        raw_state = row["state"] or "idle"
        config = configured_workers.get(worker_id, {})
        provider_ids = config.get("provider_ids") or _clean_str_list(_loads(row["model_provider_ids_json"], []))
        model_tier = config.get("model_tier") or row["model_tier"] or "deep"
        current_job_id = row["current_job_id"] or ""
        state = "offline"
        if heartbeat and heartbeat >= threshold:
            state = "running" if raw_state == "running" and current_job_id else "idle"
            if raw_state not in {"running", "idle", "polling", "no_job", "online"}:
                state = raw_state
        elif raw_state == "running":
            state = "stale"
        by_worker[worker_id] = {
            "worker_id": worker_id,
            "name": config.get("name") or worker_id,
            "enabled": config.get("enabled", True),
            "state": state,
            "pid": row["pid"],
            "job_id": current_job_id,
            "job_type": row["current_job_type"] or "",
            "job_status": "running" if state == "running" else "",
            "current_code": row["current_code"] or "",
            "current_stage": row["current_stage"] or "",
            "heartbeat_at": row["last_seen_at"],
            "last_loop_at": row["last_loop_at"],
            "last_claim_at": row["last_claim_at"],
            "lease_until": "",
            "counts": counts_by_job.get(current_job_id, {"completed": 0, "failed": 0, "waiting": 0, "running": 0, "pending": 0}),
            "model_pool": _public_provider_pool(provider_ids, model_tier=model_tier),
            "current_model": "",
            "fallback_model": "",
            "last_result_json": _loads(row["last_result_json"], {}),
            "error": row["error"] or "",
            "runtime": {},
        }
    for worker_id, config in configured_workers.items():
        if worker_id in by_worker:
            continue
        by_worker[worker_id] = {
            "worker_id": worker_id,
            "name": config.get("name") or worker_id,
            "enabled": bool(config.get("enabled", True)),
            "state": "not_started" if config.get("enabled") else "disabled",
            "job_id": "",
            "job_type": "",
            "job_status": "",
            "current_code": "",
            "current_stage": "",
            "heartbeat_at": "",
            "last_loop_at": "",
            "last_claim_at": "",
            "lease_until": "",
            "counts": {"completed": 0, "failed": 0, "waiting": 0, "running": 0, "pending": 0},
            "model_pool": _public_provider_pool(config.get("provider_ids") or [], model_tier=config.get("model_tier") or "deep"),
            "current_model": "",
            "fallback_model": "",
            "last_result_json": {},
            "error": "",
            "runtime": {},
        }
    workers = list(by_worker.values())
    summary = {
        "total": len(workers),
        "online": sum(1 for worker in workers if worker["state"] in {"online", "running", "idle"}),
        "running": sum(1 for worker in workers if worker["state"] == "running"),
        "idle": sum(1 for worker in workers if worker["state"] == "idle"),
        "not_started": sum(1 for worker in workers if worker["state"] == "not_started"),
        "stale": sum(1 for worker in workers if worker["state"] == "stale"),
        "offline": sum(1 for worker in workers if worker["state"] == "offline"),
        "disabled": sum(1 for worker in workers if worker["state"] == "disabled"),
        "latest_heartbeat_at": max([worker.get("heartbeat_at") or "" for worker in workers], default=""),
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


def _is_job_manual_completed(job_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT status FROM batch_jobs WHERE job_id = ?", (job_id,)).fetchone()
    return bool(row and row["status"] == MANUAL_COMPLETED_STATUS)


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


def manual_complete_job(job_id: str) -> dict[str, Any]:
    job = get_research_job(job_id)
    if job["status"] in {"cancelled", "failed", MANUAL_COMPLETED_STATUS}:
        return {"job_id": job_id, "status": job["status"], "remaining_items": 0}
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE batch_job_items
            SET status='pending',
                error='',
                started_at=NULL,
                completed_at=NULL,
                lease_owner=NULL,
                lease_token=NULL,
                lease_until=NULL,
                updated_at=datetime('now')
            WHERE job_id = ?
              AND status IN ('running', 'quota_paused')
            """,
            (job_id,),
        )
        conn.execute(
            """
            UPDATE batch_job_item_steps
            SET status='pending',
                error='',
                completed_at=NULL,
                updated_at=datetime('now')
            WHERE job_id = ?
              AND status IN ('running', 'quota_paused')
            """,
            (job_id,),
        )
        conn.execute(
            """
            UPDATE batch_jobs
            SET status=?,
                pause_requested=0,
                error='人工完成，剩余项目可继续',
                current_code='',
                lease_owner=NULL,
                lease_token=NULL,
                lease_until=NULL,
                completed_at=datetime('now'),
                updated_at=datetime('now')
            WHERE job_id = ?
            """,
            (MANUAL_COMPLETED_STATUS, job_id),
        )
        conn.commit()
        reset_running = cursor.rowcount
    counts = _recount_job(job_id)
    runtime = _runtime_state(job_id)
    runtime["manual_complete"] = {
        "state": "completed",
        "completed_at": _iso_now(),
        "reset_running_items": reset_running,
        "counts": counts,
    }
    _save_runtime_state(job_id, runtime)
    log_job_event(
        job_id,
        "info",
        "job_manual_completed",
        "用户手动完成批量任务，剩余项目保留为可继续",
        {"reset_running_items": reset_running, "counts": counts},
    )
    job = get_research_job(job_id)
    remaining = int(job["total_count"] or 0) - int(job["completed_count"] or 0) - int(job["skipped_count"] or 0)
    return {"job_id": job_id, "status": MANUAL_COMPLETED_STATUS, "remaining_items": max(0, remaining)}


def _schedule_job(job_id: str) -> None:
    asyncio.create_task(run_research_job(job_id))


async def create_research_job(
    *,
    job_type: str,
    codes: list[str] | None = None,
    report_ids: list[int] | None = None,
    allow_all: bool = False,
    group: str = "all",
    top_n: int = 0,
    skip_recent_days: int = 30,
    refresh_snapshots: bool = False,
    snapshot_concurrency: int = 3,
    analysis_mode: str = "snapshot-tradingagents",
    analysis_concurrency: int = 1,
    analysis_depth: str = "standard",
    model_mode: str = "balanced",
    snapshot_model_tier: str = "deep",
    plan_top_n: int = 10,
    multi_role: bool = False,
    stage: str = "final",
    parent_plan_id: str | None = None,
    context_strategy: str = "auto",
    model_strategy: str = "single",
    role_models: dict[str, Any] | None = None,
    allowed_worker_ids: list[str] | None = None,
    primary_provider_ids: list[str] | None = None,
    model_fallback_enabled: bool = True,
    fallback_provider_ids: list[str] | None = None,
    quota_exhausted_action: str = "switch_model",
    failure_retry_mode: str = "manual",
    title: str | None = None,
    trade_date: str | None = None,
    output_dir: Path | str | None = None,
    auto_start: bool = True,
    **extra,
) -> dict[str, Any]:
    if job_type not in JOB_TYPES:
        raise HTTPException(400, f"未知批量任务类型: {job_type}")
    clean_report_ids = [int(report_id) for report_id in (report_ids or []) if int(report_id) > 0]
    explicit_codes = [str(code).strip() for code in (codes or []) if str(code).strip()]
    if job_type in {"data_prefetch", "report_generation"} and not explicit_codes and not allow_all:
        raise HTTPException(400, "请先选择股票；如需全量批量任务，请显式启用 allow_all")
    stocks = _load_report_codes(clean_report_ids) if job_type == "position_plan" and clean_report_ids else _load_watchlist_codes(group, codes or None)
    stocks, excluded_by_permission = _filter_stocks_by_trading_permissions(stocks)
    if job_type == "position_plan" and clean_report_ids:
        clean_report_ids = [int(stock["report_id"]) for stock in stocks if stock.get("report_id")]
    if top_n > 0:
        stocks = stocks[:top_n]
    if not stocks:
        raise HTTPException(400, "没有可执行的股票；请检查交易权限设置或重新选择标的")
    job_id = f"{job_type[:2]}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:6]}"
    max_consecutive_failures = int(extra.pop("max_consecutive_failures", 5) or 0)
    max_failure_rate = float(extra.pop("max_failure_rate", 0.25) or 0)
    min_failure_rate_items = int(extra.pop("min_failure_rate_items", 5) or 1)
    payload = {
        "job_type": job_type,
        "group": group,
        "allow_all": allow_all,
        "codes": [stock["code"] for stock in stocks],
        "report_ids": clean_report_ids,
        "excluded_by_permission": excluded_by_permission,
        "top_n": top_n,
        "skip_recent_days": skip_recent_days,
        "refresh_snapshots": refresh_snapshots,
        "snapshot_concurrency": max(1, snapshot_concurrency),
        "analysis_mode": analysis_mode,
        "analysis_concurrency": max(1, analysis_concurrency),
        "analysis_depth": analysis_depth or "standard",
        "model_mode": model_mode or "balanced",
        "snapshot_model_tier": snapshot_model_tier,
        "plan_top_n": plan_top_n,
        "multi_role": multi_role,
        "stage": stage,
        "parent_plan_id": parent_plan_id,
        "context_strategy": context_strategy,
        "model_strategy": model_strategy,
        "role_models": role_models or {},
        "allowed_worker_ids": _clean_str_list(allowed_worker_ids),
        "primary_provider_ids": _clean_str_list(primary_provider_ids),
        "model_fallback_enabled": bool(model_fallback_enabled),
        "fallback_provider_ids": _clean_str_list(fallback_provider_ids),
        "quota_exhausted_action": quota_exhausted_action if quota_exhausted_action in {"switch_model", "pause"} else "switch_model",
        "failure_retry_mode": failure_retry_mode if failure_retry_mode in {"manual", "auto_switch_model", "auto_downgrade"} else "manual",
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
    if auto_start and not payload["allowed_worker_ids"] and job_type != "position_plan":
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
        _update_item_id(item["id"], status="waiting_snapshot", completed_at=_now_expr(), error="缺少完整七层快照", error_type="snapshot_incomplete")
        log_job_event(payload.get("job_id") or item["job_id"], "warning", "snapshot_missing", "缺少完整七层快照", {"code": code}, item_id=item["id"])
        return
    _update_item_id(item["id"], status="running", snapshot_id=snapshot["id"], locked_snapshot_id=snapshot["id"], started_at=_now_expr(), error="")
    log_job_event(payload.get("job_id") or item["job_id"], "info", "item_started", f"{code} 开始生成报告", {"snapshot_id": snapshot["id"]}, item_id=item["id"])
    ranked = batch_research.RankedCandidate(code, item.get("name") or code, "默认", 0, 0.0, {})
    analysis_mode = payload.get("analysis_mode") or "snapshot-tradingagents"
    report_depth = payload.get("analysis_depth") or "standard"
    report_model_mode = payload.get("model_mode") or "balanced"
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
        depth = report_depth
        model_mode = report_model_mode
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
        depth = report_depth
        model_mode = report_model_mode
    else:
        result = await batch_research._call_snapshot_llm(
            batch_research._snapshot_prompt(ranked, snapshot),
            config,
            timeout_seconds=timeout_seconds,
        )
        report_source = "snapshot_report"
        depth = report_depth
        model_mode = report_model_mode
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


def _current_position_stocks() -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT p.code,
                   COALESCE(NULLIF(p.name, ''), NULLIF(w.name, ''), p.code) AS name,
                   COALESCE(NULLIF(w.group_name, ''), '持仓') AS group_name,
                   p.total_shares,
                   p.market_value
            FROM portfolio p
            LEFT JOIN watchlist w ON w.code = p.code
            WHERE p.total_shares > 0
            ORDER BY p.market_value DESC, p.code
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _today_report_by_code(codes: list[str]) -> dict[str, dict[str, Any]]:
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, code, signal, created_at
            FROM analysis_reports
            WHERE code IN ({placeholders})
              AND date(created_at, 'localtime') = date('now', 'localtime')
            ORDER BY code ASC, datetime(created_at) DESC, id DESC
            """,
            codes,
        ).fetchall()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["code"] not in latest:
            latest[row["code"]] = dict(row)
    return latest


def _insert_or_get_position_plan_item(job_id: str, stock: dict[str, Any]) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM batch_job_items WHERE job_id = ? AND code = ? ORDER BY id ASC LIMIT 1",
            (job_id, stock["code"]),
        ).fetchone()
        if row:
            return dict(row)
        cursor = conn.execute(
            """
            INSERT INTO batch_job_items (job_id, code, name, status)
            VALUES (?, ?, ?, 'pending')
            """,
            (job_id, stock["code"], stock.get("name") or stock["code"]),
        )
        conn.execute("UPDATE batch_jobs SET total_count = total_count + 1, updated_at = datetime('now') WHERE job_id = ?", (job_id,))
        conn.commit()
        row = conn.execute("SELECT * FROM batch_job_items WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(row)


def _load_item(item_id: int) -> dict[str, Any]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM batch_job_items WHERE id = ?", (item_id,)).fetchone()
    return dict(row) if row else {}


async def _ensure_current_holding_reports(job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("include_current_positions") is False:
        return {"enabled": False, "report_ids": [], "generated_codes": [], "reused_codes": [], "failed": []}
    holdings = _current_position_stocks()
    if not holdings:
        return {"enabled": True, "report_ids": [], "generated_codes": [], "reused_codes": [], "failed": []}

    today_reports = _today_report_by_code([stock["code"] for stock in holdings])
    report_ids: list[int] = []
    generated_codes: list[str] = []
    reused_codes: list[str] = []
    failed: list[dict[str, str]] = []
    config = _primary_model_config(payload, model_tier=payload.get("_worker_model_tier") or payload.get("snapshot_model_tier") or "deep")
    report_payload = {
        **payload,
        "job_id": job_id,
        "skip_recent_days": 0,
        "analysis_mode": payload.get("holding_report_analysis_mode") or payload.get("analysis_mode") or "snapshot-tradingagents",
        "analysis_depth": payload.get("analysis_depth") or "standard",
        "model_mode": payload.get("model_mode") or "balanced",
    }

    for stock in holdings:
        code = stock["code"]
        existing = today_reports.get(code)
        if existing:
            report_ids.append(int(existing["id"]))
            reused_codes.append(code)
            continue
        item = _insert_or_get_position_plan_item(job_id, stock)
        try:
            snapshot = batch_research._latest_snapshot(DB_PATH, code)
            if not snapshot or not (snapshot.get("validation") or {}).get("ok"):
                log_job_event(job_id, "info", "holding_snapshot_started", f"{code} 持仓股缺少完整快照，开始预取", item_id=item["id"])
                await _run_data_prefetch_item(item, {**payload, "job_id": job_id})
                item = _load_item(item["id"])
            log_job_event(job_id, "info", "holding_report_started", f"{code} 持仓股当日报告缺失，开始补充生成", item_id=item["id"])
            await _run_report_item(item, report_payload, set(), config)
            item = _load_item(item["id"])
            if item.get("report_id"):
                report_ids.append(int(item["report_id"]))
                generated_codes.append(code)
                continue
            latest = _today_report_by_code([code]).get(code)
            if latest:
                report_ids.append(int(latest["id"]))
                generated_codes.append(code)
            else:
                failed.append({"code": code, "error": item.get("error") or "持仓股报告生成后未找到当日报告"})
        except Exception as exc:  # noqa: BLE001 - position planning should report exact missing holding
            failed.append({"code": code, "error": str(exc)})
            _update_item_id(item["id"], status="failed", error=str(exc), error_type=_classify_item_error("failed", str(exc)), completed_at=_now_expr())
            log_job_event(job_id, "error", "holding_report_failed", f"{code} 持仓股补充报告失败", {"error": str(exc)}, item_id=item["id"])

    return {
        "enabled": True,
        "holding_count": len(holdings),
        "report_ids": report_ids,
        "generated_count": len(generated_codes),
        "generated_codes": generated_codes,
        "reused_count": len(reused_codes),
        "reused_codes": reused_codes,
        "failed": failed,
    }


async def _run_position_plan(job_id: str, items: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    worker_id = payload.get("worker_id") or ""

    def mark_stage(stage: str) -> None:
        if worker_id:
            record_worker_heartbeat(
                worker_id,
                state="running",
                current_job_id=job_id,
                current_job_type="position_plan",
                current_stage=stage,
            )
        _update_job(job_id, heartbeat_at=_now_expr(), current_code="")

    mark_stage("补齐持仓当日报告")
    auto_holding_reports = await _ensure_current_holding_reports(job_id, payload)
    mark_stage("整理来源报告")
    base_report_ids = [int(report_id) for report_id in payload.get("report_ids") or [] if int(report_id) > 0]
    merged_report_ids = list(dict.fromkeys(base_report_ids + [int(report_id) for report_id in auto_holding_reports.get("report_ids") or [] if int(report_id) > 0]))
    if merged_report_ids:
        payload["report_ids"] = merged_report_ids
        report_stocks = _load_report_codes(merged_report_ids)
        by_code = {stock["code"]: stock for stock in report_stocks}
        for item in items:
            by_code.setdefault(item["code"], {"code": item["code"], "name": item.get("name") or item["code"], "group_name": "默认"})
        stocks = [batch_research.StockCandidate(stock["code"], stock.get("name") or stock["code"], stock.get("group_name") or "默认", 0) for stock in by_code.values()]
    else:
        stocks = [batch_research.StockCandidate(item["code"], item.get("name") or item["code"], "默认", 0) for item in items]
    output_dir = Path(payload.get("output_dir") or Path("data") / "batch_research")
    mark_stage("采集决策实时行情")
    market_context = await batch_research.collect_position_plan_market_context(stocks)
    if payload.get("multi_role"):
        config = batch_research._snapshot_llm_config(DB_PATH, model_tier=payload.get("snapshot_model_tier") or "deep")
        mark_stage("组合级多角色讨论")
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
            decision_market_context=market_context,
        )
    else:
        mark_stage("生成建仓建议")
        plan = batch_research.build_position_plan(DB_PATH, stocks, top_n=int(payload.get("plan_top_n") or 10))
        plan["decision_market_snapshot"] = market_context
    plan["auto_holding_reports"] = auto_holding_reports
    mark_stage("写出建仓建议文件")
    outputs = batch_research.write_position_plan(output_dir, plan)
    mark_stage("写入建仓建议数据库")
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
    mark_stage("建仓建议完成")
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


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return round(float(str(value).replace(",", "")), 3)
    except (TypeError, ValueError):
        return default


def _pct(value: float, total: int) -> float:
    return round(value / total * 100, 3) if total else 0.0


def _snapshot_change_pct(row: sqlite3.Row) -> float | None:
    for raw in (row["snapshot_json"], row["market_snapshot"]):
        parsed = _loads(raw, {})
        if not isinstance(parsed, dict):
            continue
        quote = (
            parsed.get("market", {}).get("quote")
            if isinstance(parsed.get("market"), dict)
            else parsed.get("quote")
        )
        if not isinstance(quote, dict):
            market_snapshot = parsed.get("snapshot")
            if isinstance(market_snapshot, dict):
                quote = market_snapshot.get("market", {}).get("quote") if isinstance(market_snapshot.get("market"), dict) else {}
        for key in ("change_pct", "pct_chg", "change_percent"):
            if isinstance(quote, dict) and quote.get(key) is not None:
                return _num(quote.get(key))
    return None


def _batch_analysis_observations(
    *,
    total: int,
    positive_signals: int,
    negative_signals: int,
    up: int,
    down: int,
    high_risk_count: int,
    top_group: str,
    market_avg_change: float,
) -> list[str]:
    observations: list[str] = []
    if total <= 0:
        return ["暂无可分析的批量报告。"]
    observations.append(f"正向信号占比 {_pct(positive_signals, total):.1f}%，负向信号占比 {_pct(negative_signals, total):.1f}%。")
    observations.append(f"样本内上涨比例 {_pct(up, total):.1f}%，下跌比例 {_pct(down, total):.1f}%。")
    if high_risk_count:
        observations.append(f"风险评分高于 60 的报告有 {high_risk_count} 份，建仓前应优先复核这些标的。")
    if top_group:
        observations.append(f"样本最集中的行业/分组是 {top_group}，需要注意组合集中度。")
    if market_avg_change > 0.3 and positive_signals >= negative_signals:
        observations.append("大盘偏强且报告信号不弱，可优先看强信号标的的执行价位。")
    elif market_avg_change < -0.3 and positive_signals:
        observations.append("大盘偏弱但仍有正向信号，建议降低首批仓位或等待回踩确认。")
    else:
        observations.append("大盘环境中性或分化，建议结合行业分组和风险评分分层筛选。")
    return observations


async def get_batch_analysis(job_id: str) -> dict[str, Any]:
    job = get_research_job(job_id)
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT bi.id AS item_id, bi.code, bi.name, bi.status, bi.report_id,
                   COALESCE(NULLIF(w.group_name, ''), '默认') AS group_name,
                   ar.signal, ar.confidence, ar.risk_score, ar.final_decision,
                   ar.trader_plan, ar.market_snapshot,
                   s.snapshot_json
            FROM batch_job_items bi
            LEFT JOIN analysis_reports ar ON ar.id = bi.report_id
            LEFT JOIN watchlist w ON w.code = bi.code
            LEFT JOIN stock_data_snapshots s ON s.id = COALESCE(bi.locked_snapshot_id, bi.snapshot_id)
            WHERE bi.job_id = ?
            ORDER BY bi.id ASC
            """,
            (job_id,),
        ).fetchall()

    signal_distribution = {signal: 0 for signal in ["STRONG_BUY", "BUY", "OVERWEIGHT", "HOLD", "UNDERWEIGHT", "SELL", "STRONG_SELL", "UNKNOWN"]}
    industry_groups: dict[str, dict[str, Any]] = {}
    up = down = flat = missing_change = 0
    confidence_values: list[float] = []
    risk_values: list[float] = []
    high_risk_count = 0
    completed = 0
    positive_signals = 0
    negative_signals = 0
    stock_rows: list[dict[str, Any]] = []

    for row in rows:
        signal = str(row["signal"] or "UNKNOWN").upper()
        if signal not in signal_distribution:
            signal = "UNKNOWN"
        signal_distribution[signal] += 1
        if row["status"] == "completed" and row["report_id"]:
            completed += 1
        if signal in {"STRONG_BUY", "BUY", "OVERWEIGHT"}:
            positive_signals += 1
        if signal in {"UNDERWEIGHT", "SELL", "STRONG_SELL"}:
            negative_signals += 1
        confidence = _num(row["confidence"], None) if row["confidence"] is not None else None
        risk = _num(row["risk_score"], None) if row["risk_score"] is not None else None
        if confidence is not None:
            confidence_values.append(confidence if confidence <= 1 else confidence / 100)
        if risk is not None:
            risk100 = risk * 100 if risk <= 1 else risk
            risk_values.append(risk100)
            if risk100 >= 60:
                high_risk_count += 1
        change_pct = _snapshot_change_pct(row)
        if change_pct is None:
            missing_change += 1
        elif change_pct > 0:
            up += 1
        elif change_pct < 0:
            down += 1
        else:
            flat += 1

        group_name = row["group_name"] or "默认"
        group = industry_groups.setdefault(
            group_name,
            {"count": 0, "positive_signals": 0, "negative_signals": 0, "avg_confidence": 0.0, "avg_risk": 0.0, "_confidence": [], "_risk": [], "avg_change_pct": 0.0, "_change": []},
        )
        group["count"] += 1
        if signal in {"STRONG_BUY", "BUY", "OVERWEIGHT"}:
            group["positive_signals"] += 1
        if signal in {"UNDERWEIGHT", "SELL", "STRONG_SELL"}:
            group["negative_signals"] += 1
        if confidence is not None:
            group["_confidence"].append(confidence if confidence <= 1 else confidence / 100)
        if risk is not None:
            group["_risk"].append(risk * 100 if risk <= 1 else risk)
        if change_pct is not None:
            group["_change"].append(change_pct)
        stock_rows.append(
            {
                "code": row["code"],
                "name": row["name"] or row["code"],
                "status": row["status"],
                "signal": signal,
                "confidence": confidence,
                "risk_score": risk,
                "change_pct": change_pct,
                "group_name": group_name,
                "report_id": row["report_id"],
            }
        )

    for group in industry_groups.values():
        group["avg_confidence"] = round(sum(group["_confidence"]) / len(group["_confidence"]), 3) if group["_confidence"] else 0.0
        group["avg_risk"] = round(sum(group["_risk"]) / len(group["_risk"]), 3) if group["_risk"] else 0.0
        group["avg_change_pct"] = round(sum(group["_change"]) / len(group["_change"]), 3) if group["_change"] else 0.0
        group.pop("_confidence", None)
        group.pop("_risk", None)
        group.pop("_change", None)

    try:
        indices = await asyncio.wait_for(quote_service.get_indices(), timeout=3)
    except Exception as exc:  # noqa: BLE001
        indices = {"error": str(exc)}
    index_items = [item for item in (indices or {}).values() if isinstance(item, dict) and item.get("change_pct") is not None]
    market_avg_change = round(sum(_num(item.get("change_pct")) for item in index_items) / len(index_items), 3) if index_items else 0.0
    market = {
        "indices": indices,
        "indices_count": len(index_items),
        "avg_change_pct": market_avg_change,
        "positive_indices": sum(1 for item in index_items if _num(item.get("change_pct")) > 0),
        "negative_indices": sum(1 for item in index_items if _num(item.get("change_pct")) < 0),
    }
    total = len(rows)
    top_group = ""
    if industry_groups:
        top_group = max(industry_groups.items(), key=lambda item: item[1]["count"])[0]
    return {
        "job_id": job_id,
        "generated_at": _iso_now(),
        "overview": {
            "name": job.get("name"),
            "job_type": job.get("job_type"),
            "status": job.get("status"),
            "total": total,
            "completed": completed,
            "failed": int(job.get("failed_count") or 0),
            "waiting": int(job.get("waiting_count") or 0),
            "avg_confidence": round(sum(confidence_values) / len(confidence_values), 3) if confidence_values else 0.0,
            "avg_risk": round(sum(risk_values) / len(risk_values), 3) if risk_values else 0.0,
        },
        "signal_distribution": signal_distribution,
        "breadth": {
            "up": up,
            "down": down,
            "flat": flat,
            "missing": missing_change,
            "up_ratio": _pct(up, total),
            "down_ratio": _pct(down, total),
        },
        "industry_groups": industry_groups,
        "market": market,
        "top_positive": sorted([item for item in stock_rows if item["signal"] in {"STRONG_BUY", "BUY", "OVERWEIGHT"}], key=lambda item: (item["confidence"] or 0), reverse=True)[:10],
        "top_risk": sorted([item for item in stock_rows if item["risk_score"] is not None], key=lambda item: item["risk_score"] or 0, reverse=True)[:10],
        "stocks": stock_rows,
        "observations": _batch_analysis_observations(
            total=total,
            positive_signals=positive_signals,
            negative_signals=negative_signals,
            up=up,
            down=down,
            high_risk_count=high_risk_count,
            top_group=top_group,
            market_avg_change=market_avg_change,
        ),
    }


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


async def run_research_job(
    job_id: str,
    *,
    worker_id: str | None = None,
    worker_model_provider_ids: list[str] | None = None,
    worker_model_tier: str | None = None,
) -> dict[str, Any]:
    job, items, payload = _load_job_for_run(job_id)
    payload["job_id"] = job_id
    if worker_id:
        payload["worker_id"] = worker_id
    if worker_model_provider_ids:
        payload["_worker_model_provider_ids"] = [str(item).strip() for item in worker_model_provider_ids if str(item).strip()]
    if worker_model_tier:
        payload["_worker_model_tier"] = worker_model_tier
    job_type = job["job_type"]
    if job.get("status") == "cancelled":
        return get_research_job(job_id)
    if job.get("status") == MANUAL_COMPLETED_STATUS:
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
                    if _is_job_manual_completed(job_id):
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
                        _update_item_id(item["id"], status="failed", error=str(exc), error_type=_classify_item_error("failed", str(exc)), completed_at=_now_expr())
                        _maybe_guard_pause(job_id, payload, error=str(exc))
                    finally:
                        _recount_job(job_id)

            await asyncio.gather(*(run_one(item) for item in runnable_items))
        elif job_type == "report_generation":
            recent_codes = batch_research.recent_report_codes(DB_PATH, int(payload.get("skip_recent_days") or 30))
            config = _primary_model_config(payload, model_tier=payload.get("_worker_model_tier") or payload.get("snapshot_model_tier") or "deep")
            _lock_job_snapshots(job_id, items)
            job, items, payload = _load_job_for_run(job_id)
            payload["job_id"] = job_id
            payload["worker_id"] = worker_id
            if worker_model_provider_ids:
                payload["_worker_model_provider_ids"] = [str(item).strip() for item in worker_model_provider_ids if str(item).strip()]
            if worker_model_tier:
                payload["_worker_model_tier"] = worker_model_tier
            requested_concurrency = max(1, int(payload.get("analysis_concurrency") or 1))
            semaphore = asyncio.Semaphore(_effective_concurrency(job_id, "llm", requested_concurrency))

            async def run_one(item: dict[str, Any]) -> None:
                async with semaphore:
                    if _is_job_cancelled(job_id):
                        if item["status"] not in TERMINAL_ITEM_STATUS:
                            _update_item_id(item["id"], status="cancelled", error="用户取消批量任务", completed_at=_now_expr())
                        return
                    if _is_job_manual_completed(job_id):
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
                        _update_item_id(item["id"], status="failed", error=str(exc), error_type=_classify_item_error("failed", str(exc)), completed_at=_now_expr())
                        _maybe_guard_pause(job_id, payload, error=str(exc))
                    finally:
                        _recount_job(job_id)

            lease_token = payload.get("lease_token") or uuid.uuid4().hex
            while True:
                if _is_job_cancelled(job_id) or _is_job_manual_completed(job_id) or _is_job_pause_requested(job_id):
                    break
                runnable_items = _claim_runnable_items(
                    job_id,
                    worker_id=worker_id,
                    lease_token=lease_token,
                    limit=max(1, requested_concurrency),
                    lease_seconds=int(payload.get("item_lease_seconds") or 900),
                )
                if not runnable_items:
                    break
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
        if _is_job_manual_completed(job_id):
            return get_research_job(job_id)
        if job_type == "report_generation" and _unfinished_item_count(job_id):
            _update_job(job_id, status="running", current_code="")
            return get_research_job(job_id)
        if job_type == "report_generation" and not _try_claim_finalize(job_id):
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
        if job_type == "report_generation":
            _mark_finalize_completed(job_id, status=final_status)
        log_job_event(job_id, "info", "job_completed", "批量任务执行结束", {"status": final_status, "counts": counts})
    except Exception as exc:
        log_job_event(job_id, "error", "job_failed", "批量任务执行异常", {"error": str(exc)})
        _update_job(job_id, status="failed", error=str(exc), completed_at=_now_expr(), current_code="")
    return get_research_job(job_id)


async def resume_job(job_id: str) -> dict[str, Any]:
    with _connect() as conn:
        job_row = conn.execute("SELECT status FROM batch_jobs WHERE job_id = ?", (job_id,)).fetchone()
        job_status = job_row["status"] if job_row else ""
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
    if runtime.get("manual_complete"):
        runtime["manual_complete"]["state"] = "resumed"
        runtime["manual_complete"]["resumed_at"] = _iso_now()
    if "quota" in runtime:
        runtime["quota"]["state"] = "resumed"
    if "guard" in runtime:
        runtime["guard"]["state"] = "resumed"
        runtime["guard"]["resumed_at"] = _iso_now()
        runtime["guard"]["consecutive_failures"] = 0
    _save_runtime_state(job_id, runtime)
    _update_job(job_id, status="pending", pause_requested=0, paused_at=None, error="", current_code="", completed_at=None)
    log_job_event(job_id, "info", "job_resumed", "批量任务继续执行")
    if job_status != MANUAL_COMPLETED_STATUS or get_research_job(job_id).get("job_type") != "position_plan":
        _schedule_job(job_id)
    return get_research_job(job_id)


async def retry_failed(
    job_id: str,
    *,
    error_type: str | None = None,
    model_provider_ids: list[str] | None = None,
    model_tier: str | None = None,
    auto_start: bool = True,
) -> dict[str, Any]:
    filters = ""
    params: list[Any] = [job_id]
    if error_type:
        filters = " AND COALESCE(error_type, 'unknown') = ?"
        params.append(error_type)
    with _connect() as conn:
        job_row = conn.execute("SELECT job_type, payload_json FROM batch_jobs WHERE job_id = ?", (job_id,)).fetchone()
        job_type = job_row["job_type"] if job_row else ""
        payload = _loads(job_row["payload_json"] if job_row else "{}", {})
        if model_provider_ids:
            payload["primary_provider_ids"] = _clean_str_list(model_provider_ids)
            payload["fallback_provider_ids"] = []
        if model_tier in {"quick", "deep"}:
            payload["snapshot_model_tier"] = model_tier
        cursor = conn.execute(
            f"""
            UPDATE batch_job_items
            SET status='pending', error='', retry_count=COALESCE(retry_count, 0) + 1, updated_at=datetime('now')
            WHERE job_id = ? AND status IN ('failed', 'timeout', 'cancelled', 'waiting_snapshot', 'quota_paused')
            {filters}
            """,
            params,
        )
        step_cursor = conn.execute(
            f"""
            UPDATE batch_job_item_steps
            SET status='pending',
                error='',
                retry_count=COALESCE(retry_count, 0) + 1,
                started_at=NULL,
                completed_at=NULL,
                updated_at=datetime('now')
            WHERE job_id = ?
              AND status IN ('failed', 'timeout', 'cancelled', 'quota_paused')
              {filters}
            """,
            params,
        )
        conn.execute(
            "UPDATE batch_jobs SET payload_json=?, status='pending', pause_requested=0, error='', updated_at=datetime('now') WHERE job_id = ?",
            (json.dumps(payload, ensure_ascii=False, default=str), job_id),
        )
        conn.commit()
        reset_count = cursor.rowcount + step_cursor.rowcount
    _recount_job(job_id)
    if auto_start and reset_count and job_type != "position_plan":
        _schedule_job(job_id)
    log_job_event(
        job_id,
        "info",
        "retry_failed",
        "已重置失败项",
        {"reset_count": reset_count, "error_type": error_type or "", "model_provider_ids": model_provider_ids or [], "model_tier": model_tier or ""},
    )
    return {"job_id": job_id, "reset_count": reset_count, "error_type": error_type or "", "status": "pending" if reset_count else get_research_job(job_id)["status"]}


async def run_worker_once(
    *,
    worker_id: str | None = None,
    stale_minutes: int = 15,
    model_provider_ids: list[str] | None = None,
    model_tier: str | None = None,
) -> dict[str, Any]:
    worker = worker_id or f"worker-{os.getpid()}"
    record_worker_heartbeat(
        worker,
        state="polling",
        model_provider_ids=model_provider_ids or [],
        model_tier=model_tier,
        current_stage="查找可领取任务",
    )
    stalled = mark_stalled_jobs(stale_minutes=stale_minutes)
    claimed = claim_next_job(worker_id=worker, lease_seconds=max(60, int(stale_minutes or 15) * 60))
    if not claimed:
        result = {"worker_id": worker, "ran": False, "stalled": stalled, "model_provider_ids": model_provider_ids or []}
        record_worker_heartbeat(
            worker,
            state="idle",
            model_provider_ids=model_provider_ids or [],
            model_tier=model_tier,
            current_stage="暂无可领取任务",
            last_result=result,
        )
        return result
    job_id = claimed["job_id"]
    record_worker_heartbeat(
        worker,
        state="running",
        model_provider_ids=model_provider_ids or [],
        model_tier=model_tier,
        current_job_id=job_id,
        current_job_type=claimed.get("job_type") or "",
        current_stage="已领取任务",
        mark_claim=True,
    )
    result = {"worker_id": worker, "ran": True, "job_id": job_id, "stalled": stalled, "model_provider_ids": model_provider_ids or []}
    try:
        await run_research_job(
            job_id,
            worker_id=worker,
            worker_model_provider_ids=model_provider_ids or [],
            worker_model_tier=model_tier,
        )
        record_worker_heartbeat(
            worker,
            state="idle",
            model_provider_ids=model_provider_ids or [],
            model_tier=model_tier,
            current_stage="任务循环完成",
            last_result=result,
        )
        return result
    except Exception as exc:
        record_worker_heartbeat(
            worker,
            state="error",
            model_provider_ids=model_provider_ids or [],
            model_tier=model_tier,
            current_job_id=job_id,
            current_job_type=claimed.get("job_type") or "",
            current_stage="任务执行异常",
            last_result={**result, "error": str(exc)},
            error=str(exc),
        )
        raise


# Compatibility wrappers for the old /api/batch-reports surface.
async def create_batch_report_job(**kwargs) -> dict[str, Any]:
    return await create_research_job(job_type="report_generation", **kwargs)


def get_job(job_id: str) -> dict[str, Any]:
    return get_research_job(job_id)


def list_jobs(limit: int = 50, status: str | None = None) -> dict[str, Any]:
    return list_research_jobs(limit=limit, status=status, job_type="report_generation")


async def run_batch_report_job(job_id: str, **_kwargs) -> dict[str, Any]:
    return await run_research_job(job_id)
