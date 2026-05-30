"""News application service."""

from data.helpers import tencent_quote_batch
from data.news import (
    get_cls_telegraph,
    get_global_news_724,
    get_stock_news,
    search_wechat_articles,
)


async def get_stock_news_for_code(code: str, limit: int = 20):
    news = await get_stock_news(code)
    return {"code": code, "news": news[:limit]}


async def get_cls_news(limit: int = 30):
    news = await get_cls_telegraph(limit)
    return {"news": news[:limit]}


async def get_global_news(limit: int = 30):
    news = await get_global_news_724()
    return {"news": news[:limit]}


async def get_sentiment(code: str):
    news = await get_stock_news(code)
    return {
        "code": code,
        "total": len(news),
        "positive": sum(1 for item in news if item.get("sentiment") == "positive"),
        "negative": sum(1 for item in news if item.get("sentiment") == "negative"),
        "neutral": sum(1 for item in news if item.get("sentiment") == "neutral"),
    }


async def get_wechat_news(code: str, keyword: str = "", limit: int = 15):
    if not keyword:
        try:
            quotes = await tencent_quote_batch([code])
            name = quotes.get(code, {}).get("name", "")
            keyword = name or code
        except Exception:
            keyword = code

    news = await search_wechat_articles(keyword, limit)
    return {"code": code, "keyword": keyword, "news": news}
