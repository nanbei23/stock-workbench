"""Controlled write-tool registry for Hermes console.

The LLM may suggest one of these tools, but every call is normalized and
validated here before a draft can be confirmed.
"""

from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException

from models.database import get_db
from repositories import settings_repository
from schemas.hermes_tools import HermesToolCall, HermesToolValidation
from services import portfolio_service


ALLOWED_TOOLS = {"add_watchlist", "record_trade", "set_position", "create_conditional_order"}
CONDITION_TYPES = {"price_lte", "price_gte", "change_pct_gte", "change_pct_lte"}
TRADE_DIRECTIONS = {"buy", "sell"}
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TOOL_POLICY = {
    "add_watchlist": "draft",
    "record_trade": "draft",
    "set_position": "draft",
    "create_conditional_order": "draft",
}
TOOL_RISK_LABELS = {
    "low": "低风险",
    "medium": "中风险",
    "high": "高风险",
}


def tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "tool": "add_watchlist",
            "description": "添加股票到自选股。",
            "required": ["code"],
            "args": {"code": "6位A股代码", "name": "股票名称，可空"},
        },
        {
            "tool": "record_trade",
            "description": "记录一笔买入或卖出交易，确认后会重算持仓。",
            "required": ["code", "direction", "shares", "price"],
            "args": {
                "code": "6位A股代码",
                "name": "股票名称，可空",
                "direction": "buy | sell",
                "shares": "正数股数，最多三位小数",
                "price": "正数成交价",
            },
        },
        {
            "tool": "set_position",
            "description": "把当前持仓校准到目标股数，确认后用差额交易实现。",
            "required": ["code", "shares"],
            "args": {
                "code": "6位A股代码",
                "name": "股票名称，可空",
                "shares": "非负目标持仓股数，最多三位小数",
                "price": "可选校准价；缺失时使用已有持仓均价",
            },
        },
        {
            "tool": "create_conditional_order",
            "description": "创建条件单或到价提醒。",
            "required": ["code", "trade_action", "condition_type", "target_price"],
            "args": {
                "code": "6位A股代码",
                "name": "股票名称，可空",
                "trade_action": "buy | sell",
                "condition_type": "price_lte | price_gte | change_pct_gte | change_pct_lte",
                "target_price": "正数触发价",
                "shares": "可选计划股数，默认0",
            },
        },
    ]


def tool_policy() -> dict[str, str]:
    row = settings_repository.fetch_setting("hermes_tool_policy")
    raw = row["value"] if row else ""
    try:
        parsed = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        parsed = {}
    policy = dict(DEFAULT_TOOL_POLICY)
    for tool, mode in parsed.items():
        if tool in ALLOWED_TOOLS and mode in {"draft", "disabled"}:
            policy[tool] = mode
    return policy


def update_tool_policy(policy: dict[str, Any]) -> dict[str, str]:
    current = tool_policy()
    for tool, mode in (policy or {}).items():
        if tool in ALLOWED_TOOLS and mode in {"draft", "disabled"}:
            current[tool] = mode
    settings_repository.upsert_settings({"hermes_tool_policy": json.dumps(current, ensure_ascii=False)})
    return current


def tool_permission(tool: str) -> str:
    return tool_policy().get(tool, "disabled")


def risk_level_for_tool(tool: str, args: dict[str, Any] | None = None) -> str:
    payload = args or {}
    if tool == "add_watchlist":
        return "low"
    if tool == "create_conditional_order":
        return "medium"
    if tool == "set_position":
        return "high"
    if tool == "record_trade":
        direction = str(payload.get("direction") or "").strip()
        shares = _coerce_non_negative_number(payload.get("shares")) or 0
        price = _coerce_positive_float(payload.get("price")) or 0
        if direction == "sell" or shares * price >= 100000:
            return "high"
        return "medium"
    return "high"


def risk_label(level: str) -> str:
    return TOOL_RISK_LABELS.get(level, level)


@lru_cache(maxsize=1)
def manual_context() -> str:
    manual_path = PROJECT_ROOT / "docs" / "hermes_db_write_manual.md"
    try:
        return manual_path.read_text(encoding="utf-8")
    except OSError:
        return "Hermes 只能使用 add_watchlist、record_trade、set_position、create_conditional_order 四个受控写库工具。"


def action_to_tool_call(intent: dict[str, Any]) -> HermesToolCall | None:
    action = intent.get("action")
    if action not in ALLOWED_TOOLS:
        return None
    if action == "add_watchlist":
        args = {"code": intent.get("code"), "name": intent.get("name")}
    elif action == "record_trade":
        args = {
            "code": intent.get("code"),
            "name": intent.get("name"),
            "direction": intent.get("direction"),
            "shares": intent.get("shares"),
            "price": intent.get("price"),
        }
    elif action == "set_position":
        args = {
            "code": intent.get("code"),
            "name": intent.get("name"),
            "shares": intent.get("shares"),
            "price": intent.get("price"),
        }
    else:
        args = {
            "code": intent.get("code"),
            "name": intent.get("name"),
            "trade_action": intent.get("trade_action"),
            "condition_type": intent.get("condition_type"),
            "target_price": intent.get("target_price"),
            "shares": intent.get("shares"),
        }
    return HermesToolCall(
        tool=action,
        args={key: value for key, value in args.items() if value is not None},
        confidence=intent.get("confidence"),
        reason=intent.get("reason") or "",
    )


def tool_call_to_intent(call: HermesToolCall) -> dict[str, Any]:
    args = dict(call.args or {})
    action = call.tool
    intent: dict[str, Any] = {
        "action": action,
        "code": args.get("code") or "",
        "name": args.get("name") or "",
        "reason": call.reason,
        "confidence": call.confidence,
        "tool_call": call.model_dump(),
    }
    if action == "record_trade":
        intent.update({"direction": args.get("direction"), "shares": args.get("shares"), "price": args.get("price")})
    elif action == "set_position":
        intent.update({"shares": args.get("shares"), "price": args.get("price")})
    elif action == "create_conditional_order":
        intent.update(
            {
                "trade_action": args.get("trade_action") or args.get("action"),
                "condition_type": args.get("condition_type"),
                "target_price": args.get("target_price"),
                "shares": args.get("shares") or 0,
            }
        )
    return intent


def parse_tool_call(raw: dict[str, Any]) -> HermesToolCall | None:
    tool = str(raw.get("tool") or "").strip()
    if not tool:
        return None
    args = raw.get("args") if isinstance(raw.get("args"), dict) else {
        key: value for key, value in raw.items() if key not in {"tool", "confidence", "reason"}
    }
    return HermesToolCall(
        tool=tool,
        args=args,
        confidence=_coerce_float(raw.get("confidence")),
        reason=str(raw.get("reason") or "").strip(),
    )


def validate_tool_call(call: HermesToolCall) -> HermesToolValidation:
    tool = call.tool
    blockers: list[str] = []
    warnings: list[str] = []
    args = dict(call.args or {})
    normalized: dict[str, Any] = {}

    if tool not in ALLOWED_TOOLS:
        return HermesToolValidation(valid=False, blockers=[f"不支持的工具：{tool}"], normalized_args={})

    code = _normalize_code(args.get("code"))
    if not code:
        blockers.append("缺少 6 位股票代码，无法安全写库")
    normalized["code"] = code
    normalized["name"] = str(args.get("name") or "").strip()

    if tool == "add_watchlist":
        pass
    elif tool == "record_trade":
        direction = str(args.get("direction") or "").strip()
        if direction not in TRADE_DIRECTIONS:
            blockers.append("缺少交易方向 buy/sell")
        shares = _coerce_positive_number(args.get("shares"))
        if not shares:
            blockers.append("缺少股数")
        price = _coerce_positive_float(args.get("price"))
        if not price:
            blockers.append("缺少成交价")
        normalized.update({"direction": direction, "shares": shares, "price": price})
    elif tool == "set_position":
        shares = _coerce_non_negative_number(args.get("shares"))
        if shares is None:
            blockers.append("缺少目标持仓股数")
        price = _coerce_positive_float(args.get("price"))
        if not price:
            warnings.append("未提供价格时会优先使用现有持仓均价生成校准交易。")
        normalized.update({"shares": shares, "price": price})
    elif tool == "create_conditional_order":
        trade_action = str(args.get("trade_action") or args.get("action") or "").strip()
        if trade_action not in TRADE_DIRECTIONS:
            blockers.append("缺少条件单方向 buy/sell")
        condition_type = str(args.get("condition_type") or "").strip()
        if condition_type not in CONDITION_TYPES:
            blockers.append("缺少有效触发条件")
        target_price = _coerce_positive_float(args.get("target_price"))
        if not target_price:
            blockers.append("缺少触发价格")
        shares = _coerce_non_negative_number(args.get("shares"))
        normalized.update(
            {
                "trade_action": trade_action,
                "condition_type": condition_type,
                "target_price": target_price,
                "shares": shares or 0,
            }
        )

    return HermesToolValidation(valid=not blockers, blockers=blockers, warnings=warnings, normalized_args=normalized)


async def execute_tool(tool: str, args: dict[str, Any], source_text: str = "") -> dict[str, Any]:
    if tool_permission(tool) == "disabled":
        raise HTTPException(status_code=403, detail=f"Hermes 工具已禁用：{tool}")
    call = HermesToolCall(tool=tool, args=args)
    validation = validate_tool_call(call)
    if not validation.valid:
        raise HTTPException(status_code=400, detail="; ".join(validation.blockers))
    payload = validation.normalized_args

    if tool == "add_watchlist":
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

    if tool == "record_trade":
        req = _trade_request(payload, source_text)
        return await portfolio_service.add_trade(req)

    if tool == "set_position":
        return await _execute_set_position(payload, source_text)

    if tool == "create_conditional_order":
        req = SimpleNamespace(
            code=payload["code"],
            name=payload.get("name") or payload["code"],
            condition_type=payload["condition_type"],
            target_price=float(payload["target_price"]),
            action=payload.get("trade_action") or "buy",
            shares=float(payload.get("shares") or 0),
            notes="Hermes 对话台创建",
            expires_at=None,
        )
        return await portfolio_service.create_conditional_order(req)

    raise HTTPException(status_code=400, detail=f"不支持的工具：{tool}")


async def preview_tool_call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    call = HermesToolCall(tool=tool, args=args)
    validation = validate_tool_call(call)
    if not validation.valid:
        return {
            "status": "blocked",
            "summary": "参数不完整，暂时不能预估影响。",
            "items": [{"label": "阻塞项", "value": "；".join(validation.blockers)}],
            "warnings": validation.warnings,
        }
    payload = validation.normalized_args
    if tool == "add_watchlist":
        return await _preview_add_watchlist(payload)
    if tool == "record_trade":
        return await _preview_record_trade(payload)
    if tool == "set_position":
        return await _preview_set_position(payload)
    if tool == "create_conditional_order":
        return await _preview_conditional_order(payload)
    return {"status": "blocked", "summary": f"不支持的工具：{tool}", "items": [], "warnings": []}


async def _preview_add_watchlist(payload: dict[str, Any]) -> dict[str, Any]:
    db = await get_db()
    try:
        row = await (await db.execute("SELECT * FROM watchlist WHERE code = ?", (payload["code"],))).fetchone()
    finally:
        await db.close()
    exists = dict(row) if row else None
    if exists:
        return {
            "status": "noop",
            "summary": f"{exists.get('name') or payload['code']} 已在自选，确认后不会重复新增。",
            "items": [
                {"label": "当前分组", "value": exists.get("group_name") or "默认"},
                {"label": "策略状态", "value": exists.get("strategy_state") or "watch"},
            ],
            "warnings": ["自选股使用股票代码作为主键，重复添加会被忽略。"],
        }
    return {
        "status": "ready",
        "summary": f"将把 {payload.get('name') or payload['code']} 加入默认自选分组。",
        "items": [
            {"label": "股票", "value": f"{payload.get('name') or payload['code']} {payload['code']}"},
            {"label": "分组", "value": "默认"},
        ],
        "warnings": [],
    }


async def _preview_record_trade(payload: dict[str, Any]) -> dict[str, Any]:
    current = await _position_snapshot(payload["code"])
    current_shares = round(float(current.get("total_shares") or 0), 3)
    current_cost = float(current.get("avg_cost") or 0)
    shares = round(float(payload["shares"]), 3)
    price = float(payload["price"])
    direction = payload["direction"]
    warnings: list[str] = []

    if direction == "buy":
        next_shares = current_shares + shares
        next_cost = ((current_shares * current_cost) + (shares * price)) / next_shares if next_shares else 0
        summary = f"确认后持仓预计从 {_fmt_decimal(current_shares)} 股增加到 {_fmt_decimal(next_shares)} 股。"
    else:
        if shares > current_shares:
            warnings.append("卖出数量大于当前持仓，持仓重算时会归零。")
        next_shares = max(0, current_shares - shares)
        next_cost = current_cost if next_shares else 0
        summary = f"确认后持仓预计从 {_fmt_decimal(current_shares)} 股减少到 {_fmt_decimal(next_shares)} 股。"

    return {
        "status": "ready",
        "summary": summary,
        "items": [
            {"label": "当前持仓", "value": f"{_fmt_decimal(current_shares)} 股"},
            {"label": "当前均价", "value": f"{current_cost:.4f}"},
            {"label": "交易影响", "value": f"{'买入' if direction == 'buy' else '卖出'} {_fmt_decimal(shares)} 股 @ {price:g}"},
            {"label": "预计持仓", "value": f"{_fmt_decimal(next_shares)} 股"},
            {"label": "预计均价", "value": f"{next_cost:.4f}"},
        ],
        "warnings": warnings,
    }


async def _preview_set_position(payload: dict[str, Any]) -> dict[str, Any]:
    current = await _position_snapshot(payload["code"])
    current_shares = round(float(current.get("total_shares") or 0), 3)
    target = round(float(payload["shares"]), 3)
    diff = target - current_shares
    avg_cost = float(current.get("avg_cost") or 0)
    price = payload.get("price") or avg_cost
    warnings = [] if price else ["没有提供价格，且当前也没有持仓均价，确认时会被阻止。"]
    action = "无需调整" if diff == 0 else ("补买" if diff > 0 else "补卖")
    return {
        "status": "ready" if price or diff == 0 else "blocked",
        "summary": f"确认后会通过差额交易把持仓从 {_fmt_decimal(current_shares)} 股校准到 {_fmt_decimal(target)} 股。",
        "items": [
            {"label": "当前持仓", "value": f"{_fmt_decimal(current_shares)} 股"},
            {"label": "目标持仓", "value": f"{_fmt_decimal(target)} 股"},
            {"label": "差额动作", "value": f"{action} {_fmt_decimal(abs(diff))} 股"},
            {"label": "参考价格", "value": f"{float(price):g}" if price else "缺失"},
        ],
        "warnings": warnings,
    }


async def _preview_conditional_order(payload: dict[str, Any]) -> dict[str, Any]:
    db = await get_db()
    try:
        pending_count = (
            await (
                await db.execute(
                    "SELECT COUNT(*) FROM conditional_orders WHERE code = ? AND status = 'pending'",
                    (payload["code"],),
                )
            ).fetchone()
        )[0]
        duplicate = await (
            await db.execute(
                """
                SELECT id FROM conditional_orders
                WHERE code = ? AND status = 'pending'
                  AND condition_type = ? AND target_price = ? AND action = ?
                LIMIT 1
                """,
                (payload["code"], payload["condition_type"], payload["target_price"], payload["trade_action"]),
            )
        ).fetchone()
    finally:
        await db.close()
    warnings = []
    if duplicate:
        warnings.append(f"已有相同触发条件的待执行条件单，ID {duplicate[0]}。")
    return {
        "status": "ready",
        "summary": f"确认后会新增 1 条待执行条件单；当前该股票已有 {pending_count} 条待执行条件单。",
        "items": [
            {"label": "方向", "value": "买入" if payload["trade_action"] == "buy" else "卖出"},
            {"label": "触发条件", "value": f"{payload['condition_type']} {payload['target_price']:g}"},
            {"label": "计划股数", "value": f"{_fmt_decimal(payload.get('shares'))} 股"},
            {"label": "现有待执行", "value": f"{pending_count} 条"},
        ],
        "warnings": warnings,
    }


async def _position_snapshot(code: str) -> dict[str, Any]:
    db = await get_db()
    try:
        row = await (await db.execute("SELECT * FROM portfolio WHERE code = ?", (code,))).fetchone()
        return dict(row) if row else {"code": code, "total_shares": 0, "avg_cost": 0}
    finally:
        await db.close()


async def _execute_set_position(payload: dict[str, Any], source_text: str = "") -> dict[str, Any]:
    code = payload["code"]
    target = round(float(payload["shares"]), 3)
    db = await get_db()
    try:
        row = await (await db.execute("SELECT * FROM portfolio WHERE code = ?", (code,))).fetchone()
        current = round(float(row["total_shares"]), 3) if row else 0
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
    req = _trade_request(trade_payload, source_text)
    return await portfolio_service.add_trade(req)


def _trade_request(payload: dict[str, Any], source_text: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        code=payload["code"],
        name=payload.get("name") or payload["code"],
        direction=payload["direction"],
        price=float(payload["price"]),
        shares=float(payload["shares"]),
        commission=0,
        stamp_tax=0,
        transfer_fee=0,
        notes=f"Hermes 对话台记录：{source_text}".strip(),
        trade_time=None,
    )


def _normalize_code(value: Any) -> str:
    code = str(value or "").strip()
    return code if re.fullmatch(r"[036]\d{5}", code) else ""


def _coerce_positive_number(value: Any) -> float | None:
    try:
        result = round(float(value), 3)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _coerce_non_negative_number(value: Any) -> float | None:
    try:
        result = round(float(value), 3)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _coerce_positive_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_decimal(value: Any) -> str:
    try:
        number = round(float(value or 0), 3)
    except (TypeError, ValueError):
        number = 0
    return f"{number:.3f}".rstrip("0").rstrip(".")


def compact_tool_context() -> str:
    return json.dumps(
        {
            "allowed_tools": tool_specs(),
            "manual": manual_context()[:6000],
        },
        ensure_ascii=False,
    )
