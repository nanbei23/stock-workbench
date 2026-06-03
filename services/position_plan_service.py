"""Persistent position-plan research assets."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from config import DB_PATH


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _loads(value: Any, fallback: Any):
    if value is None or value == "":
        return fallback
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return fallback


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return round(float(str(value).replace(",", "")), 3)
    except (TypeError, ValueError):
        return default


def _cash_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT key, value
        FROM settings
        WHERE key = 'cash_balance' OR key = 'cash_balance_default' OR key LIKE 'cash_balance_%'
        ORDER BY key
        """
    ).fetchall()
    balances: dict[str, float] = {}
    for row in rows:
        key = row["key"]
        if key == "cash_balance":
            account_id = "default"
        elif key == "cash_balance_default":
            account_id = "default"
        else:
            account_id = key.replace("cash_balance_", "") or "default"
        balances[account_id] = _float(row["value"])
    return {
        "balances": balances,
        "total_cash": round(sum(balances.values()), 3),
        "captured_at": datetime.now().isoformat(timespec="seconds"),
    }


def _portfolio_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT code, name, total_shares, available_shares, avg_cost, current_price,
               market_value, unrealized_pnl, unrealized_pnl_pct, account_id, updated_at
        FROM portfolio
        WHERE total_shares > 0
        ORDER BY account_id, market_value DESC, code
        """
    ).fetchall()
    positions = [dict(row) for row in rows]
    market_value = sum(_float(row.get("market_value")) for row in positions)
    return {
        "positions": positions,
        "position_count": len(positions),
        "market_value": round(market_value, 3),
        "captured_at": datetime.now().isoformat(timespec="seconds"),
    }


def _row_to_plan(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    for key, fallback in {
        "source_report_ids": [],
        "model_config_json": {},
        "role_models_json": {},
        "cash_snapshot_json": {},
        "portfolio_snapshot_json": {},
        "decision_market_snapshot_json": {},
        "risk_controls_json": [],
        "role_discussion_json": [],
        "recommendations_json": [],
        "review_result_json": {},
        "output_json": {},
        "confirmed_snapshot_json": {},
    }.items():
        item[key] = _loads(item.get(key), fallback)
    return item


def _item_payload(item: dict[str, Any]) -> tuple:
    return (
        item.get("code") or "",
        item.get("name") or item.get("code") or "",
        item.get("action") or "watch",
        _float(item.get("suggested_amount")),
        _float(item.get("position_pct")),
        _float(item.get("suggested_shares")),
        _float(item.get("confidence"), None) if item.get("confidence") is not None else None,
        _float(item.get("risk_score"), None) if item.get("risk_score") is not None else None,
        item.get("reason") or "",
        item.get("entry_plan") or "",
        item.get("stop_loss") or "",
        item.get("risk_note") or "",
        int(item["report_id"]) if item.get("report_id") else (int(item["source_report_id"]) if item.get("source_report_id") else None),
    )


def persist_position_plan(
    plan: dict[str, Any],
    *,
    db_path: Path | None = None,
    batch_job_id: str | None = None,
    payload: dict[str, Any] | None = None,
    outputs: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    outputs = outputs or {}
    recommendations = plan.get("recommendations") or []
    selected_report_ids = [int(value) for value in plan.get("selected_report_ids") or payload.get("report_ids") or [] if int(value) > 0]
    plan_id = payload.get("plan_id") or f"pp-{datetime.now().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:6]}"
    stage = payload.get("stage") or "final"
    context_strategy = plan.get("context_strategy") or payload.get("context_strategy") or "auto"
    model_config = {
        "model_strategy": payload.get("model_strategy") or "single",
        "snapshot_model_tier": payload.get("snapshot_model_tier") or "deep",
        "model_profile": payload.get("model_profile") or "",
        "review_model_profile": payload.get("review_model_profile") or "",
        "resolved_models": plan.get("model_config") or {},
    }
    output_markdown = plan.get("output_markdown") or ""
    if not output_markdown and outputs.get("markdown"):
        try:
            output_markdown = Path(outputs["markdown"]).read_text(encoding="utf-8")
        except OSError:
            output_markdown = ""
    with _connect(db_path) as conn:
        cash_snapshot = _cash_snapshot(conn)
        portfolio_snapshot = _portfolio_snapshot(conn)
        decision_market_snapshot = plan.get("decision_market_snapshot") or {}
        market_context_captured_at = decision_market_snapshot.get("captured_at") if isinstance(decision_market_snapshot, dict) else None
        conn.execute(
            """
            INSERT INTO position_plans
                (plan_id, title, status, stage, parent_plan_id, context_strategy,
                 source_report_ids, candidate_count, selected_count, model_strategy,
                 model_config_json, role_models_json, cash_snapshot_json,
                 portfolio_snapshot_json, decision_market_snapshot_json,
                 market_context_captured_at, summary, risk_controls_json,
                 role_discussion_json, recommendations_json, review_result_json,
                 output_markdown, output_json, batch_job_id)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                payload.get("title") or f"{stage} 建仓建议 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                payload.get("status") or "active",
                stage,
                payload.get("parent_plan_id") or None,
                context_strategy,
                _dumps(selected_report_ids),
                int(plan.get("candidate_count") or len(recommendations)),
                int(plan.get("selected_count") or sum(1 for item in recommendations if (item.get("action") or "").lower() in {"buy", "overweight", "add"})),
                payload.get("model_strategy") or "single",
                _dumps(model_config),
                _dumps(payload.get("role_models") or {}),
                _dumps(cash_snapshot),
                _dumps(portfolio_snapshot),
                _dumps(decision_market_snapshot),
                market_context_captured_at,
                plan.get("summary") or "",
                _dumps(plan.get("risk_controls") or []),
                _dumps(plan.get("role_discussion") or []),
                _dumps(recommendations),
                _dumps(plan.get("review_result") or {}),
                output_markdown,
                _dumps({"plan": plan, "outputs": outputs}),
                batch_job_id,
            ),
        )
        conn.executemany(
            """
            INSERT INTO position_plan_items
                (plan_id, code, name, action, suggested_amount, position_pct,
                 suggested_shares, confidence, risk_score, reason, entry_plan,
                 stop_loss, risk_note, source_report_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(plan_id, *_item_payload(item)) for item in recommendations],
        )
        conn.commit()
        row = conn.execute("SELECT * FROM position_plans WHERE plan_id = ?", (plan_id,)).fetchone()
    return _row_to_plan(row)


def _confirmed_snapshot(conn: sqlite3.Connection, plan_id: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT code, name, action, suggested_amount, position_pct, suggested_shares,
               confidence, risk_score, source_report_id
        FROM position_plan_items
        WHERE plan_id = ?
        ORDER BY id ASC
        """,
        (plan_id,),
    ).fetchall()
    return {
        "cash": _cash_snapshot(conn),
        "portfolio": _portfolio_snapshot(conn),
        "items": [dict(row) for row in rows],
        "captured_at": datetime.now().isoformat(timespec="seconds"),
    }


def adopt_position_plan(plan_id: str, *, db_path: Path | None = None, confirmed_by: str = "user") -> dict[str, Any]:
    """Mark one final-stage plan as the active formal plan used by AI performance."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM position_plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if not row:
            raise HTTPException(404, "建仓建议不存在")
        plan = _row_to_plan(row)
        if plan.get("status") == "archived":
            raise HTTPException(400, "已归档建仓计划不能采纳")
        if plan.get("stage") != "final":
            raise HTTPException(400, "只有最终建仓阶段的计划可以采纳为正式 AI 绩效基准")
        confirmed_at = datetime.now().isoformat(timespec="seconds")
        snapshot = _confirmed_snapshot(conn, plan_id)
        conn.execute(
            """
            UPDATE position_plans
            SET adoption_status = 'superseded',
                updated_at = datetime('now')
            WHERE adoption_status = 'adopted'
              AND status != 'archived'
              AND stage = 'final'
              AND plan_id != ?
            """,
            (plan_id,),
        )
        conn.execute(
            """
            UPDATE position_plans
            SET adoption_status = 'adopted',
                confirmed_at = ?,
                confirmed_by = ?,
                confirmed_snapshot_json = ?,
                updated_at = datetime('now')
            WHERE plan_id = ?
            """,
            (confirmed_at, confirmed_by, _dumps(snapshot), plan_id),
        )
        conn.commit()
        saved = conn.execute("SELECT * FROM position_plans WHERE plan_id = ?", (plan_id,)).fetchone()
    return _row_to_plan(saved)


def abandon_position_plan(plan_id: str, *, db_path: Path | None = None) -> dict[str, Any]:
    """Mark a non-adopted plan as abandoned so it is no longer actionable."""
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM position_plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if not row:
            raise HTTPException(404, "建仓建议不存在")
        plan = _row_to_plan(row)
        if plan.get("adoption_status") == "adopted":
            raise HTTPException(400, "已采纳建仓计划不能放弃")
        if plan.get("adoption_status") == "abandoned" or plan.get("status") == "abandoned":
            return plan
        conn.execute(
            """
            UPDATE position_plans
            SET status = 'abandoned',
                adoption_status = 'abandoned',
                updated_at = datetime('now')
            WHERE plan_id = ?
            """,
            (plan_id,),
        )
        conn.commit()
        saved = conn.execute("SELECT * FROM position_plans WHERE plan_id = ?", (plan_id,)).fetchone()
    return _row_to_plan(saved)


def list_position_plans(limit: int = 50, status: str | None = None, stage: str | None = None, *, db_path: Path | None = None) -> dict[str, Any]:
    where = ["1=1"]
    params: list[Any] = []
    if status:
        where.append("status = ?")
        params.append(status)
    if stage:
        where.append("stage = ?")
        params.append(stage)
    params.append(max(1, min(int(limit or 50), 200)))
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM position_plans
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    plans = [_row_to_plan(row) for row in rows]
    return {"count": len(plans), "plans": plans}


def get_position_plan(plan_id: str, *, db_path: Path | None = None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM position_plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if not row:
            raise HTTPException(404, "建仓建议不存在")
        items = conn.execute(
            "SELECT * FROM position_plan_items WHERE plan_id = ? ORDER BY id ASC",
            (plan_id,),
        ).fetchall()
    plan = _row_to_plan(row)
    plan["items"] = [dict(item) for item in items]
    return plan


def archive_position_plan(plan_id: str, *, db_path: Path | None = None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        cursor = conn.execute(
            "UPDATE position_plans SET status='archived', updated_at=datetime('now') WHERE plan_id = ?",
            (plan_id,),
        )
        conn.commit()
    if cursor.rowcount == 0:
        raise HTTPException(404, "建仓建议不存在")
    return {"status": "ok", "plan_id": plan_id}


def position_plan_markdown(plan_id: str, *, db_path: Path | None = None) -> str:
    plan = get_position_plan(plan_id, db_path=db_path)
    return plan.get("output_markdown") or ""


def list_data_snapshots(
    *,
    limit: int = 100,
    code: str | None = None,
    ok: bool | None = None,
    run_id: str | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    where = ["1=1"]
    params: list[Any] = []
    if code:
        where.append("code LIKE ?")
        params.append(f"%{code[:6]}%")
    if run_id:
        where.append("run_id = ?")
        params.append(run_id)
    params.append(max(1, min(int(limit or 100), 500)))
    with _connect(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT s.*,
                   (SELECT COUNT(*) FROM analysis_reports ar WHERE ar.market_snapshot LIKE '%' || '"snapshot_id": ' || s.id || '%') AS linked_report_count
            FROM stock_data_snapshots s
            WHERE {' AND '.join(where)}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    snapshots = []
    missing_layers: dict[str, int] = {}
    for row in rows:
        item = dict(row)
        item["validation"] = _loads(item.pop("validation_json", "{}"), {})
        item["summary"] = _loads(item.pop("summary_json", "{}"), {})
        item.pop("snapshot_json", None)
        item["ok"] = bool(item["validation"].get("ok"))
        if ok is not None and item["ok"] is not ok:
            continue
        for layer in item["validation"].get("missing_layers") or []:
            missing_layers[layer] = missing_layers.get(layer, 0) + 1
        snapshots.append(item)
    complete = sum(1 for item in snapshots if item["ok"])
    total = len(snapshots)
    return {
        "count": total,
        "summary": {
            "total": total,
            "complete": complete,
            "incomplete": total - complete,
            "complete_rate": round(complete / total * 100, 3) if total else 0.0,
            "missing_layers": dict(sorted(missing_layers.items(), key=lambda pair: (-pair[1], pair[0]))),
        },
        "snapshots": snapshots,
    }


def get_data_snapshot(snapshot_id: int, *, db_path: Path | None = None) -> dict[str, Any]:
    with _connect(db_path) as conn:
        row = conn.execute("SELECT * FROM stock_data_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    if not row:
        raise HTTPException(404, "数据快照不存在")
    item = dict(row)
    item["snapshot"] = _loads(item.pop("snapshot_json", "{}"), {})
    item["validation"] = _loads(item.pop("validation_json", "{}"), {})
    item["summary"] = _loads(item.pop("summary_json", "{}"), {})
    item["ok"] = bool(item["validation"].get("ok"))
    return item
