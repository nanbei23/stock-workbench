"""L1 rule-engine suggestion and anomaly workflows."""

import asyncio
import logging
from datetime import datetime

from fastapi import HTTPException

from scheduler.ai_engine import (
    evaluate_suggestion,
    get_index_quotes,
    get_quote,
    get_watchlist_and_portfolio,
)

logger = logging.getLogger(__name__)


async def _quote_many(stocks: list[dict]) -> list:
    return await asyncio.gather(
        *[asyncio.to_thread(get_quote, stock["code"]) for stock in stocks]
    )


def _anomaly_from_suggestion(suggestion: dict, include_advice: bool = False) -> dict:
    anomaly = {
        **suggestion["anomaly"],
        "code": suggestion["code"],
        "name": suggestion["name"],
        "price": suggestion["price"],
        "change_pct": suggestion["change_pct"],
        "time": datetime.now().strftime("%H:%M"),
    }
    if include_advice:
        anomaly["l1_advice"] = suggestion["advice"]
    return anomaly


async def _get_northbound_summary() -> dict:
    try:
        from data.signal import get_northbound

        nb_data = await get_northbound()
        if not nb_data:
            return {}
        sh_net = nb_data.get("sh_net", 0) or 0
        sz_net = nb_data.get("sz_net", 0) or 0
        total = sh_net + sz_net
        return {
            "sh_connect": round(sh_net, 2),
            "sz_connect": round(sz_net, 2),
            "total": round(total, 2),
            "direction": "net_in" if total >= 0 else "net_out",
        }
    except Exception as e:
        logger.warning("获取北向资金失败: %s", e)
        return {}


async def get_suggestions():
    stocks = get_watchlist_and_portfolio()
    quotes = await _quote_many(stocks)
    indices, northbound = await asyncio.gather(
        asyncio.to_thread(get_index_quotes),
        _get_northbound_summary(),
    )

    suggestions = []
    anomalies = []
    for stock, quote in zip(stocks, quotes):
        if not quote:
            continue

        suggestion = evaluate_suggestion(stock, quote)
        suggestions.append(suggestion)
        if suggestion.get("anomaly"):
            anomalies.append(_anomaly_from_suggestion(suggestion))

    return {
        "suggestions": suggestions,
        "indices": indices,
        "northbound": northbound,
        "anomalies": anomalies,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


async def trigger_all(memory_log: list[dict]):
    stocks = get_watchlist_and_portfolio()
    quotes = await _quote_many(stocks)

    anomalies = []
    for stock, quote in zip(stocks, quotes):
        if not quote:
            continue

        suggestion = evaluate_suggestion(stock, quote)
        if suggestion.get("anomaly"):
            anomaly = _anomaly_from_suggestion(suggestion, include_advice=True)
            anomalies.append(anomaly)
            memory_log.append(anomaly)

    return {
        "checked": len(stocks),
        "anomalies": anomalies,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


async def trigger_stock(code: str, memory_log: list[dict]):
    quote = await asyncio.to_thread(get_quote, code)
    if not quote:
        raise HTTPException(status_code=404, detail=f"无法获取 {code} 行情")

    stock = {"code": code, "name": quote.get("name", code)}
    suggestion = evaluate_suggestion(stock, quote)
    anomalies = []
    if suggestion.get("anomaly"):
        anomaly = _anomaly_from_suggestion(suggestion, include_advice=True)
        anomalies.append(anomaly)
        memory_log.append(anomaly)

    return {
        "checked": 1,
        "anomalies": anomalies,
        "suggestion": suggestion,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
