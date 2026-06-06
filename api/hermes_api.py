"""Hermes natural language operation console API."""

import inspect

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from services import hermes_console_service
from services import hermes_tool_registry
from services import auth_service


router = APIRouter(tags=["hermes-console"])


class HermesMessageRequest(BaseModel):
    message: str
    session_id: str | None = None
    account_id: str | None = None


class HermesConfirmRequest(BaseModel):
    session_id: str
    draft_id: str
    account_id: str | None = None


class HermesStepRequest(HermesConfirmRequest):
    step_id: str


class HermesToolPolicyRequest(BaseModel):
    policy: dict[str, str]


@router.post("/hermes/message")
async def send_hermes_message(req: HermesMessageRequest, user: dict = Depends(auth_service.require_login_user)):
    account_id = await auth_service.resolve_securities_account_id(user, req.account_id)
    fn = hermes_console_service.handle_message
    if "login_user_id" not in inspect.signature(fn).parameters:
        return await fn(req.message, req.session_id)
    return await fn(req.message, req.session_id, login_user_id=user.get("id"), account_id=account_id)


@router.post("/hermes/confirm")
async def confirm_hermes_draft(req: HermesConfirmRequest, user: dict = Depends(auth_service.require_login_user)):
    account_id = await auth_service.resolve_securities_account_id(user, req.account_id)
    return await hermes_console_service.confirm_draft(
        req.session_id,
        req.draft_id,
        login_user_id=user.get("id"),
        account_id=account_id,
    )


@router.post("/hermes/cancel")
async def cancel_hermes_draft(req: HermesConfirmRequest):
    return await hermes_console_service.cancel_draft(req.session_id, req.draft_id)


@router.post("/hermes/step/confirm")
async def confirm_hermes_plan_step(req: HermesStepRequest, user: dict = Depends(auth_service.require_login_user)):
    account_id = await auth_service.resolve_securities_account_id(user, req.account_id)
    fn = hermes_console_service.confirm_plan_step
    if "login_user_id" not in inspect.signature(fn).parameters:
        return await fn(req.session_id, req.draft_id, req.step_id)
    return await fn(
        req.session_id,
        req.draft_id,
        req.step_id,
        login_user_id=user.get("id"),
        account_id=account_id,
    )


@router.post("/hermes/step/skip")
async def skip_hermes_plan_step(req: HermesStepRequest):
    return await hermes_console_service.skip_plan_step(req.session_id, req.draft_id, req.step_id)


@router.get("/hermes/sessions")
async def list_hermes_sessions(limit: int = Query(default=50, ge=1, le=200)):
    return await hermes_console_service.list_sessions(limit=limit)


@router.get("/hermes/tasks")
async def list_hermes_tasks(
    limit: int = Query(default=30, ge=1, le=100),
    status: str | None = Query(default=None),
):
    return await hermes_console_service.list_tasks(limit=limit, status=status)


@router.get("/hermes/tool-policy")
async def get_hermes_tool_policy():
    policy = hermes_tool_registry.tool_policy()
    return {
        "policy": policy,
        "tools": [
            {
                "tool": spec["tool"],
                "description": spec["description"],
                "mode": policy.get(spec["tool"], "disabled"),
            }
            for spec in hermes_tool_registry.tool_specs()
        ],
    }


@router.post("/hermes/tool-policy")
async def update_hermes_tool_policy(req: HermesToolPolicyRequest):
    return {"status": "ok", "policy": hermes_tool_registry.update_tool_policy(req.policy)}


@router.get("/hermes/session/{session_id}")
async def list_hermes_session(session_id: str, limit: int = Query(default=30, ge=1, le=100)):
    return await hermes_console_service.list_session_events(session_id, limit=limit)


@router.get("/hermes/session/{session_id}/tool-runs")
async def list_hermes_tool_runs(session_id: str, limit: int = Query(default=30, ge=1, le=100)):
    return await hermes_console_service.list_tool_runs(session_id, limit=limit)


@router.post("/hermes/session/{session_id}/undo-last")
async def undo_last_hermes_tool_run(session_id: str):
    return await hermes_console_service.undo_last_tool_run(session_id)
