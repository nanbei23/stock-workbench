"""Strategy application service."""

import asyncio

from data.quote import get_realtime_quote
from models.database import get_db
from models.strategy import calc_next_triggers, calc_plan_table, calc_pnl, get_strategy_state
from repositories import strategy_repository as repo


async def _with_db(callback):
    db = await get_db()
    try:
        return await callback(db)
    finally:
        await db.close()


async def get_strategy(code: str):
    quote = await get_realtime_quote(code)
    current_price = quote.get("price", 0)

    plan = await asyncio.to_thread(calc_plan_table, code)
    triggers = await asyncio.to_thread(calc_next_triggers, code)
    pnl = await asyncio.to_thread(calc_pnl, code, current_price)

    next_buy = triggers.get("next_buy_price", 0)
    next_sell = triggers.get("next_sell_price", 0)
    state = get_strategy_state(current_price, next_buy, next_sell)

    return {
        "ok": True,
        "data": {
            "current_price": current_price,
            "plan": plan,
            "triggers": triggers,
            "pnl": pnl,
            "state": state,
        },
    }


async def get_params(code: str):
    return {"ok": True, "data": await get_params_data(code)}


async def get_params_data(code: str):
    async def _query(db):
        return await repo.get_params(db, code)

    return await _with_db(_query)


async def update_params(code: str, params):
    async def _update(db):
        await repo.upsert_params(db, code, params)

    await _with_db(_update)
    return {"ok": True}


async def get_pnl(code: str):
    quote = await get_realtime_quote(code)
    current_price = quote.get("price", 0)
    pnl = await asyncio.to_thread(calc_pnl, code, current_price)
    return {"ok": True, "data": pnl}


async def get_state(code: str):
    quote = await get_realtime_quote(code)
    current_price = quote.get("price", 0)
    triggers = await asyncio.to_thread(calc_next_triggers, code)
    next_buy = triggers.get("next_buy_price", 0)
    next_sell = triggers.get("next_sell_price", 0)
    state = get_strategy_state(current_price, next_buy, next_sell)
    return {
        "ok": True,
        "data": {
            "state": state,
            "current_price": current_price,
            "triggers": triggers,
        },
    }
