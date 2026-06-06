"""Report selection handoff APIs."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from services import report_selection_service
from services import auth_service


router = APIRouter(prefix="/report-selections", tags=["report-selections"])


class ReportSelectionPayload(BaseModel):
    source_page: str = "unknown"
    source_label: str = ""
    codes: list[str] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    ttl_hours: int = Field(default=24, ge=1, le=168)


@router.post("")
async def create_report_selection(payload: ReportSelectionPayload, user: dict = Depends(auth_service.require_login_user)):
    return report_selection_service.create_selection_set(payload.model_dump(), login_user_id=user.get("id") or "admin")


@router.get("/{selection_id}")
async def get_report_selection(selection_id: str, user: dict = Depends(auth_service.require_login_user)):
    return report_selection_service.get_selection_set(selection_id, login_user_id=user.get("id") or "admin")


@router.delete("/{selection_id}")
async def delete_report_selection(selection_id: str, user: dict = Depends(auth_service.require_login_user)):
    return report_selection_service.delete_selection_set(selection_id, login_user_id=user.get("id") or "admin")
