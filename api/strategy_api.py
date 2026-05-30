"""策略 API（aiohttp 异步）"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from services import strategy_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["策略"])


class StrategyParams(BaseModel):
    budget: float = 0
    entry_price: float = 0
    drop_pct: float = 3
    add_mult: float = 1
    bounce_pct: float = 5
    sell_pct: float = 50
    lot_size: int = 100
    target_profit_pct: float = 5
    low_water_manual: Optional[float] = None
    buy_prices: Optional[str] = None  # JSON array string like "[10.5, 9.8, 9.0]"


@router.get("/strategy/{code}")
async def get_strategy(code: str):
    """策略参数+计划表+关键价位+状态"""
    try:
        return await strategy_service.get_strategy(code)
    except Exception as e:
        logger.error("get_strategy(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/strategy/{code}/params")
async def get_params(code: str):
    try:
        return await strategy_service.get_params(code)
    except Exception as e:
        logger.error("get_params(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/strategy/{code}/params")
async def update_params(code: str, body: StrategyParams):
    try:
        return await strategy_service.update_params(code, body)
    except Exception as e:
        logger.error("update_params(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/strategy/{code}/pnl")
async def get_pnl(code: str):
    try:
        return await strategy_service.get_pnl(code)
    except Exception as e:
        logger.error("get_pnl(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/strategy/{code}/state")
async def get_state(code: str):
    try:
        return await strategy_service.get_state(code)
    except Exception as e:
        logger.error("get_state(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))
