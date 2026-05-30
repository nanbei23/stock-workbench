"""Hermes-style natural language operation console.

The console turns short Chinese commands into auditable operation drafts. Any
database write must be confirmed explicitly by draft id before execution.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import HTTPException

from models.database import get_db
from services import portfolio_service


_DRAFTS: dict[str, dict[str, Any]] = {}

ACTION_LABELS = {
    "add_watchlist": "添加自选股",
    "record_trade": "记录交易",
    "set_position": "校准持仓",
    "create_conditional_order": "创建条件单",
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

    draft = _make_draft(sid, text, intent)
    _DRAFTS[draft["id"]] = draft
    answer = f"已生成草稿：{draft['summary']}。确认后才会写入数据库。"
    await _log_event(sid, "assistant", answer, draft=draft)
    return {"session_id": sid, "answer": answer, "draft": draft, "parser": intent.get("parser", "rules")}


async def confirm_draft(session_id: str, draft_id: str) -> dict[str, Any]:
    draft = _DRAFTS.get(draft_id)
    if not draft or draft.get("session_id") != session_id:
        raise HTTPException(status_code=404, detail="草稿不存在或已过期")
    if not draft.get("executable", True):
        raise HTTPException(status_code=400, detail="草稿缺少必要信息，不能执行")

    action = draft["action"]
    payload = draft["payload"]
    if action == "add_watchlist":
        result = await _execute_add_watchlist(payload)
    elif action == "record_trade":
        result = await _execute_record_trade(payload)
    elif action == "set_position":
        result = await _execute_set_position(payload)
    elif action == "create_conditional_order":
        result = await _execute_conditional_order(payload)
    else:
        raise HTTPException(status_code=400, detail=f"不支持的操作：{action}")

    result = {
        "status": "ok",
        "action": action,
        "label": ACTION_LABELS.get(action, action),
        "summary": draft["summary"],
        "data": result,
    }
    await _log_event(session_id, "tool", f"confirmed {draft_id}", draft=draft, result=result)
    _DRAFTS.pop(draft_id, None)
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
            item["last_result"] = _loads(item.pop("last_result_json"))
            item.pop("last_id", None)
            sessions.append(item)
        return {
            "count": len(sessions),
            "sessions": sessions,
        }
    finally:
        await db.close()


async def _parse_message(text: str, session_id: str | None = None) -> dict[str, Any]:
    llm_intent = await _parse_message_with_llm(text, session_id)
    if llm_intent:
        return llm_intent
    return await _parse_message_with_rules(text)


async def _parse_message_with_rules(text: str) -> dict[str, Any]:
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
你的任务是把用户中文输入转换为安全的结构化意图。不要编造数据；不确定就留空或使用 action=unknown。
所有写库操作之后仍会由用户确认，你只负责生成草稿。"""


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
            "instruction": "根据用户输入和最近会话上下文解析意图。只返回一个 JSON object，字段遵循 schema。",
            "schema": schema,
            "rules": [
                "查询持仓、问今天持仓多少 => query_position。",
                "加入/关注/新增自选 => add_watchlist。",
                "买入/卖出/加仓/减仓且有股数 => record_trade。",
                "设置/校准/更新当前持仓为多少股 => set_position。",
                "条件单/触发/到价提醒 => create_conditional_order。",
                "股票代码缺失时可以利用 known_stocks 或最近上下文补全；仍不确定就 code 为空。",
                "不要把用户没有说的价格、股数、股票代码编造出来。",
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
        return {"answer": "我还不能确定是哪只股票。请带上 6 位代码，例如：查询 600519 持仓。"}

    db = await get_db()
    try:
        row = await (
            await db.execute("SELECT * FROM portfolio WHERE code = ?", (code,))
        ).fetchone()
        if not row:
            name = intent.get("name") or code
            return {
                "answer": f"{name} {code} 当前没有持仓记录。",
                "code": code,
                "total_shares": 0,
            }
        position = dict(row)
        name = position.get("name") or intent.get("name") or code
        answer = (
            f"{name} {code} 当前持仓 {position.get('total_shares') or 0} 股，"
            f"可用 {position.get('available_shares') or 0} 股，"
            f"均价 {float(position.get('avg_cost') or 0):.3f}。"
        )
        return {"answer": answer, **position}
    finally:
        await db.close()


def _make_draft(session_id: str, source_text: str, intent: dict[str, Any]) -> dict[str, Any]:
    action = intent["action"]
    payload = {k: v for k, v in intent.items() if k not in {"action", "reason", "parser"}}
    risks = ["写库操作会改变本地 SQLite 数据，确认前请核对代码、数量和价格。"]
    executable = bool(payload.get("code"))
    blockers = []
    if not payload.get("code"):
        blockers.append("缺少 6 位股票代码，无法安全写库")
    if action in {"record_trade", "set_position"} and not payload.get("shares"):
        blockers.append("缺少股数")
        executable = False
    if action in {"record_trade"} and not payload.get("price"):
        blockers.append("缺少成交价")
        executable = False
    if action == "set_position" and not payload.get("price"):
        risks.append("未提供价格时会优先使用现有持仓均价生成校准交易。")
    if action == "create_conditional_order" and not payload.get("target_price"):
        blockers.append("缺少触发价格")
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
        "parser": intent.get("parser", "rules"),
        "risks": risks,
        "blockers": blockers,
        "executable": executable and not blockers,
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
    return ACTION_LABELS.get(action, action)


async def _execute_add_watchlist(payload: dict[str, Any]) -> dict[str, Any]:
    req = SimpleNamespace(
        code=payload["code"],
        name=payload.get("name") or payload["code"],
        group_name="默认",
        strategy_state="watch",
        target_buy_price=None,
        target_sell_price=None,
        stop_loss_price=None,
        notes=f"Hermes 对话台添加，{date.today().isoformat()}",
    )
    return await portfolio_service.add_to_watchlist(req)


async def _execute_record_trade(payload: dict[str, Any]) -> dict[str, Any]:
    req = SimpleNamespace(
        code=payload["code"],
        name=payload.get("name") or payload["code"],
        direction=payload["direction"],
        price=float(payload["price"]),
        shares=int(payload["shares"]),
        commission=0,
        stamp_tax=0,
        transfer_fee=0,
        notes=f"Hermes 对话台记录：{payload.get('source_text', '')}".strip(),
        trade_time=None,
    )
    return await portfolio_service.add_trade(req)


async def _execute_set_position(payload: dict[str, Any]) -> dict[str, Any]:
    code = payload["code"]
    target = int(payload["shares"])
    db = await get_db()
    try:
        row = await (await db.execute("SELECT * FROM portfolio WHERE code = ?", (code,))).fetchone()
        current = int(row["total_shares"]) if row else 0
        avg_cost = float(row["avg_cost"]) if row and row["avg_cost"] else 0
    finally:
        await db.close()

    diff = target - current
    if diff == 0:
        return {"status": "ok", "portfolio": {"code": code, "total_shares": current}, "message": "持仓已一致"}
    price = payload.get("price") or avg_cost
    if not price:
        raise HTTPException(status_code=400, detail="校准持仓需要价格，或已有持仓均价可作为校准价")
    trade_payload = {
        **payload,
        "direction": "buy" if diff > 0 else "sell",
        "shares": abs(diff),
        "price": float(price),
    }
    return await _execute_record_trade(trade_payload)


async def _execute_conditional_order(payload: dict[str, Any]) -> dict[str, Any]:
    req = SimpleNamespace(
        code=payload["code"],
        name=payload.get("name") or payload["code"],
        condition_type=payload["condition_type"],
        target_price=float(payload["target_price"]),
        action=payload.get("trade_action") or "buy",
        shares=int(payload.get("shares") or 0),
        notes="Hermes 对话台创建",
        expires_at=None,
    )
    return await portfolio_service.create_conditional_order(req)


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
    prefix = f"{reason}。" if reason else ""
    return (
        prefix
        + "你可以这样说：新增 600519 贵州茅台 到自选；查询 600519 持仓；"
        + "买入 600519 100 股 成交价 1680；设置 600519 持仓为 300 股 成本价 1680。"
    )
