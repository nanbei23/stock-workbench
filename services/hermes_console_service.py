"""Hermes-style natural language operation console.

The console turns short Chinese commands into auditable operation drafts. Any
database write must be confirmed explicitly by draft id before execution.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import httpx
from fastapi import HTTPException

from models.database import get_db
from services import hermes_tool_registry, quote_service


_DRAFTS: dict[str, dict[str, Any]] = {}

ACTION_LABELS = {
    "add_watchlist": "添加自选股",
    "record_trade": "记录交易",
    "set_position": "校准持仓",
    "create_conditional_order": "创建条件单",
    "multi_step_plan": "多步任务计划",
    "query_position": "查询持仓",
}

CONDITION_LABELS = {
    "price_lte": "价格低于等于",
    "price_gte": "价格高于等于",
    "change_pct_gte": "涨幅高于等于",
    "change_pct_lte": "跌幅高于等于",
}

COMMON_STOCK_ALIASES = {
    "平安银行": {"code": "000001", "name": "平安银行"},
    "万科A": {"code": "000002", "name": "万科A"},
    "贵州茅台": {"code": "600519", "name": "贵州茅台"},
    "招商银行": {"code": "600036", "name": "招商银行"},
    "五粮液": {"code": "000858", "name": "五粮液"},
    "比亚迪": {"code": "002594", "name": "比亚迪"},
    "宁德时代": {"code": "300750", "name": "宁德时代"},
}


async def handle_message(message: str, session_id: str | None = None) -> dict[str, Any]:
    text = (message or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="message required")
    sid = session_id or f"hermes-{uuid.uuid4().hex[:12]}"
    intent = await _parse_message(text, sid)
    await _log_event(sid, "user", text)

    if intent["action"] == "query_position":
        result = await _query_position(intent)
        answer = result["answer"]
        await _log_event(sid, "assistant", answer, result=result)
        return {"session_id": sid, "answer": answer, "result": result, "parser": intent.get("parser", "rules")}

    if intent["action"] == "unknown":
        answer = _fallback_answer(intent.get("reason"))
        await _log_event(sid, "assistant", answer)
        return {"session_id": sid, "answer": answer, "parser": intent.get("parser", "rules")}

    draft = await _make_draft(sid, text, intent)
    _DRAFTS[draft["id"]] = draft
    if draft.get("action") == "multi_step_plan":
        await _persist_plan_task(draft)
    answer = _draft_answer(draft)
    await _log_event(sid, "assistant", answer, draft=draft)
    return {"session_id": sid, "answer": answer, "draft": draft, "parser": intent.get("parser", "rules")}


async def confirm_draft(session_id: str, draft_id: str) -> dict[str, Any]:
    draft = _DRAFTS.get(draft_id) or await _load_draft_from_history(session_id, draft_id)
    if not draft or draft.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="草稿不存在或已过期")
    if await _draft_already_executed(session_id, draft_id):
        _DRAFTS.pop(draft_id, None)
        raise HTTPException(status_code=409, detail="草稿已执行，不能重复确认")
    if await _draft_already_cancelled(session_id, draft_id):
        _DRAFTS.pop(draft_id, None)
        raise HTTPException(status_code=409, detail="草稿已取消，不能确认")
    if not draft.get("executable", True):
        raise HTTPException(status_code=400, detail="草稿缺少必要信息，不能执行")
    if draft.get("action") == "multi_step_plan":
        return await _confirm_plan_draft(session_id, draft)

    action = draft["action"]
    tool_call = draft.get("tool_call") or {}
    if not tool_call:
        mapped = hermes_tool_registry.action_to_tool_call({"action": action, **draft["payload"]})
        tool_call = mapped.model_dump() if mapped else {}
    if not tool_call:
        raise HTTPException(status_code=400, detail=f"不支持的操作：{action}")

    tool_run_id = await _log_tool_run(session_id, draft, "pending")
    try:
        result = await hermes_tool_registry.execute_tool(
            tool_call["tool"],
            tool_call.get("args") or {},
            source_text=draft.get("source_text") or "",
        )
    except Exception as exc:
        await _log_tool_run(session_id, draft, "error", error=str(exc), run_id=tool_run_id)
        raise

    result = {
        "status": "ok",
        "action": action,
        "tool": tool_call["tool"],
        "label": ACTION_LABELS.get(action, action),
        "summary": draft["summary"],
        "data": result,
    }
    await _log_tool_run(session_id, draft, "ok", result=result, run_id=tool_run_id)
    await _log_event(session_id, "tool", f"confirmed {draft_id}", draft=draft, result=result)
    _DRAFTS.pop(draft_id, None)
    return result


async def cancel_draft(session_id: str, draft_id: str) -> dict[str, Any]:
    draft = _DRAFTS.get(draft_id) or await _load_draft_from_history(session_id, draft_id)
    if not draft or draft.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="草稿不存在或已过期")
    if await _draft_already_executed(session_id, draft_id):
        _DRAFTS.pop(draft_id, None)
        raise HTTPException(status_code=409, detail="草稿已执行，不能取消")
    if await _draft_already_cancelled(session_id, draft_id):
        _DRAFTS.pop(draft_id, None)
        return {
            "status": "cancelled",
            "action": draft.get("action"),
            "label": ACTION_LABELS.get(draft.get("action"), draft.get("action")),
            "summary": draft.get("summary") or "草稿已取消",
            "draft_id": draft_id,
        }

    result = {
        "status": "cancelled",
        "action": draft.get("action"),
        "label": ACTION_LABELS.get(draft.get("action"), draft.get("action")),
        "summary": draft.get("summary") or "草稿已取消",
        "draft_id": draft_id,
    }
    await _log_tool_run(session_id, draft, "cancelled", result=result)
    await _log_event(session_id, "tool", f"cancelled {draft_id}", draft=draft, result=result)
    _DRAFTS.pop(draft_id, None)
    return result


async def confirm_plan_step(session_id: str, draft_id: str, step_id: str) -> dict[str, Any]:
    draft = await _load_active_draft(session_id, draft_id)
    if draft.get("action") != "multi_step_plan":
        raise HTTPException(status_code=400, detail="不是多步任务草稿")
    step = _find_plan_step(draft, step_id)
    if not step:
        raise HTTPException(status_code=404, detail="任务步骤不存在")
    if step.get("kind") != "write":
        raise HTTPException(status_code=400, detail="只读步骤不需要确认")
    if step.get("status") == "ok":
        raise HTTPException(status_code=409, detail="步骤已执行")
    if step.get("status") == "skipped":
        raise HTTPException(status_code=409, detail="步骤已跳过")
    if not step.get("executable", True):
        raise HTTPException(status_code=400, detail="步骤缺少必要信息，不能执行")

    step_draft = _plan_step_as_draft(draft, step)
    await _update_task_step_status(draft_id, step_id, "running")
    run_id = await _log_tool_run(session_id, step_draft, "pending")
    try:
        tool_call = step_draft["tool_call"]
        data = await hermes_tool_registry.execute_tool(
            tool_call["tool"],
            tool_call.get("args") or {},
            source_text=draft.get("source_text") or "",
        )
    except Exception as exc:
        await _log_tool_run(session_id, step_draft, "error", error=str(exc), run_id=run_id)
        await _update_task_step_status(draft_id, step_id, "error", error=str(exc))
        raise

    result = {
        "status": "ok",
        "action": step.get("action"),
        "tool": step_draft["tool_call"]["tool"],
        "summary": step.get("summary"),
        "step_id": step_id,
        "data": data,
    }
    await _log_tool_run(session_id, step_draft, "ok", result=result, run_id=run_id)
    await _update_task_step_status(draft_id, step_id, "ok", result=result)
    await _refresh_task_status(draft_id)
    await _log_event(session_id, "tool", f"confirmed {draft_id}:{step_id}", draft=draft, result=result)
    _DRAFTS[draft_id] = await _hydrate_plan_draft(draft)
    return result


async def skip_plan_step(session_id: str, draft_id: str, step_id: str) -> dict[str, Any]:
    draft = await _load_active_draft(session_id, draft_id)
    if draft.get("action") != "multi_step_plan":
        raise HTTPException(status_code=400, detail="不是多步任务草稿")
    step = _find_plan_step(draft, step_id)
    if not step:
        raise HTTPException(status_code=404, detail="任务步骤不存在")
    if step.get("kind") != "write":
        raise HTTPException(status_code=400, detail="只读步骤不需要跳过")
    if step.get("status") == "ok":
        raise HTTPException(status_code=409, detail="步骤已执行，不能跳过")

    step_draft = _plan_step_as_draft(draft, step)
    result = {
        "status": "skipped",
        "action": step.get("action"),
        "tool": (step_draft.get("tool_call") or {}).get("tool"),
        "summary": step.get("summary"),
        "step_id": step_id,
    }
    await _log_tool_run(session_id, step_draft, "skipped", result=result)
    await _update_task_step_status(draft_id, step_id, "skipped", result=result)
    await _refresh_task_status(draft_id)
    await _log_event(session_id, "tool", f"skipped {draft_id}:{step_id}", draft=draft, result=result)
    _DRAFTS[draft_id] = await _hydrate_plan_draft(draft)
    return result


async def list_session_events(session_id: str, limit: int = 20) -> dict[str, Any]:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """
            SELECT id, session_id, role, message, draft_json, result_json, created_at
            FROM hermes_console_events
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
        events = []
        for row in rows:
            item = dict(row)
            item["draft"] = _loads(item.pop("draft_json"))
            if isinstance(item["draft"], dict) and item["draft"].get("action") == "multi_step_plan":
                item["draft"] = await _hydrate_plan_draft(item["draft"])
            item["result"] = _loads(item.pop("result_json"))
            events.append(item)
        return {"session_id": session_id, "events": list(reversed(events))}
    finally:
        await db.close()


async def list_sessions(limit: int = 50) -> dict[str, Any]:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """
            SELECT
                h.session_id,
                COUNT(*) AS message_count,
                SUM(CASE WHEN h.draft_json IS NOT NULL THEN 1 ELSE 0 END) AS draft_count,
                SUM(CASE WHEN h.result_json LIKE '%"status": "ok"%' THEN 1 ELSE 0 END) AS executed_count,
                MAX(h.id) AS last_id,
                (
                    SELECT message FROM hermes_console_events hm
                    WHERE hm.session_id = h.session_id
                    ORDER BY hm.id DESC
                    LIMIT 1
                ) AS last_message,
                (
                    SELECT role FROM hermes_console_events hm
                    WHERE hm.session_id = h.session_id
                    ORDER BY hm.id DESC
                    LIMIT 1
                ) AS last_role,
                (
                    SELECT created_at FROM hermes_console_events hm
                    WHERE hm.session_id = h.session_id
                    ORDER BY hm.id DESC
                    LIMIT 1
                ) AS last_at,
                (
                    SELECT message FROM hermes_console_events hm
                    WHERE hm.session_id = h.session_id AND hm.role = 'user'
                    ORDER BY hm.id DESC
                    LIMIT 1
                ) AS title_message,
                (
                    SELECT draft_json FROM hermes_console_events hm
                    WHERE hm.session_id = h.session_id AND hm.draft_json IS NOT NULL
                    ORDER BY hm.id DESC
                    LIMIT 1
                ) AS last_draft_json,
                (
                    SELECT result_json FROM hermes_console_events hm
                    WHERE hm.session_id = h.session_id AND hm.result_json IS NOT NULL
                    ORDER BY hm.id DESC
                    LIMIT 1
                ) AS last_result_json
            FROM hermes_console_events h
            GROUP BY h.session_id
            ORDER BY last_id DESC
            LIMIT ?
            """,
            (limit,),
        )
        sessions: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            title = _session_title(item.pop("title_message") or item.get("last_message") or "")
            item["title"] = title or item["session_id"]
            item["last_draft"] = _loads(item.pop("last_draft_json"))
            if isinstance(item["last_draft"], dict) and item["last_draft"].get("action") == "multi_step_plan":
                item["last_draft"] = await _hydrate_plan_draft(item["last_draft"])
            item["last_result"] = _loads(item.pop("last_result_json"))
            item.pop("last_id", None)
            sessions.append(item)
        return {
            "count": len(sessions),
            "sessions": sessions,
        }
    finally:
        await db.close()


async def list_tool_runs(session_id: str, limit: int = 30) -> dict[str, Any]:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """
            SELECT id, session_id, draft_id, tool, args_json, status, result_json, error, created_at, confirmed_at
            FROM hermes_tool_runs
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
        runs = []
        for row in rows:
            item = dict(row)
            item["args"] = _loads(item.pop("args_json"))
            item["result"] = _loads(item.pop("result_json"))
            runs.append(item)
        return {"session_id": session_id, "runs": list(reversed(runs)), "count": len(runs)}
    finally:
        await db.close()


async def _load_active_draft(session_id: str, draft_id: str) -> dict[str, Any]:
    draft = _DRAFTS.get(draft_id) or await _load_draft_from_history(session_id, draft_id)
    if not draft or draft.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="草稿不存在或已过期")
    if await _draft_already_executed(session_id, draft_id):
        _DRAFTS.pop(draft_id, None)
        raise HTTPException(status_code=409, detail="草稿已执行，不能继续操作")
    if await _draft_already_cancelled(session_id, draft_id):
        _DRAFTS.pop(draft_id, None)
        raise HTTPException(status_code=409, detail="草稿已取消，不能继续操作")
    if draft.get("action") == "multi_step_plan":
        draft = await _hydrate_plan_draft(draft)
    return draft


def _find_plan_step(draft: dict[str, Any], step_id: str) -> dict[str, Any] | None:
    for step in draft.get("plan_steps") or []:
        if step.get("id") == step_id:
            return step
    return None


async def _persist_plan_task(draft: dict[str, Any]) -> None:
    if draft.get("action") != "multi_step_plan":
        return
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO hermes_tasks
                (task_id, session_id, draft_id, source_text, title, status, summary, draft_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(task_id) DO UPDATE SET
                status = excluded.status,
                summary = excluded.summary,
                draft_json = excluded.draft_json,
                updated_at = datetime('now')
            """,
            (
                draft["id"],
                draft["session_id"],
                draft["id"],
                draft.get("source_text") or "",
                (draft.get("payload") or {}).get("title") or draft.get("label") or "多步任务",
                "waiting_confirm" if draft.get("executable") else "blocked",
                draft.get("summary") or "",
                json.dumps(draft, ensure_ascii=False),
            ),
        )
        for step in draft.get("plan_steps") or []:
            status = "done" if step.get("kind") == "read" and step.get("status") == "done" else step.get("status") or "waiting_confirm"
            await db.execute(
                """
                INSERT INTO hermes_task_steps
                    (task_id, step_id, position, kind, action, title, summary, status,
                     payload_json, tool_json, impact_json, result_json, error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(task_id, step_id) DO UPDATE SET
                    title = excluded.title,
                    summary = excluded.summary,
                    payload_json = excluded.payload_json,
                    tool_json = excluded.tool_json,
                    impact_json = excluded.impact_json,
                    updated_at = datetime('now')
                """,
                (
                    draft["id"],
                    step.get("id"),
                    int(step.get("index") or 0),
                    step.get("kind") or "write",
                    step.get("action") or "",
                    step.get("title") or "",
                    step.get("summary") or "",
                    status,
                    json.dumps(step.get("payload") or {}, ensure_ascii=False),
                    json.dumps(step.get("tool_call") or {}, ensure_ascii=False) if step.get("tool_call") else None,
                    json.dumps(step.get("impact_preview") or {}, ensure_ascii=False) if step.get("impact_preview") else None,
                    json.dumps(step.get("result") or {}, ensure_ascii=False) if step.get("result") else None,
                    None,
                ),
            )
        await db.commit()
    finally:
        await db.close()


async def _hydrate_plan_draft(draft: dict[str, Any]) -> dict[str, Any]:
    task_id = draft.get("id")
    if not task_id:
        return draft
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """
            SELECT step_id, status, result_json, error
            FROM hermes_task_steps
            WHERE task_id = ?
            """,
            (task_id,),
        )
    finally:
        await db.close()
    if not rows:
        return draft
    by_step = {row["step_id"]: dict(row) for row in rows}
    hydrated = dict(draft)
    steps = []
    for step in draft.get("plan_steps") or []:
        item = dict(step)
        row = by_step.get(item.get("id"))
        if row:
            item["status"] = row.get("status") or item.get("status")
            result = _loads(row.get("result_json"))
            if result:
                item["result"] = result
            if row.get("error"):
                item["error"] = row["error"]
        steps.append(item)
    hydrated["plan_steps"] = steps
    return hydrated


async def _update_task_step_status(
    task_id: str,
    step_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    db = await get_db()
    try:
        await db.execute(
            """
            UPDATE hermes_task_steps
            SET status = ?,
                result_json = ?,
                error = ?,
                updated_at = datetime('now')
            WHERE task_id = ? AND step_id = ?
            """,
            (
                status,
                json.dumps(result, ensure_ascii=False) if result else None,
                error,
                task_id,
                step_id,
            ),
        )
        await db.execute("UPDATE hermes_tasks SET updated_at = datetime('now') WHERE task_id = ?", (task_id,))
        await db.commit()
    finally:
        await db.close()


async def _refresh_task_status(task_id: str) -> None:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT kind, status FROM hermes_task_steps WHERE task_id = ?",
            (task_id,),
        )
        write_statuses = [row["status"] for row in rows if row["kind"] == "write"]
        if write_statuses and all(status in {"ok", "skipped"} for status in write_statuses):
            status = "ok" if any(item == "ok" for item in write_statuses) else "skipped"
        elif any(item == "error" for item in write_statuses):
            status = "error"
        elif any(item == "running" for item in write_statuses):
            status = "running"
        else:
            status = "waiting_confirm"
        await db.execute(
            "UPDATE hermes_tasks SET status = ?, updated_at = datetime('now') WHERE task_id = ?",
            (status, task_id),
        )
        await db.commit()
    finally:
        await db.close()


async def _load_draft_from_history(session_id: str, draft_id: str) -> dict[str, Any] | None:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """
            SELECT draft_json
            FROM hermes_console_events
            WHERE session_id = ? AND draft_json IS NOT NULL
            ORDER BY id DESC
            LIMIT 200
            """,
            (session_id,),
        )
        for row in rows:
            draft = _loads(row["draft_json"])
            if isinstance(draft, dict) and draft.get("id") == draft_id:
                if draft.get("action") == "multi_step_plan":
                    return await _hydrate_plan_draft(draft)
                return draft
        return None
    finally:
        await db.close()


async def _draft_already_executed(session_id: str, draft_id: str) -> bool:
    db = await get_db()
    try:
        tool_row = await (
            await db.execute(
                """
                SELECT 1
                FROM hermes_tool_runs
                WHERE session_id = ? AND draft_id = ? AND status = 'ok'
                LIMIT 1
                """,
                (session_id, draft_id),
            )
        ).fetchone()
        if tool_row:
            return True
        rows = await db.execute_fetchall(
            """
            SELECT draft_json, result_json
            FROM hermes_console_events
            WHERE session_id = ?
              AND draft_json IS NOT NULL
              AND result_json IS NOT NULL
            ORDER BY id DESC
            LIMIT 300
            """,
            (session_id,),
        )
        for row in rows:
            draft = _loads(row["draft_json"])
            result = _loads(row["result_json"])
            if not isinstance(draft, dict) or not isinstance(result, dict):
                continue
            if draft.get("id") != draft_id or result.get("status") != "ok":
                continue
            if draft.get("action") == "multi_step_plan":
                return result.get("action") == "multi_step_plan" and not result.get("step_id")
            return True
        return False
    finally:
        await db.close()


async def _draft_already_cancelled(session_id: str, draft_id: str) -> bool:
    db = await get_db()
    try:
        tool_row = await (
            await db.execute(
                """
                SELECT 1
                FROM hermes_tool_runs
                WHERE session_id = ? AND draft_id = ? AND status = 'cancelled'
                LIMIT 1
                """,
                (session_id, draft_id),
            )
        ).fetchone()
        if tool_row:
            return True
        rows = await db.execute_fetchall(
            """
            SELECT draft_json, result_json
            FROM hermes_console_events
            WHERE session_id = ?
              AND draft_json IS NOT NULL
              AND result_json IS NOT NULL
            ORDER BY id DESC
            LIMIT 300
            """,
            (session_id,),
        )
        for row in rows:
            draft = _loads(row["draft_json"])
            result = _loads(row["result_json"])
            if not isinstance(draft, dict) or not isinstance(result, dict):
                continue
            if draft.get("id") == draft_id and result.get("status") == "cancelled":
                return True
        return False
    finally:
        await db.close()


async def _parse_message(text: str, session_id: str | None = None) -> dict[str, Any]:
    llm_intent = await _parse_message_with_llm(text, session_id)
    intent = llm_intent or await _parse_message_with_rules(text)
    return await _complete_missing_intent(intent, text)


async def _parse_message_with_rules(text: str) -> dict[str, Any]:
    plan = await _parse_multi_step_plan_with_rules(text)
    if plan:
        return plan

    stock = await _resolve_stock(text)
    shares = _extract_shares(text)
    price = _extract_price(text)

    if _looks_like_position_query(text):
        return {"action": "query_position", "parser": "rules", **stock}
    if _contains_any(text, ("条件单", "触发")) and price:
        action = "buy" if _contains_any(text, ("买入", "买", "加仓")) else "sell"
        condition_type = _condition_from_text(text, action)
        return {
            "action": "create_conditional_order",
            "parser": "rules",
            **stock,
            "trade_action": action,
            "condition_type": condition_type,
            "target_price": price,
            "shares": shares or 0,
        }
    if _contains_any(text, ("买入", "买了", "买", "加仓", "卖出", "卖了", "卖", "减仓")) and shares:
        direction = "sell" if _contains_any(text, ("卖出", "卖了", "卖", "减仓")) else "buy"
        return {
            "action": "record_trade",
            "parser": "rules",
            **stock,
            "direction": direction,
            "shares": shares,
            "price": price,
        }
    if _contains_any(text, ("设置持仓", "更新持仓", "校准持仓", "改成持仓", "持仓为", "持仓是")) and shares:
        return {"action": "set_position", "parser": "rules", **stock, "shares": shares, "price": price}
    if _contains_any(text, ("新增", "添加", "加入")) and _contains_any(text, ("自选", "股票", "关注")):
        return {"action": "add_watchlist", "parser": "rules", **stock}
    return {"action": "unknown", "parser": "rules", "reason": "暂时只能识别自选、持仓、交易和条件单类指令"}


async def _parse_multi_step_plan_with_rules(text: str) -> dict[str, Any] | None:
    separators = ("然后", "再", "并且", "同时", "，", ",", "；", ";")
    if not any(separator in text for separator in separators):
        return None
    clauses = [part.strip() for part in re.split(r"(?:然后|再|并且|同时|，|,|；|;)", text) if part.strip()]
    if len(clauses) < 2:
        return None
    steps: list[dict[str, Any]] = []
    for clause in clauses:
        intent = await _parse_message_with_rules(clause)
        if intent.get("action") == "unknown":
            continue
        steps.append(intent)
    if len(steps) < 2:
        return None
    return {
        "action": "multi_step_plan",
        "parser": "rules",
        "title": _session_title(text) or "多步任务",
        "steps": steps,
    }


async def _complete_missing_intent(intent: dict[str, Any], text: str) -> dict[str, Any]:
    if intent.get("action") == "multi_step_plan":
        completed_steps = []
        completion_sources: list[str] = []
        for step in intent.get("steps") or []:
            completed = await _complete_missing_intent(step, text)
            if completed.get("completion_sources"):
                completion_sources.extend(
                    f"第 {len(completed_steps) + 1} 步：{source}"
                    for source in completed.get("completion_sources") or []
                )
            completed_steps.append(completed)
        return {**intent, "steps": completed_steps, "completion_sources": completion_sources}

    if intent.get("action") == "unknown":
        return intent
    completed = dict(intent)
    sources: list[str] = list(completed.get("completion_sources") or [])

    if not completed.get("code"):
        candidate = await _ai_search_stock_candidate(completed.get("name") or text)
        if candidate:
            completed["code"] = candidate["code"]
            completed["name"] = candidate.get("name") or completed.get("name") or candidate["code"]
            completed["resolved"] = True
            sources.append(f"AI 搜索补全股票：{completed['name']} {completed['code']}")

    if completed.get("code") and not completed.get("name"):
        quote = await _quote_for_completion(completed["code"])
        if quote and quote.get("name"):
            completed["name"] = quote["name"]
            completed["resolved"] = True
            sources.append(f"行情补全名称：{quote['name']}")

    if completed.get("action") == "record_trade" and completed.get("code") and not completed.get("price"):
        quote = await _quote_for_completion(completed["code"])
        price = _coerce_float((quote or {}).get("price"))
        if price:
            completed["price"] = price
            sources.append(f"行情补全成交价参考：{price}")

    if sources:
        completed["completion_sources"] = sources
    return completed


async def _parse_message_with_llm(text: str, session_id: str | None = None) -> dict[str, Any] | None:
    settings = await _llm_settings()
    base_url = (settings.get("custom_endpoint") or "").strip()
    api_key = (settings.get("api_key") or "").strip()
    model = (settings.get("quick_think_model") or settings.get("deep_think_model") or "").strip()
    if not base_url or not api_key or not model:
        return None

    context = await _session_context(session_id) if session_id else []
    known = await _known_stocks()
    prompt = _llm_parse_prompt(text, context, list(known.values())[:60])
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                _chat_completions_url(base_url),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None

    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    raw = _loads(_extract_json_object(content))
    if not isinstance(raw, dict):
        return None
    intent = await _normalise_llm_intent(raw, text)
    if not intent:
        return None
    intent["parser"] = "llm"
    return intent


async def _ai_search_stock_candidate(query: str) -> dict[str, str] | None:
    text = (query or "").strip()
    if not text:
        return None
    alias = _match_common_stock(text)
    if alias:
        return alias
    settings = await _llm_settings()
    base_url = (settings.get("custom_endpoint") or "").strip()
    api_key = (settings.get("api_key") or "").strip()
    model = (settings.get("quick_think_model") or settings.get("deep_think_model") or "").strip()
    if not base_url or not api_key or not model:
        return None

    prompt = json.dumps(
        {
            "instruction": "从用户输入中搜索最可能的 A 股股票。只返回 JSON object，不要解释。",
            "schema": {"code": "6位A股代码", "name": "股票简称", "confidence": "0到1"},
            "rules": [
                "只允许沪深京常见 A 股代码格式：0/3/6 开头的 6 位数字。",
                "不确定就返回空 code，不能编造。",
                "如果用户说的是简称或常用名，返回最常见的 A 股匹配。",
            ],
            "query": text,
        },
        ensure_ascii=False,
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _chat_completions_url(base_url),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": "你是 A 股股票搜索器。只输出 JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None

    content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    raw = _loads(_extract_json_object(content))
    if not isinstance(raw, dict):
        return None
    code = str(raw.get("code") or "").strip()
    confidence = _coerce_float(raw.get("confidence")) or 0
    if not re.fullmatch(r"[036]\d{5}", code) or confidence < 0.55:
        return None
    name = str(raw.get("name") or "").strip() or code
    return {"code": code, "name": name}


async def _quote_for_completion(code: str) -> dict[str, Any] | None:
    try:
        return await quote_service.get_quote(code)
    except Exception:
        return None


async def _llm_settings() -> dict[str, str]:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT key, value FROM settings WHERE key IN (?, ?, ?, ?)",
            ("custom_endpoint", "api_key", "quick_think_model", "deep_think_model"),
        )
        return {row["key"]: row["value"] or "" for row in rows}
    except Exception:
        return {}
    finally:
        await db.close()


_LLM_SYSTEM_PROMPT = """你是股票工作台的 Hermes 自然语言操作解析器。只输出 JSON，不要输出解释。
你的任务是把用户中文输入转换为安全的结构化意图或受控工具调用。不要编造数据；不确定就留空或使用 action=unknown。
复杂请求要拆成 plan.steps。所有写库操作必须使用允许的工具格式，后端会生成草稿，用户确认后才执行。"""


def _llm_parse_prompt(text: str, context: list[dict[str, Any]], known_stocks: list[dict[str, str]]) -> str:
    schema = {
        "action": "query_position | add_watchlist | record_trade | set_position | create_conditional_order | unknown",
        "code": "6位A股代码；没有就空字符串",
        "name": "股票名称；没有就空字符串",
        "direction": "buy | sell，仅 record_trade",
        "trade_action": "buy | sell，仅 create_conditional_order",
        "shares": "股数整数；1手=100股；没有就 null",
        "price": "成交价/成本价，数字；没有就 null",
        "target_price": "条件单触发价，数字；没有就 null",
        "condition_type": "price_lte | price_gte | change_pct_gte | change_pct_lte",
        "reason": "无法识别或信息不足时的中文原因",
    }
    return json.dumps(
        {
            "instruction": "根据用户输入和最近会话上下文解析意图。单步写库请求返回 tool/args；复杂请求返回 plan.steps；查询类请求可返回 action=query_position。只返回一个 JSON object。",
            "schema": schema,
            "tool_call_schema": {
                "tool": "add_watchlist | record_trade | set_position | create_conditional_order",
                "args": "工具参数 object，必须符合 allowed_tools",
                "confidence": "0到1",
                "reason": "中文简述为什么这样解析",
            },
            "plan_schema": {
                "plan": {
                    "title": "任务标题",
                    "steps": [
                        {
                            "title": "步骤标题",
                            "action": "query_position 用于只读查询；写库步骤用 tool/args",
                            "tool": "add_watchlist | record_trade | set_position | create_conditional_order",
                            "args": "工具参数 object",
                            "requires_confirmation": "写库步骤 true，只读查询 false",
                            "reason": "中文依据",
                        }
                    ],
                }
            },
            "tool_context": _loads(hermes_tool_registry.compact_tool_context()),
            "rules": [
                "查询持仓、问今天持仓多少 => query_position。",
                "加入/关注/新增自选 => tool=add_watchlist。",
                "买入/卖出/加仓/减仓且有股数 => tool=record_trade。",
                "设置/校准/更新当前持仓为多少股 => tool=set_position。",
                "条件单/触发/到价提醒 => tool=create_conditional_order。",
                "股票代码缺失时可以利用 known_stocks 或最近上下文补全；仍不确定就 code 为空。",
                "不要把用户没有说的价格、股数、股票代码编造出来。",
                "不要返回 SQL，不要返回未列入 allowed_tools 的工具。",
                "当用户一次说多个动作，例如查询持仓、加入自选、创建条件单、记录交易混合出现时，返回 plan.steps。",
                "plan.steps 中只读查询可以 action=query_position；写库步骤必须用 tool/args。",
            ],
            "known_stocks": known_stocks,
            "recent_context": context,
            "user_message": text,
        },
        ensure_ascii=False,
    )


async def _session_context(session_id: str | None, limit: int = 8) -> list[dict[str, Any]]:
    if not session_id:
        return []
    data = await list_session_events(session_id, limit=limit)
    return [
        {
            "role": item.get("role"),
            "message": item.get("message"),
            "draft": item.get("draft"),
            "result": item.get("result"),
        }
        for item in data.get("events", [])
    ]


def _chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


async def _normalise_llm_intent(raw: dict[str, Any], text: str) -> dict[str, Any] | None:
    plan_intent = await _normalise_llm_plan(raw, text)
    if plan_intent:
        return plan_intent

    tool_call = hermes_tool_registry.parse_tool_call(raw)
    if tool_call:
        validation = hermes_tool_registry.validate_tool_call(tool_call)
        normalized_call = tool_call.model_copy(update={"args": validation.normalized_args})
        intent = hermes_tool_registry.tool_call_to_intent(normalized_call)
        intent["tool_validation"] = validation.model_dump()
        if not intent.get("name"):
            stock = await _resolve_stock_from_llm({"code": intent.get("code"), "name": ""}, text)
            intent["name"] = stock.get("name") or intent.get("name") or ""
            if intent.get("code") and not normalized_call.args.get("name"):
                intent["tool_call"]["args"]["name"] = intent["name"]
        return intent

    action = str(raw.get("action") or "unknown").strip()
    allowed = {"query_position", "add_watchlist", "record_trade", "set_position", "create_conditional_order", "unknown"}
    if action not in allowed:
        action = "unknown"
    stock = await _resolve_stock_from_llm(raw, text)
    intent: dict[str, Any] = {
        "action": action,
        **stock,
        "reason": str(raw.get("reason") or "").strip(),
    }
    shares = _coerce_int(raw.get("shares"))
    price = _coerce_float(raw.get("price"))
    target_price = _coerce_float(raw.get("target_price"))
    if action == "record_trade":
        direction = raw.get("direction")
        intent["direction"] = "sell" if direction == "sell" else "buy"
        intent["shares"] = shares
        intent["price"] = price
    elif action == "set_position":
        intent["shares"] = shares
        intent["price"] = price
    elif action == "create_conditional_order":
        trade_action = raw.get("trade_action") or raw.get("direction")
        trade_action = "sell" if trade_action == "sell" else "buy"
        condition_type = raw.get("condition_type") or _condition_from_text(text, trade_action)
        if condition_type not in CONDITION_LABELS:
            condition_type = _condition_from_text(text, trade_action)
        intent.update({
            "trade_action": trade_action,
            "condition_type": condition_type,
            "target_price": target_price or price,
            "shares": shares or 0,
        })
    return intent


async def _normalise_llm_plan(raw: dict[str, Any], text: str) -> dict[str, Any] | None:
    plan = raw.get("plan") if isinstance(raw.get("plan"), dict) else raw
    raw_steps = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(raw_steps, list) or len(raw_steps) < 2:
        return None

    steps: list[dict[str, Any]] = []
    for index, raw_step in enumerate(raw_steps, start=1):
        if not isinstance(raw_step, dict):
            continue
        step = await _normalise_llm_plan_step(raw_step, text)
        if not step or step.get("action") == "unknown":
            continue
        step["title"] = str(raw_step.get("title") or step.get("title") or _draft_summary(step["action"], step)).strip()
        step["step_index"] = index
        steps.append(step)
    if len(steps) < 2:
        return None
    return {
        "action": "multi_step_plan",
        "title": str(plan.get("title") or raw.get("title") or _session_title(text) or "多步任务").strip(),
        "reason": str(plan.get("reason") or raw.get("reason") or "").strip(),
        "steps": steps,
    }


async def _normalise_llm_plan_step(raw_step: dict[str, Any], text: str) -> dict[str, Any] | None:
    tool_call = hermes_tool_registry.parse_tool_call(raw_step)
    if tool_call:
        validation = hermes_tool_registry.validate_tool_call(tool_call)
        normalized_call = tool_call.model_copy(update={"args": validation.normalized_args})
        intent = hermes_tool_registry.tool_call_to_intent(normalized_call)
        intent["tool_validation"] = validation.model_dump()
        if not intent.get("name"):
            stock = await _resolve_stock_from_llm({"code": intent.get("code"), "name": ""}, text)
            intent["name"] = stock.get("name") or intent.get("name") or ""
            if intent.get("code") and not normalized_call.args.get("name"):
                intent["tool_call"]["args"]["name"] = intent["name"]
        return intent

    action = str(raw_step.get("action") or "unknown").strip()
    if action == "query_position":
        stock = await _resolve_stock_from_llm(raw_step, text)
        return {
            "action": "query_position",
            **stock,
            "reason": str(raw_step.get("reason") or "").strip(),
        }
    return await _normalise_llm_intent({**raw_step, "action": action}, text)


async def _resolve_stock_from_llm(raw: dict[str, Any], text: str) -> dict[str, Any]:
    code = str(raw.get("code") or "").strip()
    if not re.fullmatch(r"[036]\d{5}", code):
        code = _extract_code(text)
    name = str(raw.get("name") or "").strip()
    known = await _known_stocks()
    if code:
        found = known.get(code, {})
        return {"code": code, "name": found.get("name") or name or _extract_name_near_code(text, code), "resolved": bool(found or name)}
    if name:
        alias = _match_common_stock(name)
        if alias:
            return {**alias, "resolved": True}
        for item in known.values():
            if item.get("name") == name or name in (item.get("name") or ""):
                return {"code": item["code"], "name": item.get("name") or name, "resolved": True}
    fallback = await _resolve_stock(text)
    if name and not fallback.get("name"):
        fallback["name"] = name
    return fallback


async def _resolve_stock(text: str) -> dict[str, Any]:
    code = _extract_code(text)
    known = await _known_stocks()
    if code:
        found = known.get(code, {})
        name = found.get("name") or _extract_name_near_code(text, code) or ""
        return {"code": code, "name": name, "resolved": bool(found or name)}

    for item in known.values():
        name = item.get("name") or ""
        if name and name in text:
            return {"code": item["code"], "name": name, "resolved": True}
    alias = _match_common_stock(text)
    if alias:
        return {**alias, "resolved": True}

    return {"code": "", "name": _extract_loose_name(text), "resolved": False}


async def _known_stocks() -> dict[str, dict[str, str]]:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """
            SELECT code, name FROM watchlist
            UNION
            SELECT code, name FROM portfolio
            UNION
            SELECT code, name FROM trades
            """
        )
        return {
            row["code"]: {"code": row["code"], "name": row["name"] or ""}
            for row in rows
            if row["code"]
        }
    finally:
        await db.close()


async def _query_position(intent: dict[str, Any]) -> dict[str, Any]:
    code = intent.get("code")
    if not code:
        return {"answer": "我还没确定你问的是哪只股票。直接补一句股票代码或名称就行，比如“查 600519 持仓”。"}

    db = await get_db()
    try:
        row = await (
            await db.execute("SELECT * FROM portfolio WHERE code = ?", (code,))
        ).fetchone()
        if not row:
            name = intent.get("name") or code
            return {
                "answer": f"我查了一下，{name} {code} 现在没有持仓记录。",
                "code": code,
                "total_shares": 0,
            }
        position = dict(row)
        name = position.get("name") or intent.get("name") or code
        answer = (
            f"{name} {code} 现在持仓 {position.get('total_shares') or 0} 股，"
            f"其中可用 {position.get('available_shares') or 0} 股，"
            f"均价 {float(position.get('avg_cost') or 0):.3f}。"
        )
        return {"answer": answer, **position}
    finally:
        await db.close()


async def _make_draft(session_id: str, source_text: str, intent: dict[str, Any]) -> dict[str, Any]:
    if intent.get("action") == "multi_step_plan":
        return await _make_plan_draft(session_id, source_text, intent)
    return await _make_single_draft(session_id, source_text, intent)


async def _make_single_draft(session_id: str, source_text: str, intent: dict[str, Any]) -> dict[str, Any]:
    action = intent["action"]
    completion_sources = list(intent.get("completion_sources") or [])
    tool_call_model = hermes_tool_registry.action_to_tool_call(intent)
    tool_validation = hermes_tool_registry.validate_tool_call(tool_call_model) if tool_call_model else None
    tool_call = tool_call_model.model_dump() if tool_call_model else None
    if tool_call and tool_validation:
        tool_call["args"] = tool_validation.normalized_args
    payload = {
        k: v
        for k, v in intent.items()
        if k
        not in {
            "action",
            "reason",
            "parser",
            "completion_sources",
            "tool_call",
            "tool_validation",
            "confidence",
        }
    }
    if tool_validation:
        payload.update({key: value for key, value in tool_validation.normalized_args.items() if value is not None})
    risks = ["写库操作会改变本地 SQLite 数据，确认前请核对代码、数量和价格。"]
    if completion_sources:
        risks.append("部分信息由 AI/行情自动补全，请确认无误后再写入。")
    executable = bool(payload.get("code"))
    blockers = []
    if not payload.get("code"):
        blockers.append("缺少 6 位股票代码，无法安全写库")
    if action == "record_trade" and not payload.get("shares"):
        blockers.append("缺少股数")
        executable = False
    if action == "set_position" and payload.get("shares") is None:
        blockers.append("缺少目标持仓股数")
        executable = False
    if action in {"record_trade"} and not payload.get("price"):
        blockers.append("缺少成交价")
        executable = False
    if action == "set_position" and not payload.get("price"):
        risks.append("未提供价格时会优先使用现有持仓均价生成校准交易。")
    if action == "create_conditional_order" and not payload.get("target_price"):
        blockers.append("缺少触发价格")
        executable = False
    if tool_validation:
        blockers.extend(item for item in tool_validation.blockers if item not in blockers)
        risks.extend(item for item in tool_validation.warnings if item not in risks)
    if not tool_call:
        blockers.append(f"不支持的操作：{action}")
        executable = False

    impact_preview = (
        await hermes_tool_registry.preview_tool_call(tool_call["tool"], tool_call.get("args") or {})
        if tool_call
        else None
    )
    if impact_preview and impact_preview.get("status") == "blocked":
        blockers.append(impact_preview.get("summary") or "影响预览失败")
        executable = False

    summary = _draft_summary(action, payload)
    return {
        "id": f"draft-{uuid.uuid4().hex[:10]}",
        "session_id": session_id,
        "source_text": source_text,
        "action": action,
        "label": ACTION_LABELS.get(action, action),
        "summary": summary,
        "payload": payload,
        "tool_call": tool_call,
        "impact_preview": impact_preview,
        "parser": intent.get("parser", "rules"),
        "completion_sources": completion_sources,
        "risks": risks,
        "blockers": blockers,
        "executable": executable and not blockers,
        "requires_confirmation": True,
    }


async def _make_plan_draft(session_id: str, source_text: str, intent: dict[str, Any]) -> dict[str, Any]:
    plan_steps: list[dict[str, Any]] = []
    completion_sources = list(intent.get("completion_sources") or [])
    blockers: list[str] = []
    risks = ["多步任务会按顺序执行；只读步骤已自动预览，写库步骤确认后才执行。"]
    write_count = 0
    read_count = 0

    for index, step_intent in enumerate(intent.get("steps") or [], start=1):
        step_action = step_intent.get("action")
        step_id = f"step-{index}"
        if step_action == "query_position":
            result = await _query_position(step_intent)
            step_blockers = [] if step_intent.get("code") else ["缺少 6 位股票代码，无法查询持仓"]
            read_count += 1
            plan_steps.append(
                {
                    "id": step_id,
                    "index": index,
                    "kind": "read",
                    "action": "query_position",
                    "label": ACTION_LABELS["query_position"],
                    "title": step_intent.get("title") or "查询持仓",
                    "summary": result.get("answer") or _draft_summary("query_position", step_intent),
                    "payload": {k: v for k, v in step_intent.items() if k not in {"parser", "completion_sources"}},
                    "result": result,
                    "status": "done" if not step_blockers else "blocked",
                    "requires_confirmation": False,
                    "executable": not step_blockers,
                    "blockers": step_blockers,
                    "risks": [],
                }
            )
            blockers.extend(f"第 {index} 步：{item}" for item in step_blockers)
            continue

        step_draft = await _make_single_draft(session_id, source_text, {**step_intent, "parser": intent.get("parser", "rules")})
        write_count += 1
        step_blockers = step_draft.get("blockers") or []
        plan_steps.append(
            {
                "id": step_id,
                "index": index,
                "kind": "write",
                "action": step_draft["action"],
                "label": step_draft["label"],
                "title": step_intent.get("title") or step_draft["label"],
                "summary": step_draft["summary"],
                "payload": step_draft["payload"],
                "tool_call": step_draft.get("tool_call"),
                "impact_preview": step_draft.get("impact_preview"),
                "status": "ready" if step_draft.get("executable") else "blocked",
                "requires_confirmation": True,
                "executable": step_draft.get("executable", False),
                "blockers": step_blockers,
                "risks": step_draft.get("risks") or [],
            }
        )
        blockers.extend(f"第 {index} 步：{item}" for item in step_blockers)

    if not plan_steps:
        blockers.append("没有可执行的任务步骤")
    if not write_count:
        blockers.append("多步任务里没有需要确认的写库步骤")

    title = intent.get("title") or "多步任务"
    summary = f"{title}：共 {len(plan_steps)} 步，{read_count} 个只读预览，{write_count} 个写库步骤"
    return {
        "id": f"draft-{uuid.uuid4().hex[:10]}",
        "session_id": session_id,
        "source_text": source_text,
        "action": "multi_step_plan",
        "label": ACTION_LABELS["multi_step_plan"],
        "summary": summary,
        "payload": {"title": title, "read_count": read_count, "write_count": write_count},
        "plan_steps": plan_steps,
        "parser": intent.get("parser", "rules"),
        "completion_sources": completion_sources,
        "risks": risks,
        "blockers": blockers,
        "executable": bool(write_count) and not blockers,
        "requires_confirmation": True,
    }


async def _confirm_plan_draft(session_id: str, draft: dict[str, Any]) -> dict[str, Any]:
    step_results: list[dict[str, Any]] = []
    for step in draft.get("plan_steps") or []:
        if step.get("kind") != "write":
            step_results.append({"step_id": step.get("id"), "status": "skipped", "kind": "read", "summary": step.get("summary")})
            continue
        step_draft = _plan_step_as_draft(draft, step)
        if step.get("status") in {"ok", "skipped"} or await _draft_already_executed(session_id, step_draft["id"]):
            step_results.append({"step_id": step.get("id"), "status": "skipped", "summary": step.get("summary")})
            continue
        await _update_task_step_status(draft["id"], step.get("id"), "running")
        run_id = await _log_tool_run(session_id, step_draft, "pending")
        try:
            tool_call = step_draft["tool_call"]
            data = await hermes_tool_registry.execute_tool(
                tool_call["tool"],
                tool_call.get("args") or {},
                source_text=draft.get("source_text") or "",
            )
        except Exception as exc:
            await _log_tool_run(session_id, step_draft, "error", error=str(exc), run_id=run_id)
            await _update_task_step_status(draft["id"], step.get("id"), "error", error=str(exc))
            raise
        step_result = {
            "step_id": step.get("id"),
            "status": "ok",
            "action": step.get("action"),
            "tool": step_draft["tool_call"]["tool"],
            "summary": step.get("summary"),
            "data": data,
        }
        await _log_tool_run(session_id, step_draft, "ok", result=step_result, run_id=run_id)
        await _update_task_step_status(draft["id"], step.get("id"), "ok", result=step_result)
        step_results.append(step_result)

    await _refresh_task_status(draft["id"])
    result = {
        "status": "ok",
        "action": "multi_step_plan",
        "label": ACTION_LABELS["multi_step_plan"],
        "summary": draft["summary"],
        "steps": step_results,
    }
    await _log_event(session_id, "tool", f"confirmed {draft['id']}", draft=draft, result=result)
    _DRAFTS.pop(draft["id"], None)
    return result


def _plan_step_as_draft(plan_draft: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"{plan_draft['id']}:{step.get('id')}",
        "session_id": plan_draft["session_id"],
        "source_text": plan_draft.get("source_text") or "",
        "action": step.get("action"),
        "label": step.get("label"),
        "summary": step.get("summary"),
        "payload": step.get("payload") or {},
        "tool_call": step.get("tool_call"),
        "impact_preview": step.get("impact_preview"),
        "parser": plan_draft.get("parser"),
        "risks": step.get("risks") or [],
        "blockers": step.get("blockers") or [],
        "executable": step.get("executable", False),
        "requires_confirmation": True,
    }


def _draft_summary(action: str, payload: dict[str, Any]) -> str:
    code = payload.get("code") or "未知代码"
    name = payload.get("name") or ""
    stock = f"{name} {code}".strip()
    if action == "add_watchlist":
        return f"将 {stock} 加入自选股"
    if action == "record_trade":
        direction = "买入" if payload.get("direction") == "buy" else "卖出"
        return f"{direction} {stock} {payload.get('shares') or 0} 股，价格 {payload.get('price') or '待补充'}"
    if action == "set_position":
        return f"将 {stock} 当前持仓校准为 {payload.get('shares') or 0} 股"
    if action == "create_conditional_order":
        action_label = "买入" if payload.get("trade_action") == "buy" else "卖出"
        cond = CONDITION_LABELS.get(payload.get("condition_type"), payload.get("condition_type"))
        return f"为 {stock} 创建{action_label}条件单：{cond} {payload.get('target_price')}"
    if action == "query_position":
        return f"查询 {stock} 当前持仓"
    if action == "multi_step_plan":
        return payload.get("title") or "多步任务计划"
    return ACTION_LABELS.get(action, action)


def _draft_answer(draft: dict[str, Any]) -> str:
    summary = draft.get("summary") or draft.get("label") or "操作草稿"
    completion_note = "我已先补齐能查到的信息，右侧会标出来。" if draft.get("completion_sources") else ""
    if draft.get("executable"):
        action = draft.get("action")
        if action == "multi_step_plan":
            steps = draft.get("plan_steps") or []
            write_count = sum(1 for step in steps if step.get("kind") == "write")
            return f"我已拆成 {len(steps)} 步任务，其中 {write_count} 步需要确认写库。{completion_note}右侧可以逐步核对，确认后按顺序执行。"
        if action == "record_trade":
            return f"我理解为一笔交易：{summary}。{completion_note}你看一下右侧草稿，确认无误后再写入。"
        if action == "add_watchlist":
            return f"我先整理成自选股草稿：{summary}。{completion_note}确认后会加入自选。"
        if action == "set_position":
            return f"我理解为持仓校准：{summary}。{completion_note}这会通过差额交易调整，确认后再执行。"
        if action == "create_conditional_order":
            return f"我已经生成条件单草稿：{summary}。{completion_note}确认前请重点看触发价、方向和数量。"
        return f"我整理好了：{summary}。{completion_note}确认后再写入。"

    blockers = draft.get("blockers") or []
    if blockers:
        return f"我大概明白你的意思了，但还差一点信息：{'; '.join(blockers)}。你可以直接补充，比如股票代码、股数或成交价。"
    return "我先放到草稿里了，但还不能直接执行。请补充缺失信息后再确认。"


async def _log_event(
    session_id: str,
    role: str,
    message: str,
    draft: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO hermes_console_events (session_id, role, message, draft_json, result_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                session_id,
                role,
                message,
                json.dumps(draft, ensure_ascii=False) if draft else None,
                json.dumps(result, ensure_ascii=False) if result else None,
            ),
        )
        await db.commit()
    finally:
        await db.close()


async def _log_tool_run(
    session_id: str,
    draft: dict[str, Any],
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    run_id: int | None = None,
) -> int | None:
    tool_call = draft.get("tool_call") or {}
    if not tool_call.get("tool"):
        return None
    db = await get_db()
    try:
        if run_id:
            await db.execute(
                """
                UPDATE hermes_tool_runs
                SET status = ?,
                    result_json = ?,
                    error = ?,
                    confirmed_at = CASE WHEN ? IN ('ok', 'error') THEN datetime('now') ELSE confirmed_at END
                WHERE id = ?
                """,
                (
                    status,
                    json.dumps(result, ensure_ascii=False) if result else None,
                    error,
                    status,
                    run_id,
                ),
            )
            await db.commit()
            return run_id
        cursor = await db.execute(
            """
            INSERT INTO hermes_tool_runs
                (session_id, draft_id, tool, args_json, status, result_json, error, confirmed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CASE WHEN ? IN ('ok', 'error') THEN datetime('now') ELSE NULL END)
            """,
            (
                session_id,
                draft.get("id"),
                tool_call.get("tool"),
                json.dumps(tool_call.get("args") or {}, ensure_ascii=False),
                status,
                json.dumps(result, ensure_ascii=False) if result else None,
                error,
                status,
            ),
        )
        await db.commit()
        return cursor.lastrowid
    finally:
        await db.close()


def _extract_code(text: str) -> str:
    match = re.search(r"(?<!\d)([036]\d{5})(?!\d)", text)
    return match.group(1) if match else ""


def _extract_shares(text: str) -> int | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*(万股|手|股)", text)
    if match:
        value = float(match.group(1))
        unit = match.group(2)
    else:
        cn_match = re.search(r"([一二两三四五六七八九十]+)\s*手", text)
        if not cn_match:
            return None
        value = _cn_number(cn_match.group(1)) or 0
        unit = "手"
    if unit == "手":
        value *= 100
    elif unit == "万股":
        value *= 10000
    return int(value)


def _extract_price(text: str) -> float | None:
    patterns = [
        r"(?:价格|价位|成交价|成本价|均价|触发价|目标价|到|@)\s*([0-9]+(?:\.[0-9]+)?)",
        r"([0-9]+(?:\.[0-9]+)?)\s*元",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1))
    colloquial = re.search(r"([一二两三四五六七八九十]+)块([一二两三四五六七八九])?", text)
    if colloquial:
        whole = _cn_number(colloquial.group(1))
        decimal = _cn_number(colloquial.group(2)) if colloquial.group(2) else None
        if whole is not None:
            return float(whole) + (float(decimal) / 10 if decimal is not None else 0)
    return None


def _extract_name_near_code(text: str, code: str) -> str:
    before = re.search(r"([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9·]{1,12})\s*(?:股票)?\s*" + re.escape(code), text)
    after = re.search(re.escape(code) + r"\s*([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9·]{1,12})", text)
    before_name = _clean_name(before.group(1)) if before else ""
    after_name = _clean_name(after.group(1)) if after else ""
    return before_name or after_name


def _match_common_stock(text: str) -> dict[str, str] | None:
    compact = re.sub(r"\s+", "", text or "")
    for alias, stock in COMMON_STOCK_ALIASES.items():
        if alias in compact:
            return stock.copy()
    return None


def _extract_json_object(text: str) -> str:
    value = (text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    if value.startswith("{") and value.endswith("}"):
        return value
    start = value.find("{")
    end = value.rfind("}")
    if start >= 0 and end > start:
        return value[start : end + 1]
    return value


def _coerce_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _coerce_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cn_number(value: str | None) -> int | None:
    if not value:
        return None
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value in digits:
        return digits[value]
    if value == "十":
        return 10
    if value.startswith("十"):
        return 10 + digits.get(value[1:], 0)
    if value.endswith("十"):
        return digits.get(value[:-1], 0) * 10
    if "十" in value:
        left, right = value.split("十", 1)
        return digits.get(left, 0) * 10 + digits.get(right, 0)
    return None


def _extract_loose_name(text: str) -> str:
    cleaned = re.sub(r"(帮我把|帮我|请把|给我|记到账上|新增|添加|加入|自选|股票|买入|卖出|持仓|多少|查询|今天|设置|更新|附近)", " ", text)
    match = re.search(r"([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9·]{1,12})", cleaned)
    return _clean_name(match.group(1)) if match else ""


def _clean_name(name: str) -> str:
    if not name:
        return ""
    stop_words = (
        "帮我把", "帮我", "请把", "给我", "把", "记到账上", "今天", "附近",
        "新增", "添加", "加入", "买入", "卖出", "查询", "股票", "自选",
    )
    value = name.strip(" ，,。()（）")
    for word in stop_words:
        value = value.replace(word, "")
    return value.strip()


def _looks_like_position_query(text: str) -> bool:
    if "持仓" not in text:
        return False
    if _contains_any(text, ("设置", "更新", "校准", "改成", "持仓为", "持仓是", "录入", "买入", "卖出")):
        return False
    return _contains_any(text, ("多少", "几", "查询", "看", "?","？"))


def _condition_from_text(text: str, action: str) -> str:
    if _contains_any(text, ("高于", "大于", "突破", "涨到")):
        return "price_gte"
    if _contains_any(text, ("低于", "小于", "跌到", "回落")):
        return "price_lte"
    return "price_lte" if action == "buy" else "price_gte"


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _session_title(message: str) -> str:
    text = (message or "").strip().replace("\n", " ")
    if not text:
        return ""
    return text[:28] + ("..." if len(text) > 28 else "")


def _fallback_answer(reason: str | None = None) -> str:
    prefix = f"{reason}。" if reason else "这句话我还没转成可执行操作。"
    return (
        prefix
        + "你可以换成更接近交易动作的话，比如："
        + "“查 600519 持仓”、“把贵州茅台加入自选”、“买入平安银行两手，10.5 成交”，"
        + "或者“低于 1680 给茅台建一个买入条件单”。"
    )
