"""
炒股小牛马工作台 — Layer 4 资金面
融资融券 / 大宗交易 / 股东变动 / 分红 / 资金流向
全部使用东方财富数据中心或 push2his
"""
import asyncio
import json
import logging
from typing import Optional

import aiohttp

from data.helpers import (
    _pure_code,
    _secid,
    eastmoney_datacenter,
    PUSH2_HIS_URL,
    HEADERS,
    get_session,
)
from cache.shared_cache import cache

logger = logging.getLogger(__name__)


async def get_margin_trading(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取融资融券数据（东方财富数据中心）。
    返回 list[dict]，含 RZYE（融资余额）/RQYE（融券余额）/RZMRE（融资买入额）等。
    """
    code = _pure_code(code)
    cached = cache.read('fundamentals', code)
    if cached is not None:
        return cached
    result = await eastmoney_datacenter(
        report_name="RPTA_WEB_RZRQ_GGMX",
        columns="ALL",
        filter_str=f'(SCODE="{code}")',
        sort_columns="DATE",
        sort_types="-1",
        page_size=60,
        session=session,
    )
    cache.write('fundamentals', code, result)
    return result


async def get_block_trade(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取大宗交易数据（东方财富数据中心）。
    返回 list[dict]，含 TRADE_DATE/PRICE/VOLUME/DEAL_AMT/BUYER/SELLER 等。
    """
    code = _pure_code(code)
    cached = cache.read('fundamentals', code)
    if cached is not None:
        return cached
    result = await eastmoney_datacenter(
        report_name="RPT_BLOCKTRADE_DETAIL",
        columns="ALL",
        filter_str=f'(SECURITY_CODE="{code}")',
        sort_columns="TRADE_DATE",
        sort_types="-1",
        page_size=50,
        session=session,
    )
    cache.write('fundamentals', code, result)
    return result


async def get_holder_change(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取股东变动/十大股东数据（东方财富数据中心）。
    返回 list[dict]，含 HOLDER_NAME/HOLD_NUM/HOLD_RATIO/CHANGE 等。
    """
    code = _pure_code(code)
    cached = cache.read('fundamentals', code)
    if cached is not None:
        return cached

    # 先试十大流通股东
    result = await eastmoney_datacenter(
        report_name="RPT_F10_EH_FREEHOLDERS",
        columns="ALL",
        filter_str=f'(SECURITY_CODE="{code}")',
        sort_columns="END_DATE,HOLDER_RANK",
        sort_types="-1,1",
        page_size=50,
        session=session,
    )

    if not result:
        # 备用：股东人数
        result = await eastmoney_datacenter(
            report_name="RPT_F10_EH_HOLDERNUMCHANGE",
            columns="ALL",
            filter_str=f'(SECURITY_CODE="{code}")',
            sort_columns="END_DATE",
            sort_types="-1",
            page_size=50,
            session=session,
        )

    cache.write('fundamentals', code, result)
    return result


async def get_dividend_history(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取分红历史（东方财富数据中心）。
    返回 list[dict]，含 EX_DIVIDEND_DATE/PLAN_NOTICE_DATE/DIVIDEND_PER_SHARE/TAX_PER_SHARE 等。
    """
    code = _pure_code(code)
    cached = cache.read('fundamentals', code)
    if cached is not None:
        return cached
    result = await eastmoney_datacenter(
        report_name="RPT_SHAREBONUS_DET",
        columns="ALL",
        filter_str=f'(SECURITY_CODE="{code}")',
        sort_columns="EX_DIVIDEND_DATE",
        sort_types="-1",
        page_size=50,
        session=session,
    )
    cache.write('fundamentals', code, result)
    return result


async def get_fund_flow_120d(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> dict:
    """
    获取近120日资金流向（东方财富 push2his kline 接口）。
    返回 dict：
      {klines: [{date, main_inflow, retail_inflow, ...}, ...], ...}
    失败返回空 dict。
    """
    code = _pure_code(code)
    cached = cache.read('fundamentals', code)
    if cached is not None:
        return cached
    secid = _secid(code)

    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "klt": "101",  # 日线
        "fqt": "1",
        "lmt": "120",
        "end": "20500101",
        "iscca": "1",
        "cb": "jQuery",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
    }

    _session = session or await get_session()
    try:
        async with _session.get(PUSH2_HIS_URL, params=params) as resp:
            text = await resp.text()
            # 去掉 JSONP callback
            start = text.find("(")
            end = text.rfind(")")
            if start != -1 and end != -1:
                text = text[start + 1 : end]

            data = json.loads(text)
            klines_raw = data.get("data", {}).get("klines", [])

            result = {
                "code": code,
                "klines": [],
            }
            for line in klines_raw:
                fields = line.split(",") if isinstance(line, str) else line
                if len(fields) >= 11:
                    result["klines"].append({
                        "date": fields[0],
                        "close": float(fields[2] or 0),
                        "change_pct": float(fields[8] or 0),
                        "main_net_inflow": float(fields[1] or 0),      # 主力净流入
                        "small_net_inflow": float(fields[2] or 0),     # 小单净流入（如可用）
                        "main_pct": float(fields[10] or 0) if len(fields) > 10 else 0,
                    })

            cache.write('fundamentals', code, result)
            return result
    except Exception as e:
        logger.warning("get_fund_flow_120d(%s) error: %s", code, e)
        return {}


async def get_all_fund_data(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> dict:
    """
    聚合所有资金面数据，供七层信号面板使用。
    返回 dict：
      {main_net_flow, north_net, margin_balance, dragon_tiger_count}
    """
    code = _pure_code(code)
    cached = cache.read('fundamentals', code)
    if cached is not None:
        return cached

    result = {
        "main_net_flow": None,
        "north_net": None,
        "margin_balance": None,
        "dragon_tiger_count": None,
    }

    # 并发获取所有数据
    async def _fetch_flow():
        try:
            flow = await get_fund_flow_120d(code, session=session)
            klines = flow.get("klines", [])
            if klines:
                return klines[-1].get("main_net_inflow", 0)
        except Exception:
            pass
        return None

    async def _fetch_north():
        try:
            from data.signal import get_northbound
            nb = await get_northbound(session=session)
            if nb:
                return nb.get("total_net", 0)
        except Exception:
            pass
        return None

    async def _fetch_margin():
        try:
            margin = await get_margin_trading(code, session=session)
            if margin:
                return margin[0].get("RZYE", 0)
        except Exception:
            pass
        return None

    async def _fetch_dragon():
        try:
            from data.signal import get_dragon_tiger
            dragon = await get_dragon_tiger(code, session=session)
            return len(dragon) if dragon else 0
        except Exception:
            pass
        return None

    flow_result, north_result, margin_result, dragon_result = await asyncio.gather(
        _fetch_flow(),
        _fetch_north(),
        _fetch_margin(),
        _fetch_dragon(),
        return_exceptions=True,
    )

    if not isinstance(flow_result, Exception):
        result["main_net_flow"] = flow_result
    if not isinstance(north_result, Exception):
        result["north_net"] = north_result
    if not isinstance(margin_result, Exception):
        result["margin_balance"] = margin_result
    if not isinstance(dragon_result, Exception):
        result["dragon_tiger_count"] = dragon_result

    cache.write('fundamentals', code, result)
    return result
