"""Local login-user and securities-account ownership helpers."""

from __future__ import annotations

import hashlib
import asyncio
import re
import secrets
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, Request

from models.database import get_db, ensure_identity_tables


SESSION_COOKIE = "stock_workbench_session"
DEFAULT_LOGIN_USER_ID = "admin"
DEFAULT_SECURITIES_ACCOUNT_ID = "default"
DEFAULT_LOGIN_USERNAME = "admin"
DEFAULT_LOGIN_PASSWORD = "123456"
_identity_lock: asyncio.Lock | None = None
_identity_lock_loop: asyncio.AbstractEventLoop | None = None

IDENTITY_READY_COLUMNS = {
    "accounts": {"id", "name", "broker"},
    "login_users": {
        "id",
        "username",
        "password_hash",
        "status",
        "default_securities_account_id",
        "must_change_credentials",
    },
    "securities_accounts": {"id", "login_user_id", "name", "broker", "status", "is_default"},
}

# Compatibility contract:
# physical account_id columns are securities account ids. New code should use
# variable names like securities_account_id or aid at API and service boundaries,
# while persisted columns stay stable for legacy scripts and migrations.


def _identity_bootstrap_lock() -> asyncio.Lock:
    global _identity_lock, _identity_lock_loop
    loop = asyncio.get_running_loop()
    if _identity_lock is None or _identity_lock_loop is not loop:
        _identity_lock = asyncio.Lock()
        _identity_lock_loop = loop
    return _identity_lock


async def _identity_schema_ready(db) -> bool:
    tables = {
        row[0]
        for row in await db.execute_fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, required_columns in IDENTITY_READY_COLUMNS.items():
        if table not in tables:
            return False
        columns = {row[1] for row in await db.execute_fetchall(f"PRAGMA table_info({table})")}
        if not required_columns.issubset(columns):
            return False
    return True


async def ensure_identity_ready(db) -> None:
    if await _identity_schema_ready(db):
        return
    async with _identity_bootstrap_lock():
        if not await _identity_schema_ready(db):
            await ensure_identity_tables(db)


def hash_password(password: str) -> str:
    return "sha256$" + hashlib.sha256(str(password or "").encode("utf-8")).hexdigest()


def verify_password(password: str, password_hash: str | None) -> bool:
    expected = password_hash or ""
    if not expected:
        return str(password or "") == ""
    if expected.startswith("sha256$"):
        return secrets.compare_digest(hash_password(password), expected)
    return secrets.compare_digest(str(password or ""), expected)


def public_user(row: dict[str, Any] | None, *, authenticated: bool = False) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "id": row.get("id"),
        "username": row.get("username"),
        "display_name": row.get("display_name") or row.get("username"),
        "role": row.get("role") or "owner",
        "status": row.get("status") or "active",
        "default_securities_account_id": row.get("default_securities_account_id") or DEFAULT_SECURITIES_ACCOUNT_ID,
        "must_change_credentials": bool(row.get("must_change_credentials")),
        "authenticated": authenticated,
    }


async def _fetch_login_user(db, login_user_id: str) -> dict[str, Any] | None:
    row = await (
        await db.execute(
            "SELECT * FROM login_users WHERE id = ? AND status = 'active'",
            (login_user_id,),
        )
    ).fetchone()
    return dict(row) if row else None


async def _fetch_session_user(db, session_id: str | None) -> dict[str, Any] | None:
    if not session_id:
        return None
    row = await (
        await db.execute(
            """
            SELECT u.*
            FROM login_sessions s
            JOIN login_users u ON u.id = s.login_user_id
            WHERE s.session_id = ?
              AND u.status = 'active'
              AND (s.expires_at IS NULL OR datetime(s.expires_at) > datetime('now'))
            """,
            (session_id,),
        )
    ).fetchone()
    if not row:
        return None
    await db.execute(
        "UPDATE login_sessions SET last_seen_at = datetime('now') WHERE session_id = ?",
        (session_id,),
    )
    await db.commit()
    return dict(row)


async def current_login_user(request: Request | None = None) -> dict[str, Any]:
    db = await get_db()
    try:
        await ensure_identity_ready(db)
        session_id = request.cookies.get(SESSION_COOKIE) if request else None
        authenticated = bool(session_id)
        user = await _fetch_session_user(db, session_id)
        if user:
            return public_user(user, authenticated=True)
        if authenticated:
            raise HTTPException(status_code=401, detail="登录会话已失效")
        fallback = await _fetch_login_user(db, DEFAULT_LOGIN_USER_ID)
        return public_user(fallback, authenticated=False)
    finally:
        await db.close()


async def require_login_user(request: Request) -> dict[str, Any]:
    return await current_login_user(request)


async def login(username: str, password: str) -> dict[str, Any]:
    db = await get_db()
    try:
        await ensure_identity_ready(db)
        row = await (
            await db.execute(
                "SELECT * FROM login_users WHERE username = ? AND status = 'active'",
                (str(username or "").strip(),),
            )
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        session_id = secrets.token_urlsafe(32)
        expires_at = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        await db.execute(
            """
            INSERT INTO login_sessions (session_id, login_user_id, expires_at)
            VALUES (?, ?, ?)
            """,
            (session_id, row["id"], expires_at),
        )
        await db.commit()
        return {"session_id": session_id, "user": public_user(dict(row), authenticated=True)}
    finally:
        await db.close()


async def logout(session_id: str | None) -> dict[str, Any]:
    if not session_id:
        return {"success": True}
    db = await get_db()
    try:
        await db.execute("DELETE FROM login_sessions WHERE session_id = ?", (session_id,))
        await db.commit()
        return {"success": True}
    finally:
        await db.close()


async def list_login_users() -> dict[str, Any]:
    db = await get_db()
    try:
        await ensure_identity_ready(db)
        rows = await db.execute_fetchall(
            """
            SELECT id, username, display_name, role, status, default_securities_account_id,
                   must_change_credentials, created_at, updated_at
            FROM login_users
            ORDER BY CASE WHEN id = ? THEN 0 ELSE 1 END, created_at ASC, username ASC
            """,
            (DEFAULT_LOGIN_USER_ID,),
        )
        return {"users": [public_user(dict(row), authenticated=False) for row in rows]}
    finally:
        await db.close()


def _login_user_id_from_username(username: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(username or "").strip().lower()).strip("-")
    return base[:36] or f"user-{uuid4().hex[:8]}"


async def create_login_user(username: str, password: str = "", display_name: str | None = None) -> dict[str, Any]:
    clean_username = str(username or "").strip()
    if not clean_username:
        raise HTTPException(status_code=400, detail="用户名不能为空")
    user_id = _login_user_id_from_username(clean_username)
    default_account_id = f"{user_id}-default"
    db = await get_db()
    try:
        await ensure_identity_ready(db)
        existing = await (
            await db.execute("SELECT 1 FROM login_users WHERE username = ?", (clean_username,))
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="用户名已存在")
        while await (await db.execute("SELECT 1 FROM login_users WHERE id = ?", (user_id,))).fetchone():
            user_id = f"{_login_user_id_from_username(clean_username)[:24]}-{uuid4().hex[:6]}"
            default_account_id = f"{user_id}-default"
        await db.execute(
            """
            INSERT INTO login_users (id, username, password_hash, display_name, default_securities_account_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, clean_username, hash_password(password), display_name or clean_username, default_account_id),
        )
        await db.execute(
            """
            INSERT INTO securities_accounts (id, login_user_id, name, broker, is_default, display_order)
            VALUES (?, ?, ?, '', 1, 0)
            """,
            (default_account_id, user_id, f"{display_name or clean_username} 默认账户"),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM login_users WHERE id = ?", (user_id,))).fetchone()
        account = await (await db.execute("SELECT * FROM securities_accounts WHERE id = ?", (default_account_id,))).fetchone()
        return {
            "user": public_user(dict(row), authenticated=False),
            "default_securities_account": dict(account),
        }
    finally:
        await db.close()


async def update_login_user(login_user_id: str, values: dict[str, Any]) -> dict[str, Any]:
    clean_username = str(values.get("username") or "").strip() if values.get("username") is not None else None
    allowed = {
        "username": clean_username,
        "display_name": values.get("display_name"),
        "role": values.get("role"),
        "default_securities_account_id": values.get("default_securities_account_id"),
    }
    updates = {key: value for key, value in allowed.items() if value is not None}
    if values.get("password") is not None:
        updates["password_hash"] = hash_password(values.get("password") or "")
    if not updates:
        raise HTTPException(status_code=400, detail="没有要更新的登录账户字段")
    db = await get_db()
    try:
        await ensure_identity_ready(db)
        current = await (await db.execute("SELECT * FROM login_users WHERE id = ?", (login_user_id,))).fetchone()
        if not current:
            raise HTTPException(status_code=404, detail="登录账户不存在")
        if "username" in updates:
            if not updates["username"]:
                raise HTTPException(status_code=400, detail="用户名不能为空")
            existing = await (
                await db.execute(
                    "SELECT 1 FROM login_users WHERE username = ? AND id != ?",
                    (updates["username"], login_user_id),
                )
            ).fetchone()
            if existing:
                raise HTTPException(status_code=409, detail="用户名已存在")
        if updates.get("default_securities_account_id"):
            account = await (
                await db.execute(
                    """
                    SELECT 1 FROM securities_accounts
                    WHERE id = ? AND login_user_id = ? AND status = 'active'
                    """,
                    (updates["default_securities_account_id"], login_user_id),
                )
            ).fetchone()
            if not account:
                raise HTTPException(status_code=400, detail="默认证券账户不属于该登录账户")
        if current["must_change_credentials"]:
            next_username = updates.get("username") or current["username"]
            has_new_password = values.get("password") is not None and str(values.get("password") or "") != DEFAULT_LOGIN_PASSWORD
            if next_username != DEFAULT_LOGIN_USERNAME and has_new_password:
                updates["must_change_credentials"] = 0
        assignments = ", ".join(f"{key}=?" for key in updates)
        params = [*updates.values(), login_user_id]
        cursor = await db.execute(
            f"UPDATE login_users SET {assignments}, updated_at = datetime('now') WHERE id = ?",
            params,
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="登录账户不存在")
        row = await (await db.execute("SELECT * FROM login_users WHERE id = ?", (login_user_id,))).fetchone()
        return {"user": public_user(dict(row), authenticated=False)}
    finally:
        await db.close()


async def update_current_profile(login_user_id: str, values: dict[str, Any]) -> dict[str, Any]:
    db = await get_db()
    try:
        await ensure_identity_ready(db)
        row = await (await db.execute("SELECT * FROM login_users WHERE id = ?", (login_user_id,))).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="登录账户不存在")
        if row["must_change_credentials"]:
            username = str(values.get("username") or "").strip()
            password = str(values.get("password") or "")
            if not username or username == DEFAULT_LOGIN_USERNAME:
                raise HTTPException(status_code=400, detail="首次登录必须修改用户名")
            if not password or password == DEFAULT_LOGIN_PASSWORD:
                raise HTTPException(status_code=400, detail="首次登录必须修改默认密码")
    finally:
        await db.close()
    return await update_login_user(login_user_id, values)


async def archive_login_user(login_user_id: str) -> dict[str, Any]:
    if login_user_id == DEFAULT_LOGIN_USER_ID:
        raise HTTPException(status_code=400, detail="默认登录账户不能停用")
    db = await get_db()
    try:
        await ensure_identity_ready(db)
        cursor = await db.execute(
            "UPDATE login_users SET status='archived', updated_at=datetime('now') WHERE id = ?",
            (login_user_id,),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="登录账户不存在")
        await db.execute(
            "UPDATE securities_accounts SET status='archived', updated_at=datetime('now') WHERE login_user_id = ?",
            (login_user_id,),
        )
        await db.execute("DELETE FROM login_sessions WHERE login_user_id = ?", (login_user_id,))
        await db.commit()
        return {"success": True, "id": login_user_id}
    finally:
        await db.close()


async def securities_account_for_user(login_user_id: str, account_id: str | None) -> dict[str, Any]:
    aid = account_id or DEFAULT_SECURITIES_ACCOUNT_ID
    db = await get_db()
    try:
        await ensure_identity_ready(db)
        row = await (
            await db.execute(
                """
                SELECT *
                FROM securities_accounts
                WHERE id = ? AND login_user_id = ? AND status = 'active'
                """,
                (aid, login_user_id or DEFAULT_LOGIN_USER_ID),
            )
        ).fetchone()
        if not row:
            raise HTTPException(status_code=403, detail="证券账户不属于当前登录账户")
        return dict(row)
    finally:
        await db.close()


async def resolve_securities_account_id(user: dict[str, Any], account_id: str | None = None) -> str:
    login_user_id = user.get("id") or DEFAULT_LOGIN_USER_ID
    requested = account_id or user.get("default_securities_account_id") or DEFAULT_SECURITIES_ACCOUNT_ID
    try:
        account = await securities_account_for_user(login_user_id, requested)
    except HTTPException:
        if account_id:
            raise
        db = await get_db()
        try:
            row = await (
                await db.execute(
                    """
                    SELECT *
                    FROM securities_accounts
                    WHERE login_user_id = ? AND status = 'active'
                    ORDER BY is_default DESC, display_order ASC, created_at ASC
                    LIMIT 1
                    """,
                    (login_user_id,),
                )
            ).fetchone()
            if not row:
                raise
            account = dict(row)
        finally:
            await db.close()
    return account["id"]
