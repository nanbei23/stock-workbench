"""新闻API — FastAPI Router（aiohttp 异步）"""
from fastapi import APIRouter

from services import news_service

router = APIRouter(tags=["新闻"])


@router.get("/news/{code}")
async def get_news(code: str, limit: int = 20):
    """获取个股新闻"""
    return await news_service.get_stock_news_for_code(code, limit)


@router.get("/news/cls")
async def get_cls_news(limit: int = 30):
    """获取财联社电报快讯"""
    return await news_service.get_cls_news(limit)


@router.get("/news/global")
async def get_global_news(limit: int = 30):
    """获取东方财富7×24全球资讯"""
    return await news_service.get_global_news(limit)


@router.get("/news/sentiment/{code}")
async def get_sentiment(code: str):
    """获取个股新闻情感统计"""
    return await news_service.get_sentiment(code)


@router.get("/news/wechat/{code}")
async def get_wechat_news(code: str, keyword: str = "", limit: int = 15):
    """获取微信公众号文章（搜狗微信搜索）"""
    return await news_service.get_wechat_news(code, keyword, limit)
