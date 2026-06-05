"""Position plan and data snapshot research asset APIs."""

from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from services import position_plan_service

router = APIRouter(tags=["position-plans"])


@router.get("/position-plans")
async def list_position_plans(
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None),
    stage: str | None = Query(default=None),
):
    return position_plan_service.list_position_plans(limit=limit, status=status, stage=stage)


@router.get("/position-plans/{plan_id}")
async def get_position_plan(plan_id: str):
    return position_plan_service.get_position_plan(plan_id)


@router.get("/position-plans/{plan_id}/items")
async def get_position_plan_items(plan_id: str):
    plan = position_plan_service.get_position_plan(plan_id)
    return {"count": len(plan.get("items") or []), "items": plan.get("items") or []}


@router.get("/position-plans/{plan_id}/markdown", response_class=PlainTextResponse)
async def get_position_plan_markdown(plan_id: str):
    return position_plan_service.position_plan_markdown(plan_id)


@router.post("/position-plans/{plan_id}/archive")
async def archive_position_plan(plan_id: str):
    return position_plan_service.archive_position_plan(plan_id)


@router.post("/position-plans/{plan_id}/adopt")
async def adopt_position_plan(plan_id: str):
    return position_plan_service.adopt_position_plan(plan_id)


@router.post("/position-plans/{plan_id}/partial-adopt")
async def partially_adopt_position_plan(plan_id: str):
    return position_plan_service.partially_adopt_position_plan(plan_id)


@router.post("/position-plans/{plan_id}/abandon")
async def abandon_position_plan(plan_id: str):
    return position_plan_service.abandon_position_plan(plan_id)


@router.get("/reports/snapshots")
async def list_data_snapshots(
    limit: int = Query(default=100, ge=1, le=500),
    code: str | None = Query(default=None),
    ok: bool | None = Query(default=None),
    run_id: str | None = Query(default=None),
):
    return position_plan_service.list_data_snapshots(limit=limit, code=code, ok=ok, run_id=run_id)


@router.get("/reports/snapshots/{snapshot_id}")
async def get_data_snapshot(snapshot_id: int):
    return position_plan_service.get_data_snapshot(snapshot_id)
