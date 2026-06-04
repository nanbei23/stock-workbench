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
