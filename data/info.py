"""
炒股小牛马工作台 — Layer 6 基础数据
个股基本信息 / 主营业务构成
"""
import logging
import json
from typing import Optional

import aiohttp

from data.helpers import (
    _pure_code,
    _secid,
    eastmoney_datacenter,
    PUSH2_URL,
    HEADERS,
    get_session,
)
from cache.shared_cache import cache

logger = logging.getLogger(__name__)


async def get_stock_info(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> dict:
    """
    获取个股基本信息（东方财富 push2 接口）。
    返回 dict，含 name/price/pe/pb/market_cap/industry/area/list_date 等。
    失败返回空 dict。
    """
    code = _pure_code(code)
    cached = cache.read('fundamentals', code)
    if cached is not None:
        return cached
    secid = _secid(code)

    params = {
        "secid": secid,
        "fields": (
            "f43,f44,f45,f46,f47,f48,f50,f51,f52,f55,f57,f58,f60,"
            "f62,f71,f84,f85,f86,f92,f100,f104,f105,f116,f117,"
            "f162,f167,f168,f169,f170,f171,f177,f292"
        ),
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "invt": "2",
        "fltt": "2",
    }

    _session = session or await get_session()
    try:
        async with _session.get(PUSH2_URL, params=params) as resp:
            data = await resp.json(content_type=None)
            d = data.get("data", {})

            if not d:
                return {}

            result = {
                "code": code,
                "name": d.get("f58", ""),
                "price": d.get("f43", 0),
                "high": d.get("f44", 0),
                "low": d.get("f45", 0),
                "open": d.get("f46", 0),
                "prev_close": d.get("f60", 0),
                "volume": d.get("f47", 0),
                "amount": d.get("f48", 0),
                "change": d.get("f169", 0),
                "change_pct": d.get("f170", 0),
                "turnover": d.get("f168", 0),
                "amplitude": d.get("f171", 0),
                "pe": d.get("f162", d.get("f9", 0)),
                "pb": d.get("f167", 0),
                "total_market_cap": d.get("f116", 0),
                "circ_market_cap": d.get("f117", 0),
                "industry": d.get("f100", ""),
                "total_shares": d.get("f84", 0),
                "circ_shares": d.get("f85", 0),
                "list_date": d.get("f177", ""),
                "eps": d.get("f162", 0),
                "roe": d.get("f173", 0) if "f173" in d else 0,
                "high_52w": d.get("f51", 0),
                "low_52w": d.get("f52", 0),
            }
            cache.write('fundamentals', code, result)
            return result
    except Exception as e:
        logger.warning("get_stock_info(%s) error: %s", code, e)
        return {}


async def get_business_segments(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取主营业务构成（东方财富数据中心 RPT_F10_FN_MAINOP）。
    返回 list[dict]，含 MAINOP_TYPE/ITEM_NAME/MAIN_BUSINESS_INCOME/MBI_RATIO 等。
    """
    code = _pure_code(code)

    # 按产品分类
    result = await eastmoney_datacenter(
        report_name="RPT_F10_FN_MAINOP",
        columns="ALL",
        filter_str=f'(SECURITY_CODE="{code}")(REPORT_DATE_TYPE="年报")',
        sort_columns="REPORT_DATE",
        sort_types="-1",
        page_size=50,
        session=session,
    )

    if not result:
        # 备用：按行业分类
        result = await eastmoney_datacenter(
            report_name="RPT_F10_FN_MAINOP",
            columns="ALL",
            filter_str=f'(SECURITY_CODE="{code}")',
            sort_columns="REPORT_DATE",
            sort_types="-1",
            page_size=50,
            session=session,
        )

    return result
