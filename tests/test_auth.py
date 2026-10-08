"""Auth: bootstrap admin, login/logout, /me, rate limiting, admin guard."""

from __future__ import annotations

from ark.auth import reset_rate_limiter


def test_me_anonymous_in_open_mode(client) -> None:
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["user"] is None


def test_credentials_file_written_with_0600(paths, client) -> None:
    creds = paths.config_dir / "initial-credentials.txt"
    assert creds.is_file()
    assert (creds.stat().st_mode & 0o777) == 0o600


def test_login_logout_flow(admin_client, client) -> None:
    resp = client.get("/api/auth/me")
    assert resp.json()["user"]["role"] == "admin"

    out = client.post("/api/auth/logout")
    assert out.status_code == 200
    resp = client.get("/api/auth/me")
    assert resp.json()["user"] is None


def test_wrong_password_401(admin_creds, client) -> None:
    user, _ = admin_creds
    resp = client.post("/api/auth/login", json={"username": user, "password": "guessed-wrong"})
    assert resp.status_code == 401
    assert "request_id" in resp.json()


def test_rate_limit_blocks_probing(client, config, monkeypatch) -> None:
    monkeypatch.setattr(config.auth, "login_max_attempts", 3)
    monkeypatch.setattr(config.auth, "login_window_seconds", 600)
    reset_rate_limiter()
    for _ in range(3):
        r = client.post("/api/auth/login", json={"username": "admin", "password": "x"})
        assert r.status_code == 401
    blocked = client.post("/api/auth/login", json={"username": "admin", "password": "x"})
    assert blocked.status_code == 429
    assert blocked.json()["detail"] != ""


def test_admin_endpoints_require_login(client) -> None:
    for path, method in [
        ("/api/admin/logs/history", "get"),
        ("/api/admin/logs/files", "get"),
        ("/api/admin/logs/level", "get"),
        ("/api/admin/logs/download?file=ark.log", "get"),
        ("/api/admin/jobs", "get"),
    ]:
        resp = getattr(client, method)(path)
        assert resp.status_code == 401, (path, resp.status_code)


def test_admin_endpoints_reject_non_admin(client, config, monkeypatch) -> None:
    # Required mode forces login for reads; verify a plain (non-admin) session is 403.
    with client.app.state.db.session() as db:
        from ark.auth import create_session
        from ark.models import User

        user = User(username="viewer", password_hash="x", role="viewer")
        db.add(user)
        db.commit()
        db.refresh(user)
        session = create_session(db, user, ttl_hours=1)
        token = session.token
    resp = client.get("/api/admin/logs/history", cookies={"ark_session": token})
    assert resp.status_code == 403
