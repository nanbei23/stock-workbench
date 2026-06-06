"""Bind AI suggestions to real trades and classify execution deviation."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

import models.database as database

BUY_ACTIONS = {"add", "buy", "overweight"}
SELL_ACTIONS = {"sell", "reduce", "underweight", "take_profit"}
NO_TRADE_ACTIONS = {"hold", "watch", "forbid_buy", "avoid"}
EXECUTION_LABELS = {
    "full_executed": "完全执行",
    "partial_executed": "部分执行",
    "over_executed": "超额执行",
    "not_executed": "未执行",
    "reverse_executed": "反向执行",
    "mixed_execution": "混合执行",
    "complied_no_trade": "遵守不交易",
    "discretionary_trade": "自主交易",
    "violated": "违反建议",
}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None):
            return default
        return round(float(value), 3)
    except (TypeError, ValueError):
        return default


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("T", " ")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    if len(text) == 10:
        text = f"{text} 00:00:00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _day_start(value: Any) -> datetime:
    parsed = _parse_dt(value) or datetime.now()
    return parsed.replace(hour=0, minute=0, second=0, microsecond=0)


def _row_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def _expected_direction(action: str) -> str:
    clean = str(action or "").lower()
    if clean in BUY_ACTIONS:
        return "buy"
    if clean in SELL_ACTIONS:
        return "sell"
    return "none"


def _trade_amount(trade: dict[str, Any]) -> float:
    amount = _num(trade.get("amount"))
    if amount:
        return amount
    total_cost = _num(trade.get("total_cost"))
    if total_cost:
        return total_cost
    return round(_num(trade.get("price")) * _num(trade.get("shares")), 3)


def _trade_window(source_time: Any, *, days: int) -> tuple[datetime, datetime]:
    start = _day_start(source_time)
    return start, start + timedelta(days=days)


def _in_window(trade: dict[str, Any], start: datetime, end: datetime) -> bool:
    trade_at = _parse_dt(trade.get("trade_time"))
    if not trade_at:
        return False
    return start <= trade_at < end


def _match_trades(item: dict[str, Any], trades_by_code: dict[str, list[dict[str, Any]]], *, days: int) -> list[dict[str, Any]]:
    start, end = _trade_window(item.get("source_time") or item.get("date") or item.get("created_at"), days=days)
    account_id = str(item.get("account_id") or "default")
    return [
        trade
        for trade in trades_by_code.get(str(item.get("code") or ""), [])
        if str(trade.get("account_id") or "default") == account_id and _in_window(trade, start, end)
    ]


def classify_execution(action: str, suggested_amount: Any, trades: list[dict[str, Any]]) -> dict[str, Any]:
    expected = _expected_direction(action)
    buy_trades = [trade for trade in trades if str(trade.get("direction") or "").lower() == "buy"]
    sell_trades = [trade for trade in trades if str(trade.get("direction") or "").lower() == "sell"]
    buy_amount = round(sum(_trade_amount(trade) for trade in buy_trades), 3)
    sell_amount = round(sum(_trade_amount(trade) for trade in sell_trades), 3)
    expected_amount = _num(suggested_amount)
    expected_trade_amount = buy_amount if expected == "buy" else sell_amount if expected == "sell" else 0.0
    opposite_amount = sell_amount if expected == "buy" else buy_amount if expected == "sell" else 0.0

    if expected == "none":
        if buy_amount == 0 and sell_amount == 0:
            classification = "complied_no_trade"
        elif str(action or "").lower() == "forbid_buy" and buy_amount > 0:
            classification = "violated"
        else:
            classification = "discretionary_trade"
    elif expected_trade_amount == 0 and opposite_amount > 0:
        classification = "reverse_executed"
    elif expected_trade_amount == 0:
        classification = "not_executed"
    elif opposite_amount > 0:
        classification = "mixed_execution"
    elif expected_amount > 0:
        ratio = expected_trade_amount / expected_amount
        if ratio < 0.8:
            classification = "partial_executed"
        elif ratio <= 1.2:
            classification = "full_executed"
        else:
            classification = "over_executed"
    else:
        classification = "full_executed"

    return {
        "classification": classification,
        "label": EXECUTION_LABELS.get(classification, classification),
        "expected_direction": expected,
        "suggested_amount": expected_amount,
        "matched_trade_ids": [int(trade.get("id") or 0) for trade in trades if trade.get("id")],
        "matched_buy_amount": buy_amount,
        "matched_sell_amount": sell_amount,
        "matched_amount": round(buy_amount + sell_amount, 3),
        "deviation_amount": round(expected_trade_amount - expected_amount, 3) if expected in {"buy", "sell"} else round(buy_amount + sell_amount, 3),
        "followed": classification in {"full_executed", "partial_executed", "over_executed", "complied_no_trade"},
    }


async def _load_trades(codes: list[str], account_id: str | None = None) -> dict[str, list[dict[str, Any]]]:
    if not codes:
        return {}
    db = await database.get_db()
    try:
        placeholders = ",".join("?" for _ in codes)
        params: list[Any] = list(codes)
        account_filter = ""
        if account_id:
            account_filter = " AND account_id = ?"
            params.append(account_id)
        rows = await db.execute_fetchall(
            f"""
            SELECT id, code, name, direction, price, shares, amount, total_cost, trade_time, account_id
            FROM trades
            WHERE code IN ({placeholders}){account_filter}
            ORDER BY trade_time ASC, id ASC
            """,
            tuple(params),
        )
    finally:
        await db.close()
    trades_by_code: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        trade = _row_dict(row)
        trades_by_code.setdefault(str(trade.get("code") or ""), []).append(trade)
    return trades_by_code


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_classification = Counter(row["execution"]["classification"] for row in rows)
    by_action = Counter(str(row.get("action") or row.get("decision_action") or "").lower() for row in rows)
    return {
        "items": len(rows),
        "matched_items": sum(1 for row in rows if row["execution"]["matched_trade_ids"]),
        "followed_items": sum(1 for row in rows if row["execution"]["followed"]),
        "by_classification": dict(sorted(by_classification.items())),
        "by_action": dict(sorted(by_action.items())),
    }


async def daily_decision_execution(
    review_id: str | None = None,
    *,
    limit: int = 300,
    account_id: str | None = None,
) -> dict[str, Any]:
    db = await database.get_db()
    try:
        params: list[Any] = []
        where = "h.status != 'archived'"
        if review_id:
            where += " AND h.review_id = ?"
            params.append(review_id)
        if account_id:
            where += " AND h.account_id = ?"
            params.append(account_id)
        params.append(max(1, min(int(limit or 300), 1000)))
        rows = await db.execute_fetchall(
            f"""
            SELECT h.review_id,
                   h.date,
                   h.created_at AS review_created_at,
                   h.account_id,
                   hri.id AS item_id,
                   hri.item_type,
                   hri.code,
                   hri.name,
                   hri.decision_action,
                   hri.decision_status,
                   hri.suggested_amount,
                   hri.target_position_pct,
                   hri.latest_report_id,
                   hri.created_at
            FROM holding_daily_reviews h
            JOIN holding_review_items hri ON hri.review_id = h.review_id
            WHERE {where}
            ORDER BY h.date DESC, h.id DESC, hri.id ASC
            LIMIT ?
            """,
            tuple(params),
        )
    finally:
        await db.close()
    items = [_row_dict(row) for row in rows]
    codes = sorted({str(item.get("code") or "") for item in items if item.get("code")})
    trades_by_code = await _load_trades(codes, account_id=account_id)
    result_rows: list[dict[str, Any]] = []
    for item in items:
        item["source_time"] = item.get("date") or item.get("review_created_at") or item.get("created_at")
        matches = _match_trades(item, trades_by_code, days=8)
        action = str(item.get("decision_action") or "watch").lower()
        result_rows.append(
            {
                **item,
                "source": "daily_decision",
                "action": action,
                "execution": classify_execution(action, item.get("suggested_amount"), matches),
            }
        )
    return {
        "scope": "daily_decision_execution",
        "review_id": review_id,
        "summary": _summary(result_rows),
        "rows": result_rows,
    }


async def position_plan_execution(
    plan_id: str | None = None,
    *,
    limit: int = 300,
    account_id: str | None = None,
) -> dict[str, Any]:
    db = await database.get_db()
    try:
        params: list[Any] = []
        where = "pp.status != 'archived' AND ppi.adoption_status = 'adopted'"
        if plan_id:
            where += " AND pp.plan_id = ?"
            params.append(plan_id)
        params.append(max(1, min(int(limit or 300), 1000)))
        rows = await db.execute_fetchall(
            f"""
            SELECT pp.plan_id,
                   pp.title,
                   pp.stage,
                   pp.adoption_status AS plan_adoption_status,
                   COALESCE(pp.confirmed_at, pp.created_at) AS plan_time,
                   ppi.id AS item_id,
                   ppi.code,
                   ppi.name,
                   ppi.action,
                   ppi.suggested_amount,
                   ppi.position_pct,
                   ppi.suggested_shares,
                   ppi.adoption_status,
                   COALESCE(ppi.adopted_at, pp.confirmed_at, pp.created_at) AS source_time,
                   ppi.source_report_id
            FROM position_plans pp
            JOIN position_plan_items ppi ON ppi.plan_id = pp.plan_id
            WHERE {where}
            ORDER BY COALESCE(ppi.adopted_at, pp.confirmed_at, pp.created_at) DESC, ppi.id ASC
            LIMIT ?
            """,
            tuple(params),
        )
    finally:
        await db.close()
    items = [_row_dict(row) for row in rows]
    codes = sorted({str(item.get("code") or "") for item in items if item.get("code")})
    trades_by_code = await _load_trades(codes, account_id=account_id)
    result_rows: list[dict[str, Any]] = []
    for item in items:
        item["account_id"] = account_id or "default"
        matches = _match_trades(item, trades_by_code, days=31)
        action = str(item.get("action") or "watch").lower()
        result_rows.append(
            {
                **item,
                "source": "position_plan",
                "execution": classify_execution(action, item.get("suggested_amount"), matches),
            }
        )
    return {
        "scope": "position_plan_execution",
        "plan_id": plan_id,
        "summary": {
            **_summary(result_rows),
            "adopted_items": len(result_rows),
            "ignored_items": 0,
        },
        "rows": result_rows,
    }


async def overview(limit: int = 300, account_id: str | None = None) -> dict[str, Any]:
    daily = await daily_decision_execution(limit=limit, account_id=account_id)
    plans = await position_plan_execution(limit=limit, account_id=account_id)
    rows = [*daily["rows"], *plans["rows"]]
    return {
        "scope": "suggestion_execution_review",
        "summary": _summary(rows),
        "daily_decisions": daily,
        "position_plans": plans,
        "rows": rows,
    }
