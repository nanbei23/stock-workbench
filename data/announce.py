"""
炒股小牛马工作台 — Layer 7 公告
东方财富 np-anotice-stock API（替代被反爬的巨潮资讯）
"""
import datetime
import logging
import re
from typing import Optional

import aiohttp

from data.helpers import _pure_code, get_prefix, UA, get_session
from cache.shared_cache import cache

logger = logging.getLogger(__name__)

# 东方财富公告API
EM_ANN_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"


async def get_announcements(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取个股公告（东方财富 np-anotice-stock API）。
    返回 list[dict]，每条含 title/date/url/type。
    """
    code = _pure_code(code)
    cached = cache.read('announce', code)
    if cached is not None:
        return cached

    market = "SHA" if get_prefix(code) == "sh" else "SZA"
    params = {
        "sr": "-1",
        "page_size": "30",
        "page_index": "1",
        "ann_type": market,
        "client_source": "web",
        "stock_list": code,
        "f_node": "0",
        "s_node": "0",
    }

    _session = session or await get_session()
    try:
        async with _session.get(EM_ANN_URL, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

            items = data.get("data", {})
            if isinstance(items, dict):
                announcements = items.get("list", []) or []
            else:
                announcements = []

            result = []
            for ann in announcements:
                # 公告日期
                date_str = ann.get("notice_date", "")
                if isinstance(date_str, str) and len(date_str) > 16:
                    date_str = date_str[:16]

                title = ann.get("title", "")
                # 去除 HTML 标签
                title = re.sub(r"<[^>]+>", "", title)

                # 拼接PDF链接
                art_code = ann.get("art_code", "")
                url = f"https://np-cnotice-stock.eastmoney.com/api/content/ann?art_code={art_code}" if art_code else ""

                # 公告类型
                columns = ann.get("columns", [])
                col_name = ""
                if columns and isinstance(columns, list):
                    col_name = columns[0].get("column_name", "") if columns[0] else ""

                result.append({
                    "title": title,
                    "date": date_str,
                    "url": url,
                    "type": col_name,
                    "art_code": art_code,
                })

            if result:
                cache.write('announce', code, result)
            return result
    except Exception as e:
        logger.warning("get_announcements(%s) error: %s", code, e)
        return []
