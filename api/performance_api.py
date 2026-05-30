"""Unified AI performance API."""

import logging

from fastapi import APIRouter, HTTPException, Query

from services import performance_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/performance", tags=["AI绩效"])


@router.get("/overview")
async def get_performance_overview(
    window: str = Query(default="all"),
    model_mode: str | None = Query(default=None),
    depth: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    try:
        return await performance_service.overview(
            window=window,
            model_mode=model_mode,
            depth=depth,
            limit=limit,
        )
    except Exception as exc:
        logger.error("get_performance_overview error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/filters")
async def get_performance_filters():
    try:
        return await performance_service.filter_options()
    except Exception as exc:
        logger.error("get_performance_filters error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
