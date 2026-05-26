"""
炒股小牛马工作台 — 市场情绪数据
涨跌家数 / 涨停跌停 / 北向资金
数据源: 新浪行情(涨跌家数) + 东方财富(北向资金)
"""
import logging
from typing import Optional

import aiohttp

from data.helpers import get_session
from data.signal import get_northbound
from cache.shared_cache import cache

logger = logging.getLogger(__name__)

# 新浪行情API — 单次可返回全部A股数据
SINA_HQ_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php"
    "/Market_Center.getHQNodeDataSimple"
)


async def get_market_breadth(
    session: Optional[aiohttp.ClientSession] = None,
) -> dict:
    """
    获取市场涨跌家数。
    优先用新浪行情API（一次请求获取全部A股），
    失败则尝试东方财富push2 API。
    返回 dict: {up, down, flat, limit_up, limit_down, total}
    """
    cached = cache.read('market', 'breadth')
    if cached is not None:
        return cached

    _session = session or await get_session()
    result = await _breadth_via_sina(_session)
    if result is None:
        result = await _breadth_via_push2(_session)
    if result is None:
        result = {"up": 0, "down": 0, "flat": 0, "limit_up": 0, "limit_down": 0, "total": 0}

    cache.write('market', 'breadth', result)
    return result


async def _breadth_via_sina(session: aiohttp.ClientSession) -> dict | None:
    """用新浪行情API一次性获取全部A股涨跌幅，本地统计。"""
    try:
        params = {
            "page": 1,
            "num": 6000,  # 覆盖全部A股(~5500)
            "sort": "symbol",
            "asc": 1,
            "node": "hs_a",
        }
        async with session.get(SINA_HQ_URL, params=params) as resp:
            import json
            text = await resp.text()
            stocks = json.loads(text)

        if not stocks or not isinstance(stocks, list):
            logger.warning("_breadth_via_sina: empty response")
            return None

        up = down = flat = limit_up = limit_down = 0
        for s in stocks:
            pct = float(s.get("changepercent", 0) or 0)
            if pct > 0:
                up += 1
            elif pct < 0:
                down += 1
            else:
                flat += 1
            # 涨停/跌停(>=9.9% 或 <=-9.9%，覆盖10%/20%涨停板)
            if pct >= 9.9:
                limit_up += 1
            elif pct <= -9.9:
                limit_down += 1

        return {
            "up": up,
            "down": down,
            "flat": flat,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "total": len(stocks),
        }
    except Exception as e:
        logger.warning("_breadth_via_sina error: %s", e)
        return None


async def _breadth_via_push2(session: aiohttp.ClientSession) -> dict | None:
    """东方财富push2 clist备用方案（可能被服务端断连）。"""
    try:
        url = (
            "https://push2.eastmoney.com/api/qt/clist/get?"
            "pn=1&pz=6000&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
            "&fltt=2&invt=2&fid=f3"
            "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
            "&fields=f3"
        )
        async with session.get(url) as resp:
            data = await resp.json(content_type=None)
            items = data.get("data", {}).get("diff", [])
            total = data.get("data", {}).get("total", 0)

        if not items:
            return None

        up = down = flat = limit_up = limit_down = 0
        for item in items:
            pct = item.get("f3")
            if pct is None:
                continue
            if pct > 0:
                up += 1
            elif pct < 0:
                down += 1
            else:
                flat += 1
            if pct >= 9.9:
                limit_up += 1
            elif pct <= -9.9:
                limit_down += 1

        return {
            "up": up,
            "down": down,
            "flat": flat,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "total": total or len(items),
        }
    except Exception as e:
        logger.warning("_breadth_via_push2 error: %s", e)
        return None


async def get_market_sentiment() -> dict:
    """
    获取完整市场情绪数据（涨跌家数 + 北向资金）。
    """
    breadth = await get_market_breadth()
    northbound = await get_northbound()

    return {
        "breadth": breadth,
        "northbound": northbound,
    }
