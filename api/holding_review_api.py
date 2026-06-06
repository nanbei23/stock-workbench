"""Holding daily review APIs."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from services import holding_review_service
from services import auth_service

router = APIRouter(tags=["holding-reviews"])


class HoldingReviewRunPayload(BaseModel):
    account_id: str = "default"
    date: str | None = None
    include_watchlist_candidates: bool = False
    include_observation_pool: bool = False
    candidate_codes: list[str] = Field(default_factory=list)
    candidate_signal_filters: list[str] = Field(default_factory=list)
    force_refresh_holdings: bool = True
    force_refresh_candidates: bool = False
    refresh_snapshots_for_reports: bool = True


class HoldingReviewItemStatusPayload(BaseModel):
    status: str


async def _owned_account_id(user: dict, account_id: str | None = None) -> str:
    return await auth_service.resolve_securities_account_id(user, account_id)


async def _assert_review_owner(review_id: str, user: dict) -> dict:
    try:
        review = await holding_review_service.get_review(review_id)
    except HTTPException as exc:
        if exc.status_code == 404 and (user.get("id") or "admin") == "admin" and not user.get("authenticated"):
            return {"review_id": review_id, "account_id": None}
        raise
    await _owned_account_id(user, review.get("account_id"))
    return review


async def _scoped_review_account(user: dict, account_id: str | None) -> str | None:
    if account_id is None and (user.get("id") or "admin") == "admin" and not user.get("authenticated"):
        return None
    return await _owned_account_id(user, account_id)


@router.get("/holding-reviews")
async def list_holding_reviews(
    limit: int = Query(default=30, ge=1, le=200),
    account_id: str | None = Query(default=None),
    user: dict = Depends(auth_service.require_login_user),
):
    aid = await _scoped_review_account(user, account_id)
    return await holding_review_service.list_reviews(limit=limit, account_id=aid)


@router.post("/holding-reviews/run")
async def run_holding_review(payload: HoldingReviewRunPayload, user: dict = Depends(auth_service.require_login_user)):
    payload.account_id = await _owned_account_id(user, payload.account_id)
    return await holding_review_service.run_daily_review(
        account_id=payload.account_id,
        login_user_id=user.get("id") or "admin",
        date_text=payload.date,
        include_watchlist_candidates=payload.include_watchlist_candidates,
        include_observation_pool=payload.include_observation_pool,
        candidate_codes=payload.candidate_codes,
        candidate_signal_filters=payload.candidate_signal_filters,
        force_refresh_holdings=payload.force_refresh_holdings,
        force_refresh_candidates=payload.force_refresh_candidates,
        refresh_snapshots_for_reports=payload.refresh_snapshots_for_reports,
    )


@router.get("/holding-reviews/{review_id}")
async def get_holding_review(review_id: str, user: dict = Depends(auth_service.require_login_user)):
    return await _assert_review_owner(review_id, user)


@router.get("/holding-reviews/{review_id}/items")
async def get_holding_review_items(review_id: str, user: dict = Depends(auth_service.require_login_user)):
    await _assert_review_owner(review_id, user)
    return await holding_review_service.get_review_items(review_id)


@router.post("/holding-reviews/{review_id}/items/{item_id}/status")
async def update_holding_review_item_status(
    review_id: str,
    item_id: int,
    payload: HoldingReviewItemStatusPayload,
    user: dict = Depends(auth_service.require_login_user),
):
    await _assert_review_owner(review_id, user)
    return await holding_review_service.update_review_item_decision_status(review_id, item_id, payload.status)


@router.get("/holding-reviews/{review_id}/flags")
async def get_holding_review_flags(review_id: str, user: dict = Depends(auth_service.require_login_user)):
    await _assert_review_owner(review_id, user)
    return await holding_review_service.get_review_flags(review_id)


@router.get("/holding-reviews/{review_id}/markdown", response_class=PlainTextResponse)
async def get_holding_review_markdown(review_id: str, user: dict = Depends(auth_service.require_login_user)):
    review = await _assert_review_owner(review_id, user)
    return review.get("tomorrow_plan_markdown") or ""


@router.post("/holding-reviews/{review_id}/archive")
async def archive_holding_review(review_id: str, user: dict = Depends(auth_service.require_login_user)):
    await _assert_review_owner(review_id, user)
    return await holding_review_service.archive_review(review_id)


@router.get("/daily-decision-reports")
async def list_daily_decision_reports(
    limit: int = Query(default=30, ge=1, le=200),
    account_id: str | None = Query(default=None),
    user: dict = Depends(auth_service.require_login_user),
):
    aid = await _scoped_review_account(user, account_id)
    return await holding_review_service.list_reviews(limit=limit, account_id=aid)


@router.post("/daily-decision-reports/run")
async def run_daily_decision_report(payload: HoldingReviewRunPayload, user: dict = Depends(auth_service.require_login_user)):
    return await run_holding_review(payload, user)


@router.get("/daily-decision-reports/{review_id}")
async def get_daily_decision_report(review_id: str, user: dict = Depends(auth_service.require_login_user)):
    return await get_holding_review(review_id, user)


@router.get("/daily-decision-reports/{review_id}/items")
async def get_daily_decision_report_items(review_id: str, user: dict = Depends(auth_service.require_login_user)):
    return await get_holding_review_items(review_id, user)


@router.post("/daily-decision-reports/{review_id}/items/{item_id}/status")
async def update_daily_decision_report_item_status(
    review_id: str,
    item_id: int,
    payload: HoldingReviewItemStatusPayload,
    user: dict = Depends(auth_service.require_login_user),
):
    return await update_holding_review_item_status(review_id, item_id, payload, user)


@router.get("/daily-decision-reports/{review_id}/flags")
async def get_daily_decision_report_flags(review_id: str, user: dict = Depends(auth_service.require_login_user)):
    return await get_holding_review_flags(review_id, user)


@router.get("/daily-decision-reports/{review_id}/markdown", response_class=PlainTextResponse)
async def get_daily_decision_report_markdown(review_id: str, user: dict = Depends(auth_service.require_login_user)):
    return await get_holding_review_markdown(review_id, user)


@router.post("/daily-decision-reports/{review_id}/archive")
async def archive_daily_decision_report(review_id: str, user: dict = Depends(auth_service.require_login_user)):
    return await archive_holding_review(review_id, user)
