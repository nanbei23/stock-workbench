"""Holding daily review service.

The review is a persistent, read-only research asset. It snapshots the real
portfolio context before producing any next-day suggestion so the advice never
assumes a fresh all-cash or all-in account.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from data.market import get_market_sentiment
from models.database import get_db
from repositories import portfolio_repository
from scheduler.ai_engine import get_index_quotes
from services import portfolio_service
from services import batch_report_service
from services import investment_profile_service


POSITIVE_SIGNALS = {"STRONG_BUY", "BUY", "OVERWEIGHT"}
NEGATIVE_SIGNALS = {"UNDERWEIGHT", "SELL", "STRONG_SELL"}
ACTION_LABELS = {
    "hold": "持有观察",
    "review": "需要复核",
    "reduce": "考虑减仓",
    "take_profit": "进入止盈观察",
    "candidate": "候选观察",
    "wait": "等待",
}


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _loads(value: Any, fallback: Any):
    if value is None or value == "":
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return round(float(value or 0), 3)
    except (TypeError, ValueError):
        return default


def _date_today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _review_id() -> str:
    return f"hr-{datetime.now().strftime('%Y%m%d%H%M%S')}-{str(uuid.uuid4())[:6]}"


def _cash_pct(summary: dict[str, Any]) -> float:
    total = _float(summary.get("total_assets"))
    return round(_float(summary.get("cash")) / total * 100, 3) if total else 0.0


def _position_usage_pct(summary: dict[str, Any]) -> float:
    total = _float(summary.get("total_assets"))
    return round(_float(summary.get("market_value")) / total * 100, 3) if total else 0.0


def _asset_snapshot(summary: dict[str, Any], account_id: str) -> dict[str, Any]:
    return {
        "account_id": account_id or "default",
        "total_assets": _float(summary.get("total_assets")),
        "cash": _float(summary.get("cash")),
        "market_value": _float(summary.get("market_value")),
        "cash_pct": _cash_pct(summary),
        "position_usage_pct": _position_usage_pct(summary),
        "holding_pnl": _float(summary.get("unrealized_pnl")),
        "holding_pnl_pct": _float(summary.get("unrealized_pnl_pct")),
        "daily_change_pct": _float(summary.get("daily_pnl_pct")),
        "cash_source": summary.get("cash_source") or "unset",
        "captured_at": datetime.now().isoformat(timespec="seconds"),
    }


async def _latest_report_map(codes: list[str]) -> dict[str, dict[str, Any]]:
    if not codes:
        return {}
    db = await get_db()
    try:
        return await portfolio_repository.fetch_latest_report_map(db, codes)
    finally:
        await db.close()


async def _latest_snapshot_map(codes: list[str]) -> dict[str, dict[str, Any]]:
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            f"""
            SELECT id, code, name, snapshot_json, validation_json, summary_json, created_at
            FROM stock_data_snapshots
            WHERE code IN ({placeholders})
            ORDER BY code ASC, datetime(created_at) DESC, id DESC
            """,
            codes,
        )
    finally:
        await db.close()
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = row["code"]
        if code not in latest:
            latest[code] = dict(row)
    return latest


async def _watchlist_candidates(
    holding_codes: set[str],
    *,
    include_watchlist_candidates: bool,
    include_observation_pool: bool,
    candidate_codes: list[str] | None,
) -> tuple[list[dict[str, Any]], str]:
    clean_codes = [str(code).strip()[:6] for code in (candidate_codes or []) if str(code).strip()]
    if not clean_codes:
        return [], "none"
    params: list[Any] = []
    where = []
    where.append(f"code IN ({','.join('?' for _ in clean_codes)})")
    params.extend(clean_codes)
    if include_watchlist_candidates and not include_observation_pool:
        where.append("COALESCE(group_name, '默认') != '观察池'")
    sql_where = "WHERE " + " AND ".join(where) if where else ""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            f"""
            SELECT code, name, COALESCE(group_name, '默认') AS group_name,
                   COALESCE(sort_order, 0) AS sort_order
            FROM watchlist
            {sql_where}
            ORDER BY CASE WHEN COALESCE(group_name, '默认') = '观察池' THEN 1 ELSE 0 END,
                     sort_order ASC, added_at ASC
            """,
            params,
        )
    finally:
        await db.close()
    candidates = [dict(row) for row in rows if row["code"] not in holding_codes]
    scope = "selected"
    return candidates, scope


def _report_is_today(report: dict[str, Any] | None, date_text: str) -> bool:
    created = str((report or {}).get("created_at") or "")
    return bool(created.startswith(date_text))


def _flag(
    *,
    review_id: str,
    date_text: str,
    account_id: str,
    code: str,
    name: str,
    flag_type: str,
    severity: str,
    source: str,
    description: str,
    evidence: dict[str, Any],
    requires_full_report: bool = False,
    source_report_id: int | None = None,
    source_snapshot_id: int | None = None,
) -> dict[str, Any]:
    return {
        "review_id": review_id,
        "date": date_text,
        "account_id": account_id,
        "code": code,
        "name": name,
        "flag_type": flag_type,
        "severity": severity,
        "source": source,
        "description": description,
        "evidence": evidence,
        "requires_full_report": 1 if requires_full_report else 0,
        "source_report_id": source_report_id,
        "source_snapshot_id": source_snapshot_id,
    }


def _holding_item(
    position: dict[str, Any],
    report: dict[str, Any] | None,
    snapshot: dict[str, Any] | None,
    date_text: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    code = str(position.get("code") or "")[:6]
    name = position.get("name") or code
    latest_signal = (report or {}).get("signal")
    latest_risk = (report or {}).get("risk_score")
    needs_report = not _report_is_today(report, date_text)
    action_hint = "hold"
    reasons: list[str] = []
    flags: list[dict[str, Any]] = []

    change_pct = _float(position.get("change_pct"))
    holding_pnl_pct = _float(position.get("unrealized_pnl_pct"))
    position_pct = _float(position.get("weight_pct"))

    if change_pct >= 9.8:
        flags.append(("limit_up", "warning", "涨幅接近或达到涨停，需要检查是否继续持有。", False))
    elif change_pct >= 5:
        flags.append(("large_up", "info", "当日涨幅超过 5%，进入收益保护观察。", False))
    if change_pct <= -9.8:
        flags.append(("limit_down", "critical", "跌幅接近或达到跌停，需要优先复核风险。", True))
        action_hint = "review"
    elif change_pct <= -5:
        flags.append(("large_down", "warning", "当日跌幅超过 5%，需要复核下跌原因。", True))
        action_hint = "review"
    if position_pct >= 40:
        flags.append(("position_overweight", "warning", f"单股仓位 {position_pct:.3f}% 偏高。", False))
    if holding_pnl_pct <= -8:
        flags.append(("holding_loss_alert", "warning", f"持仓亏损 {holding_pnl_pct:.3f}% 接近止损区。", True))
        action_hint = "review"
    if holding_pnl_pct >= 15:
        flags.append(("holding_profit_take_zone", "info", f"持仓盈利 {holding_pnl_pct:.3f}% 进入止盈观察区。", False))
        action_hint = "take_profit"
    if latest_signal in NEGATIVE_SIGNALS:
        flags.append(("signal_conflict", "critical", f"当前仍持仓，但最新报告信号为 {latest_signal}。", True))
        action_hint = "reduce"
    if needs_report:
        flags.append(("report_stale", "warning", "当前持仓没有当日报告，建议补跑单股报告。", True))
    if not snapshot:
        flags.append(("snapshot_stale", "warning", "当前持仓没有七层快照，建议先补齐数据。", True))

    item = {
        "item_type": "holding",
        "code": code,
        "name": name,
        "source_group": "portfolio",
        "shares": _float(position.get("total_shares")),
        "avg_cost": _float(position.get("avg_cost")),
        "price": _float(position.get("price")),
        "change_pct": change_pct,
        "market_value": _float(position.get("market_value")),
        "position_pct": position_pct,
        "holding_pnl": _float(position.get("unrealized_pnl")),
        "holding_pnl_pct": holding_pnl_pct,
        "latest_signal": latest_signal,
        "latest_risk_score": _float(latest_risk) if latest_risk is not None else None,
        "latest_report_id": (report or {}).get("id"),
        "latest_report_created_at": (report or {}).get("created_at"),
        "latest_snapshot_id": (snapshot or {}).get("id"),
        "needs_report": 1 if needs_report or any(flag[3] for flag in flags) else 0,
        "action_hint": action_hint,
        "reason": "；".join(reasons) if reasons else ACTION_LABELS.get(action_hint, action_hint),
    }
    return item, [
        _flag(
            review_id="",
            date_text="",
            account_id="",
            code=code,
            name=name,
            flag_type=flag_type,
            severity=severity,
            source="holding",
            description=description,
            evidence={"change_pct": change_pct, "holding_pnl_pct": holding_pnl_pct, "position_pct": position_pct, "latest_signal": latest_signal},
            requires_full_report=requires,
            source_report_id=(report or {}).get("id"),
            source_snapshot_id=(snapshot or {}).get("id"),
        )
        for flag_type, severity, description, requires in flags
    ]


def _candidate_item(candidate: dict[str, Any], quote: dict[str, Any], report: dict[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    code = str(candidate.get("code") or "")[:6]
    name = quote.get("name") or candidate.get("name") or code
    latest_signal = (report or {}).get("signal")
    item = {
        "item_type": "candidate",
        "code": code,
        "name": name,
        "source_group": candidate.get("group_name") or "默认",
        "shares": 0.0,
        "avg_cost": 0.0,
        "price": _float(quote.get("price")),
        "change_pct": _float(quote.get("change_pct")),
        "market_value": 0.0,
        "position_pct": 0.0,
        "holding_pnl": 0.0,
        "holding_pnl_pct": 0.0,
        "latest_signal": latest_signal,
        "latest_risk_score": _float((report or {}).get("risk_score")) if (report or {}).get("risk_score") is not None else None,
        "latest_report_id": (report or {}).get("id"),
        "latest_report_created_at": (report or {}).get("created_at"),
        "latest_snapshot_id": None,
        "needs_report": 0,
        "action_hint": "candidate",
        "reason": "自选股候选池，仅作为替代或加仓候选，不参与当前持仓风险主体。",
    }
    flags = []
    if latest_signal in POSITIVE_SIGNALS:
        flags.append(
            _flag(
                review_id="",
                date_text="",
                account_id="",
                code=code,
                name=name,
                flag_type="positive_candidate",
                severity="info",
                source="watchlist",
                description=f"自选候选股最新报告信号为 {latest_signal}，可作为替代候选。",
                evidence={"latest_signal": latest_signal, "change_pct": item["change_pct"]},
                source_report_id=(report or {}).get("id"),
            )
        )
    return item, flags


def _flag_count(flags: list[dict[str, Any]], flag_type: str) -> int:
    return sum(1 for flag in flags if flag.get("flag_type") == flag_type)


async def _settings_map() -> dict[str, Any]:
    db = await get_db()
    try:
        rows = await db.execute_fetchall("SELECT key, value FROM settings")
    finally:
        await db.close()
    return {row["key"]: row["value"] for row in rows}


def _snapshot_available_layers(snapshot: dict[str, Any]) -> list[str]:
    layers = []
    for key, value in snapshot.items():
        if key.startswith("_"):
            continue
        if isinstance(value, dict) and value:
            layers.append(key)
        elif isinstance(value, list) and value:
            layers.append(key)
        elif value not in (None, "", [], {}):
            layers.append(key)
    return sorted(layers)


def _snapshot_highlights(snapshot: dict[str, Any]) -> list[str]:
    highlights: list[str] = []
    market = snapshot.get("market") if isinstance(snapshot.get("market"), dict) else {}
    quote = market.get("quote") if isinstance(market.get("quote"), dict) else {}
    if quote:
        highlights.append(f"行情: 现价 {_float(quote.get('price')):.3f}, 涨跌幅 {_float(quote.get('change_pct')):.3f}%")
    news = snapshot.get("news") if isinstance(snapshot.get("news"), dict) else {}
    news_items = news.get("items") or news.get("stock_news") or []
    if isinstance(news_items, list) and news_items:
        title = ""
        first = news_items[0]
        if isinstance(first, dict):
            title = str(first.get("title") or first.get("content") or "")[:40]
        highlights.append(f"新闻: {len(news_items)} 条" + (f"，首条 {title}" if title else ""))
    fundamentals = snapshot.get("fundamentals") if isinstance(snapshot.get("fundamentals"), dict) else {}
    if fundamentals:
        highlights.append("基本面: " + "、".join(list(fundamentals.keys())[:4]))
    hot_money = snapshot.get("hot_money") if isinstance(snapshot.get("hot_money"), dict) else {}
    if hot_money:
        flow = hot_money.get("main_net_inflow") or hot_money.get("main_net") or hot_money.get("net_inflow")
        highlights.append(f"资金: 主力净流入 {_float(flow):.3f}" if flow is not None else "资金: 有资金层数据")
    return highlights[:5]


def _layer_context(codes: list[str], snapshots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    items = []
    for code in codes:
        row = snapshots.get(code) or {}
        snapshot = _loads(row.get("snapshot_json"), {})
        validation = _loads(row.get("validation_json"), {})
        summary = _loads(row.get("summary_json"), {})
        if not row:
            items.append(
                {
                    "code": code,
                    "status": "missing",
                    "snapshot_id": None,
                    "created_at": "",
                    "available_layers": [],
                    "missing_layers": ["all"],
                    "empty_layers": [],
                    "layer_errors": {},
                    "highlights": ["没有七层快照"],
                }
            )
            continue
        items.append(
            {
                "code": code,
                "name": row.get("name") or code,
                "status": "ok" if validation.get("ok") else "partial",
                "snapshot_id": row.get("id"),
                "created_at": row.get("created_at") or "",
                "available_layers": _snapshot_available_layers(snapshot),
                "missing_layers": validation.get("missing_layers") or [],
                "empty_layers": validation.get("empty_layers") or [],
                "layer_errors": validation.get("layer_errors") or {},
                "summary": summary,
                "highlights": _snapshot_highlights(snapshot),
            }
        )
    ok_count = sum(1 for item in items if item.get("status") == "ok")
    return {"count": len(items), "ok_count": ok_count, "items": items}


async def _market_context() -> dict[str, Any]:
    captured_at = datetime.now().isoformat(timespec="seconds")
    context: dict[str, Any] = {"captured_at": captured_at, "status": "ok", "indices": [], "sentiment": {}}
    try:
        indices, sentiment = await asyncio.gather(
            asyncio.to_thread(get_index_quotes),
            get_market_sentiment(),
        )
        context["indices"] = indices or []
        context["sentiment"] = sentiment or {}
    except Exception as exc:  # noqa: BLE001 - daily review should still be generated when market context is temporarily unavailable
        context["status"] = "partial"
        context["error"] = str(exc)
    breadth = (context.get("sentiment") or {}).get("breadth") or {}
    total = _float(breadth.get("total"))
    up = _float(breadth.get("up"))
    down = _float(breadth.get("down"))
    context["breadth_summary"] = {
        "up": up,
        "down": down,
        "up_pct": round(up / total * 100, 3) if total else 0.0,
        "down_pct": round(down / total * 100, 3) if total else 0.0,
        "limit_up": _float(breadth.get("limit_up")),
        "limit_down": _float(breadth.get("limit_down")),
        "total": total,
    }
    return context


async def _create_report_refresh_job(codes: list[str], *, date_text: str) -> dict[str, Any]:
    clean_codes = list(dict.fromkeys(code for code in codes if code))
    if not clean_codes:
        return {"status": "not_needed", "codes": []}
    try:
        result = await batch_report_service.create_research_job(
            job_type="report_generation",
            codes=clean_codes,
            skip_recent_days=0,
            refresh_snapshots=False,
            analysis_mode="snapshot-tradingagents",
            analysis_depth="standard",
            model_mode="balanced",
            title=f"持仓日更补报告 {date_text}",
            auto_start=True,
        )
        return {"status": "created", "codes": clean_codes, **result}
    except Exception as exc:  # noqa: BLE001 - keep daily review durable even when background job creation fails
        return {"status": "failed", "codes": clean_codes, "error": str(exc)}


def _battle_plan(
    holdings: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    flags: list[dict[str, Any]],
    asset_snapshot: dict[str, Any],
    investment_profile: dict[str, Any],
    market_context: dict[str, Any],
) -> dict[str, Any]:
    flags_by_code: dict[str, list[dict[str, Any]]] = {}
    for flag in flags:
        flags_by_code.setdefault(flag.get("code") or "", []).append(flag)
    max_single_pct = _float(investment_profile.get("max_single_position_pct"), 15.0)
    min_cash_pct = _float(investment_profile.get("min_cash_pct"), 5.0)
    market_status = "偏强" if _float((market_context.get("breadth_summary") or {}).get("up_pct")) >= 55 else "分化或偏弱"
    holding_management = []
    do_not_touch = []
    trigger_conditions = []
    for item in holdings:
        code_flags = flags_by_code.get(item["code"], [])
        action = item.get("action_hint") or "hold"
        holding_management.append(
            {
                "code": item["code"],
                "name": item["name"],
                "action": action,
                "action_label": ACTION_LABELS.get(action, action),
                "position_pct": item.get("position_pct") or 0,
                "reason": item.get("reason") or "",
                "trigger_count": len(code_flags),
            }
        )
        if any(flag.get("severity") == "critical" for flag in code_flags):
            do_not_touch.append(
                {
                    "code": item["code"],
                    "name": item["name"],
                    "reason": "存在高风险触发项，未完成复核前禁止加仓。",
                }
            )
        trigger_conditions.append(
            {
                "code": item["code"],
                "name": item["name"],
                "condition": "若补报告后信号仍为减持/卖出，或跌破纪律线，优先降仓；若放量转强且风险解除，再恢复观察。",
            }
        )
    offensive_candidates = []
    for item in candidates:
        signal = str(item.get("latest_signal") or "").upper()
        positive = signal in POSITIVE_SIGNALS
        offensive_candidates.append(
            {
                "code": item["code"],
                "name": item["name"],
                "signal": signal or "--",
                "eligible": bool(positive),
                "condition": "只在市场环境不弱、放量突破或回踩确认后试仓；首仓不得突破单票仓位上限。",
            }
        )
        if not positive:
            do_not_touch.append({"code": item["code"], "name": item["name"], "reason": "候选股没有正向报告信号，不作为明日进攻标的。"})
    if _float(asset_snapshot.get("cash_pct")) < min_cash_pct:
        do_not_touch.append({"code": "CASH", "name": "现金约束", "reason": f"现金占比低于最低保留 {min_cash_pct:.3f}%，禁止新增进攻仓位。"})
    return {
        "market_status": market_status,
        "style_constraints": {
            "style": investment_profile.get("label") or "",
            "max_single_position_pct": max_single_pct,
            "min_cash_pct": min_cash_pct,
        },
        "holding_management": holding_management,
        "offensive_candidates": offensive_candidates,
        "do_not_touch": do_not_touch,
        "trigger_conditions": trigger_conditions,
    }


def _role_discussion(
    asset_snapshot: dict[str, Any],
    holdings: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    flags: list[dict[str, Any]],
    rerun_report_codes: list[str],
) -> list[dict[str, Any]]:
    critical_count = sum(1 for flag in flags if flag.get("severity") == "critical")
    warning_count = sum(1 for flag in flags if flag.get("severity") == "warning")
    signal_conflicts = _flag_count(flags, "signal_conflict")
    stale_reports = _flag_count(flags, "report_stale")
    overweight_positions = _flag_count(flags, "position_overweight")
    positive_candidates = _flag_count(flags, "positive_candidate")
    cash = _float(asset_snapshot.get("cash"))
    position_usage_pct = _float(asset_snapshot.get("position_usage_pct"))
    cash_pct = _float(asset_snapshot.get("cash_pct"))

    manager_view = (
        f"当前持仓 {len(holdings)} 只，仓位使用率 {position_usage_pct:.3f}%，可用资金 {cash:.3f}，"
        f"现金占比 {cash_pct:.3f}%。"
    )
    if critical_count:
        manager_view += f"先处理 {critical_count} 个高风险触发项，明日以风险收敛和仓位管理为主。"
    elif positive_candidates:
        manager_view += f"候选池有 {positive_candidates} 个正向信号，可在持仓风险清理后作为替代观察。"
    else:
        manager_view += "未出现必须立刻调仓的组合级信号，默认维持持仓观察。"

    risk_parts = []
    if signal_conflicts:
        risk_parts.append(f"存在 {signal_conflicts} 个信号冲突")
    if stale_reports:
        risk_parts.append(f"{stale_reports} 只持仓缺少当日报告")
    if overweight_positions:
        risk_parts.append(f"{overweight_positions} 只股票仓位偏高")
    if warning_count and not risk_parts:
        risk_parts.append(f"{warning_count} 个提醒级触发项")
    risk_view = "；".join(risk_parts) if risk_parts else "暂无高优先级风控异常"
    risk_view += "。风控口径是不在信息缺失或信号冲突时扩大仓位。"

    trade_actions = []
    if rerun_report_codes:
        trade_actions.append(f"补跑完整报告: {', '.join(rerun_report_codes)}")
    reduce_codes = [item["code"] for item in holdings if item.get("action_hint") == "reduce"]
    if reduce_codes:
        trade_actions.append(f"减仓观察: {', '.join(reduce_codes)}")
    review_codes = [item["code"] for item in holdings if item.get("action_hint") == "review"]
    if review_codes:
        trade_actions.append(f"重点复核: {', '.join(review_codes)}")
    if positive_candidates:
        trade_actions.append("候选池仅进入观察，不自动转为交易")
    if not trade_actions:
        trade_actions.append("维持原仓位，等待新的触发条件")

    return [
        {
            "role": "持仓经理",
            "stance": "组合优先",
            "view": manager_view,
            "action_items": [
                "先看真实仓位和可用资金，再讨论候选股",
                "持仓风险未处理前，不把候选池当作默认买入清单",
            ],
        },
        {
            "role": "风控经理",
            "stance": "风险约束",
            "view": risk_view,
            "action_items": [
                "高风险触发项优先于进攻动作",
                "缺少当日报告或七层快照时，先补数据再扩大决策",
            ],
        },
        {
            "role": "交易员/最终执行",
            "stance": "明日动作",
            "view": "；".join(trade_actions) + "。",
            "action_items": trade_actions,
        },
    ]


def _markdown(
    review: dict[str, Any],
    holdings: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    flags: list[dict[str, Any]],
    role_discussion: list[dict[str, Any]],
    tomorrow_plan: dict[str, Any],
) -> str:
    asset = review["asset_snapshot"]
    profile = tomorrow_plan.get("investment_profile") or {}
    market = tomorrow_plan.get("market_context") or {}
    layer_context = tomorrow_plan.get("layer_context") or {}
    battle_plan = tomorrow_plan.get("battle_plan") or {}
    report_refresh = tomorrow_plan.get("report_refresh_job") or {}
    lines = [
        f"# 明日交易作战计划 {review['date']}",
        "",
        "## 资产上下文",
        "",
        f"- 总资产: {asset['total_assets']:.3f}",
        f"- 可用资金: {asset['cash']:.3f}",
        f"- 持仓市值: {asset['market_value']:.3f}",
        f"- 现金占比: {asset['cash_pct']:.3f}%",
        f"- 仓位使用率: {asset['position_usage_pct']:.3f}%",
        f"- 持仓盈亏: {asset['holding_pnl']:.3f} ({asset['holding_pnl_pct']:.3f}%)",
        "",
        "> 所有建议必须基于当前真实仓位和可用资金，不允许默认全仓建仓。",
        "",
        "## 用户投资风格",
        "",
        f"- 风格: {profile.get('label') or '--'} ({profile.get('preset') or '--'})",
        f"- 单票仓位上限: {profile.get('max_single_position_pct') or '--'}%",
        f"- 最低现金保留: {profile.get('min_cash_pct') or '--'}%",
        f"- 买入触发偏好: {profile.get('entry_preference') or '--'}",
        f"- 卖出纪律: {profile.get('exit_discipline') or '--'}",
        "",
        "## 大盘与板块环境",
        "",
        f"- 状态: {market.get('status') or '--'}，市场判断: {battle_plan.get('market_status') or '--'}",
    ]
    for item in market.get("indices") or []:
        lines.append(f"- {item.get('name') or item.get('code')}: {_float(item.get('price')):.3f} / {_float(item.get('change_pct')):.3f}%")
    breadth = market.get("breadth_summary") or {}
    if breadth:
        lines.append(
            f"- 涨跌家数: 上涨 {breadth.get('up') or 0:.0f} / 下跌 {breadth.get('down') or 0:.0f}，上涨占比 {breadth.get('up_pct') or 0:.3f}%"
        )
    lines.extend(
        [
            "",
            "## 七层快照摘要",
            "",
        ]
    )
    for item in layer_context.get("items") or []:
        layers = "、".join(item.get("available_layers") or []) or "无"
        missing = "、".join(item.get("missing_layers") or []) or "无"
        lines.append(f"- {item.get('name') or item.get('code')} {item.get('code')}: {item.get('status')}，可用层 {layers}，缺失 {missing}")
        for highlight in item.get("highlights") or []:
            lines.append(f"  - {highlight}")
    lines.extend(
        [
            "",
            "## 当日报告补齐任务",
            "",
            f"- 状态: {report_refresh.get('status') or '--'}",
            f"- 标的: {', '.join(report_refresh.get('codes') or []) or '无'}",
            f"- 任务: {report_refresh.get('job_id') or '--'}",
            "",
            "## 当前持仓",
            "",
            "| 股票 | 仓位 | 成本 | 现价 | 涨跌幅 | 持仓盈亏 | 动作提示 |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    if holdings:
        for item in holdings:
            lines.append(
                f"| {item['name']} {item['code']} | {item['position_pct']:.3f}% | {item['avg_cost']:.3f} | "
                f"{item['price']:.3f} | {item['change_pct']:.3f}% | {item['holding_pnl']:.3f} | "
                f"{ACTION_LABELS.get(item['action_hint'], item['action_hint'])} |"
            )
    else:
        lines.append("| 无持仓 | 0.000% | 0.000 | 0.000 | 0.000% | 0.000 | 空仓 |")
    lines.extend(["", "## 触发项", ""])
    if flags:
        for flag in flags:
            lines.append(f"- [{flag['severity']}] {flag['name']} {flag['code']}: {flag['description']}")
    else:
        lines.append("- 暂无异常触发项。")
    if candidates:
        lines.extend(["", "## 自选候选池", "", "| 股票 | 分组 | 现价 | 涨跌幅 | 最新信号 |", "|---|---|---:|---:|---|"])
        for item in candidates:
            lines.append(f"| {item['name']} {item['code']} | {item['source_group']} | {item['price']:.3f} | {item['change_pct']:.3f}% | {item.get('latest_signal') or '--'} |")
    lines.extend(["", "## 三角色讨论", ""])
    for role in role_discussion:
        lines.extend(
            [
                f"### {role['role']} ({role['stance']})",
                "",
                role["view"],
                "",
            ]
        )
        for action in role.get("action_items") or []:
            lines.append(f"- {action}")
        lines.append("")
    lines.extend(["", "## 作战清单", ""])
    sections = [
        ("持仓管理建议", "holding_management", "action_label"),
        ("明日进攻候选", "offensive_candidates", "condition"),
        ("禁止操作清单", "do_not_touch", "reason"),
        ("触发条件", "trigger_conditions", "condition"),
    ]
    for title, key, detail_key in sections:
        lines.extend([f"### {title}", ""])
        items = battle_plan.get(key) or []
        if not items:
            lines.append("- 无")
        for item in items:
            lines.append(f"- {item.get('name') or item.get('code')} {item.get('code') or ''}: {item.get(detail_key) or item.get('reason') or '--'}")
        lines.append("")
    lines.extend(
        [
            "",
            "## 明日建议口径",
            "",
            "先处理持仓风险，再考虑自选候选股。现金、仓位、单股集中度是硬约束；自选股只作为替代候选或加仓候选，不自动转为交易。",
        ]
    )
    return "\n".join(lines)


def _row_to_review(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["asset_snapshot"] = _loads(item.pop("asset_snapshot_json", "{}"), {})
    item["portfolio_snapshot"] = _loads(item.pop("portfolio_snapshot_json", "{}"), {})
    item["candidate_context"] = _loads(item.pop("candidate_context_json", "{}"), {})
    item["market_snapshot"] = _loads(item.pop("market_snapshot_json", "{}"), {})
    item["source_report_ids"] = _loads(item.pop("source_report_ids_json", "[]"), [])
    item["source_snapshot_ids"] = _loads(item.pop("source_snapshot_ids_json", "[]"), [])
    item["rerun_report_codes"] = _loads(item.pop("rerun_report_codes_json", "[]"), [])
    item["tomorrow_plan"] = _loads(item.pop("tomorrow_plan_json", "{}"), {})
    item["model_config"] = _loads(item.pop("model_config_json", "{}"), {})
    return item


async def run_daily_review(
    *,
    account_id: str = "default",
    date_text: str | None = None,
    include_watchlist_candidates: bool = False,
    include_observation_pool: bool = False,
    candidate_codes: list[str] | None = None,
) -> dict[str, Any]:
    date_text = date_text or _date_today()
    account_id = account_id or "default"
    review_id = _review_id()
    positions, summary = await portfolio_service._portfolio_snapshot(account_id)
    holding_codes = {position["code"] for position in positions}
    candidate_rows, candidate_scope = await _watchlist_candidates(
        holding_codes,
        include_watchlist_candidates=include_watchlist_candidates,
        include_observation_pool=include_observation_pool,
        candidate_codes=candidate_codes,
    )
    candidate_codes_clean = [row["code"] for row in candidate_rows]
    all_codes = [position["code"] for position in positions] + candidate_codes_clean
    latest_reports = await _latest_report_map(all_codes)
    latest_snapshots = await _latest_snapshot_map(all_codes)
    candidate_quotes = await portfolio_service.get_batch_quotes(candidate_codes_clean) if candidate_codes_clean else {}

    holdings: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    for position in positions:
        item, item_flags = _holding_item(position, latest_reports.get(position["code"]), latest_snapshots.get(position["code"]), date_text)
        holdings.append(item)
        flags.extend(item_flags)
    for candidate in candidate_rows:
        item, item_flags = _candidate_item(candidate, candidate_quotes.get(candidate["code"], {}), latest_reports.get(candidate["code"]))
        candidates.append(item)
        flags.extend(item_flags)

    for flag in flags:
        flag["review_id"] = review_id
        flag["date"] = date_text
        flag["account_id"] = account_id

    asset_snapshot = _asset_snapshot(summary, account_id)
    portfolio_snapshot = {"items": holdings, "count": len(holdings), "captured_at": asset_snapshot["captured_at"]}
    candidate_context = {"scope": candidate_scope, "items": candidates, "count": len(candidates), "captured_at": asset_snapshot["captured_at"]}
    source_report_ids = sorted({int(item["latest_report_id"]) for item in holdings + candidates if item.get("latest_report_id")})
    source_snapshot_ids = sorted({int(item["latest_snapshot_id"]) for item in holdings if item.get("latest_snapshot_id")})
    rerun_report_codes = sorted({flag["code"] for flag in flags if flag.get("requires_full_report")})
    critical_count = sum(1 for flag in flags if flag.get("severity") == "critical")
    settings = await _settings_map()
    investment_profile = investment_profile_service.investment_profile_snapshot(settings)
    layer_context = _layer_context(all_codes, latest_snapshots)
    market_context = await _market_context()
    report_refresh_job = await _create_report_refresh_job(rerun_report_codes, date_text=date_text)
    battle_plan = _battle_plan(holdings, candidates, flags, asset_snapshot, investment_profile, market_context)
    role_discussion = _role_discussion(asset_snapshot, holdings, candidates, flags, rerun_report_codes)

    review = {
        "review_id": review_id,
        "date": date_text,
        "account_id": account_id,
        "status": "completed",
        "holding_count": len(holdings),
        "candidate_count": len(candidates),
        "trigger_count": len(flags),
        "critical_count": critical_count,
        "candidate_scope": candidate_scope,
        "asset_snapshot": asset_snapshot,
        "portfolio_snapshot": portfolio_snapshot,
        "candidate_context": candidate_context,
        "market_snapshot": market_context,
        "source_report_ids": source_report_ids,
        "source_snapshot_ids": source_snapshot_ids,
        "rerun_report_codes": rerun_report_codes,
        "summary": f"{date_text} 持仓 {len(holdings)} 只，触发 {len(flags)} 项，其中高风险 {critical_count} 项；候选池 {len(candidates)} 只。",
    }
    review["tomorrow_plan"] = {
        "title": "明日交易作战计划",
        "constraint": "所有建议必须基于当前真实仓位和可用资金，不允许默认全仓建仓。",
        "role_discussion": role_discussion,
        "report_refresh_job": report_refresh_job,
        "investment_profile": investment_profile,
        "layer_context": layer_context,
        "market_context": market_context,
        "battle_plan": battle_plan,
        "rerun_report_codes": rerun_report_codes,
        "candidate_scope": candidate_scope,
        "candidate_count": len(candidates),
    }
    review["tomorrow_plan_markdown"] = _markdown(review, holdings, candidates, flags, role_discussion, review["tomorrow_plan"])

    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO holding_daily_reviews (
                review_id, date, account_id, status, holding_count, candidate_count,
                trigger_count, critical_count, candidate_scope, rerun_report_codes_json,
                source_report_ids_json, source_snapshot_ids_json, asset_snapshot_json,
                portfolio_snapshot_json, candidate_context_json, market_snapshot_json,
                summary, tomorrow_plan_markdown, tomorrow_plan_json, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                review_id,
                date_text,
                account_id,
                "completed",
                len(holdings),
                len(candidates),
                len(flags),
                critical_count,
                candidate_scope,
                _dumps(rerun_report_codes),
                _dumps(source_report_ids),
                _dumps(source_snapshot_ids),
                _dumps(asset_snapshot),
                _dumps(portfolio_snapshot),
                _dumps(candidate_context),
                _dumps(review["market_snapshot"]),
                review["summary"],
                review["tomorrow_plan_markdown"],
                _dumps(review["tomorrow_plan"]),
            ),
        )
        for item in holdings + candidates:
            await db.execute(
                """
                INSERT INTO holding_review_items (
                    review_id, date, account_id, item_type, code, name, source_group,
                    shares, avg_cost, price, change_pct, market_value, position_pct,
                    holding_pnl, holding_pnl_pct, latest_signal, latest_risk_score,
                    latest_report_id, latest_report_created_at, needs_report,
                    action_hint, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    date_text,
                    account_id,
                    item["item_type"],
                    item["code"],
                    item["name"],
                    item.get("source_group"),
                    item["shares"],
                    item["avg_cost"],
                    item["price"],
                    item["change_pct"],
                    item["market_value"],
                    item["position_pct"],
                    item["holding_pnl"],
                    item["holding_pnl_pct"],
                    item.get("latest_signal"),
                    item.get("latest_risk_score"),
                    item.get("latest_report_id"),
                    item.get("latest_report_created_at"),
                    item.get("needs_report") or 0,
                    item.get("action_hint"),
                    item.get("reason"),
                ),
            )
        for flag in flags:
            await db.execute(
                """
                INSERT INTO holding_trigger_flags (
                    review_id, date, account_id, code, name, flag_type, severity,
                    source, description, evidence_json, requires_full_report,
                    source_report_id, source_snapshot_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    date_text,
                    account_id,
                    flag["code"],
                    flag["name"],
                    flag["flag_type"],
                    flag["severity"],
                    flag["source"],
                    flag["description"],
                    _dumps(flag.get("evidence") or {}),
                    flag.get("requires_full_report") or 0,
                    flag.get("source_report_id"),
                    flag.get("source_snapshot_id"),
                ),
            )
        await db.commit()
    finally:
        await db.close()
    return review


async def list_reviews(limit: int = 30, account_id: str | None = None) -> dict[str, Any]:
    params: list[Any] = []
    where = ""
    if account_id:
        where = "WHERE account_id = ?"
        params.append(account_id)
    params.append(max(1, min(int(limit or 30), 200)))
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            f"""
            SELECT * FROM holding_daily_reviews
            {where}
            ORDER BY date DESC, datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            params,
        )
    finally:
        await db.close()
    reviews = [_row_to_review(row) for row in rows]
    return {"count": len(reviews), "reviews": reviews}


async def get_review(review_id: str) -> dict[str, Any]:
    db = await get_db()
    try:
        row = await (await db.execute("SELECT * FROM holding_daily_reviews WHERE review_id = ?", (review_id,))).fetchone()
    finally:
        await db.close()
    if not row:
        raise HTTPException(404, "持仓日更不存在")
    return _row_to_review(row)


async def get_review_items(review_id: str) -> dict[str, Any]:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """
            SELECT * FROM holding_review_items
            WHERE review_id = ?
            ORDER BY CASE item_type WHEN 'holding' THEN 0 ELSE 1 END,
                     position_pct DESC, source_group ASC, code ASC
            """,
            (review_id,),
        )
    finally:
        await db.close()
    return {"count": len(rows), "items": [dict(row) for row in rows]}


async def get_review_flags(review_id: str) -> dict[str, Any]:
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            """
            SELECT * FROM holding_trigger_flags
            WHERE review_id = ?
            ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                     id ASC
            """,
            (review_id,),
        )
    finally:
        await db.close()
    flags = []
    for row in rows:
        item = dict(row)
        item["evidence"] = _loads(item.pop("evidence_json", "{}"), {})
        flags.append(item)
    return {"count": len(flags), "flags": flags}


async def archive_review(review_id: str) -> dict[str, Any]:
    db = await get_db()
    try:
        cursor = await db.execute(
            "UPDATE holding_daily_reviews SET status = 'archived', updated_at = datetime('now') WHERE review_id = ?",
            (review_id,),
        )
        await db.commit()
    finally:
        await db.close()
    if cursor.rowcount == 0:
        raise HTTPException(404, "持仓日更不存在")
    return {"status": "ok", "review_id": review_id}
