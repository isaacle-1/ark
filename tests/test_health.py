"""Health endpoints, security headers, request-id plumbing, SPA fallback."""

from __future__ import annotations


def test_healthz_is_liveness(client) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.headers["X-Request-ID"]


def test_security_headers_present(client) -> None:
    resp = client.get("/healthz")
    assert "Content-Security-Policy" in resp.headers
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "X-Request-ID" in resp.headers


def test_api_health_shape(client) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"].startswith("0.")
    assert body["auth_mode"] == "open"
    assert "modules" in body
    assert any(m["name"] == "logging" for m in body["modules"])
    assert body["sidecars"] == []  # phase 0
    assert "system" in body
    assert body["system"]["cpu_percent"] is not None
    assert "disk" in body


def test_request_id_propagates(client) -> None:
    resp = client.get("/healthz", headers={"X-Request-ID": "abc-123-def-456"})
    assert resp.headers["X-Request-ID"] == "abc-123-def-456"

    events = client.get("/api/admin/logs/history", params={"request_id": "abc-123-def-456"})
    # 401 here: admin required; but request_id is echoed on errors too.
    assert "request_id" in events.json()


def test_unknown_api_404_with_request_id(client) -> None:
    resp = client.get("/api/nope")
    assert resp.status_code == 404
    body = resp.json()
    assert "detail" in body
    assert "request_id" in body
    assert resp.headers["X-Request-ID"] == body["request_id"]


def test_spa_fallback_page(client) -> None:
    resp = client.get("/some/client/route")
    assert resp.status_code == 200
    assert "ARK" in resp.text  # branded placeholder until dist is built


def test_fake_dist_served(client) -> None:
    dist = client.app.state.paths.frontend_dist
    assets = dist / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<html><body>ARK fake index</body></html>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('x')", encoding="utf-8")

    index = client.get("/")
    assert index.status_code == 200
    assert "fake index" in index.text
    assert index.headers.get("Cache-Control") == "no-cache"

    ass = client.get("/assets/app.js")
    assert ass.status_code == 200
    assert "immutable" in ass.headers["Cache-Control"]

    # Path traversal must be rejected.
    assert client.get("/..%2f..%2fetc%2fpasswd").status_code == 404
    # API routes are never swallowed by the SPA catch-all.
    assert client.get("/api/nope").status_code == 404
