"""Logs API: history filtering, files, download, runtime level, client-log."""

from __future__ import annotations

import logging
import time

from ark.logging_setup import set_root_level
from ark.models import Setting


def _seed(admin_client) -> None:
    logging.getLogger("ark.seedlog").info("seed line", extra={"phase": "test"})
    logging.getLogger("ark.seedlog").error("boom line")


def test_history_filters(admin_client, client) -> None:
    _seed(admin_client)
    # Give the file handler a beat to flush.
    time.sleep(0.2)
    items = client.get("/api/admin/logs/history", params={"contains": "boom"}).json()["items"]
    assert items and all("boom" in it["msg"] for it in items)

    items = client.get("/api/admin/logs/history", params={"module": "seedlog"}).json()["items"]
    assert items and all(it["logger"] == "ark.seedlog" for it in items)

    items = client.get(
        "/api/admin/logs/history", params={"level": "ERROR", "contains": "boom"}
    ).json()["items"]
    assert items and all(it["level"] == "ERROR" for it in items)


def test_history_rejects_bad_file(admin_client) -> None:
    resp = admin_client.get("/api/admin/logs/history", params={"file": "../../etc/passwd"})
    assert resp.status_code in (400, 404)


def test_files_lists_module_log(admin_client, client) -> None:
    logging.getLogger("ark.library").info("lib line")
    time.sleep(0.2)
    files = client.get("/api/admin/logs/files").json()["files"]
    names = {f["name"] for f in files}
    assert "ark.log" in names
    assert "library.log" in names


def test_download_returns_file(admin_client, client) -> None:
    logging.getLogger("ark.dl").info("download me")
    time.sleep(0.2)
    resp = admin_client.get("/api/admin/logs/download", params={"file": "ark.log"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "download me" in resp.text


def test_level_get_post(admin_client, client) -> None:
    level = client.get("/api/admin/logs/level").json()
    assert level["level"] in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    assert level["source"] in ("env", "runtime", "config")

    resp = admin_client.post("/api/admin/logs/level", json={"level": "ERROR"})
    assert resp.status_code == 200
    assert resp.json()["level"] == "ERROR"
    assert resp.json()["source"] in ("env", "runtime")

    with client.app.state.db.session() as db:
        row = db.get(Setting, "log_level")
        if row:
            assert row.value == "ERROR"
    set_root_level("DEBUG")


def test_client_log_endpoint(client) -> None:
    resp = client.post(
        "/api/client-log",
        json={"message": "TypeError: x is undefined", "url": "/logs", "stack": "at fn (a.js:1)"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_level_rejects_garbage(admin_client) -> None:
    resp = admin_client.post("/api/admin/logs/level", json={"level": "LOUD"})
    assert resp.status_code == 422
