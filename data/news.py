"""
炒股小牛马工作台 — Layer 5 新闻
东方财富 search-api / 财联社电报 / 东方财富全球新闻 + 简易情感分析
"""
import datetime
import json
import logging
import re
from typing import Optional

import aiohttp

from data.helpers import _pure_code, HEADERS, UA, get_session
from cache.shared_cache import cache

logger = logging.getLogger(__name__)

# ── 情感关键词 ──────────────────────────────────────────────
POSITIVE_KEYWORDS = [
    "利好", "涨停", "突破", "超预期", "增长", "创新高",
    "回购", "增持", "扭亏", "业绩大增", "战略合作",
    "重大突破", "中标", "签约", "预增", "翻倍",
]
NEGATIVE_KEYWORDS = [
    "利空", "跌停", "违规", "处罚", "亏损", "暴跌",
    "减持", "质押", "退市", "风险", "立案", "调查",
    "诉讼", "被罚", "业绩下滑", "预亏", "爆雷",
]


def _simple_sentiment(text: str) -> str:
    """
    基于关键词的简易情感分析。
    返回 'positive' / 'negative' / 'neutral'。
    """
    pos = sum(1 for kw in POSITIVE_KEYWORDS if kw in text)
    neg = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text)
    if pos > neg:
        return "positive"
    elif neg > pos:
        return "negative"
    return "neutral"


def _sentiment_score(text: str) -> float:
    """
    返回 -1.0 到 1.0 的情感分数。
    """
    pos = sum(1 for kw in POSITIVE_KEYWORDS if kw in text)
    neg = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text)
    total = pos + neg
    if total == 0:
        return 0.0
    return round((pos - neg) / total, 2)


async def get_stock_news(
    code: str,
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取个股新闻（东方财富 search-api JSONP）。
    返回 list[dict]，每条含 title/url/date/media/sentiment。
    """
    code = _pure_code(code)
    cached = cache.read('news', code)
    if cached is not None:
        return cached

    url = (
        f"https://search-api-web.eastmoney.com/search/jsonp?"
        f"cb=jQuery&param=%7B%22uid%22%3A%22%22%2C%22keyword%22%3A%22{code}%22%2C"
        f"%22type%22%3A%5B%22cmsArticleWebOld%22%5D%2C%22client%22%3A%22web%22%2C"
        f"%22clientType%22%3A%22web%22%2C%22clientVersion%22%3A%22curr%22%2C"
        f"%22param%22%3A%7B%22cmsArticleWebOld%22%3A%7B%22searchScope%22%3A%22default%22%2C"
        f"%22sort%22%3A%22default%22%2C%22pageIndex%22%3A1%2C%22pageSize%22%3A20%2C"
        f"%22preTag%22%3A%22%22%2C%22postTag%22%3A%22%22%7D%7D%7D"
    )

    _session = session or await get_session()
    try:
        async with _session.get(url) as resp:
            text = await resp.text()
            # 解析 JSONP
            start = text.find("(")
            end = text.rfind(")")
            if start != -1 and end != -1:
                text = text[start + 1 : end]

            data = json.loads(text)
            result = data.get("result", {})
            # result 可能是 dict 或 list
            if isinstance(result, list):
                # 新版 API 直接返回列表
                articles = result
            elif isinstance(result, dict):
                caw = result.get("cmsArticleWebOld", [])
                if isinstance(caw, list):
                    articles = caw
                elif isinstance(caw, dict):
                    articles = caw.get("list", [])
                else:
                    articles = []
            else:
                articles = []

            result = []
            for a in articles:
                title = a.get("title", "")
                # 去除 HTML 标签
                title = re.sub(r"<[^>]+>", "", title)
                date = a.get("date", a.get("showTime", ""))

                item = {
                    "title": title,
                    "url": a.get("url", a.get("articleUrl", "")),
                    "date": str(date)[:16],
                    "media": a.get("mediaName", a.get("source", "")),
                    "content": re.sub(r"<[^>]+>", "", a.get("content", ""))[:300],
                }
                # 添加情感分析
                full_text = title + " " + item.get("content", "")
                item["sentiment"] = _simple_sentiment(full_text)
                item["sentiment_score"] = _sentiment_score(full_text)
                result.append(item)

            cache.write('news', code, result)
            return result
    except Exception as e:
        logger.warning("get_stock_news(%s) error: %s", code, e)
        return []


async def get_cls_telegraph(
    limit: int = 30,
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取财联社电报（cls.cn）。
    返回 list[dict]，每条含 title/content/time/sentiment。
    """
    url = "https://www.cls.cn/nodeapi/updateTelegraphList"
    params = {
        "app": "CailianpressWeb",
        "os": "web",
        "sv": "8.4.6",
        "rn": str(limit),
    }

    _session = session or await get_session()
    try:
        async with _session.get(url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

            result = []
            telegraphs = data.get("data", {}).get("roll_data", [])
            for t in telegraphs:
                content = t.get("content", "")
                title = t.get("title", content[:50])
                timestamp = t.get("ctime", 0)

                # 时间戳转日期
                date_str = ""
                if timestamp:
                    try:
                        date_str = datetime.datetime.fromtimestamp(timestamp).strftime(
                            "%Y-%m-%d %H:%M"
                        )
                    except Exception:
                        date_str = str(timestamp)

                full_text = title + " " + content
                result.append({
                    "title": title,
                    "content": content[:500],
                    "time": date_str,
                    "sentiment": _simple_sentiment(full_text),
                    "sentiment_score": _sentiment_score(full_text),
                })

            return result
    except Exception as e:
        logger.warning("get_cls_telegraph() error: %s", e)
        return []


async def search_wechat_articles(
    keyword: str,
    limit: int = 15,
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    搜狗微信公众号文章搜索。
    URL: https://weixin.sogou.com/weixin?query={keyword}&type=2
    返回 list[dict]，每条含 title/summary/source/date/url/sentiment。
    """
    import random
    import urllib.parse

    uas = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/118.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/117.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    ]

    encoded = urllib.parse.quote(keyword)
    url = f"https://weixin.sogou.com/weixin?query={encoded}&type=2"

    headers = {
        "User-Agent": random.choice(uas),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://weixin.sogou.com/",
    }

    _session = session or await get_session()
    try:
        async with _session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            html = await resp.text()

        result = []
        # 解析搜狗微信搜索结果 HTML
        # 每条结果在 <li> 中，包含 .txt-box > h3 > a (标题) + p.txt-info (摘要)
        items = re.findall(
            r'<li[^>]*>.*?<div\s+class="txt-box">(.*?)</li>',
            html, re.DOTALL
        )

        for item in items[:limit]:
            # 标题 + URL
            title_m = re.search(r'<h3>(.*?)</h3>', item, re.DOTALL)
            if not title_m:
                continue
            title_block = title_m.group(1)
            link_m = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', title_block, re.DOTALL)
            if not link_m:
                continue
            raw_url = link_m.group(1).replace('&amp;', '&')
            article_url = raw_url if raw_url.startswith('http') else f"https://weixin.sogou.com{raw_url}"
            title = re.sub(r'<[^>]+>', '', link_m.group(2)).strip()
            title = re.sub(r'\s+', ' ', title)

            # 摘要
            summary_m = re.search(r'<p\s+class="txt-info"[^>]*>(.*?)</p>', item, re.DOTALL)
            summary = ""
            if summary_m:
                summary = re.sub(r'<[^>]+>', '', summary_m.group(1)).strip()
                summary = re.sub(r'\s+', ' ', summary)

            # 公众号名称 — class="all-time-y2"
            source_m = re.search(r'class="all-time-y2"[^>]*>(.*?)</span>', item, re.DOTALL)
            source = ""
            if source_m:
                source = re.sub(r'<[^>]+>', '', source_m.group(1)).strip()

            # 日期 — timeConvert('TIMESTAMP')
            date_str = ""
            ts_m = re.search(r"timeConvert\('(\d+)'\)", item)
            if ts_m:
                try:
                    ts = int(ts_m.group(1))
                    date_str = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                except Exception:
                    date_str = ts_m.group(1)

            if not title:
                continue

            full_text = title + " " + summary
            result.append({
                "title": title,
                "url": article_url,
                "summary": summary[:300],
                "source": source,
                "date": date_str,
                "sentiment": _simple_sentiment(full_text),
                "sentiment_score": _sentiment_score(full_text),
            })

        return result
    except Exception as e:
        logger.warning("search_wechat_articles(%s) error: %s", keyword, e)
        return []


async def get_global_news(
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取全球财经新闻（东方财富 np-weblist）。
    返回 list[dict]，每条含 title/url/date/media/content/sentiment。
    """
    url = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
    params = {
        "columns": "74,467",
        "pageSize": "30",
        "listId": "",
        "type": "1",
    }

    _session = session or await get_session()
    try:
        async with _session.get(url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

            result = []
            items = data.get("data", {}).get("list", [])
            for item in items:
                title = item.get("title", "")
                content = item.get("digest", item.get("content", ""))
                full_text = title + " " + content

                result.append({
                    "title": title,
                    "url": item.get("url", ""),
                    "date": (item.get("showTime", item.get("date", "")))[:16],
                    "media": item.get("source", item.get("mediaName", "")),
                    "content": content[:500],
                    "sentiment": _simple_sentiment(full_text),
                    "sentiment_score": _sentiment_score(full_text),
                })

            return result
    except Exception as e:
        logger.warning("get_global_news() error: %s", e)
        return []


async def get_global_news_724(
    session: Optional[aiohttp.ClientSession] = None,
) -> list[dict]:
    """
    获取东方财富7×24全球资讯。
    返回 list[dict]，每条含 title/url/date/media/content/sentiment。
    """
    url = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
    params = {
        "client": "web",
        "biz": "web_724",
        "column": "724",
        "order": "1",
        "needInteractData": "0",
        "page_index": "1",
        "page_size": "30",
    }

    _session = session or await get_session()
    try:
        async with _session.get(url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

            result = []
            items = data.get("data", {}).get("list", [])
            for item in items:
                title = item.get("title", "")
                content = item.get("digest", item.get("content", ""))
                full_text = title + " " + content

                result.append({
                    "title": title,
                    "url": item.get("url_w", item.get("url", "")),
                    "date": (item.get("showTime", item.get("date", "")))[:16],
                    "media": item.get("source", item.get("mediaName", "")),
                    "content": content[:500],
                    "sentiment": _simple_sentiment(full_text),
                    "sentiment_score": _sentiment_score(full_text),
                })

            return result
    except Exception as e:
        logger.warning("get_global_news_724() error: %s", e)
        return []
