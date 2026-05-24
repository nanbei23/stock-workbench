"""七层数据 API（aiohttp 异步）"""
import asyncio
import logging
from datetime import datetime
from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["七层数据"])


@router.get("/7layer/{code}")
async def get_7layer(code: str, layer: str = Query("", description="quote|signal|fund|research|news|info|announce")):
    """七层数据 — ?layer=quote|signal|fund|research|news|info|announce"""
    try:
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
            reports, eps = await asyncio.gather(
                get_reports(code, 2),
                get_eps_forecast(code),
            )
            data = {"research": {"reports": reports, "eps_forecast": eps}}
        elif layer == "news":
            from data.news import get_stock_news, get_cls_telegraph
            news, telegraph = await asyncio.gather(
                get_stock_news(code),
                get_cls_telegraph(20),
            )
            data = {"news": {"stock_news": news, "cls_telegraph": telegraph}}
        elif layer == "info":
            from data.info import get_stock_info, get_business_segments
            info, segs = await asyncio.gather(
                get_stock_info(code),
                get_business_segments(code),
            )
            data = {"info": {"basic": info, "segments": segs}}
        elif layer == "announce":
            from data.announce import get_announcements
            data = {"announce": await get_announcements(code)}
        else:
            raise HTTPException(status_code=400, detail=f"未知层级: {layer}")

        return {"ok": True, "data": data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_7layer(%s, %s) error: %s", code, layer, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/7layer/{code}/all")
async def get_7layer_all(code: str):
    """七层全量 — 真正并发获取（aiohttp 异步）"""
    from data.quote import get_realtime_quote
    from data.signal import get_all_signals
    from data.fund import get_all_fund_data
    from data.research import get_reports, get_eps_forecast
    from data.news import get_stock_news
    from data.info import get_stock_info
    from data.announce import get_announcements

    try:
        # 真正的并发（不是线程池，而是 async 协程并发）
        tasks = [
            get_realtime_quote(code),
            get_all_signals(code),
            get_all_fund_data(code),
            get_reports(code, 1),
            get_eps_forecast(code),
            get_stock_news(code),
            get_stock_info(code),
            get_announcements(code),
        ]
        # 策略数据（从 strategy_params 表读取）
        async def _get_strategy(code6):
            from models.database import get_db
            async with get_db() as db:
                row = await db.execute_fetchall(
                    "SELECT * FROM strategy_params WHERE code6=?", (code6,)
                )
                return dict(row[0]) if row else None

        tasks.append(_get_strategy(code[-6:]))
        results = await asyncio.gather(*tasks, return_exceptions=True)

        def _layer(val, label=""):
            if isinstance(val, Exception):
                logger.warning("7layer/%s fetch %s failed: %s", code, label, val)
                return None
            return val

        data = {
            "quote": _layer(results[0], "quote"),
            "signal": _layer(results[1], "signal"),
            "fund": _layer(results[2], "fund"),
            "research": {
                "reports": _layer(results[3], "reports"),
                "eps_forecast": _layer(results[4], "eps_forecast"),
            },
            "news": {"stock_news": _layer(results[5], "news")},
            "info": _layer(results[6], "info"),
            "announce": _layer(results[7], "announce"),
            "strategy": _layer(results[8], "strategy"),
        }
        return {"ok": True, "data": data}
    except Exception as e:
        logger.error("get_7layer_all(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dragon-tiger")
async def dragon_tiger(date: str = Query("", description="日期 YYYY-MM-DD")):
    """全市场龙虎榜 — ?date=2026-05-22"""
    try:
        from data.signal import get_dragon_tiger
        data = await get_dragon_tiger(code="", date=date)
        return {"ok": True, "data": data}
    except Exception as e:
        logger.error("dragon_tiger(%s) error: %s", date, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/research/{code}")
async def get_research(code: str):
    """个股研报列表"""
    from data.research import get_reports, get_eps_forecast
    try:
        reports, eps = await asyncio.gather(
            get_reports(code, 3),
            get_eps_forecast(code),
        )
        return {"ok": True, "data": {"reports": reports or [], "eps_forecast": eps or {}}}
    except Exception as e:
        logger.error("get_research(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/announce/{code}")
async def get_announce(code: str):
    """个股公告列表"""
    from data.announce import get_announcements
    try:
        data = await get_announcements(code)
        return {"ok": True, "data": data or []}
    except Exception as e:
        logger.error("get_announce(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/industry")
async def industry():
    """行业排名"""
    from data.signal import get_industry_ranking
    try:
        data = await get_industry_ranking()
        return {"ok": True, "data": data}
    except Exception as e:
        logger.error("industry error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
