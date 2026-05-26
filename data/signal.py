"""
炒股小牛马工作台 — Layer 3 信号
概念板块 / 热门原因 / 北向资金 / 分时资金 / 龙虎榜 / 解禁 / 行业排名
"""
import asyncio
import json
import logging
from typing import Optional

import aiohttp

from data.helpers import (
    _pure_code,
    _secid,
    _tencent_code,
    get_prefix,
    eastmoney_datacenter,
    PUSH2_URL,
    PUSH2_MINUTE_URL,
    HEADERS,
    UA,
    get_session,
)
from cache.shared_cache import cache

logger = logging.getLogger(__name__)


async def get_concept_blocks(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取个股所属概念板块（百度 API）。
    返回 list[dict]，每条含 block_name/block_code/change_pct。
    """
    code = _pure_code(code)
    cached = cache.read('signal', code)
    if cached is not None:
        return cached
    prefix = get_prefix(code)
    full_code = f"{prefix}{code}"

    url = "https://finance.pae.baidu.com/vapi/v1/getquotation"
    params = {
        "srcid": "5353",
        "all": 1,
        "is498": 1,
        "isBk": "false",
        "isBlock": "true",
        "isFutures": "false",
        "isStock": "false",
        "newFormat": 1,
        "group": "quotation_kline_ab",
        "code": full_code,
        "market_type": "ab",
        "finClientType": "pc",
    }

    _session = session or await get_session()
    try:
        async with _session.get(url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

            blocks = []
            Result = data.get("Result", {})
            if isinstance(Result, list):
                for item in Result:
                    if isinstance(item, dict) and "RelatedPlate" in str(item):
                        Result = item
                        break

            # 百度返回中找板块列表
            plate_list = Result.get("platelist", Result.get("blocklist", []))
            if isinstance(plate_list, list):
                for p in plate_list:
                    if isinstance(p, dict):
                        blocks.append({
                            "block_name": p.get("name", p.get("platename", "")),
                            "block_code": p.get("code", p.get("platecode", "")),
                            "change_pct": float(p.get("percent", p.get("changepercent", 0)) or 0),
                        })
            cache.write('signal', code, blocks)
            return blocks
    except Exception as e:
        logger.warning("get_concept_blocks(%s) error: %s", code, e)
        return []


async def get_hot_reasons(
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取市场热点（同花顺热门概念/涨停原因）。
    返回 list[dict]，每条含 concept/change_pct/lead_stock/reason。
    """
    cached = cache.read('signal', 'global_hot_reasons')
    if cached is not None:
        return cached

    url = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour"
    headers = {**HEADERS, "Referer": "https://www.10jqka.com.cn/"}

    _session = session or await get_session()
    try:
        result = []
        async with _session.get(url, headers=headers) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json(content_type=None)
                    items = data.get("data", {}).get("stock_list", [])
                    for item in items:
                        result.append({
                            "code": item.get("code", ""),
                            "name": item.get("name", ""),
                            "hot_value": item.get("hot_value", 0),
                            "change_pct": item.get("rate", 0),
                        })
                except Exception:
                    pass

        # 备用：东方财富涨停原因
        if not result:
            url2 = (
                "https://push2.eastmoney.com/api/qt/clist/get?"
                "pn=1&pz=30&po=1&np=1&fltt=2&invt=2&"
                "fields=f2,f3,f4,f12,f14&"
                "fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23&"
                "fid=f3&_=1"
            )
            async with _session.get(url2) as resp2:
                data2 = await resp2.json(content_type=None)
                for item in (data2.get("data", {}).get("diff", []) or []):
                    result.append({
                        "code": item.get("f12", ""),
                        "name": item.get("f14", ""),
                        "price": item.get("f2", 0),
                        "change_pct": item.get("f3", 0),
                    })

        cache.write('signal', 'global_hot_reasons', result)
        return result
    except Exception as e:
        logger.warning("get_hot_reasons() error: %s", e)
        return []


async def get_northbound(
    session: Optional[aiohttp.ClientSession] = None,
) -> dict:
    """
    获取北向资金实时数据（东方财富 kamt/get 端点）。
    返回 dict：
      {total_net, sh_net, sz_net, status, date}
    单位：亿元。失败返回空 dict。
    数据来源：hk2sh（沪股通北向）+ hk2sz（深股通北向）
    status: 1=交易中, 2=已收盘, 3=未开盘/休市
    """
    cached = cache.read('signal', 'global_northbound')
    if cached is not None:
        return cached

    url = (
        "https://push2.eastmoney.com/api/qt/kamt/get?"
        "fields1=f1,f2,f3,f4&fields2=f51,f52,f53,f54,f55,f56&"
        "ut=b2884a393a59ad64002292a3e90d46a5"
    )

    _session = session or await get_session()
    try:
        async with _session.get(url) as resp:
            data = await resp.json(content_type=None)
            d = data.get("data", {})

            # hk2sh = 沪股通(北向), hk2sz = 深股通(北向)
            # dayNetAmtIn 单位: 万元
            hk2sh = d.get("hk2sh", {})
            hk2sz = d.get("hk2sz", {})

            sh_net_wan = float(hk2sh.get("dayNetAmtIn", 0) or 0)
            sz_net_wan = float(hk2sz.get("dayNetAmtIn", 0) or 0)

            # 万元 → 亿元
            sh_net = sh_net_wan / 10000
            sz_net = sz_net_wan / 10000

            # status: 1=交易中 2=已收盘 3=未开盘
            status = hk2sh.get("status", 0)
            date_str = hk2sh.get("date", "")

            result = {
                "total_net": sh_net + sz_net,
                "sh_net": sh_net,
                "sz_net": sz_net,
                "status": status,
                "date": date_str,
            }
            cache.write('signal', 'global_northbound', result)
            return result
    except Exception as e:
        logger.warning("get_northbound() error: %s", e)
        return {}


async def get_fund_flow_minute(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取个股分时资金流向（东方财富 push2 trends2）。
    返回 list[dict]，每条含 time/price/volume/avg_price。
    """
    code = _pure_code(code)
    cached = cache.read('signal', code)
    if cached is not None:
        return cached
    secid = _secid(code)

    url = (
        f"{PUSH2_MINUTE_URL}?"
        f"secid={secid}&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&"
        f"fields2=f51,f52,f53,f54,f55,f56,f57,f58&"
        f"iscr=0&ndays=1&ut=fa5fd1943c7b386f172d6893dbfba10b"
    )

    _session = session or await get_session()
    try:
        async with _session.get(url) as resp:
            data = await resp.json(content_type=None)
            trends = data.get("data", {}).get("trends", [])
            pre_close = data.get("data", {}).get("preClose", 0)

            result = []
            for t in trends:
                fields = t.split(",") if isinstance(t, str) else t
                if len(fields) >= 6:
                    result.append({
                        "time": fields[0],
                        "open": float(fields[1] or 0),
                        "close": float(fields[2] or 0),
                        "high": float(fields[3] or 0),
                        "low": float(fields[4] or 0),
                        "volume": float(fields[5] or 0),
                        "avg_price": float(fields[6] or 0) if len(fields) > 6 else 0,
                    })
            cache.write('signal', code, result)
            return result
    except Exception as e:
        logger.warning("get_fund_flow_minute(%s) error: %s", code, e)
        return []


async def get_dragon_tiger(
    code: str,
    date: str = "",
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取龙虎榜数据（东方财富数据中心）。
    date 格式 YYYY-MM-DD，空则取最近。
    返回 list[dict]。
    """
    code = _pure_code(code)
    cached = cache.read('signal', code)
    if cached is not None:
        return cached
    filter_str = f'(SECURITY_CODE="{code}")'
    if date:
        filter_str += f"(TRADE_DATE='{date}')"

    result = await eastmoney_datacenter(
        report_name="BILLBOARD_DAILYDETAILS",
        columns="ALL",
        filter_str=filter_str,
        sort_columns="TRADE_DATE",
        sort_types="-1",
        page_size=50,
        session=session,
    )
    cache.write('signal', code, result)
    return result


async def get_lockup_expiry(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取限售解禁数据（东方财富数据中心）。
    返回 list[dict]，含 FREE_DATE/FREE_SHARES/FREE_MARKET_CAP 等。
    """
    code = _pure_code(code)
    cached = cache.read('signal', code)
    if cached is not None:
        return cached
    filter_str = f'(SECURITY_CODE="{code}")'

    result = await eastmoney_datacenter(
        report_name="RPT_LIFT_STAGE",
        columns="ALL",
        filter_str=filter_str,
        sort_columns="FREE_DATE",
        sort_types="1",
        page_size=50,
        session=session,
    )
    cache.write('signal', code, result)
    return result


async def get_industry_ranking(
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取行业板块涨幅排名（东方财富 push2）。
    返回 list[dict]，含 name/change_pct/lead_stock 等。
    """
    cached = cache.read('signal', 'global_industry_ranking')
    if cached is not None:
        return cached

    url = (
        "https://push2.eastmoney.com/api/qt/clist/get?"
        "pn=1&pz=50&po=1&np=1&fltt=2&invt=2&"
        "fid=f3&fs=m:90+t:2&"
        "fields=f2,f3,f4,f12,f14,f104,f105,f128,f140,f136&_=1"
    )

    _session = session or await get_session()
    try:
        async with _session.get(url) as resp:
            data = await resp.json(content_type=None)
            result = []
            for item in (data.get("data", {}).get("diff", []) or []):
                result.append({
                    "name": item.get("f14", ""),
                    "code": item.get("f12", ""),
                    "price": item.get("f2", 0),
                    "change_pct": item.get("f3", 0),
                    "change": item.get("f4", 0),
                    "up_count": item.get("f104", 0),
                    "down_count": item.get("f105", 0),
                    "lead_stock": item.get("f140", ""),
                    "lead_change_pct": item.get("f136", 0),
                })
            cache.write('signal', 'global_industry_ranking', result)
            return result
    except Exception as e:
        logger.warning("get_industry_ranking() error: %s", e)
        return []


async def get_all_signals(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> dict:
    """
    聚合所有信号面数据，供七层信号面板使用。
    返回 dict：
      {sentiment, rating, institution, policy, sector,
       lockup, pledge, goodwill}
    """
    code = _pure_code(code)
    cached = cache.read('signal', code)
    if cached is not None:
        return cached

    result = {
        "sentiment": None,
        "rating": None,
        "institution": None,
        "policy": None,
        "sector": None,
        "lockup": None,
        "pledge": None,
        "goodwill": None,
    }

    # 并发获取所有数据
    async def _fetch_news():
        try:
            from data.news import get_stock_news
            return await get_stock_news(code, session=session)
        except Exception:
            return []

    async def _fetch_reports():
        try:
            from data.research import get_reports
            return await get_reports(code, max_pages=1, session=session)
        except Exception:
            return []

    async def _fetch_holders():
        try:
            from data.fund import get_holder_change
            return await get_holder_change(code, session=session)
        except Exception:
            return []

    async def _fetch_info():
        try:
            from data.info import get_stock_info
            return await get_stock_info(code, session=session)
        except Exception:
            return {}

    async def _fetch_blocks():
        try:
            return await get_concept_blocks(code, session=session)
        except Exception:
            return []

    async def _fetch_lockup():
        try:
            return await get_lockup_expiry(code, session=session)
        except Exception:
            return []

    async def _fetch_pledge():
        try:
            return await eastmoney_datacenter(
                report_name="RPT_F10_EH_EQUITYPLEDGE",
                columns="ALL",
                filter_str=f'(SECURITY_CODE="{code}")',
                sort_columns="END_DATE",
                sort_types="-1",
                page_size=5,
                session=session,
            )
        except Exception:
            return []

    async def _fetch_goodwill():
        try:
            return await eastmoney_datacenter(
                report_name="RPT_F10_FN_BALANCE",
                columns="ALL",
                filter_str=f'(SECURITY_CODE="{code}")',
                sort_columns="REPORT_DATE",
                sort_types="-1",
                page_size=5,
                session=session,
            )
        except Exception:
            return []

    # 并发执行所有请求
    (
        news, reports, holders, info,
        blocks, lockup, pledge_data, goodwill_data,
    ) = await asyncio.gather(
        _fetch_news(),
        _fetch_reports(),
        _fetch_holders(),
        _fetch_info(),
        _fetch_blocks(),
        _fetch_lockup(),
        _fetch_pledge(),
        _fetch_goodwill(),
        return_exceptions=True,
    )

    # 处理结果（gather 返回 Exception 的需要捕获）
    if isinstance(news, Exception):
        news = []
    if isinstance(reports, Exception):
        reports = []
    if isinstance(holders, Exception):
        holders = []
    if isinstance(info, Exception):
        info = {}
    if isinstance(blocks, Exception):
        blocks = []
    if isinstance(lockup, Exception):
        lockup = []
    if isinstance(pledge_data, Exception):
        pledge_data = []
    if isinstance(goodwill_data, Exception):
        goodwill_data = []

    # 1) 新闻情绪
    if news:
        scores = [n.get("sentiment_score", 0) for n in news]
        avg = sum(scores) / len(scores) if scores else 0
        pos = sum(1 for s in scores if s > 0)
        neg = sum(1 for s in scores if s < 0)
        total = len(scores)
        result["sentiment"] = {
            "score": round(avg, 2),
            "positive": pos,
            "negative": neg,
            "total": total,
            "label": "看多" if avg > 0.1 else ("看空" if avg < -0.1 else "中性"),
        }

    # 2) 研报评级
    if reports:
        latest = reports[0]
        result["rating"] = {
            "rating": latest.get("rating", ""),
            "org": latest.get("org", ""),
            "title": latest.get("title", ""),
            "date": latest.get("date", ""),
        }

    # 3) 机构动向
    if holders:
        result["institution"] = {
            "count": len(holders),
            "latest": holders[0].get("HOLDER_NAME", "") if holders else "",
        }

    # 4) 行业政策
    industry = info.get("industry", "") if info else ""
    result["policy"] = {"industry": industry}

    # 5) 板块热度
    if blocks:
        result["sector"] = {
            "blocks": blocks[:5],
            "count": len(blocks),
        }

    # 6) 解禁压力
    if lockup:
        result["lockup"] = {
            "count": len(lockup),
            "next_date": lockup[0].get("FREE_DATE", "") if lockup else "",
            "next_shares": lockup[0].get("FREE_SHARES", 0) if lockup else 0,
        }

    # 7) 质押比例
    if pledge_data:
        latest = pledge_data[0]
        result["pledge"] = {
            "ratio": latest.get("PLEDGE_RATIO", latest.get("PLEDGE_PROPORTION", 0)),
            "date": latest.get("END_DATE", ""),
        }

    # 8) 商誉占比
    if goodwill_data:
        latest = goodwill_data[0]
        gw = latest.get("GOODWILL", 0) or 0
        ta = latest.get("TOTAL_ASSETS", 1) or 1
        result["goodwill"] = {
            "amount": gw,
            "ratio": round(gw / ta * 100, 2) if ta else 0,
            "date": latest.get("REPORT_DATE", ""),
        }

    cache.write('signal', code, result)
    return result
