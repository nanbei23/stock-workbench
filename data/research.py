"""
炒股小牛马工作台 — Layer 2 研报
东方财富 reportapi + 同花顺 EPS 预测
"""
import logging
import json
from typing import Optional

import aiohttp

from data.helpers import _pure_code, _secid, HEADERS, UA, get_session
from cache.shared_cache import cache

logger = logging.getLogger(__name__)


async def get_reports(
    code: str,
    max_pages: int = 3,
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取个股研报（东方财富 reportapi）。
    返回 list[dict]，每条含 title/org/author/date/rating/target_price 等。
    """
    code = _pure_code(code)
    cached = cache.read('research', code)
    if cached is not None:
        return cached
    all_reports = []
    _session = session or await get_session()

    for page in range(1, max_pages + 1):
        url = (
            f"https://reportapi.eastmoney.com/report/list"
            f"?industryCode=*&pageSize=20&industry=*&rating=*&"
            f"ratingChange=*&beginTime=&endTime=&pageNo={page}&"
            f"fields=&qType=0&orgCode=&rcode=&code={code}&"
            f"p={page}&pageNum={page}&_=1"
        )
        try:
            async with _session.get(url) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
                reports = data.get("data", []) or []
                if not reports:
                    break
                for r in reports:
                    all_reports.append({
                        "title": r.get("title", ""),
                        "org": r.get("orgSName", r.get("orgName", "")),
                        "author": r.get("researcher", ""),
                        "date": (r.get("publishDate", "") or "")[:10],
                        "rating": r.get("emRatingName", ""),
                        "target_price": r.get("predictThisYearPe", ""),
                        "eps_forecast": r.get("predictThisYearEps", ""),
                        "industry": r.get("industryName", ""),
                        "content": r.get("content", "")[:500],
                    })
        except Exception as e:
            logger.warning("get_reports(%s) page=%d error: %s", code, page, e)
            break

    if all_reports:
        cache.write('research', code, all_reports)
    return all_reports


async def get_eps_forecast(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> dict:
    """
    获取 EPS 盈利预测（同花顺接口）。
    返回 dict：
      {eps_current_year: float, eps_next_year: float,
       pe_current_year: float, pe_next_year: float,
       analysts: int, reports: [...]}
    失败返回空 dict。
    """
    code = _pure_code(code)
    _session = session or await get_session()

    # 同花顺盈利预测接口
    url = f"https://data.10jqka.com.cn/financial/field/eps/code/{code}.html"
    # 尝试用 JSON API
    api_url = (
        f"https://datacenter.10jqka.com.cn/finance/financial/"
        f"getFinancialAnalysisData?code={code}&type=0"
    )

    try:
        # 使用同花顺数据中心
        headers = {**HEADERS, "Referer": "https://data.10jqka.com.cn/"}
        result = {"code": code}

        try:
            async with _session.get(api_url, headers=headers) as resp:
                if resp.status == 200:
                    try:
                        data = await resp.json(content_type=None)
                        if isinstance(data, dict):
                            result.update(data)
                    except Exception:
                        pass
        except Exception:
            pass

        # 备用：东方财富盈利预测
        eastmoney_url = (
            f"https://datacenter-web.eastmoney.com/api/data/v1/get?"
            f"reportName=RPT_F10_PREDICT_BYYEAR&columns=ALL&"
            f"filter=(SECURITY_CODE%3D%22{code}%22)&"
            f"pageNumber=1&pageSize=10&sortColumns=REPORT_YEAR&sortTypes=-1&"
            f"source=WEB&client=WEB"
        )
        async with _session.get(eastmoney_url) as resp2:
            data2 = await resp2.json(content_type=None)
            if data2.get("success"):
                items = data2.get("result", {}).get("data", [])
                if items:
                    latest = items[0]
                    result["eps_current_year"] = latest.get("PREDICT_EPS", 0)
                    result["pe_current_year"] = latest.get("PREDICT_PE", 0)
                    result["net_profit"] = latest.get("PREDICT_NETPROFIT", 0)
                    result["revenue"] = latest.get("PREDICT_OPERATEINCOME", 0)
                    result["analysts"] = latest.get("NUM_ANALYSTS", 0)
                    result["report_year"] = latest.get("REPORT_YEAR", "")

        return result
    except Exception as e:
        logger.warning("get_eps_forecast(%s) error: %s", code, e)
        return {}
