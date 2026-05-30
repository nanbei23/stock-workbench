"""行情API — Phase 2 实现（aiohttp 异步）"""
import logging
from fastapi import APIRouter, Query, HTTPException

from services import quote_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["行情"])

@router.get("/quote/batch")
async def get_batch(codes: str = Query(..., description="逗号分隔的股票代码")):
    """批量实时行情"""
    try:
        return await quote_service.get_batch(codes)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_batch error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/quote/{code}")
async def get_quote(code: str):
    """实时行情（含持仓盈亏）"""
    try:
        return await quote_service.get_quote(code)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_quote(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/kline/{code}")
async def get_kline_data(
    code: str,
    period: str = Query("day", description="周期: m1/m5/15/30/60/day/week/month"),
    count: int = Query(120, ge=1, le=1000, description="K线数量"),
):
    """K线数据"""
    try:
        return await quote_service.get_kline_data(code, period, count)
    except Exception as e:
        logger.error("get_kline(%s, %s) error: %s", code, period, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/index")
async def get_indices():
    """大盘指数（上证/深证/创业板）"""
    try:
        return await quote_service.get_indices()
    except Exception as e:
        logger.error("get_indices error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/market/sentiment")
async def get_market_sentiment_api():
    """市场情绪（涨跌家数 + 北向资金）"""
    try:
        return await quote_service.get_market_sentiment_snapshot()
    except Exception as e:
        logger.error("get_market_sentiment error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
