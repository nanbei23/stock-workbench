"""Shared API helpers for login-user and securities-account scope."""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, Query

from services import auth_service


async def current_user(user: dict = Depends(auth_service.require_login_user)) -> dict:
    return user


async def owned_account_id(
    account_id: Optional[str] = Query(None),
    user: dict = Depends(current_user),
) -> str:
    return await auth_service.resolve_securities_account_id(user, account_id)


async def resolve_owned_account_id(user: dict, account_id: str | None = None) -> str:
    return await auth_service.resolve_securities_account_id(user, account_id)
