"""Auth routes: login, logout, current user."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ark.auth import (
    SESSION_COOKIE,
    authenticate,
    client_ip,
    create_session,
    current_session_token,
    delete_session,
    get_rate_limiter,
    get_user_for_token,
)
from ark.models import User

logger = logging.getLogger("ark.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


def _user_dict(user: User | None) -> dict[str, object] | None:
    if user is None:
        return None
    return {"id": user.id, "username": user.username, "role": user.role}


@router.post("/login")
async def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    config = request.app.state.config
    ip = client_ip(request)
    limiter = get_rate_limiter(config)
    retry = limiter.retry_after(ip, payload.username)
    if retry > 0:
        logger.warning(
            "login blocked by rate limit",
            extra={"username": payload.username, "ip": ip, "retry_after": retry},
        )
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed attempts. Try again in {retry}s.",
            headers={"Retry-After": str(retry)},
        )

    with request.app.state.db.session() as db:
        user = authenticate(db, payload.username, payload.password)
        if user is None:
            limiter.record_failure(ip, payload.username)
            remaining = limiter.retry_after(ip, payload.username)
            logger.warning(
                "login failed",
                extra={"username": payload.username, "ip": ip, "blocked_for": remaining},
            )
            raise HTTPException(status_code=401, detail="Invalid username or password")
        limiter.reset(ip, payload.username)
        session_row = create_session(db, user, config.auth.session_ttl_hours, ip=ip)

    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_row.token,
        max_age=config.auth.session_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=False,  # LAN HTTP by default; flip when HTTPS is configured
        path="/",
    )
    logger.info("login ok", extra={"username": user.username, "ip": ip})
    return {"user": _user_dict(user)}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    token = current_session_token(request)
    if token:
        with request.app.state.db.session() as db:
            delete_session(db, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    logger.info("logout", extra={"ip": client_ip(request)})
    return {"ok": True}


@router.get("/me")
async def me(request: Request) -> dict:
    """Who am I? Returns {user: null} (never 401) so the UI can render."""
    with request.app.state.db.session() as db:
        user = get_user_for_token(db, current_session_token(request))
    return {"user": _user_dict(user), "mode": request.app.state.config.auth.mode}
