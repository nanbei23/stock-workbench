"""AI shadow portfolio service.

The shadow portfolio follows AI report signals mechanically and stays fully
separate from the user's real portfolio. It lets the app compare "AI did this"
against "I actually did this" without contaminating either dataset.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from data.quote import get_batch_quotes
from models.database import get_db

logger = logging.getLogger(__name__)

BUY_SIGNALS = {"STRONG_BUY", "BUY", "OVERWEIGHT"}
SELL_SIGNALS = {"STRONG_SELL", "SELL", "UNDERWEIGHT"}
ACTIONABLE_SIGNALS = BUY_SIGNALS | SELL_SIGNALS
COMMISSION_RATE = 0.0003
TRANSFER_FEE_RATE = 0.00001
STAMP_TAX_RATE = 0.0005
ASSUMED_SLIPPAGE_PCT = 0.1
ASSUMED_INITIAL_CASH = 1_000_000.0
MAX_SINGLE_POSITION_PCT = 0.2
CONFIDENCE_BUCKETS = [
    ("low", "低置信", 0.0, 0.5),
    ("medium", "中置信", 0.5, 0.7),
    ("high", "高置信", 0.7, 0.85),
    ("very_high", "极高置信", 0.85, 1.01),
]


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _num(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _norm_ratio(value: Any) -> float | None:
    number = _num(value)
    if number is None:
        return None
    return round(number / 100, 4) if number > 1 else round(number, 4)


def _signal_action(signal: str | None) -> str | None:
    sig = (signal or "HOLD").upper()
    if sig in BUY_SIGNALS:
        return "buy"
    if sig in SELL_SIGNALS:
        return "sell"
    return None


def _order_fee(action: str, price: float, shares: int) -> float:
    amount = max(price, 0) * max(shares, 0)
    if amount <= 0:
        return 0.0
    fee = amount * (COMMISSION_RATE + TRANSFER_FEE_RATE)
    if action == "sell":
        fee += amount * STAMP_TAX_RATE
    return round(fee, 2)


def _shadow_filters(alias: str = "ar", window: str = "all", model_mode: str | None = None, depth: str | None = None):
    clauses = []
    params: list[Any] = []
    if window and window != "all":
        try:
            days = max(1, min(int(window), 3650))
            clauses.append(f"date({alias}.created_at) >= date('now', ?)")
            params.append(f"-{days} day")
        except (TypeError, ValueError):
            pass
    if model_mode:
        clauses.append(f"COALESCE({alias}.model_mode, 'manual') = ?")
        params.append(model_mode)
    if depth:
        clauses.append(f"COALESCE({alias}.depth, 'manual') = ?")
        params.append(depth)
    return clauses, params


def _directional_return_pct(order: dict[str, Any], current_price: float | None) -> float | None:
    fill_price = _num(order.get("fill_price") or order.get("suggested_price"))
    if not fill_price or fill_price <= 0 or not current_price or current_price <= 0:
        return None
    if order.get("action") == "sell":
        return round((fill_price - current_price) / fill_price * 100, 2)
    return round((current_price - fill_price) / fill_price * 100, 2)


def _confidence_bucket(confidence: float | None) -> tuple[str, str]:
    value = confidence if confidence is not None else -1
    for key, label, low, high in CONFIDENCE_BUCKETS:
        if low <= value < high:
            return key, label
    return "unknown", "未标注"


def _empty_stats(key: str, label: str) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "count": 0,
        "wins": 0,
        "avg_return_pct": 0.0,
        "avg_confidence": None,
        "hit_rate": 0.0,
        "_return_sum": 0.0,
        "_confidence_sum": 0.0,
        "_confidence_count": 0,
    }


def _add_stat(bucket: dict[str, Any], return_pct: float, confidence: float | None) -> None:
    bucket["count"] += 1
    bucket["wins"] += 1 if return_pct > 0 else 0
    bucket["_return_sum"] += return_pct
    if confidence is not None:
        bucket["_confidence_sum"] += confidence
        bucket["_confidence_count"] += 1


def _finalize_stats(bucket: dict[str, Any]) -> dict[str, Any]:
    count = bucket.pop("count", 0)
    wins = bucket.pop("wins", 0)
    return_sum = bucket.pop("_return_sum", 0.0)
    confidence_sum = bucket.pop("_confidence_sum", 0.0)
    confidence_count = bucket.pop("_confidence_count", 0)
    bucket["count"] = count
    bucket["wins"] = wins
    bucket["hit_rate"] = round(wins / count * 100, 2) if count else 0.0
    bucket["avg_return_pct"] = round(return_sum / count, 2) if count else 0.0
    bucket["avg_confidence"] = round(confidence_sum / confidence_count * 100, 2) if confidence_count else None
    bucket["calibration_gap"] = (
        round(bucket["hit_rate"] - bucket["avg_confidence"], 2)
        if bucket["avg_confidence"] is not None else None
    )
    return bucket


def _first_price(raw: dict[str, Any], report: dict[str, Any], quote: dict[str, Any] | None = None) -> float | None:
    quote = quote or {}
    for key in ("entry_price", "current_price", "suggested_price", "price", "target_buy_price"):
        price = _num(raw.get(key))
        if price and price > 0:
            return price
    for key in ("current_price", "entry_price", "target_price"):
        price = _num(report.get(key))
        if price and price > 0:
            return price
    price = _num(quote.get("price"))
    return price if price and price > 0 else None


def _target_price(raw: dict[str, Any], report: dict[str, Any]) -> float | None:
    for key in ("target_price", "target_sell_price", "take_profit_price"):
        price = _num(raw.get(key))
        if price and price > 0:
            return price
    price = _num(report.get("target_price"))
    return price if price and price > 0 else None


def _stop_loss(raw: dict[str, Any]) -> float | None:
    for key in ("stop_loss_price", "stop_loss", "stop_price"):
        price = _num(raw.get(key))
        if price and price > 0:
            return price
    return None


def _shares_for_report(action: str, signal: str, raw: dict[str, Any], confidence: float | None, risk_score: float | None) -> int:
    explicit = raw.get("shares") or raw.get("plan_shares") or raw.get("quantity")
    try:
        if explicit:
            shares = int(float(explicit))
            return max(0, shares // 100 * 100)
    except (TypeError, ValueError):
        pass

    if action == "sell":
        return 100

    shares = 100
    if signal == "STRONG_BUY" or (confidence is not None and confidence >= 0.78):
        shares = 200
    if risk_score is not None and risk_score >= 0.75:
        shares = 100
    return shares


def _reason(report: dict[str, Any], raw: dict[str, Any]) -> str:
    for value in (
        raw.get("reasoning"),
        raw.get("summary"),
        report.get("final_decision"),
        report.get("trader_plan"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()[:260]
    return "来自AI分析报告的结构化信号"


def _draft_order(report: dict[str, Any], quote: dict[str, Any] | None = None) -> dict[str, Any] | None:
    raw = _loads(report.get("raw_state"))
    signal = (report.get("signal") or raw.get("signal") or "HOLD").upper()
    action = _signal_action(signal)
    if not action:
        return None

    confidence = _norm_ratio(report.get("confidence") if report.get("confidence") is not None else raw.get("confidence"))
    risk_score = _norm_ratio(report.get("risk_score") if report.get("risk_score") is not None else raw.get("risk_score"))
    price = _first_price(raw, report, quote)
    shares = _shares_for_report(action, signal, raw, confidence, risk_score)
    status = "filled" if price and shares > 0 else "pending"

    return {
        "report_id": report.get("id"),
        "code": report.get("code"),
        "name": raw.get("name") or report.get("name") or report.get("code"),
        "action": action,
        "signal": signal,
        "suggested_price": price,
        "fill_price": price if status == "filled" else None,
        "target_price": _target_price(raw, report),
        "stop_loss_price": _stop_loss(raw),
        "shares": shares,
        "confidence": confidence,
        "risk_score": risk_score,
        "status": status,
        "source_reason": _reason(report, raw),
        "notes": f"自动来自AI报告 #{report.get('id')}",
    }


async def _fetch_report_rows(db, limit: int) -> list[dict[str, Any]]:
    rows = await db.execute_fetchall(
        """
        SELECT id, code, signal, confidence, risk_score, raw_state,
               final_decision, trader_plan, created_at
        FROM analysis_reports
        WHERE UPPER(COALESCE(signal, 'HOLD')) IN (
            'STRONG_BUY', 'BUY', 'OVERWEIGHT',
            'UNDERWEIGHT', 'SELL', 'STRONG_SELL'
        )
        ORDER BY created_at ASC, id ASC
        LIMIT ?
        """,
        (max(1, min(int(limit or 100), 500)),),
    )
    return [dict(row) for row in rows]


async def _insert_order(db, order: dict[str, Any]) -> bool:
    cursor = await db.execute(
        """
        INSERT OR IGNORE INTO ai_shadow_orders (
            report_id, code, name, action, signal, suggested_price, fill_price,
            target_price, stop_loss_price, shares, confidence, risk_score,
            status, source_reason, notes, filled_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CASE WHEN ? = 'filled' THEN datetime('now') ELSE NULL END)
        """,
        (
            order["report_id"],
            order["code"],
            order["name"],
            order["action"],
            order["signal"],
            order["suggested_price"],
            order["fill_price"],
            order["target_price"],
            order["stop_loss_price"],
            order["shares"],
            order["confidence"],
            order["risk_score"],
            order["status"],
            order["source_reason"],
            order["notes"],
            order["status"],
        ),
    )
    return cursor.rowcount > 0


async def sync_reports(limit: int = 100) -> dict[str, Any]:
    """Create shadow orders from actionable AI reports that are not synced yet."""
    db = await get_db()
    try:
        reports = await _fetch_report_rows(db, limit)
        quotes = {}
        codes = sorted({row["code"] for row in reports if row.get("code")})
        if codes:
            quotes = await get_batch_quotes(codes)

        created = 0
        pending = 0
        for report in reports:
            order = _draft_order(report, quotes.get(report.get("code"), {}))
            if not order:
                continue
            inserted = await _insert_order(db, order)
            if inserted:
                created += 1
                if order["status"] == "pending":
                    pending += 1
        await db.commit()
        await recalc_positions(db)
        return {
            "status": "ok",
            "scanned": len(reports),
            "created": created,
            "pending": pending,
            "message": f"已同步 {created} 条AI影子委托",
        }
    finally:
        await db.close()


async def recalc_positions(db=None) -> dict[str, Any]:
    own_db = db is None
    if own_db:
        db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """
            SELECT * FROM ai_shadow_orders
            WHERE status IN ('filled', 'closed')
            ORDER BY COALESCE(filled_at, created_at) ASC, id ASC
            """
        )
        books: dict[str, dict[str, Any]] = {}
        cash = ASSUMED_INITIAL_CASH
        total_fees = 0.0
        constraint_warnings = []
        for row in rows:
            order = dict(row)
            code = order["code"]
            book = books.setdefault(
                code,
                {
                    "code": code,
                    "name": order.get("name") or code,
                    "total_shares": 0,
                    "avg_cost": 0.0,
                    "cost_basis": 0.0,
                    "realized_pnl": 0.0,
                    "source_order_ids": [],
                },
            )
            shares = int(order.get("shares") or 0)
            price = float(order.get("fill_price") or order.get("suggested_price") or 0)
            if shares <= 0 or price <= 0:
                continue
            fee = _order_fee(order["action"], price, shares)
            total_fees += fee
            if order["action"] == "buy":
                gross = price * shares
                if gross + fee > cash:
                    constraint_warnings.append(f"{order.get('name') or code} 买入金额超过影子现金约束")
                cash -= gross + fee
                book["cost_basis"] += gross + fee
                book["total_shares"] += shares
                book["avg_cost"] = book["cost_basis"] / book["total_shares"] if book["total_shares"] else 0
                book["source_order_ids"].append(order["id"])
            elif order["action"] == "sell" and book["total_shares"] > 0:
                sell_shares = min(shares, book["total_shares"])
                avg_cost = book["avg_cost"] or 0
                gross = price * sell_shares
                cash += gross - fee
                book["realized_pnl"] += (price - avg_cost) * sell_shares - fee
                book["total_shares"] -= sell_shares
                book["cost_basis"] = avg_cost * book["total_shares"]
                book["avg_cost"] = avg_cost if book["total_shares"] else 0
                book["source_order_ids"].append(order["id"])

        await db.execute("DELETE FROM ai_shadow_positions")
        active = [book for book in books.values() if book["total_shares"] > 0]
        quotes = await get_batch_quotes([book["code"] for book in active]) if active else {}
        for book in active:
            quote = quotes.get(book["code"], {})
            price = float(quote.get("price") or book["avg_cost"] or 0)
            market_value = price * book["total_shares"]
            if market_value > ASSUMED_INITIAL_CASH * MAX_SINGLE_POSITION_PCT:
                constraint_warnings.append(f"{book['name']} 单票市值超过 {int(MAX_SINGLE_POSITION_PCT * 100)}% 影子仓位上限")
            unrealized = (price - book["avg_cost"]) * book["total_shares"] if book["avg_cost"] else 0
            unrealized_pct = (price - book["avg_cost"]) / book["avg_cost"] * 100 if book["avg_cost"] else 0
            await db.execute(
                """
                INSERT INTO ai_shadow_positions (
                    code, name, total_shares, avg_cost, current_price, market_value,
                    unrealized_pnl, unrealized_pnl_pct, realized_pnl, source_order_ids,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    book["code"],
                    book["name"],
                    book["total_shares"],
                    round(book["avg_cost"], 4),
                    round(price, 4),
                    round(market_value, 2),
                    round(unrealized, 2),
                    round(unrealized_pct, 2),
                    round(book["realized_pnl"], 2),
                    json.dumps(book["source_order_ids"], ensure_ascii=False),
                ),
            )
        await db.commit()
        return {
            "status": "ok",
            "positions": len(active),
            "simulation": {
                "initial_cash": ASSUMED_INITIAL_CASH,
                "cash": round(cash, 2),
                "total_fees": round(total_fees, 2),
                "slippage_pct": ASSUMED_SLIPPAGE_PCT,
                "max_single_position_pct": round(MAX_SINGLE_POSITION_PCT * 100, 2),
                "warnings": constraint_warnings[:8],
            },
        }
    finally:
        if own_db:
            await db.close()


async def _fill_pending_orders(db) -> int:
    rows = await db.execute_fetchall(
        """
        SELECT id, code, suggested_price, shares
        FROM ai_shadow_orders
        WHERE status = 'pending'
        """
    )
    pending = [dict(row) for row in rows]
    if not pending:
        return 0
    quotes = await get_batch_quotes(sorted({row["code"] for row in pending}))
    filled = 0
    for row in pending:
        quote_price = _num((quotes.get(row["code"]) or {}).get("price"))
        price = _num(row.get("suggested_price")) or quote_price
        shares = int(row.get("shares") or 0)
        if not price or price <= 0 or shares <= 0:
            continue
        await db.execute(
            """
            UPDATE ai_shadow_orders
            SET fill_price = ?, suggested_price = COALESCE(suggested_price, ?),
                status = 'filled', filled_at = datetime('now')
            WHERE id = ?
            """,
            (price, price, row["id"]),
        )
        filled += 1
    await db.commit()
    return filled


async def mark_to_market() -> dict[str, Any]:
    db = await get_db()
    try:
        filled = await _fill_pending_orders(db)
        result = await recalc_positions(db)
        result["filled_pending"] = filled
        return result
    finally:
        await db.close()


async def _refresh_positions_only() -> dict[str, Any]:
    db = await get_db()
    try:
        result = await recalc_positions(db)
        return result
    finally:
        await db.close()


async def list_orders(limit: int = 100, window: str = "all", model_mode: str | None = None, depth: str | None = None) -> dict[str, Any]:
    db = await get_db()
    try:
        clauses, params = _shadow_filters("ar", window, model_mode, depth)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = await db.execute_fetchall(
            f"""
            SELECT o.*, COALESCE(ar.model_mode, 'manual') AS model_mode, COALESCE(ar.depth, 'manual') AS depth
            FROM ai_shadow_orders o
            LEFT JOIN analysis_reports ar ON ar.id = o.report_id
            {where_sql}
            ORDER BY o.created_at DESC, o.id DESC
            LIMIT ?
            """,
            (*params, max(1, min(int(limit or 100), 500))),
        )
        orders = [dict(row) for row in rows]
        codes = sorted({row["code"] for row in orders if row.get("code")})
        quotes = await get_batch_quotes(codes) if codes else {}
        for order in orders:
            quote = quotes.get(order["code"], {})
            current_price = _num(quote.get("price"))
            order["current_price"] = current_price
            order["directional_return_pct"] = _directional_return_pct(order, current_price)
            order["estimated_fee"] = _order_fee(order.get("action"), _num(order.get("fill_price") or order.get("suggested_price")) or 0, int(order.get("shares") or 0))
        return {"count": len(orders), "orders": orders}
    finally:
        await db.close()


async def list_positions() -> dict[str, Any]:
    await _refresh_positions_only()
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM ai_shadow_positions ORDER BY market_value DESC, code ASC"
        )
        positions = [dict(row) for row in rows]
        return {"count": len(positions), "positions": positions}
    finally:
        await db.close()


async def comparison() -> dict[str, Any]:
    shadow = (await list_positions())["positions"]
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT * FROM portfolio WHERE total_shares > 0"
        )
        real = [dict(row) for row in rows]
    finally:
        await db.close()

    codes = sorted({item["code"] for item in shadow} | {item["code"] for item in real})
    quotes = await get_batch_quotes(codes) if codes else {}
    shadow_map = {item["code"]: item for item in shadow}
    real_map = {item["code"]: item for item in real}
    rows = []
    totals = {
        "shadow_market_value": 0.0,
        "shadow_unrealized_pnl": 0.0,
        "real_market_value": 0.0,
        "real_unrealized_pnl": 0.0,
    }
    for code in codes:
        quote = quotes.get(code, {})
        price = float(quote.get("price") or 0)
        s = shadow_map.get(code, {})
        r = real_map.get(code, {})
        real_shares = int(r.get("total_shares") or 0)
        real_avg = float(r.get("avg_cost") or 0)
        real_value = price * real_shares if price else float(r.get("market_value") or 0)
        real_pnl = (price - real_avg) * real_shares if price and real_avg else float(r.get("unrealized_pnl") or 0)
        shadow_value = float(s.get("market_value") or 0)
        shadow_pnl = float(s.get("unrealized_pnl") or 0)
        totals["shadow_market_value"] += shadow_value
        totals["shadow_unrealized_pnl"] += shadow_pnl
        totals["real_market_value"] += real_value
        totals["real_unrealized_pnl"] += real_pnl
        rows.append({
            "code": code,
            "name": s.get("name") or r.get("name") or quote.get("name") or code,
            "price": price,
            "shadow_shares": int(s.get("total_shares") or 0),
            "shadow_avg_cost": float(s.get("avg_cost") or 0),
            "shadow_market_value": round(shadow_value, 2),
            "shadow_unrealized_pnl": round(shadow_pnl, 2),
            "real_shares": real_shares,
            "real_avg_cost": real_avg,
            "real_market_value": round(real_value, 2),
            "real_unrealized_pnl": round(real_pnl, 2),
            "share_gap": int(s.get("total_shares") or 0) - real_shares,
            "pnl_gap": round(shadow_pnl - real_pnl, 2),
        })

    totals = {key: round(value, 2) for key, value in totals.items()}
    totals["market_value_gap"] = round(totals["shadow_market_value"] - totals["real_market_value"], 2)
    totals["pnl_gap"] = round(totals["shadow_unrealized_pnl"] - totals["real_unrealized_pnl"], 2)
    return {"totals": totals, "count": len(rows), "rows": rows}


async def execution_deviation() -> dict[str, Any]:
    comp = await comparison()
    buckets = {
        "ai_only": {"label": "AI有仓位，实盘未跟随", "count": 0, "pnl_gap": 0.0, "rows": []},
        "human_only": {"label": "实盘有仓位，AI未持有", "count": 0, "pnl_gap": 0.0, "rows": []},
        "ai_heavier": {"label": "AI仓位更重", "count": 0, "pnl_gap": 0.0, "rows": []},
        "human_heavier": {"label": "实盘仓位更重", "count": 0, "pnl_gap": 0.0, "rows": []},
        "aligned": {"label": "仓位基本一致", "count": 0, "pnl_gap": 0.0, "rows": []},
    }
    for row in comp["rows"]:
        shadow_shares = int(row.get("shadow_shares") or 0)
        real_shares = int(row.get("real_shares") or 0)
        if shadow_shares > 0 and real_shares == 0:
            key = "ai_only"
        elif real_shares > 0 and shadow_shares == 0:
            key = "human_only"
        elif shadow_shares > real_shares:
            key = "ai_heavier"
        elif real_shares > shadow_shares:
            key = "human_heavier"
        else:
            key = "aligned"
        item = buckets[key]
        item["count"] += 1
        item["pnl_gap"] += float(row.get("pnl_gap") or 0)
        if len(item["rows"]) < 5:
            item["rows"].append(row)
    rows = []
    for key, item in buckets.items():
        rows.append({
            "key": key,
            "label": item["label"],
            "count": item["count"],
            "pnl_gap": round(item["pnl_gap"], 2),
            "rows": item["rows"],
        })
    rows.sort(key=lambda item: abs(item["pnl_gap"]), reverse=True)
    return {"rows": rows, "totals": comp["totals"]}


async def calibration(limit: int = 200, window: str = "all", model_mode: str | None = None, depth: str | None = None) -> dict[str, Any]:
    db = await get_db()
    try:
        clauses, params = _shadow_filters("ar", window, model_mode, depth)
        where_extra = f" AND {' AND '.join(clauses)}" if clauses else ""
        rows = await db.execute_fetchall(
            f"""
            SELECT o.*, ar.fact_check, ar.bystander_verify, ar.created_at AS report_created_at
            FROM ai_shadow_orders o
            LEFT JOIN analysis_reports ar ON ar.id = o.report_id
            WHERE o.status IN ('filled', 'closed') AND o.fill_price IS NOT NULL
            {where_extra}
            ORDER BY o.created_at DESC, o.id DESC
            LIMIT ?
            """,
            (*params, max(1, min(int(limit or 200), 500))),
        )
        orders = [dict(row) for row in rows]
    finally:
        await db.close()

    codes = sorted({order["code"] for order in orders if order.get("code")})
    quotes = await get_batch_quotes(codes) if codes else {}
    evaluated = []
    signal_stats: dict[str, dict[str, Any]] = {}
    bucket_stats = {
        key: _empty_stats(key, label)
        for key, label, _low, _high in CONFIDENCE_BUCKETS
    }
    bucket_stats["unknown"] = _empty_stats("unknown", "未标注")
    total_return = 0.0
    wins = 0
    brier_sum = 0.0

    for order in orders:
        quote = quotes.get(order["code"], {})
        current_price = _num(quote.get("price"))
        return_pct = _directional_return_pct(order, current_price)
        if return_pct is None:
            continue
        confidence = _norm_ratio(order.get("confidence"))
        bucket_key, bucket_label = _confidence_bucket(confidence)
        signal = (order.get("signal") or "UNKNOWN").upper()
        signal_bucket = signal_stats.setdefault(signal, _empty_stats(signal, signal))
        _add_stat(signal_bucket, return_pct, confidence)
        _add_stat(bucket_stats.setdefault(bucket_key, _empty_stats(bucket_key, bucket_label)), return_pct, confidence)
        total_return += return_pct
        is_win = 1 if return_pct > 0 else 0
        wins += is_win
        if confidence is not None:
            brier_sum += (confidence - is_win) ** 2
        evaluated.append({
            "id": order["id"],
            "report_id": order.get("report_id"),
            "code": order["code"],
            "name": order.get("name") or order["code"],
            "action": order.get("action"),
            "signal": signal,
            "confidence": confidence,
            "risk_score": _norm_ratio(order.get("risk_score")),
            "fill_price": order.get("fill_price"),
            "current_price": current_price,
            "directional_return_pct": return_pct,
            "bucket": bucket_label,
            "source_reason": order.get("source_reason") or "",
        })

    evaluated_count = len(evaluated)
    by_signal = sorted(
        [_finalize_stats(dict(value)) for value in signal_stats.values()],
        key=lambda item: item["count"],
        reverse=True,
    )
    confidence_order = [item[0] for item in CONFIDENCE_BUCKETS] + ["unknown"]
    by_confidence = [
        _finalize_stats(dict(bucket_stats[key]))
        for key in confidence_order
        if bucket_stats[key]["count"] > 0
    ]
    top_wins = sorted(evaluated, key=lambda item: item["directional_return_pct"], reverse=True)[:5]
    top_misses = sorted(evaluated, key=lambda item: item["directional_return_pct"])[:5]
    recommendations = _calibration_recommendations(evaluated_count, by_signal, by_confidence)
    return {
        "summary": {
            "evaluated": evaluated_count,
            "hit_rate": round(wins / evaluated_count * 100, 2) if evaluated_count else 0.0,
            "avg_return_pct": round(total_return / evaluated_count, 2) if evaluated_count else 0.0,
            "brier_score": round(brier_sum / evaluated_count, 4) if evaluated_count else None,
            "positive": wins,
            "negative": evaluated_count - wins,
        },
        "by_signal": by_signal,
        "by_confidence": by_confidence,
        "top_wins": top_wins,
        "top_misses": top_misses,
        "recommendations": recommendations,
    }


def _calibration_recommendations(evaluated_count: int, by_signal: list[dict[str, Any]], by_confidence: list[dict[str, Any]]) -> list[str]:
    if evaluated_count == 0:
        return ["暂无可评估影子委托。先同步AI报告并刷新估值后，再观察校准结果。"]
    tips = []
    for item in by_confidence:
        if item["count"] >= 3 and item["avg_confidence"] is not None:
            if item["calibration_gap"] is not None and item["calibration_gap"] < -20:
                tips.append(f"{item['label']}命中率低于平均置信度 {abs(item['calibration_gap']):.1f} 个百分点，后续应下调该置信区间的仓位权重。")
            elif item["calibration_gap"] is not None and item["calibration_gap"] > 20:
                tips.append(f"{item['label']}命中率显著高于平均置信度，可考虑提高该区间信号的执行优先级。")
    for item in by_signal:
        if item["count"] >= 3 and item["avg_return_pct"] < 0:
            tips.append(f"{item['label']} 信号后验收益为 {item['avg_return_pct']}%，需要复核该类信号的触发条件和风险扣分。")
    if not tips:
        tips.append("样本量仍偏少或暂未出现明显偏差，建议继续积累影子委托后再调整模型权重。")
    return tips[:5]


async def summary() -> dict[str, Any]:
    simulation = (await _refresh_positions_only()).get("simulation", {})
    db = await get_db()
    try:
        order_row = await (await db.execute(
            """
            SELECT
              COUNT(*) AS total,
              SUM(CASE WHEN status = 'filled' THEN 1 ELSE 0 END) AS filled,
              SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
              SUM(CASE WHEN action = 'buy' THEN 1 ELSE 0 END) AS buys,
              SUM(CASE WHEN action = 'sell' THEN 1 ELSE 0 END) AS sells
            FROM ai_shadow_orders
            """
        )).fetchone()
        position_rows = await db.execute_fetchall("SELECT * FROM ai_shadow_positions")
    finally:
        await db.close()

    positions = [dict(row) for row in position_rows]
    market_value = sum(float(row.get("market_value") or 0) for row in positions)
    unrealized = sum(float(row.get("unrealized_pnl") or 0) for row in positions)
    order_stats = dict(order_row) if order_row else {}
    comp = await comparison()
    calib = await calibration(limit=200)
    return {
        "orders": {
            "total": int(order_stats.get("total") or 0),
            "filled": int(order_stats.get("filled") or 0),
            "pending": int(order_stats.get("pending") or 0),
            "buys": int(order_stats.get("buys") or 0),
            "sells": int(order_stats.get("sells") or 0),
        },
        "positions": {
            "count": len(positions),
            "market_value": round(market_value, 2),
            "unrealized_pnl": round(unrealized, 2),
            "unrealized_pnl_pct": round(unrealized / (market_value - unrealized) * 100, 2)
            if market_value and market_value != unrealized else 0,
        },
        "comparison": comp["totals"],
        "calibration": calib["summary"],
        "simulation": simulation,
    }


def sync_report_from_sqlite(sqlite_db, report_id: int) -> bool:
    """Best-effort sync hook used by the synchronous TradingAgents bridge."""
    try:
        row = sqlite_db.execute(
            """
            SELECT id, code, signal, confidence, risk_score, raw_state,
                   final_decision, trader_plan, created_at
            FROM analysis_reports
            WHERE id = ?
            """,
            (report_id,),
        ).fetchone()
        if not row:
            return False
        report = dict(row)
        if sqlite_db.execute("SELECT id FROM ai_shadow_orders WHERE report_id = ?", (report_id,)).fetchone():
            return False
        order = _draft_order(report)
        if not order:
            return False
        sqlite_db.execute(
            """
            INSERT OR IGNORE INTO ai_shadow_orders (
                report_id, code, name, action, signal, suggested_price, fill_price,
                target_price, stop_loss_price, shares, confidence, risk_score,
                status, source_reason, notes, filled_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CASE WHEN ? = 'filled' THEN datetime('now') ELSE NULL END)
            """,
            (
                order["report_id"],
                order["code"],
                order["name"],
                order["action"],
                order["signal"],
                order["suggested_price"],
                order["fill_price"],
                order["target_price"],
                order["stop_loss_price"],
                order["shares"],
                order["confidence"],
                order["risk_score"],
                order["status"],
                order["source_reason"],
                order["notes"],
                order["status"],
            ),
        )
        sqlite_db.commit()
        return True
    except Exception as exc:
        logger.warning("AI影子盘自动同步失败: %s", exc)
        return False
