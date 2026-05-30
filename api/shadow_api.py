"""AI shadow portfolio API."""

import logging

from fastapi import APIRouter, HTTPException, Query

from services import shadow_portfolio_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/shadow", tags=["AI影子盘"])


@router.get("/summary")
async def get_shadow_summary():
    try:
        return await shadow_portfolio_service.summary()
    except Exception as exc:
        logger.error("get_shadow_summary error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/sync-reports")
async def sync_shadow_reports(limit: int = Query(default=100, ge=1, le=500)):
    try:
        return await shadow_portfolio_service.sync_reports(limit)
    except Exception as exc:
        logger.error("sync_shadow_reports error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/mark-to-market")
async def mark_shadow_to_market():
    try:
        return await shadow_portfolio_service.mark_to_market()
    except Exception as exc:
        logger.error("mark_shadow_to_market error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/orders")
async def list_shadow_orders(
    limit: int = Query(default=100, ge=1, le=500),
    window: str = Query(default="all"),
    model_mode: str | None = Query(default=None),
    depth: str | None = Query(default=None),
):
    try:
        return await shadow_portfolio_service.list_orders(limit, window, model_mode, depth)
    except Exception as exc:
        logger.error("list_shadow_orders error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/positions")
async def list_shadow_positions():
    try:
        return await shadow_portfolio_service.list_positions()
    except Exception as exc:
        logger.error("list_shadow_positions error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/comparison")
async def get_shadow_comparison():
    try:
        return await shadow_portfolio_service.comparison()
    except Exception as exc:
        logger.error("get_shadow_comparison error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/calibration")
async def get_shadow_calibration(
    limit: int = Query(default=200, ge=1, le=500),
    window: str = Query(default="all"),
    model_mode: str | None = Query(default=None),
    depth: str | None = Query(default=None),
):
    try:
        return await shadow_portfolio_service.calibration(limit, window, model_mode, depth)
    except Exception as exc:
        logger.error("get_shadow_calibration error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/execution-deviation")
async def get_shadow_execution_deviation():
    try:
        return await shadow_portfolio_service.execution_deviation()
    except Exception as exc:
        logger.error("get_shadow_execution_deviation error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
