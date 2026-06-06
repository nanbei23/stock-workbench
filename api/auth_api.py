"""Authentication API for local login users."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services import auth_service


router = APIRouter(tags=["登录账户"])


class LoginRequest(BaseModel):
    username: str
    password: str = ""


class LoginUserCreateRequest(BaseModel):
    username: str
    password: str = ""
    display_name: str | None = None


class LoginUserUpdateRequest(BaseModel):
    username: str | None = None
    display_name: str | None = None
    password: str | None = None
    role: str | None = None
    default_securities_account_id: str | None = None


class ProfileUpdateRequest(BaseModel):
    username: str | None = None
    display_name: str | None = None
    password: str | None = None


@router.get("/auth/session")
async def auth_session(request: Request):
    user = await auth_service.current_login_user(request)
    return {"user": user}


@router.post("/auth/login")
async def auth_login(req: LoginRequest):
    result = await auth_service.login(req.username, req.password)
    resp = JSONResponse({"user": result["user"]})
    resp.set_cookie(
        auth_service.SESSION_COOKIE,
        result["session_id"],
        httponly=True,
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
    )
    return resp


@router.post("/auth/logout")
async def auth_logout(request: Request):
    await auth_service.logout(request.cookies.get(auth_service.SESSION_COOKIE))
    resp = JSONResponse({"success": True})
    resp.delete_cookie(auth_service.SESSION_COOKIE)
    return resp


@router.put("/auth/profile")
async def update_current_profile(req: ProfileUpdateRequest, user: dict = Depends(auth_service.require_login_user)):
    return await auth_service.update_current_profile(user["id"], req.model_dump(exclude_none=True))


@router.get("/auth/users")
async def list_login_users(_user: dict = Depends(auth_service.require_login_user)):
    return await auth_service.list_login_users()


@router.post("/auth/users")
async def create_login_user(req: LoginUserCreateRequest, _user: dict = Depends(auth_service.require_login_user)):
    return await auth_service.create_login_user(req.username, req.password, req.display_name)


@router.put("/auth/users/{login_user_id}")
async def update_login_user(
    login_user_id: str,
    req: LoginUserUpdateRequest,
    _user: dict = Depends(auth_service.require_login_user),
):
    return await auth_service.update_login_user(login_user_id, req.model_dump(exclude_none=True))


@router.delete("/auth/users/{login_user_id}")
async def archive_login_user(login_user_id: str, _user: dict = Depends(auth_service.require_login_user)):
    return await auth_service.archive_login_user(login_user_id)
