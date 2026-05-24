"""行情API — Phase 2 实现（aiohttp 异步）"""
import asyncio
import logging
from fastapi import APIRouter, Query, HTTPException

from data.quote import get_realtime_quote, get_batch_quotes
from data.kline import get_kline
from data.helpers import tencent_quote_batch, get_session, _safe_float

logger = logging.getLogger(__name__)
router = APIRouter(tags=["行情"])

# 大盘指数代码（腾讯格式，需要带市场前缀）
INDEX_CODES = {
    "sh": {"code": "000001", "name": "上证指数", "tencent": "sh000001"},
    "sz": {"code": "399001", "name": "深证成指", "tencent": "sz399001"},
    "cyb": {"code": "399006", "name": "创业板指", "tencent": "sz399006"},
}


@router.get("/quote/batch")
async def get_batch(codes: str = Query(..., description="逗号分隔的股票代码")):
    """批量实时行情"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        raise HTTPException(status_code=400, detail="codes 参数不能为空")
    if len(code_list) > 50:
        raise HTTPException(status_code=400, detail="单次最多查询50只股票")
    try:
        result = await get_batch_quotes(code_list)
        return result
    except Exception as e:
        logger.error("get_batch error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/quote/{code}")
async def get_quote(code: str):
    """实时行情（含持仓盈亏）"""
    try:
        result = await get_realtime_quote(code)
        if not result:
            raise HTTPException(status_code=404, detail=f"未找到股票 {code} 的行情数据")
        # 合并持仓数据
        from models.database import get_db
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT avg_cost, total_shares FROM portfolio WHERE code = ?", (code,)
            )
            p = await cursor.fetchone()
        finally:
            await db.close()
        if p:
            avg_cost = p["avg_cost"]
            total_shares = p["total_shares"]
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
        # get_kline uses mootdx TCP (sync), wrap in thread
        klines = await asyncio.to_thread(get_kline, code, period, count)
        return {"code": code, "period": period, "count": len(klines), "klines": klines}
    except Exception as e:
        logger.error("get_kline(%s, %s) error: %s", code, period, e)
        raise HTTPException(status_code=500, detail=str(e))


async def _fetch_index_quotes() -> dict:
    """异步获取大盘指数行情（腾讯接口，使用带前缀的指数代码）。"""
    codes_with_prefix = [v["tencent"] for v in INDEX_CODES.values()]
    from data.helpers import TENCENT_QUOTE_URL, TENCENT_BATCH_URL
    url = TENCENT_QUOTE_URL + ",".join(codes_with_prefix)

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
        raw_code = var_part.split("_")[-1]  # e.g. sh000001
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

    result = {}
    for key, meta in INDEX_CODES.items():
        result[key] = raw_map.get(meta["tencent"], {"name": meta["name"], "code": meta["code"]})
    return result


@router.get("/index")
async def get_indices():
    """大盘指数（上证/深证/创业板）"""
    try:
        result = await _fetch_index_quotes()
        return result
    except Exception as e:
        logger.error("get_indices error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
