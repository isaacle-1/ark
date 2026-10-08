"""Local accounts: argon2 password hashing, session cookies, rate limiting.

Security posture (LAN-first, per spec):
- Optional auth: ``auth.mode = "open"`` (read APIs are anonymous; admin
  endpoints always require an admin session) or ``"required"`` (every /api
  call needs a session).
- Session = opaque random token in an HttpOnly cookie, row in ``auth_sessions``.
- Login attempts rate-limited per (IP, username): 10 failures / 5 min -> 429.
- First boot creates the admin account; the generated password lands in
  ``data/config/initial-credentials.txt`` (chmod 600), never in the repo.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time
from collections import deque
from datetime import timedelta
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ark.config import ArkConfig
from ark.models import User, UserSession, utcnow

logger = logging.getLogger("ark.auth")

SESSION_COOKIE = "ark_session"
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = get_user_by_username(db, username)
    if user is None or user.disabled:
        # Spend the same hashing work on unknown users so timing does not
        # trivially distinguish "no such account" from "wrong password".
        _hasher.hash(password)
        return None
    if not verify_password(user.password_hash, password):
        return None
    return user


def create_session(db: Session, user: User, ttl_hours: int, ip: str | None = None) -> UserSession:
    token = secrets.token_urlsafe(32)
    row = UserSession(
        token=token,
        user_id=user.id,
        expires_at=utcnow() + timedelta(hours=ttl_hours),
        ip=ip,
    )
    db.add(row)
    user.last_login_at = utcnow()
    db.commit()
    db.refresh(row)
    return row


def get_user_for_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    row = db.get(UserSession, token)
    if row is None:
        return None
    if row.expires_at < utcnow():
        db.delete(row)
        db.commit()
        return None
    user = db.get(User, row.user_id)
    if user is None or user.disabled:
        return None
    return user


def delete_session(db: Session, token: str | None) -> None:
    if not token:
        return
    row = db.get(UserSession, token)
    if row is not None:
        db.delete(row)
        db.commit()


class LoginRateLimiter:
    """Sliding-window limiter keyed by (ip, username)."""

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._mu = threading.Lock()

    def retry_after(self, ip: str, username: str) -> int:
        """Seconds until the next attempt is allowed (0 = allowed now)."""
        with self._mu:
            hits = self._hits.get((ip, username))
            if not hits:
                return 0
            cutoff = time.monotonic() - self.window
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) < self.max_attempts:
                return 0
            return max(1, int(hits[0] + self.window - time.monotonic()))

    def record_failure(self, ip: str, username: str) -> None:
        with self._mu:
            hits = self._hits.setdefault((ip, username), deque())
            hits.append(time.monotonic())

    def reset(self, ip: str, username: str) -> None:
        with self._mu:
            self._hits.pop((ip, username), None)


_limiter: LoginRateLimiter | None = None


def get_rate_limiter(config: ArkConfig) -> LoginRateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = LoginRateLimiter(
            config.auth.login_max_attempts, config.auth.login_window_seconds
        )
    return _limiter


def reset_rate_limiter() -> None:
    """Test hook: forget all rate-limit state."""
    global _limiter
    _limiter = None


def bootstrap_admin(db: Session, config: ArkConfig, paths: object) -> User | None:
    """Create the initial admin account if no users exist yet.

    Returns the created user (or None if users already exist). The generated
    password is written to data/config/initial-credentials.txt (0600) — the
    password itself is never logged.
    """
    from ark.paths import Paths

    assert isinstance(paths, Paths)
    existing = db.scalar(select(User).limit(1))
    if existing is not None:
        return None

    password = os.environ.get("ARK_ADMIN_PASSWORD") or secrets.token_urlsafe(12)
    user = User(username="admin", password_hash=hash_password(password), role="admin")
    db.add(user)
    db.commit()
    db.refresh(user)

    creds_file: Path = paths.config_dir / "initial-credentials.txt"
    creds_file.write_text(
        "ARK initial admin credentials (rotate after first login, then delete this file):\n"
        f"  username: admin\n"
        f"  password: {password}\n"
        f"  login:    http://<server>:{config.server.port}/login\n",
        encoding="utf-8",
    )
    creds_file.chmod(0o600)
    logger.warning(
        "created initial admin account 'admin'",
        extra={"credentials_file": str(creds_file), "auth_mode": config.auth.mode},
    )
    return user


# --- FastAPI dependencies -------------------------------------------------


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def current_session_token(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE)


def _unauthorized(request: Request, msg: str) -> HTTPException:
    return HTTPException(status_code=401, detail=msg)


def require_admin(request: Request) -> User:
    """Allow only a logged-in admin. 401 = no session, 403 = not admin."""
    with request.app.state.db.session() as db:
        user = get_user_for_token(db, current_session_token(request))
    if user is None:
        raise _unauthorized(request, "Admin login required")
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def require_read(request: Request) -> User | None:
    """Access control for read APIs depending on auth.mode.

    open mode: anonymous allowed (returns None). required mode: any logged-in
    user, else 401.
    """
    config: ArkConfig = request.app.state.config
    with request.app.state.db.session() as db:
        user = get_user_for_token(db, current_session_token(request))
    if config.auth.mode == "required" and user is None:
        raise _unauthorized(request, "Login required")
    return user
