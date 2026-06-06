"""Unified AI performance workspace service."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from models.database import get_db
from services import execution_review_service, shadow_portfolio_service, signal_tracking_service

BUY_ACTIONS = {"buy", "overweight", "add"}
SELL_ACTIONS = {"sell", "underweight", "reduce"}
HORIZON_DAYS = (1, 3, 5, 10, 20)


def _clean_filter(value: str | None) -> str | None:
    if value in ("", "all", None):
        return None
    return value


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _loads(value: Any, fallback: Any):
    if value in ("", None):
        return fallback
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        return parsed if parsed is not None else fallback
    except (TypeError, ValueError):
        return fallback


def _date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _pct_return(entry: float, current: float, action: str) -> float | None:
    if entry <= 0 or current <= 0:
        return None
    raw = (current - entry) / entry * 100
    return round(-raw if action in SELL_ACTIONS else raw, 3)


def _weighted_average(values: list[tuple[float, float]]) -> float | None:
    valid = [(value, weight) for value, weight in values if weight > 0]
    if not valid:
        return None
    total_weight = sum(weight for _value, weight in valid)
    if total_weight <= 0:
        return None
    return round(sum(value * weight for value, weight in valid) / total_weight, 3)


def _plan_item_weight(row: dict[str, Any]) -> float:
    amount = _num(row.get("suggested_amount"))
    if amount > 0:
        return amount
    pct = _num(row.get("position_pct"))
    if pct > 1:
        pct = pct / 100
    if pct > 0:
        return pct
    action = str(row.get("action") or "").lower()
    return 1.0 if action in BUY_ACTIONS else 0.0


def _finalize_group_stats(groups: dict[str, dict[str, Any]], *, label_key: str) -> list[dict[str, Any]]:
    rows = []
    for key, bucket in groups.items():
        tracked = bucket["tracked"]
        rows.append(
            {
                label_key: key,
                "plans": bucket["plans"],
                "tracked": tracked,
                "avg_plan_pnl_pct": round(bucket["avg_pnl_sum"] / tracked, 3) if tracked else None,
                "avg_portfolio_return_pct": (
                    round(bucket["portfolio_return_sum"] / bucket["portfolio_return_count"], 3)
                    if bucket["portfolio_return_count"] else None
                ),
            }
        )
    return sorted(rows, key=lambda item: str(item[label_key]))


async def filter_options(login_user_id: str | None = None) -> dict[str, Any]:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """
            SELECT DISTINCT COALESCE(model_mode, 'manual') AS model_mode,
                            COALESCE(depth, 'manual') AS depth
            FROM analysis_reports
            WHERE COALESCE(login_user_id, 'admin') = ?
            ORDER BY model_mode, depth
            """,
            (login_user_id or "admin",),
        )
        plan_rows = await db.execute_fetchall(
            """
            SELECT DISTINCT COALESCE(stage, 'final') AS stage,
                            COALESCE(model_strategy, 'single') AS model_strategy
            FROM position_plans
            ORDER BY stage, model_strategy
            """
        )
    finally:
        await db.close()
    model_modes = sorted({row["model_mode"] for row in rows if row["model_mode"]})
    depths = sorted({row["depth"] for row in rows if row["depth"]})
    return {
        "windows": [
            {"value": "7", "label": "近7天"},
            {"value": "30", "label": "近30天"},
            {"value": "90", "label": "近90天"},
            {"value": "all", "label": "全部"},
        ],
        "model_modes": model_modes,
        "depths": depths,
        "position_plan_stages": sorted({row["stage"] for row in plan_rows if row["stage"]}),
        "position_plan_model_strategies": sorted({row["model_strategy"] for row in plan_rows if row["model_strategy"]}),
    }


async def position_plan_performance(limit: int = 100, account_id: str | None = None) -> dict[str, Any]:
    db = await get_db()
    try:
        params: list[Any] = []
        account_filter = ""
        portfolio_join_filter = ""
        if account_id:
            account_filter = """
                  AND (
                    pp.cash_snapshot_json IS NULL
                    OR pp.cash_snapshot_json = ''
                    OR pp.cash_snapshot_json LIKE ?
                  )
            """
            params.append(f'%"{account_id}"%')
            portfolio_join_filter = " AND portfolio.account_id = ?"
        params.append(max(1, min(int(limit or 100), 500)))
        if account_id:
            params.append(account_id)
        rows = await db.execute_fetchall(
            f"""
            WITH recent_plans AS (
                SELECT *
                FROM position_plans
                WHERE status != 'archived'
                  AND adoption_status IN ('adopted', 'partially_adopted')
                  {account_filter}
                ORDER BY COALESCE(confirmed_at, created_at) DESC
                LIMIT ?
            )
            SELECT pp.plan_id,
                   pp.title,
                   pp.stage,
                   pp.model_strategy,
                   pp.context_strategy,
                   pp.adoption_status,
                   pp.confirmed_at,
                   pp.cash_snapshot_json,
                   pp.created_at,
                   COALESCE(pp.confirmed_at, pp.created_at) AS performance_start_at,
                   ppi.code,
                   ppi.name,
                   ppi.action,
                   ppi.suggested_amount,
                   ppi.position_pct,
                   ppi.suggested_shares,
                   ppi.adoption_status AS item_adoption_status,
                   ppi.source_report_id,
                   portfolio.total_shares AS real_shares,
                   portfolio.current_price AS real_current_price,
                   portfolio.market_value AS real_market_value,
                   ai_shadow_positions.total_shares AS shadow_shares,
                   ai_shadow_positions.market_value AS shadow_market_value,
                   st.pnl_pct,
                   st.excess_return,
                   st.entry_price AS tracking_entry_price,
                   st.current_price AS tracking_current_price,
                   st.status AS tracking_status
            FROM recent_plans pp
            LEFT JOIN position_plan_items ppi ON ppi.plan_id = pp.plan_id
            LEFT JOIN signal_tracking st ON st.report_id = ppi.source_report_id
            LEFT JOIN portfolio ON portfolio.code = ppi.code{portfolio_join_filter}
            LEFT JOIN ai_shadow_positions ON ai_shadow_positions.code = ppi.code
            ORDER BY pp.created_at DESC, ppi.id ASC
            """,
            tuple(params),
        )
        codes = sorted({row["code"] for row in rows if row["code"]})
        price_rows = []
        if codes:
            placeholders = ",".join("?" for _ in codes)
            price_rows = await db.execute_fetchall(
                f"""
                SELECT code6 AS code, date, close_price
                FROM daily_pnl
                WHERE code6 IN ({placeholders})
                  AND close_price IS NOT NULL
                ORDER BY code6, date
                """,
                tuple(codes),
            )
    finally:
        await db.close()
    price_history: dict[str, list[tuple[date, float]]] = {}
    for row in price_rows:
        day = _date(row["date"])
        price = _num(row["close_price"])
        if not day or price <= 0:
            continue
        price_history.setdefault(row["code"], []).append((day, price))

    def price_on_or_after(code: str, day: date) -> float | None:
        for price_day, price in price_history.get(code, []):
            if price_day >= day:
                return price
        return None

    plans: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_dict = dict(row)
        plan_adoption_status = row_dict.get("adoption_status")
        item_adoption_status = row_dict.get("item_adoption_status") or ("adopted" if plan_adoption_status == "adopted" else "pending")
        if plan_adoption_status not in {"adopted", "partially_adopted"}:
            continue
        if plan_adoption_status == "partially_adopted" and item_adoption_status != "adopted":
            continue
        start_at = row_dict.get("performance_start_at") or row_dict.get("confirmed_at") or row_dict.get("created_at")
        plan = plans.setdefault(
            row["plan_id"],
            {
                "plan_id": row["plan_id"],
                "title": row["title"],
                "stage": row["stage"],
                "model_strategy": row["model_strategy"],
                "context_strategy": row["context_strategy"],
                "adoption_status": row_dict.get("adoption_status") or "adopted",
                "confirmed_at": row_dict.get("confirmed_at"),
                "created_at": row["created_at"],
                "performance_start_at": start_at,
                "items": 0,
                "actionable_items": 0,
                "tracked": 0,
                "wins": 0,
                "pnl_sum": 0.0,
                "excess_sum": 0.0,
                "weighted_pnl": [],
                "weighted_excess": [],
                "horizon_weighted": {str(day): [] for day in HORIZON_DAYS},
                "deviation": {
                    "evaluated": 0,
                    "aligned": 0,
                    "underfollowed": 0,
                    "overfollowed": 0,
                    "missing_real_position": 0,
                    "real_market_value": 0.0,
                    "suggested_amount": 0.0,
                    "amount_gap": 0.0,
                    "shadow_market_value": 0.0,
                },
                "allocation": {
                    "suggested_amount": 0.0,
                    "position_pct_sum": 0.0,
                    "max_position_pct": 0.0,
                    "cash_total": 0.0,
                    "warnings": [],
                },
            },
        )
        if not row["code"]:
            continue
        plan["items"] += 1
        action = str(row["action"] or "").lower()
        if action in BUY_ACTIONS:
            plan["actionable_items"] += 1
        weight = _plan_item_weight(row_dict)
        suggested_amount = _num(row["suggested_amount"])
        position_pct = _num(row["position_pct"])
        if position_pct > 1:
            position_pct = position_pct / 100
        suggested_shares = _num(row["suggested_shares"])
        real_shares = _num(row["real_shares"])
        real_value = _num(row["real_market_value"])
        real_price = _num(row["real_current_price"])
        shadow_value = _num(row["shadow_market_value"])
        plan["allocation"]["suggested_amount"] += suggested_amount
        plan["allocation"]["position_pct_sum"] += max(position_pct, 0)
        plan["allocation"]["max_position_pct"] = max(plan["allocation"]["max_position_pct"], max(position_pct, 0))
        plan["deviation"]["real_market_value"] += real_value
        plan["deviation"]["suggested_amount"] += suggested_amount
        plan["deviation"]["shadow_market_value"] += shadow_value
        if action in BUY_ACTIONS:
            plan["deviation"]["evaluated"] += 1
            suggested_value = suggested_amount
            if suggested_value <= 0 and suggested_shares > 0 and real_price > 0:
                suggested_value = suggested_shares * real_price
            amount_gap = suggested_value - real_value
            plan["deviation"]["amount_gap"] += amount_gap
            if real_value <= 0:
                plan["deviation"]["missing_real_position"] += 1
            elif suggested_value > 0:
                ratio_gap = amount_gap / suggested_value
                if abs(ratio_gap) <= 0.1:
                    plan["deviation"]["aligned"] += 1
                elif ratio_gap > 0:
                    plan["deviation"]["underfollowed"] += 1
                else:
                    plan["deviation"]["overfollowed"] += 1
            elif suggested_shares > 0:
                share_gap = suggested_shares - real_shares
                if abs(share_gap) <= max(1, suggested_shares * 0.1):
                    plan["deviation"]["aligned"] += 1
                elif share_gap > 0:
                    plan["deviation"]["underfollowed"] += 1
                else:
                    plan["deviation"]["overfollowed"] += 1
        if row["pnl_pct"] is not None:
            pnl = float(row["pnl_pct"])
            plan["tracked"] += 1
            plan["wins"] += 1 if pnl > 0 else 0
            plan["pnl_sum"] += pnl
            plan["excess_sum"] += float(row["excess_return"] or 0)
            plan["weighted_pnl"].append((pnl, weight))
            plan["weighted_excess"].append((float(row["excess_return"] or 0), weight))
        plan_day = _date(start_at)
        if plan_day and action in BUY_ACTIONS:
            entry = _num(row["tracking_entry_price"]) or price_on_or_after(row["code"], plan_day) or 0.0
            for day in HORIZON_DAYS:
                current = price_on_or_after(row["code"], plan_day + timedelta(days=day)) or 0.0
                horizon_return = _pct_return(entry, current, action)
                if horizon_return is not None:
                    plan["horizon_weighted"][str(day)].append((horizon_return, weight))
    result = []
    for plan in plans.values():
        tracked = plan["tracked"]
        plan["avg_pnl_pct"] = round(plan.pop("pnl_sum") / tracked, 3) if tracked else None
        plan["avg_excess_return"] = round(plan.pop("excess_sum") / tracked, 3) if tracked else None
        plan["win_rate"] = round(plan["wins"] / tracked * 100, 3) if tracked else None
        plan["portfolio_return_pct"] = _weighted_average(plan.pop("weighted_pnl"))
        plan["portfolio_excess_return"] = _weighted_average(plan.pop("weighted_excess"))
        plan["horizon_returns"] = {
            day: _weighted_average(values)
            for day, values in plan.pop("horizon_weighted").items()
        }
        horizon_values = [value for value in plan["horizon_returns"].values() if value is not None]
        plan["max_drawdown_pct"] = round(min(horizon_values), 3) if horizon_values else None
        cash_snapshot = _loads(plan.get("cash_snapshot_json"), {})
        balances = cash_snapshot.get("balances") if isinstance(cash_snapshot, dict) else {}
        cash_total = _num(cash_snapshot.get("total_cash")) if isinstance(cash_snapshot, dict) else 0.0
        if not cash_total and isinstance(balances, dict):
            cash_total = sum(_num(value) for value in balances.values())
        allocation = plan["allocation"]
        allocation["cash_total"] = round(cash_total, 3)
        allocation["suggested_amount"] = round(allocation["suggested_amount"], 3)
        allocation["position_pct_sum"] = round(allocation["position_pct_sum"], 3)
        allocation["max_position_pct"] = round(allocation["max_position_pct"], 3)
        if cash_total and allocation["suggested_amount"] > cash_total:
            allocation["warnings"].append("建议金额超过当前现金快照")
        if allocation["position_pct_sum"] > 1:
            allocation["warnings"].append("建议仓位合计超过100%")
        if allocation["max_position_pct"] > 0.25:
            allocation["warnings"].append("存在单票仓位超过25%")
        deviation = plan["deviation"]
        deviation["real_market_value"] = round(deviation["real_market_value"], 3)
        deviation["suggested_amount"] = round(deviation["suggested_amount"], 3)
        deviation["amount_gap"] = round(deviation["amount_gap"], 3)
        deviation["shadow_market_value"] = round(deviation["shadow_market_value"], 3)
        deviation["follow_rate"] = (
            round((deviation["aligned"] + deviation["overfollowed"]) / deviation["evaluated"] * 100, 3)
            if deviation["evaluated"] else None
        )
        plan.pop("cash_snapshot_json", None)
        result.append(plan)
    by_stage: dict[str, dict[str, Any]] = {}
    by_model_strategy: dict[str, dict[str, Any]] = {}
    by_context_strategy: dict[str, dict[str, Any]] = {}
    for plan in result:
        for groups, key in (
            (by_stage, plan["stage"] or "final"),
            (by_model_strategy, plan["model_strategy"] or "single"),
            (by_context_strategy, plan["context_strategy"] or "auto"),
        ):
            bucket = groups.setdefault(
                key,
                {"plans": 0, "tracked": 0, "avg_pnl_sum": 0.0, "portfolio_return_sum": 0.0, "portfolio_return_count": 0},
            )
            bucket["plans"] += 1
            if plan["avg_pnl_pct"] is not None:
                bucket["tracked"] += 1
                bucket["avg_pnl_sum"] += plan["avg_pnl_pct"]
            if plan["portfolio_return_pct"] is not None:
                bucket["portfolio_return_count"] += 1
                bucket["portfolio_return_sum"] += plan["portfolio_return_pct"]
    return {
        "count": len(result),
        "scope": "position_plan_items",
        "plans": result,
        "by_stage": _finalize_group_stats(by_stage, label_key="stage"),
        "by_model_strategy": _finalize_group_stats(by_model_strategy, label_key="model_strategy"),
        "by_context_strategy": _finalize_group_stats(by_context_strategy, label_key="context_strategy"),
        "horizons": list(HORIZON_DAYS),
        "note": "组合级绩效统计整份采纳方案的全部建议，以及部分采纳方案中被逐项采纳的建议；未采纳、忽略和草稿只作为复盘参考。优先使用 signal_tracking 闭环收益，如 daily_pnl 有收盘价，则补充 1/3/5/10/20 日后验收益和最大回撤。",
    }


async def daily_decision_performance(limit: int = 100, account_id: str | None = None) -> dict[str, Any]:
    db = await get_db()
    try:
        params: list[Any] = []
        account_filter = ""
        if account_id:
            account_filter = " AND h.account_id = ?"
            params.append(account_id)
        params.append(max(1, min(int(limit or 100), 1000)))
        rows = await db.execute_fetchall(
            f"""
            SELECT h.review_id,
                   h.date,
                   h.status,
                   h.created_at,
                   h.asset_snapshot_json,
                   h.candidate_scope,
                   hri.id AS item_id,
                   hri.item_type,
                   hri.code,
                   hri.name,
                   hri.decision_action,
                   hri.decision_status,
                   hri.holding_pnl_pct,
                   hri.change_pct,
                   hri.latest_report_id,
                   st.pnl_pct,
                   st.excess_return,
                   st.status AS tracking_status
            FROM holding_daily_reviews h
            JOIN holding_review_items hri ON hri.review_id = h.review_id
            LEFT JOIN signal_tracking st ON st.report_id = hri.latest_report_id
            WHERE h.status != 'archived'{account_filter}
            ORDER BY h.date DESC, h.id DESC, hri.id ASC
            LIMIT ?
            """,
            tuple(params),
        )
    finally:
        await db.close()

    reviews = {row["review_id"] for row in rows if row["review_id"]}
    by_action: dict[str, dict[str, Any]] = {}
    by_status: dict[str, dict[str, Any]] = {}
    tracked = {"tracked": 0, "wins": 0, "pnl_sum": 0.0, "excess_sum": 0.0}
    items = []
    for row in rows:
        item = dict(row)
        action = str(item.get("decision_action") or "watch")
        status = str(item.get("decision_status") or "not_executed")
        action_bucket = by_action.setdefault(action, {"count": 0, "executed": 0, "tracked": 0, "avg_pnl_pct": None})
        status_bucket = by_status.setdefault(status, {"count": 0})
        action_bucket["count"] += 1
        status_bucket["count"] += 1
        if status == "executed":
            action_bucket["executed"] += 1
        if item.get("pnl_pct") is not None:
            pnl = _num(item.get("pnl_pct"))
            action_bucket["tracked"] += 1
            action_bucket["_pnl_sum"] = _num(action_bucket.get("_pnl_sum")) + pnl
            tracked["tracked"] += 1
            tracked["wins"] += 1 if pnl > 0 else 0
            tracked["pnl_sum"] += pnl
            tracked["excess_sum"] += _num(item.get("excess_return"))
        items.append(item)
    for bucket in by_action.values():
        if bucket["tracked"]:
            bucket["avg_pnl_pct"] = round(_num(bucket.pop("_pnl_sum")) / bucket["tracked"], 3)
        else:
            bucket.pop("_pnl_sum", None)
    tracked_count = tracked["tracked"]
    tracked_summary = {
        "tracked": tracked_count,
        "wins": tracked["wins"],
        "win_rate": round(tracked["wins"] / tracked_count * 100, 3) if tracked_count else None,
        "avg_pnl_pct": round(tracked["pnl_sum"] / tracked_count, 3) if tracked_count else None,
        "avg_excess_return": round(tracked["excess_sum"] / tracked_count, 3) if tracked_count else None,
    }
    return {
        "scope": "daily_decision_items",
        "summary": {
            "reviews": len(reviews),
            "items": len(rows),
            "holding_items": sum(1 for row in rows if row["item_type"] == "holding"),
            "candidate_items": sum(1 for row in rows if row["item_type"] == "candidate"),
        },
        "by_action": by_action,
        "by_status": by_status,
        "tracked": tracked_summary,
        "items": items,
        "note": "每日决策绩效只统计每日 AI 决策报告的逐条动作执行状态和后验表现，不与组合研究方案绩效混用。",
    }


async def overview(
    window: str = "all",
    model_mode: str | None = None,
    depth: str | None = None,
    limit: int = 100,
    login_user_id: str | None = None,
    account_id: str | None = None,
) -> dict[str, Any]:
    model_mode = _clean_filter(model_mode)
    depth = _clean_filter(depth)
    limit = max(1, min(int(limit or 100), 500))

    signal_stats = signal_tracking_service.get_stats(
        window=window,
        model_mode=model_mode,
        depth=depth,
    )
    signal_tracking = signal_tracking_service.list_tracking(
        window=window,
        model_mode=model_mode,
        depth=depth,
    )
    summary = await shadow_portfolio_service.summary()
    positions = await shadow_portfolio_service.list_positions()
    comparison = await shadow_portfolio_service.comparison()
    orders = await shadow_portfolio_service.list_orders(
        limit=limit,
        window=window,
        model_mode=model_mode,
        depth=depth,
    )
    calibration = await shadow_portfolio_service.calibration(
        limit=limit,
        window=window,
        model_mode=model_mode,
        depth=depth,
    )
    deviation = await shadow_portfolio_service.execution_deviation()
    plan_performance = await position_plan_performance(limit=limit, account_id=account_id)
    daily_performance = await daily_decision_performance(limit=limit, account_id=account_id)
    execution_review = await execution_review_service.overview(limit=limit, account_id=account_id)
    return {
        "filters": {
            "window": window,
            "model_mode": model_mode,
            "depth": depth,
            "options": await filter_options(login_user_id=login_user_id),
        },
        "signal": {
            "stats": signal_stats,
            "tracking": signal_tracking,
        },
        "shadow": {
            "summary": summary,
            "positions": positions,
            "comparison": comparison,
            "orders": orders,
            "calibration": calibration,
            "deviation": deviation,
        },
        "daily_decisions": daily_performance,
        "position_plans": plan_performance,
        "execution_review": execution_review,
    }
