"""Position plan and data snapshot research asset APIs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from services import position_plan_service
from services import auth_service

router = APIRouter(tags=["position-plans"])


class PositionPlanItemAdoptionPayload(BaseModel):
    adoption_status: str
    note: str = ""


async def _owned_account_id(user: dict, account_id: str | None = None) -> str:
    return await auth_service.resolve_securities_account_id(user, account_id)


def _first_account_id(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("account_id", "securities_account_id"):
            if value.get(key):
                return str(value[key])
        balances = value.get("balances")
        if isinstance(balances, dict) and len(balances) == 1:
            return str(next(iter(balances)))
        for child in value.values():
            found = _first_account_id(child)
            if found:
                return found
    if isinstance(value, list):
        for child in value:
            found = _first_account_id(child)
            if found:
                return found
    return None


async def _assert_plan_owner(plan_id: str, user: dict) -> dict:
    plan = position_plan_service.get_position_plan(plan_id)
    account_id = (
        _first_account_id(plan.get("cash_snapshot_json"))
        or _first_account_id(plan.get("portfolio_snapshot_json"))
        or _first_account_id(plan.get("output_json"))
        or "default"
    )
    await _owned_account_id(user, account_id)
    return plan


@router.get("/position-plans")
async def list_position_plans(
    limit: int = Query(default=50, ge=1, le=200),
    status: str | None = Query(default=None),
    stage: str | None = Query(default=None),
    account_id: str | None = Query(default=None),
    user: dict = Depends(auth_service.require_login_user),
):
    if account_id:
        await _owned_account_id(user, account_id)
    return position_plan_service.list_position_plans(limit=limit, status=status, stage=stage)


@router.get("/position-plans/{plan_id}")
async def get_position_plan(plan_id: str, user: dict = Depends(auth_service.require_login_user)):
    return await _assert_plan_owner(plan_id, user)


@router.get("/position-plans/{plan_id}/items")
async def get_position_plan_items(plan_id: str, user: dict = Depends(auth_service.require_login_user)):
    plan = await _assert_plan_owner(plan_id, user)
    return {"count": len(plan.get("items") or []), "items": plan.get("items") or []}


@router.post("/position-plans/{plan_id}/items/{item_id}/adoption")
async def update_position_plan_item_adoption(
    plan_id: str,
    item_id: int,
    payload: PositionPlanItemAdoptionPayload,
    user: dict = Depends(auth_service.require_login_user),
):
    await _assert_plan_owner(plan_id, user)
    return position_plan_service.update_position_plan_item_adoption(
        plan_id,
        item_id,
        payload.adoption_status,
        note=payload.note,
    )


@router.get("/position-plans/{plan_id}/markdown", response_class=PlainTextResponse)
async def get_position_plan_markdown(plan_id: str, user: dict = Depends(auth_service.require_login_user)):
    await _assert_plan_owner(plan_id, user)
    return position_plan_service.position_plan_markdown(plan_id)


@router.post("/position-plans/{plan_id}/archive")
async def archive_position_plan(plan_id: str, user: dict = Depends(auth_service.require_login_user)):
    await _assert_plan_owner(plan_id, user)
    return position_plan_service.archive_position_plan(plan_id)


@router.post("/position-plans/{plan_id}/adopt")
async def adopt_position_plan(plan_id: str, user: dict = Depends(auth_service.require_login_user)):
    await _assert_plan_owner(plan_id, user)
    return position_plan_service.adopt_position_plan(plan_id)


@router.post("/position-plans/{plan_id}/partial-adopt")
async def partially_adopt_position_plan(plan_id: str, user: dict = Depends(auth_service.require_login_user)):
    await _assert_plan_owner(plan_id, user)
    return position_plan_service.partially_adopt_position_plan(plan_id)


@router.post("/position-plans/{plan_id}/abandon")
async def abandon_position_plan(plan_id: str, user: dict = Depends(auth_service.require_login_user)):
    await _assert_plan_owner(plan_id, user)
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
