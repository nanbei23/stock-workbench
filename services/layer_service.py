"""Seven-layer data aggregation service."""

import asyncio
import logging

from fastapi import HTTPException

from services import strategy_service

logger = logging.getLogger(__name__)


async def get_layer(code: str, layer: str = ""):
    if layer == "quote" or not layer:
        from data.quote import get_realtime_quote
        data = {"quote": await get_realtime_quote(code)}
    elif layer == "signal":
        from data.signal import get_all_signals
        data = {"signal": await get_all_signals(code)}
    elif layer == "fund":
        from data.fund import get_all_fund_data
        data = {"fund": await get_all_fund_data(code)}
    elif layer == "research":
        from data.research import get_reports, get_eps_forecast
        reports, eps = await asyncio.gather(get_reports(code, 2), get_eps_forecast(code))
        data = {"research": {"reports": reports, "eps_forecast": eps}}
    elif layer == "news":
        from data.news import get_stock_news, get_cls_telegraph
        news, telegraph = await asyncio.gather(get_stock_news(code), get_cls_telegraph(20))
        data = {"news": {"stock_news": news, "cls_telegraph": telegraph}}
    elif layer == "info":
        from data.info import get_stock_info, get_business_segments
        info, segments = await asyncio.gather(get_stock_info(code), get_business_segments(code))
        data = {"info": {"basic": info, "segments": segments}}
    elif layer == "announce":
        from data.announce import get_announcements
        data = {"announce": await get_announcements(code)}
    else:
        raise HTTPException(status_code=400, detail=f"未知层级: {layer}")

    return {"ok": True, "data": data}


def _layer_value(value, code: str, label: str = ""):
    if isinstance(value, Exception):
        logger.warning("7layer/%s fetch %s failed: %s", code, label, value)
        return None
    return value


async def get_all_layers(code: str):
    from data.quote import get_realtime_quote
    from data.signal import get_all_signals
    from data.fund import get_all_fund_data
    from data.research import get_reports, get_eps_forecast
    from data.news import get_stock_news
    from data.info import get_stock_info
    from data.announce import get_announcements

    tasks = [
        get_realtime_quote(code),
        get_all_signals(code),
        get_all_fund_data(code),
        get_reports(code, 1),
        get_eps_forecast(code),
        get_stock_news(code),
        get_stock_info(code),
        get_announcements(code),
        strategy_service.get_params_data(code[-6:]),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    data = {
        "quote": _layer_value(results[0], code, "quote"),
        "signal": _layer_value(results[1], code, "signal"),
        "fund": _layer_value(results[2], code, "fund"),
        "research": {
            "reports": _layer_value(results[3], code, "reports"),
            "eps_forecast": _layer_value(results[4], code, "eps_forecast"),
        },
        "news": {"stock_news": _layer_value(results[5], code, "news")},
        "info": _layer_value(results[6], code, "info"),
        "announce": _layer_value(results[7], code, "announce"),
        "strategy": _layer_value(results[8], code, "strategy"),
    }
    return {"ok": True, "data": data}


async def get_dragon_tiger(date: str = ""):
    from data.signal import get_dragon_tiger
    return {"ok": True, "data": await get_dragon_tiger(code="", date=date)}


async def get_research(code: str):
    from data.research import get_reports, get_eps_forecast
    reports, eps = await asyncio.gather(get_reports(code, 3), get_eps_forecast(code))
    return {"ok": True, "data": {"reports": reports or [], "eps_forecast": eps or {}}}


async def get_announce(code: str):
    from data.announce import get_announcements
    return {"ok": True, "data": await get_announcements(code) or []}


async def get_industry():
    from data.signal import get_industry_ranking
    return {"ok": True, "data": await get_industry_ranking()}
