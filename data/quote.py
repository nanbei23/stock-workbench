"""
炒股小牛马工作台 — Layer 1 行情
腾讯实时行情 + mootdx 委托队列
"""
import logging
from typing import Optional

import aiohttp

from data.helpers import (
    _pure_code,
    tencent_quote_batch,
)

logger = logging.getLogger(__name__)

# mootdx 客户端（惰性初始化，失败时返回 None）
_mootdx_client = None
_mootdx_tried = False


def _get_mootdx_client():
    """懒加载 mootdx Client，连接失败返回 None。"""
    global _mootdx_client, _mootdx_tried
    if _mootdx_tried:
        return _mootdx_client
    _mootdx_tried = True
    try:
        from mootdx.quotes import Quotes
        _mootdx_client = Quotes.factory(market="std")
        logger.info("mootdx 客户端初始化成功")
    except Exception as e:
        logger.warning("mootdx 初始化失败: %s", e)
        _mootdx_client = None
    return _mootdx_client


async def get_realtime_quote(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> dict:
    """
    获取单只股票实时行情（腾讯接口）。
    返回 dict，包含 name/price/change_pct 等字段。失败返回空 dict。
    """
    code = _pure_code(code)
    quotes = await tencent_quote_batch([code], session=session)
    return quotes.get(code, {})


async def get_batch_quotes(
    codes: list[str],
    session: Optional[aiohttp.ClientSession] = None,
) -> dict[str, dict]:
    """
    批量获取多只股票实时行情（腾讯接口）。
    返回 {code: {...}} 字典。失败返回空 dict。
    """
    pure_codes = [_pure_code(c) for c in codes]
    return await tencent_quote_batch(pure_codes, session=session)


def get_orderbook(code: str) -> dict:
    """
    获取五档委托盘口（mootdx quotes 接口）。
    返回 dict：
      {buy: [{price, vol}, ...], sell: [{price, vol}, ...]}
    失败返回空 dict。
    注意：此函数使用 mootdx TCP，保持同步。
    """
    code = _pure_code(code)
    client = _get_mootdx_client()
    if client is None:
        return {}

    try:
        from mootdx.reader import Reader
        # 使用 mootdx quotes 接口
        market = 1 if code.startswith(("6",)) else 0
        df = client.quotes(symbol=[code])
        if df is None or df.empty:
            return {}

        row = df.iloc[0]
        result = {
            "code": code,
            "buy": [],
            "sell": [],
            "price": float(row.get("price", 0)),
        }
        # 买1-买5
        for i in range(1, 6):
            bp = float(row.get(f"bid{i}", 0) or 0)
            bv = int(row.get(f"bid_vol{i}", 0) or 0)
            result["buy"].append({"price": bp, "vol": bv})

        # 卖1-卖5
        for i in range(1, 6):
            sp = float(row.get(f"ask{i}", 0) or 0)
            sv = int(row.get(f"ask_vol{i}", 0) or 0)
            result["sell"].append({"price": sp, "vol": sv})

        return result
    except Exception as e:
        logger.warning("get_orderbook(%s) error: %s", code, e)
        return {}
