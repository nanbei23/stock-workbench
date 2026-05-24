"""策略 API（aiohttp 异步）"""
import asyncio
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

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
    from models.strategy import calc_plan_table, calc_next_triggers, calc_pnl, get_strategy_state
    from data.quote import get_realtime_quote

    try:
        quote = await get_realtime_quote(code)
        current_price = quote.get("price", 0)

        plan = await asyncio.to_thread(calc_plan_table, code)
        triggers = await asyncio.to_thread(calc_next_triggers, code)
        pnl = await asyncio.to_thread(calc_pnl, code, current_price)

        next_buy = triggers.get("next_buy_price", 0)
        next_sell = triggers.get("next_sell_price", 0)
        state = get_strategy_state(current_price, next_buy, next_sell)

        return {"ok": True, "data": {
            "current_price": current_price,
            "plan": plan,
            "triggers": triggers,
            "pnl": pnl,
            "state": state,
        }}
    except Exception as e:
        logger.error("get_strategy(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/strategy/{code}/params")
async def get_params(code: str):
    from models.database import get_db

    async def _query():
        db = await get_db()
        try:
            cursor = await db.execute("SELECT * FROM strategy_params WHERE code6=?", (code,))
            row = await cursor.fetchone()
            return dict(row) if row else {}
        finally:
            await db.close()

    try:
        data = await _query()
        return {"ok": True, "data": data}
    except Exception as e:
        logger.error("get_params(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/strategy/{code}/params")
async def update_params(code: str, body: StrategyParams):
    from models.database import get_db

    try:
        db = await get_db()
        try:
            await db.execute("""
                INSERT INTO strategy_params (code6, budget, entry_price, drop_pct, add_mult,
                    bounce_pct, sell_pct, lot_size, target_profit_pct, low_water_manual, buy_prices)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, '[]'))
                ON CONFLICT(code6) DO UPDATE SET
                    budget=excluded.budget, entry_price=excluded.entry_price,
                    drop_pct=excluded.drop_pct, add_mult=excluded.add_mult,
                    bounce_pct=excluded.bounce_pct, sell_pct=excluded.sell_pct,
                    lot_size=excluded.lot_size, target_profit_pct=excluded.target_profit_pct,
                    low_water_manual=excluded.low_water_manual,
                    buy_prices=excluded.buy_prices
            """, (
                code,
                body.budget, body.entry_price,
                body.drop_pct, body.add_mult,
                body.bounce_pct, body.sell_pct,
                body.lot_size, body.target_profit_pct,
                body.low_water_manual,
                body.buy_prices,
            ))
            await db.commit()
        finally:
            await db.close()
        return {"ok": True}
    except Exception as e:
        logger.error("update_params(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/strategy/{code}/pnl")
async def get_pnl(code: str):
    from models.strategy import calc_pnl
    from data.quote import get_realtime_quote
    try:
        quote = await get_realtime_quote(code)
        current_price = quote.get("price", 0)
        pnl = await asyncio.to_thread(calc_pnl, code, current_price)
        return {"ok": True, "data": pnl}
    except Exception as e:
        logger.error("get_pnl(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/strategy/{code}/state")
async def get_state(code: str):
    from models.strategy import calc_next_triggers, get_strategy_state
    from data.quote import get_realtime_quote
    try:
        quote = await get_realtime_quote(code)
        current_price = quote.get("price", 0)
        triggers = await asyncio.to_thread(calc_next_triggers, code)
        next_buy = triggers.get("next_buy_price", 0)
        next_sell = triggers.get("next_sell_price", 0)
        state = get_strategy_state(current_price, next_buy, next_sell)
        return {"ok": True, "data": {
            "state": state,
            "current_price": current_price,
            "triggers": triggers,
        }}
    except Exception as e:
        logger.error("get_state(%s) error: %s", code, e)
        raise HTTPException(status_code=500, detail=str(e))
