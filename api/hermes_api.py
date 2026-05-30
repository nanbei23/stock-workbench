"""Hermes natural language operation console API."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from services import hermes_console_service


router = APIRouter(tags=["hermes-console"])


class HermesMessageRequest(BaseModel):
    message: str
    session_id: str | None = None


class HermesConfirmRequest(BaseModel):
    session_id: str
    draft_id: str


class HermesStepRequest(HermesConfirmRequest):
    step_id: str


@router.post("/hermes/message")
async def send_hermes_message(req: HermesMessageRequest):
    return await hermes_console_service.handle_message(req.message, req.session_id)


@router.post("/hermes/confirm")
async def confirm_hermes_draft(req: HermesConfirmRequest):
    return await hermes_console_service.confirm_draft(req.session_id, req.draft_id)


@router.post("/hermes/cancel")
async def cancel_hermes_draft(req: HermesConfirmRequest):
    return await hermes_console_service.cancel_draft(req.session_id, req.draft_id)


@router.post("/hermes/step/confirm")
async def confirm_hermes_plan_step(req: HermesStepRequest):
    return await hermes_console_service.confirm_plan_step(req.session_id, req.draft_id, req.step_id)


@router.post("/hermes/step/skip")
async def skip_hermes_plan_step(req: HermesStepRequest):
    return await hermes_console_service.skip_plan_step(req.session_id, req.draft_id, req.step_id)


@router.get("/hermes/sessions")
async def list_hermes_sessions(limit: int = Query(default=50, ge=1, le=200)):
    return await hermes_console_service.list_sessions(limit=limit)


@router.get("/hermes/session/{session_id}")
async def list_hermes_session(session_id: str, limit: int = Query(default=30, ge=1, le=100)):
    return await hermes_console_service.list_session_events(session_id, limit=limit)


@router.get("/hermes/session/{session_id}/tool-runs")
async def list_hermes_tool_runs(session_id: str, limit: int = Query(default=30, ge=1, le=100)):
    return await hermes_console_service.list_tool_runs(session_id, limit=limit)
