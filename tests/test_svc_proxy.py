"""/svc reverse proxy against a real spawned sidecar (monkeypatched spec)."""

from __future__ import annotations

import sys

import ark.server as server_module
from ark.supervisor import SidecarSpec


def test_svc_proxies_to_registered_sidecar(paths, config, monkeypatch) -> None:
    # Serve a tiny dir via python's builtin http.server as the sidecar.
    content_dir = paths.tmp_dir / "svc-root"
    content_dir.mkdir(parents=True, exist_ok=True)
    (content_dir / "hello.txt").write_text("hi from sidecar\n", encoding="utf-8")

    spec = SidecarSpec(
        name="mini",
        command=(sys.executable, "-m", "http.server", "18765", "--directory", str(content_dir)),
        port=18765,
    )
    monkeypatch.setattr(server_module, "get_sidecar_specs", lambda _paths: [spec])

    from fastapi.testclient import TestClient

    app = server_module.create_app(config, paths)
    with TestClient(app) as client:
        # Proxy a real request, retrying until the sidecar is up.
        for _ in range(100):
            resp = client.get("/svc/mini/hello.txt")
            if resp.status_code == 200:
                break
            import time

            time.sleep(0.1)
        assert resp.status_code == 200, resp.text
        assert resp.text.strip() == "hi from sidecar"

        missing = client.get("/svc/mini/404.txt")
        assert missing.status_code == 404

        unknown = client.get("/svc/ghost/anything")
        assert unknown.status_code == 404
        assert "ghost" in unknown.json()["detail"]


def test_svc_503_when_sidecar_exited(paths, config, monkeypatch) -> None:
    spec = SidecarSpec(
        name="dead",
        command=(sys.executable, "-c", "raise SystemExit(0)"),
        port=18766,
        auto_restart=False,
    )
    monkeypatch.setattr(server_module, "get_sidecar_specs", lambda _paths: [spec])

    from fastapi.testclient import TestClient

    app = server_module.create_app(config, paths)
    with TestClient(app) as client:
        import time

        time.sleep(0.5)  # let it exit
        resp = client.get("/svc/dead/x")
        assert resp.status_code == 503
        assert "not running" in str(resp.json()["detail"])
