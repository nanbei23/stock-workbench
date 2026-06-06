"""Unified AI performance API."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from services import execution_review_service, performance_service
from services import auth_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/performance", tags=["AI绩效"])


@router.get("/overview")
async def get_performance_overview(
    window: str = Query(default="all"),
    model_mode: str | None = Query(default=None),
    depth: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    account_id: str | None = Query(default=None),
    user: dict = Depends(auth_service.require_login_user),
):
    try:
        aid = await auth_service.resolve_securities_account_id(user, account_id)
        kwargs = {"window": window, "model_mode": model_mode, "depth": depth, "limit": limit}
        if account_id or user.get("authenticated") or (user.get("id") and user.get("id") != auth_service.DEFAULT_LOGIN_USER_ID):
            kwargs.update({"login_user_id": user.get("id"), "account_id": aid})
        return await performance_service.overview(**kwargs)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_performance_overview error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/filters")
async def get_performance_filters(user: dict = Depends(auth_service.require_login_user)):
    try:
        return await performance_service.filter_options(login_user_id=user.get("id"))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_performance_filters error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/suggestion-execution")
async def get_suggestion_execution(
    source: str = Query(default="all"),
    source_id: str | None = Query(default=None),
    limit: int = Query(default=300, ge=1, le=1000),
    account_id: str | None = Query(default=None),
    user: dict = Depends(auth_service.require_login_user),
):
    try:
        aid = await auth_service.resolve_securities_account_id(user, account_id)
        clean = (source or "all").strip().lower()
        if clean in {"daily", "daily_decision", "daily_decisions"}:
            return await execution_review_service.daily_decision_execution(review_id=source_id, limit=limit, account_id=aid)
        if clean in {"position_plan", "position_plans", "plan", "plans"}:
            return await execution_review_service.position_plan_execution(plan_id=source_id, limit=limit, account_id=aid)
        return await execution_review_service.overview(limit=limit, account_id=aid)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("get_suggestion_execution error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))
