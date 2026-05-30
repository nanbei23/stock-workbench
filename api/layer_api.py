"""七层数据 API（aiohttp 异步）"""
import logging
from fastapi import APIRouter, Query, HTTPException

from services import layer_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["七层数据"])


@router.get("/7layer/{code}")
async def get_7layer(code: str, layer: str = Query("", description="quote|signal|fund|research|news|info|announce")):
    """七层数据 — ?layer=quote|signal|fund|research|news|info|announce"""
    try:
        return await layer_service.get_layer(code, layer)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_7layer(%s, %s) error: %s", code, layer, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/7layer/{code}/all")
async def get_7layer_all(code: str):
    """七层全量 — 真正并发获取（aiohttp 异步）"""
    try:
        return await layer_service.get_all_layers(code)
    except Exception as e:
        logger.error("get_7layer_all(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dragon-tiger")
async def dragon_tiger(date: str = Query("", description="日期 YYYY-MM-DD")):
    """全市场龙虎榜 — ?date=2026-05-22"""
    try:
        return await layer_service.get_dragon_tiger(date)
    except Exception as e:
        logger.error("dragon_tiger(%s) error: %s", date, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/research/{code}")
async def get_research(code: str):
    """个股研报列表"""
    try:
        return await layer_service.get_research(code)
    except Exception as e:
        logger.error("get_research(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/announce/{code}")
async def get_announce(code: str):
    """个股公告列表"""
    try:
        return await layer_service.get_announce(code)
    except Exception as e:
        logger.error("get_announce(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/industry")
async def industry():
    """行业排名"""
    try:
        return await layer_service.get_industry()
    except Exception as e:
        logger.error("industry error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
