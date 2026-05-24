"""新闻API — FastAPI Router（aiohttp 异步）"""
import asyncio
from fastapi import APIRouter
from data.news import get_stock_news, get_cls_telegraph, get_global_news_724, search_wechat_articles
from data.helpers import tencent_quote_batch

router = APIRouter(tags=["新闻"])


@router.get("/news/{code}")
async def get_news(code: str, limit: int = 20):
    """获取个股新闻"""
    news = await get_stock_news(code)
    return {"code": code, "news": news[:limit]}


@router.get("/news/cls")
async def get_cls_news(limit: int = 30):
    """获取财联社电报快讯"""
    news = await get_cls_telegraph(limit)
    return {"news": news[:limit]}


@router.get("/news/global")
async def get_global_news(limit: int = 30):
    """获取东方财富7×24全球资讯"""
    news = await get_global_news_724()
    return {"news": news[:limit]}


@router.get("/news/sentiment/{code}")
async def get_sentiment(code: str):
    """获取个股新闻情感统计"""
    news = await get_stock_news(code)
    pos = sum(1 for n in news if n.get("sentiment") == "positive")
    neg = sum(1 for n in news if n.get("sentiment") == "negative")
    neu = sum(1 for n in news if n.get("sentiment") == "neutral")
    return {
        "code": code,
        "total": len(news),
        "positive": pos,
        "negative": neg,
        "neutral": neu,
    }


@router.get("/news/wechat/{code}")
async def get_wechat_news(code: str, keyword: str = "", limit: int = 15):
    """获取微信公众号文章（搜狗微信搜索）"""
    # 如果没传 keyword，用股票名 + 代码作为默认搜索词
    if not keyword:
        try:
            quotes = await tencent_quote_batch([code])
            name = quotes.get(code, {}).get("name", "")
            keyword = name or code
        except Exception:
            keyword = code

    news = await search_wechat_articles(keyword, limit)
    return {"code": code, "keyword": keyword, "news": news}
