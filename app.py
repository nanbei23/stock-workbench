"""炒股小牛马工作台 v2.0 — FastAPI入口"""
import sys
import json
import asyncio
import logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

from models.database import init_db, get_db
from scheduler.jobs import setup_scheduler
from config import HOST, PORT

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化数据库"""
    await init_db()
    sched = setup_scheduler()
    print("✅ 数据库初始化完成")
    print("✅ 定时任务已启动")
    yield
    sched.shutdown()
    from data.helpers import close_session
    await close_session()
    print("🛑 服务关闭")

app = FastAPI(title="炒股小牛马工作台", version="2.0", lifespan=lifespan)

# 静态文件 + 模板
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# === 页面路由 ===
@app.get("/", response_class=HTMLResponse)
async def page_watchlist(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/portfolio", response_class=HTMLResponse)
async def page_portfolio(request: Request):
    return templates.TemplateResponse(request=request, name="portfolio.html")

@app.get("/ai", response_class=HTMLResponse)
async def page_ai(request: Request):
    return templates.TemplateResponse(request=request, name="ai.html")

@app.get("/settings", response_class=HTMLResponse)
async def page_settings(request: Request):
    return templates.TemplateResponse(request=request, name="settings.html")

@app.get("/stock/{code}", response_class=HTMLResponse)
async def stock_detail(request: Request, code: str):
    """Stock detail redirect — detail is embedded in index.html"""
    return templates.TemplateResponse(request=request, name="stock.html", context={"code": code})

# === API路由 ===
from api.quote_api import router as quote_router
from api.portfolio_api import router as portfolio_router
from api.ai_api import router as ai_router
from api.news_api import router as news_router
from api.settings_api import router as settings_router
from api.layer_api import router as layer_router
from api.strategy_api import router as strategy_router
from api.pdf_export import router as pdf_router
from api.signal_api import router as signal_router

app.include_router(quote_router, prefix="/api")
app.include_router(portfolio_router, prefix="/api")
app.include_router(ai_router, prefix="/api")
app.include_router(news_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(layer_router, prefix="/api")
app.include_router(strategy_router, prefix="/api")
app.include_router(pdf_router, prefix="/api")
app.include_router(signal_router, prefix="/api")

# === WebSocket 实时行情 ===
@app.websocket("/ws/quotes")
async def websocket_quotes(ws: WebSocket):
    """WebSocket 实时行情推送 — 每5秒推送自选股行情"""
    await ws.accept()
    logger.info("WebSocket client connected")
    try:
        while True:
            try:
                from data.quote import get_batch_quotes
                db = await get_db()
                try:
                    cursor = await db.execute("SELECT code FROM watchlist ORDER BY sort_order ASC")
                    rows = await cursor.fetchall()
                    codes = [r["code"] for r in rows]
                finally:
                    await db.close()

                if codes:
                    quotes = await get_batch_quotes(codes)
                    await ws.send_json({"type": "quotes", "data": quotes})
                else:
                    await ws.send_json({"type": "quotes", "data": {}})

                await asyncio.sleep(5)
            except Exception as e:
                logger.warning("WS quote push error: %s", e)
                await asyncio.sleep(5)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.warning("WebSocket error: %s", e)


@app.get("/performance", response_class=HTMLResponse)
async def performance_page(request: Request):
    """信号绩效独立页面"""
    return templates.TemplateResponse(request=request, name="performance.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host=HOST, port=PORT, reload=True)
