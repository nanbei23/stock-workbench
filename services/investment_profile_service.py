"""Investment style profile helpers shared by reports and position plans."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from config import DB_PATH

INVESTMENT_PROFILE_VERSION = "investment-profile-v2"


DEFAULT_INVESTMENT_SETTINGS = {
    "investment_style_preset": "balanced",
    "investment_max_single_position_pct": "15",
    "investment_min_cash_pct": "5",
    "investment_max_drawdown_pct": "12",
    "investment_entry_preference": "右侧确认、趋势突破、资金流入、题材催化",
    "investment_exit_discipline": "逻辑失效、跌破关键位、硬止损、分批止盈",
    "investment_allow_left_side": "false",
    "investment_allow_high_volatility": "cautious",
    "investment_custom_notes": "",
    "investment_profile_inferred_summary": "",
}


PRESET_LABELS = {
    "conservative": "稳健型",
    "balanced": "均衡型",
    "aggressive": "进攻型",
    "speculative": "激进型",
    "custom": "自定义",
}


VOLATILITY_LABELS = {
    "allow": "允许高波动题材，但必须给出止损和失效条件",
    "cautious": "谨慎参与高波动题材，优先要求趋势和资金确认",
    "forbid": "禁止高波动题材",
}


def _float_text(value: Any, default: str) -> str:
    try:
        return f"{float(str(value).replace(',', '')):.3f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return default


def _bool_text(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on", "allow"}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _safe_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("T", " ").split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:len(fmt)], fmt)
        except ValueError:
            continue
    return None


def normalize_investment_profile(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = {**DEFAULT_INVESTMENT_SETTINGS, **(settings or {})}
    preset = str(raw.get("investment_style_preset") or "balanced").strip() or "balanced"
    label = PRESET_LABELS.get(preset, PRESET_LABELS["balanced"])
    return {
        "preset": preset,
        "label": label,
        "max_single_position_pct": _float_text(raw.get("investment_max_single_position_pct"), "30"),
        "min_cash_pct": _float_text(raw.get("investment_min_cash_pct"), "5"),
        "max_drawdown_pct": _float_text(raw.get("investment_max_drawdown_pct"), "12"),
        "entry_preference": str(raw.get("investment_entry_preference") or "").strip(),
        "exit_discipline": str(raw.get("investment_exit_discipline") or "").strip(),
        "allow_left_side": _bool_text(raw.get("investment_allow_left_side")),
        "allow_high_volatility": str(raw.get("investment_allow_high_volatility") or "cautious").strip(),
        "custom_notes": str(raw.get("investment_custom_notes") or "").strip(),
        "inferred_summary": str(raw.get("investment_profile_inferred_summary") or "").strip(),
    }


def investment_profile_context(settings: dict[str, Any] | None = None) -> str:
    profile = normalize_investment_profile(settings)
    left_side = "允许左侧试仓，但必须小仓位、明确止损和补仓条件" if profile["allow_left_side"] else "不允许左侧交易，优先等待右侧确认或触发条件"
    volatility = VOLATILITY_LABELS.get(profile["allow_high_volatility"], VOLATILITY_LABELS["cautious"])
    style_guidance = {
        "conservative": "优先保护本金，机会不充分时宁可观察，仓位建议偏低。",
        "balanced": "在胜率和赔率之间平衡，机会成立时允许分批建仓。",
        "aggressive": "更重视趋势确认、资金强度、题材催化和赔率；机会成立时不要默认 HOLD，应给出试仓、加仓、观察和放弃条件。",
        "speculative": "允许更高波动和更强进攻性，但必须给出硬止损、失效条件和仓位上限。",
        "custom": "优先遵循用户自定义说明，同时保留可审计的风控约束。",
    }.get(profile["preset"], "")
    lines = [
        "【用户投资风格画像】",
        f"- 画像版本：{INVESTMENT_PROFILE_VERSION}",
        f"- 风格档位：{profile['label']}（{profile['preset']}）",
        f"- 决策偏好：{style_guidance}",
        f"- 单票仓位上限：{profile['max_single_position_pct']}%",
        f"- 最低现金保留：{profile['min_cash_pct']}%",
        f"- 可接受最大回撤：{profile['max_drawdown_pct']}%",
        f"- 买入触发偏好：{profile['entry_preference']}",
        f"- 卖出纪律：{profile['exit_discipline']}",
        f"- 左侧交易：{left_side}",
        f"- 高波动题材：{volatility}",
        "- 输出要求：最终报告必须说明“是否匹配当前投资风格”，并给出风格匹配度、试仓条件、加仓条件、放弃条件、止损位、最大仓位和不买的核心原因。",
        '- 结构化字段要求：raw_state 或 final_decision 中尽量包含 style_match={"match_score":0-100,"match_reason":"","trial_entry":"","add_condition":"","abandon_condition":"","stop_loss":"","max_position_pct":""}。',
    ]
    if profile["custom_notes"]:
        lines.append(f"- 用户自定义说明：{profile['custom_notes']}")
    if profile["inferred_summary"]:
        lines.append(f"- 交易历史推断摘要：{profile['inferred_summary']}")
    return "\n".join(lines)


def investment_profile_snapshot(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = normalize_investment_profile(settings)
    output_contract = {
        "style_match": {
            "match_score": "0-100，说明该标的/计划与当前投资风格的匹配度",
            "match_reason": "匹配或不匹配的核心原因",
            "trial_entry": "试仓条件",
            "add_condition": "加仓条件",
            "abandon_condition": "放弃条件",
            "stop_loss": "止损或失效条件",
            "max_position_pct": "不超过投资风格单票仓位上限",
        }
    }
    return {
        **profile,
        "version": INVESTMENT_PROFILE_VERSION,
        "output_contract": output_contract,
        "context": investment_profile_context(settings),
    }


def style_match_assessment(result: dict[str, Any] | None, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    result = result or {}
    profile = profile or investment_profile_snapshot({})
    preset = profile.get("preset") or "balanced"
    style_label = profile.get("label") or PRESET_LABELS.get(preset, "均衡型")
    signal = str(result.get("signal") or "").upper()
    confidence = _safe_float(result.get("confidence"), 0.0)
    if confidence > 1:
        confidence /= 100
    risk_score = _safe_float(result.get("risk_score"), 50.0)
    if risk_score <= 1:
        risk_score *= 100
    text = "\n".join(
        str(result.get(key) or "")
        for key in ("final_decision", "trader_plan", "reasoning", "investment_debate", "risk_debate")
    )
    score = 50
    if signal in {"STRONG_BUY", "BUY", "OVERWEIGHT"}:
        score += 16 if preset in {"aggressive", "speculative"} else 8
    if confidence >= 0.7:
        score += 12
    elif confidence >= 0.55:
        score += 6
    if risk_score <= 45:
        score += 10
    elif risk_score >= 75:
        score -= 12
    if any(token in text for token in ("突破", "放量", "资金", "题材", "试仓", "加仓")):
        score += 8 if preset in {"aggressive", "speculative"} else 4
    if any(token in text for token in ("止损", "失效", "破位", "放弃")):
        score += 8
    if not profile.get("allow_left_side") and any(token in text for token in ("左侧", "抄底")):
        score -= 10
    score = max(0, min(100, round(score, 3)))
    return {
        "profile_version": profile.get("version") or INVESTMENT_PROFILE_VERSION,
        "style_preset": preset,
        "style_label": style_label,
        "match_score": score,
        "match_reason": f"按{style_label}画像评估，结合信号、置信度、风险评分和交易计划完整性得到该匹配度。",
        "trial_entry": "参考报告交易计划中的试仓触发条件；若未写明，应等待右侧确认。",
        "add_condition": "仅在趋势、资金和题材继续共振且未突破单票仓位上限时加仓。",
        "abandon_condition": "触发报告失效条件、资金转弱、关键位破位或风格匹配度过低时放弃。",
        "stop_loss": "以报告中的关键支撑/硬止损为准；未写明时要求重跑或人工补充。",
        "max_position_pct": profile.get("max_single_position_pct") or DEFAULT_INVESTMENT_SETTINGS["investment_max_single_position_pct"],
    }


def infer_profile_from_trade_history(db_path: Path | None = None) -> dict[str, Any]:
    path = db_path or DB_PATH
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT code, name, direction, price, shares, amount, trade_time
            FROM trades
            ORDER BY trade_time ASC, id ASC
            """
        ).fetchall()
    trade_count = len(rows)
    buy_rows = [row for row in rows if str(row["direction"]).lower() in {"buy", "b", "买入"}]
    sell_rows = [row for row in rows if str(row["direction"]).lower() in {"sell", "s", "卖出"}]
    buy_amounts = [_safe_float(row["amount"]) for row in buy_rows]
    total_buy_amount = sum(buy_amounts)
    max_trade_amount = max(buy_amounts) if buy_amounts else 0.0
    concentration_pct = (max_trade_amount / total_buy_amount * 100) if total_buy_amount > 0 else 0.0
    code_count = len({row["code"] for row in rows})
    holding_days: list[float] = []
    first_buy_at: dict[str, datetime] = {}
    for row in rows:
        direction = str(row["direction"]).lower()
        code = row["code"]
        ts = _safe_datetime(row["trade_time"])
        if not ts:
            continue
        if direction in {"buy", "b", "买入"}:
            first_buy_at.setdefault(code, ts)
        elif direction in {"sell", "s", "卖出"} and code in first_buy_at:
            holding_days.append(max(0.0, (ts - first_buy_at.pop(code)).total_seconds() / 86400))
    avg_holding_days = sum(holding_days) / len(holding_days) if holding_days else None
    if trade_count == 0:
        preset = "balanced"
    elif concentration_pct >= 55 and (avg_holding_days is not None and avg_holding_days <= 1):
        preset = "speculative"
    elif concentration_pct >= 30 or (avg_holding_days is not None and avg_holding_days <= 5):
        preset = "aggressive"
    elif concentration_pct <= 15 and (avg_holding_days is None or avg_holding_days >= 20):
        preset = "conservative"
    else:
        preset = "balanced"
    preset_settings = {
        "conservative": ("10", "20", "6", "forbid", "false"),
        "balanced": ("15", "5", "12", "cautious", "false"),
        "aggressive": ("40", "3", "15", "allow", "false"),
        "speculative": ("50", "0", "20", "allow", "true"),
    }[preset]
    entry = {
        "conservative": "右侧确认优先，要求趋势站稳、基本面和资金面同时确认。",
        "balanced": "右侧确认、趋势突破、资金流入、题材催化，机会成立时分批建仓。",
        "aggressive": "右侧突破优先，要求放量站上关键位、资金连续流入、板块或题材共振；回踩不破关键支撑后转强可加仓。",
        "speculative": "强题材启动、放量突破、资金快速流入、板块情绪升温；允许小仓位左侧试错。",
    }[preset]
    exit_rule = {
        "conservative": "跌破关键支撑或基本面恶化时减仓，资金持续流出时退出；达到目标位分批止盈。",
        "balanced": "逻辑失效、跌破关键位、硬止损、分批止盈。",
        "aggressive": "跌破买入触发位或关键支撑先减仓，放量破位清仓；题材逻辑失效、资金连续流出或报告核心假设被证伪时退出；上涨后按压力位和目标位分批止盈。",
        "speculative": "触发硬止损立即退出，放量破位清仓；情绪退潮、资金转流出或题材证伪时退出。",
    }[preset]
    summary = (
        f"交易历史推断：共 {trade_count} 笔交易，买入 {len(buy_rows)} 笔，卖出 {len(sell_rows)} 笔，"
        f"覆盖 {code_count} 只股票，最大单笔买入占买入总额 {concentration_pct:.3f}%。"
    )
    if avg_holding_days is not None:
        summary += f" 已闭环交易平均持有 {avg_holding_days:.3f} 天。"
    return {
        "version": INVESTMENT_PROFILE_VERSION,
        "summary": summary,
        "metrics": {
            "trade_count": trade_count,
            "buy_count": len(buy_rows),
            "sell_count": len(sell_rows),
            "stock_count": code_count,
            "total_buy_amount": round(total_buy_amount, 3),
            "max_trade_amount": round(max_trade_amount, 3),
            "max_trade_concentration_pct": round(concentration_pct, 3),
            "avg_holding_days": round(avg_holding_days, 3) if avg_holding_days is not None else None,
        },
        "suggested_settings": {
            "investment_style_preset": preset,
            "investment_max_single_position_pct": preset_settings[0],
            "investment_min_cash_pct": preset_settings[1],
            "investment_max_drawdown_pct": preset_settings[2],
            "investment_entry_preference": entry,
            "investment_exit_discipline": exit_rule,
            "investment_allow_high_volatility": preset_settings[3],
            "investment_allow_left_side": preset_settings[4],
            "investment_profile_inferred_summary": summary,
            "investment_custom_notes": summary,
        },
    }


def investment_profile_from_db(db_path: Path | None = None) -> dict[str, Any]:
    path = db_path or DB_PATH
    with sqlite3.connect(str(path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    settings = {row["key"]: row["value"] for row in rows}
    return investment_profile_snapshot(settings)
