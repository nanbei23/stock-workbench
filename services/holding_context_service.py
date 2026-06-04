"""Per-stock holding context for account-aware report generation."""

from __future__ import annotations

import json
from typing import Any

from models.database import get_db


def _loads(value: Any, fallback: Any):
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed is not None else fallback


def _float(value: Any) -> float:
    try:
        return round(float(value or 0), 3)
    except (TypeError, ValueError):
        return 0.0


def _prompt_context(ctx: dict[str, Any]) -> str:
    if not ctx["is_holding"]:
        return (
            "## 当前账户持仓上下文\n"
            f"- 股票: {ctx['name']} {ctx['code']}\n"
            "- 当前账户未持仓，只能输出观察、建仓或回避建议。\n"
            "- 研究信号可以独立判断股票质量；账户信号应按空仓视角判断是否值得新开仓。\n"
        )
    return (
        "## 当前账户持仓上下文\n"
        f"- 股票: {ctx['name']} {ctx['code']}\n"
        f"- 真实持仓: {ctx['shares']:.3f} 股\n"
        f"- 持仓成本: {ctx['avg_cost']:.3f}\n"
        f"- 当前价: {ctx['current_price']:.3f}\n"
        f"- 持仓市值: {ctx['market_value']:.3f}\n"
        f"- 持仓盈亏: {ctx['holding_pnl']:.3f} ({ctx['holding_pnl_pct']:.3f}%)\n"
        f"- 仓位占比: {ctx['position_pct_of_assets']:.3f}%\n"
        f"- 可用资金: {ctx['cash']:.3f}\n"
        f"- 上次账户信号: {(ctx.get('last_report') or {}).get('signal') or '--'}\n"
        "- 研究信号只判断股票本身；账户信号必须结合成本、仓位、浮盈亏和可用资金。\n"
    )


async def build_holding_context(code: str, *, account_id: str = "default") -> dict[str, Any]:
    code = str(code or "")[:6]
    account_id = account_id or "default"
    db = await get_db()
    try:
        position = await (
            await db.execute(
                """
                SELECT code, name, total_shares, avg_cost, current_price, market_value,
                       unrealized_pnl, unrealized_pnl_pct, account_id
                FROM portfolio
                WHERE account_id = ? AND code = ?
                """,
                (account_id, code),
            )
        ).fetchone()
        cash_row = await (
            await db.execute("SELECT value FROM settings WHERE key = ?", (f"cash_balance_{account_id}",))
        ).fetchone()
        report = await (
            await db.execute(
                """
                SELECT id, code, signal, confidence, risk_score, raw_state, created_at
                FROM analysis_reports
                WHERE code = ?
                ORDER BY datetime(created_at) DESC, id DESC
                LIMIT 1
                """,
                (code,),
            )
        ).fetchone()
        tracking = await (
            await db.execute(
                """
                SELECT id, report_id, code, signal, status, entry_price, current_price, pnl_pct, excess_return
                FROM signal_tracking
                WHERE code = ?
                ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, id DESC
                LIMIT 1
                """,
                (code,),
            )
        ).fetchone()
        shadow = await (
            await db.execute(
                """
                SELECT code, name, total_shares, avg_cost, current_price, market_value,
                       unrealized_pnl, unrealized_pnl_pct
                FROM ai_shadow_positions
                WHERE code = ?
                LIMIT 1
                """,
                (code,),
            )
        ).fetchone()
    finally:
        await db.close()

    pos = dict(position) if position else {}
    shadow_position = dict(shadow) if shadow else {}
    last_report = dict(report) if report else {}
    if last_report:
        last_report["raw_state"] = _loads(last_report.get("raw_state"), {})

    cash = _float(cash_row["value"] if cash_row else 0)
    shares = _float(pos.get("total_shares"))
    avg_cost = _float(pos.get("avg_cost"))
    current_price = _float(pos.get("current_price"))
    market_value = _float(pos.get("market_value")) or round(shares * current_price, 3)
    total_assets = round(cash + market_value, 3)
    holding_pnl = _float(pos.get("unrealized_pnl")) or round((current_price - avg_cost) * shares, 3)
    cost_amount = round(avg_cost * shares, 3)
    holding_pnl_pct = _float(pos.get("unrealized_pnl_pct")) or (round(holding_pnl / cost_amount * 100, 3) if cost_amount else 0.0)

    ctx = {
        "version": "holding-context-v1",
        "account_id": account_id,
        "code": code,
        "name": pos.get("name") or shadow_position.get("name") or code,
        "is_holding": shares > 0,
        "shares": shares,
        "avg_cost": avg_cost,
        "current_price": current_price,
        "market_value": market_value,
        "cash": cash,
        "total_assets": total_assets,
        "position_pct_of_assets": round(market_value / total_assets * 100, 3) if total_assets else 0.0,
        "holding_pnl": holding_pnl,
        "holding_pnl_pct": holding_pnl_pct,
        "last_report": last_report,
        "signal_tracking": dict(tracking) if tracking else {},
        "shadow_position": shadow_position,
        "position_action_scope": "holding_action" if shares > 0 else "watch_only",
    }
    ctx["prompt_context"] = _prompt_context(ctx)
    return ctx
