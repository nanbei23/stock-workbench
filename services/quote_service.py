"""Quote application service."""

import asyncio

from fastapi import HTTPException

from data.helpers import get_session, _safe_float
from data.kline import get_kline
from data.market import get_market_sentiment
from data.quote import get_batch_quotes, get_realtime_quote
from models.database import get_db
from repositories import quote_repository as repo


INDEX_CODES = {
    "sh": {"code": "000001", "name": "上证指数", "tencent": "sh000001"},
    "sz": {"code": "399001", "name": "深证成指", "tencent": "sz399001"},
    "cyb": {"code": "399006", "name": "创业板指", "tencent": "sz399006"},
}


async def get_batch(codes: str):
    code_list = [code.strip() for code in codes.split(",") if code.strip()]
    if not code_list:
        raise HTTPException(status_code=400, detail="codes 参数不能为空")
    if len(code_list) > 50:
        raise HTTPException(status_code=400, detail="单次最多查询50只股票")
    return await get_batch_quotes(code_list)


async def get_quote(code: str):
    result = await get_realtime_quote(code)
    if not result:
        raise HTTPException(status_code=404, detail=f"未找到股票 {code} 的行情数据")

    db = await get_db()
    try:
        position = await repo.get_position_cost(db, code)
    finally:
        await db.close()

    if position:
        avg_cost = position["avg_cost"]
        total_shares = position["total_shares"]
        price = result.get("price", 0)
        prev_close = result.get("prev_close", 0)
        if avg_cost and total_shares and price:
            result["avg_cost"] = avg_cost
            result["total_shares"] = total_shares
            result["unrealized_pnl"] = round((price - avg_cost) * total_shares, 2)
            result["unrealized_pnl_pct"] = round((price - avg_cost) / avg_cost * 100, 2)
        if prev_close and total_shares and price:
            result["daily_pnl"] = round((price - prev_close) * total_shares, 2)
    return result


async def get_kline_data(code: str, period: str, count: int):
    klines = await asyncio.to_thread(get_kline, code, period, count)
    return {"code": code, "period": period, "count": len(klines), "klines": klines}


async def get_indices():
    from data.helpers import TENCENT_QUOTE_URL

    url = TENCENT_QUOTE_URL + ",".join(item["tencent"] for item in INDEX_CODES.values())
    session = await get_session()
    async with session.get(url) as resp:
        raw_text = await resp.read()
        text = raw_text.decode("gbk", errors="replace")

    raw_map = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        var_part, _, val_part = line.partition("=")
        raw_code = var_part.split("_")[-1]
        fields = val_part.strip('" ;').split("~")
        if len(fields) < 35:
            continue
        raw_map[raw_code] = {
            "name": fields[1],
            "code": fields[2],
            "price": _safe_float(fields[3]),
            "prev_close": _safe_float(fields[4]),
            "open": _safe_float(fields[5]),
            "change": _safe_float(fields[31]),
            "change_pct": _safe_float(fields[32]),
            "high": _safe_float(fields[33]),
            "low": _safe_float(fields[34]),
            "volume": _safe_float(fields[6]),
            "amount": _safe_float(fields[37]),
        }

    return {
        key: raw_map.get(meta["tencent"], {"name": meta["name"], "code": meta["code"]})
        for key, meta in INDEX_CODES.items()
    }


async def get_market_sentiment_snapshot():
    return await get_market_sentiment()
