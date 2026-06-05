"""Holding daily review APIs."""

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from services import holding_review_service

router = APIRouter(tags=["holding-reviews"])


class HoldingReviewRunPayload(BaseModel):
    account_id: str = "default"
    date: str | None = None
    include_watchlist_candidates: bool = False
    include_observation_pool: bool = False
    candidate_codes: list[str] = Field(default_factory=list)
    force_refresh_holdings: bool = False
    force_refresh_candidates: bool = False
    refresh_snapshots_for_reports: bool = False


@router.get("/holding-reviews")
async def list_holding_reviews(
    limit: int = Query(default=30, ge=1, le=200),
    account_id: str | None = Query(default=None),
):
    return await holding_review_service.list_reviews(limit=limit, account_id=account_id)


@router.post("/holding-reviews/run")
async def run_holding_review(payload: HoldingReviewRunPayload):
    return await holding_review_service.run_daily_review(
        account_id=payload.account_id,
        date_text=payload.date,
        include_watchlist_candidates=payload.include_watchlist_candidates,
        include_observation_pool=payload.include_observation_pool,
        candidate_codes=payload.candidate_codes,
        force_refresh_holdings=payload.force_refresh_holdings,
        force_refresh_candidates=payload.force_refresh_candidates,
        refresh_snapshots_for_reports=payload.refresh_snapshots_for_reports,
    )


@router.get("/holding-reviews/{review_id}")
async def get_holding_review(review_id: str):
    return await holding_review_service.get_review(review_id)


@router.get("/holding-reviews/{review_id}/items")
async def get_holding_review_items(review_id: str):
    return await holding_review_service.get_review_items(review_id)


@router.get("/holding-reviews/{review_id}/flags")
async def get_holding_review_flags(review_id: str):
    return await holding_review_service.get_review_flags(review_id)


@router.get("/holding-reviews/{review_id}/markdown", response_class=PlainTextResponse)
async def get_holding_review_markdown(review_id: str):
    review = await holding_review_service.get_review(review_id)
    return review.get("tomorrow_plan_markdown") or ""


@router.post("/holding-reviews/{review_id}/archive")
async def archive_holding_review(review_id: str):
    return await holding_review_service.archive_review(review_id)


@router.get("/daily-decision-reports")
async def list_daily_decision_reports(
    limit: int = Query(default=30, ge=1, le=200),
    account_id: str | None = Query(default=None),
):
    return await holding_review_service.list_reviews(limit=limit, account_id=account_id)


@router.post("/daily-decision-reports/run")
async def run_daily_decision_report(payload: HoldingReviewRunPayload):
    return await run_holding_review(payload)


@router.get("/daily-decision-reports/{review_id}")
async def get_daily_decision_report(review_id: str):
    return await get_holding_review(review_id)


@router.get("/daily-decision-reports/{review_id}/items")
async def get_daily_decision_report_items(review_id: str):
    return await get_holding_review_items(review_id)


@router.get("/daily-decision-reports/{review_id}/flags")
async def get_daily_decision_report_flags(review_id: str):
    return await get_holding_review_flags(review_id)


@router.get("/daily-decision-reports/{review_id}/markdown", response_class=PlainTextResponse)
async def get_daily_decision_report_markdown(review_id: str):
    return await get_holding_review_markdown(review_id)


@router.post("/daily-decision-reports/{review_id}/archive")
async def archive_daily_decision_report(review_id: str):
    return await archive_holding_review(review_id)
