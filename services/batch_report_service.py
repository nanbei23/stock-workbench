"""v2.8 background batch research jobs.

The service intentionally separates three long-running workflows:
- data_prefetch: write seven-layer snapshots to stock_data_snapshots
- report_generation: generate analysis_reports from existing snapshots
- position_plan: generate a position plan from existing reports
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from config import DB_PATH
from scripts import batch_research


JOB_TYPES = {"data_prefetch", "report_generation", "position_plan"}
TERMINAL_ITEM_STATUS = {"completed", "failed", "skipped", "waiting_snapshot", "cancelled"}
WAITING_STATUSES = {"waiting_snapshot"}
FAILED_STATUSES = {"failed", "timeout", "cancelled"}


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _now_expr() -> str:
    return "datetime('now')"


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
    job["items"] = [dict(item) for item in items]
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
    return {"count": len(rows), "items": [dict(row) for row in rows]}


def _schedule_job(job_id: str) -> None:
    asyncio.create_task(run_research_job(job_id))


async def create_research_job(
    *,
    job_type: str,
    codes: list[str] | None = None,
    group: str = "all",
    top_n: int = 0,
    skip_recent_days: int = 30,
    refresh_snapshots: bool = False,
    snapshot_concurrency: int = 3,
    analysis_mode: str = "snapshot",
    analysis_concurrency: int = 1,
    snapshot_model_tier: str = "deep",
    plan_top_n: int = 10,
    trade_date: str | None = None,
    output_dir: Path | str | None = None,
    auto_start: bool = True,
    **extra,
) -> dict[str, Any]:
    if job_type not in JOB_TYPES:
        raise HTTPException(400, f"未知批量任务类型: {job_type}")
    stocks = _load_watchlist_codes(group, codes or None)
    if top_n > 0:
        stocks = stocks[:top_n]
    if not stocks:
        raise HTTPException(400, "没有可执行的股票")
    job_id = f"{job_type[:2]}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:6]}"
    payload = {
        "job_type": job_type,
        "group": group,
        "codes": [stock["code"] for stock in stocks],
        "top_n": top_n,
        "skip_recent_days": skip_recent_days,
        "refresh_snapshots": refresh_snapshots,
        "snapshot_concurrency": max(1, snapshot_concurrency),
        "analysis_mode": analysis_mode,
        "analysis_concurrency": max(1, analysis_concurrency),
        "snapshot_model_tier": snapshot_model_tier,
        "plan_top_n": plan_top_n,
        "trade_date": trade_date or date.today().isoformat(),
        "output_dir": str(output_dir) if output_dir else str(Path("data") / "batch_research"),
        **extra,
    }
    _insert_job(job_id, job_type, payload, stocks)
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
    snapshot = batch_research._latest_snapshot(DB_PATH, code)
    if not snapshot or not (snapshot.get("validation") or {}).get("ok"):
        _update_item_id(item["id"], status="waiting_snapshot", completed_at=_now_expr(), error="缺少完整七层快照")
        return
    _update_item_id(item["id"], status="running", snapshot_id=snapshot["id"], started_at=_now_expr(), error="")
    ranked = batch_research.RankedCandidate(code, item.get("name") or code, "默认", 0, 0.0, {})
    result = await batch_research._call_snapshot_llm(
        batch_research._snapshot_prompt(ranked, snapshot),
        config,
        timeout_seconds=int(payload.get("timeout_seconds") or 1800),
    )
    report_id = batch_research._save_snapshot_report(
        DB_PATH,
        ranked,
        result,
        snapshot,
        run_id=payload.get("job_id") or "batch-ui",
        duration_seconds=0,
        model=config.get("model", ""),
    )
    _update_item_id(item["id"], status="completed", report_id=report_id, completed_at=_now_expr(), error="")


async def _run_position_plan(job_id: str, items: list[dict[str, Any]], payload: dict[str, Any]) -> dict[str, Any]:
    stocks = [batch_research.StockCandidate(item["code"], item.get("name") or item["code"], "默认", 0) for item in items]
    output_dir = Path(payload.get("output_dir") or Path("data") / "batch_research")
    plan = batch_research.build_position_plan(DB_PATH, stocks, top_n=int(payload.get("plan_top_n") or 10))
    outputs = batch_research.write_position_plan(output_dir, plan)
    reports = {item["code"]: item for item in plan.get("recommendations", []) if item.get("report_id")}
    for item in items:
        report = reports.get(item["code"])
        if report:
            _update_item_id(item["id"], status="completed", report_id=report.get("report_id"), completed_at=_now_expr())
        else:
            _update_item_id(item["id"], status="waiting_snapshot", error="缺少分析报告", completed_at=_now_expr())
    return {"plan": plan, "outputs": outputs}


async def run_research_job(job_id: str) -> dict[str, Any]:
    job, items, payload = _load_job_for_run(job_id)
    payload["job_id"] = job_id
    job_type = job["job_type"]
    _update_job(job_id, status="running", started_at=job.get("started_at") or _now_expr(), error="", current_code="")
    result: dict[str, Any] = {}
    try:
        runnable_items = [item for item in items if item["status"] in {"pending", "failed", "running"}]
        if job_type == "position_plan":
            result = await _run_position_plan(job_id, items, payload)
        elif job_type == "data_prefetch":
            semaphore = asyncio.Semaphore(max(1, int(payload.get("snapshot_concurrency") or 3)))

            async def run_one(item: dict[str, Any]) -> None:
                async with semaphore:
                    _update_job(job_id, current_code=item["code"])
                    try:
                        await _run_data_prefetch_item(item, payload)
                    except Exception as exc:
                        _update_item_id(item["id"], status="failed", error=str(exc), completed_at=_now_expr())
                    finally:
                        _recount_job(job_id)

            await asyncio.gather(*(run_one(item) for item in runnable_items))
        elif job_type == "report_generation":
            recent_codes = batch_research.recent_report_codes(DB_PATH, int(payload.get("skip_recent_days") or 30))
            config = batch_research._snapshot_llm_config(DB_PATH, model_tier=payload.get("snapshot_model_tier") or "deep")
            semaphore = asyncio.Semaphore(max(1, int(payload.get("analysis_concurrency") or 1)))

            async def run_one(item: dict[str, Any]) -> None:
                async with semaphore:
                    _update_job(job_id, current_code=item["code"])
                    try:
                        await _run_report_item(item, payload, recent_codes, config)
                    except Exception as exc:
                        _update_item_id(item["id"], status="failed", error=str(exc), completed_at=_now_expr())
                    finally:
                        _recount_job(job_id)

            await asyncio.gather(*(run_one(item) for item in runnable_items))
        counts = _recount_job(job_id)
        final_status = "failed" if counts["failed_count"] and not counts["completed_count"] and not counts["skipped_count"] else "completed"
        if counts["waiting_count"] and not counts["completed_count"] and not counts["skipped_count"]:
            final_status = "failed"
        _update_job(
            job_id,
            status=final_status,
            completed_at=_now_expr(),
            current_code="",
            result_json=json.dumps({"counts": counts, **result}, ensure_ascii=False, default=str),
        )
    except Exception as exc:
        _update_job(job_id, status="failed", error=str(exc), completed_at=_now_expr(), current_code="")
    return get_research_job(job_id)


async def resume_job(job_id: str) -> dict[str, Any]:
    _update_job(job_id, status="pending", error="", current_code="")
    _schedule_job(job_id)
    return get_research_job(job_id)


async def retry_failed(job_id: str, *, auto_start: bool = True) -> dict[str, Any]:
    with _connect() as conn:
        cursor = conn.execute(
            """
            UPDATE batch_job_items
            SET status='pending', error='', retry_count=COALESCE(retry_count, 0) + 1, updated_at=datetime('now')
            WHERE job_id = ? AND status IN ('failed', 'timeout', 'cancelled', 'waiting_snapshot')
            """,
            (job_id,),
        )
        conn.commit()
        reset_count = cursor.rowcount
    _recount_job(job_id)
    if auto_start and reset_count:
        _schedule_job(job_id)
    return {"job_id": job_id, "reset_count": reset_count, "status": "pending" if reset_count else get_research_job(job_id)["status"]}


# Compatibility wrappers for the old /api/batch-reports surface.
async def create_batch_report_job(**kwargs) -> dict[str, Any]:
    return await create_research_job(job_type="report_generation", **kwargs)


def get_job(job_id: str) -> dict[str, Any]:
    return get_research_job(job_id)


def list_jobs(limit: int = 50, status: str | None = None) -> dict[str, Any]:
    return list_research_jobs(limit=limit, status=status, job_type="report_generation")


async def run_batch_report_job(job_id: str, **_kwargs) -> dict[str, Any]:
    return await run_research_job(job_id)
