"""Phase 1 library: catalog, download engine (resume/hash/mirrors), manuals."""

from __future__ import annotations

import asyncio
import hashlib
import http.server
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from ark.db import Database, run_migrations
from ark.jobs import JobContext, enqueue
from ark.library import LibraryError, download_zim
from ark.models import Job, LibraryItem

FIXTURE = Path(__file__).parent / "fixtures" / "ark-fixture.zim"
FIXTURE_BYTES = FIXTURE.read_bytes()
FIXTURE_SHA = hashlib.sha256(FIXTURE_BYTES).hexdigest()
FIXTURE_SIZE = len(FIXTURE_BYTES)


# -- local Range-capable file server ----------------------------------------


@dataclass
class ServerCfg:
    data: bytes
    support_range: bool = True
    piece_sleep: float = 0.0
    hits: int = 0
    ranges: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        pass

    def do_HEAD(self) -> None:
        self._serve(head=True)

    def do_GET(self) -> None:
        self._serve(head=False)

    def _serve(self, *, head: bool) -> None:
        cfg: ServerCfg = self.server.cfg  # type: ignore[attr-defined]
        rng = self.headers.get("Range")
        with cfg.lock:
            cfg.hits += 1
            if rng:
                cfg.ranges.append(rng)
        start = 0
        if rng and cfg.support_range:
            match = re.match(r"bytes=(\d+)-", rng)
            start = int(match.group(1)) if match else 0
            if start >= len(cfg.data):
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(cfg.data)}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = cfg.data[start:]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(cfg.data) - 1}/{len(cfg.data)}")
        else:
            body = cfg.data
            self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if head:
            return
        if cfg.piece_sleep > 0:
            for i in range(0, len(body), 4096):
                self.wfile.write(body[i : i + 4096])
                self.wfile.flush()
                time.sleep(cfg.piece_sleep)
        else:
            self.wfile.write(body)


@pytest.fixture()
def file_server():
    """Start throwaway local HTTP servers; returns (url, cfg) factories."""
    started: list[http.server.ThreadingHTTPServer] = []

    def start(
        data: bytes = FIXTURE_BYTES, *, support_range: bool = True, piece_sleep: float = 0.0
    ) -> tuple[str, ServerCfg]:
        cfg = ServerCfg(data=data, support_range=support_range, piece_sleep=piece_sleep)
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
        srv.cfg = cfg  # type: ignore[attr-defined]
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        started.append(srv)
        host, port = srv.server_address[:2]
        return f"http://{host}:{port}/ark-fixture.zim", cfg

    yield start
    for srv in started:
        srv.shutdown()
        srv.server_close()


def _payload(
    urls: list[str],
    *,
    sha256: str = FIXTURE_SHA,
    size: int = FIXTURE_SIZE,
    filename: str = "ark-fixture.zim",
    item_id: str | None = None,
) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "name": "mini_en_all",
        "flavour": None,
        "title": "Mini",
        "filename": filename,
        "size": size,
        "sha256": sha256,
        "urls": urls,
    }


def _engine(paths, config, payload: dict[str, Any]) -> tuple[JobContext, Database]:
    run_migrations(paths)
    db = Database(paths.db_file)
    job = enqueue(db, "library.download", payload)
    ctx = JobContext(job_id=job.id, job_type="library.download", payload=payload, db=db)
    return ctx, db


def _write_catalog(paths, entries: list[dict[str, Any]]) -> None:
    paths.catalog_file.write_text(
        json.dumps({"schema": 1, "updated": "2026-01-01T00:00:00+00:00", "entries": entries}),
        encoding="utf-8",
    )


def _entry(url: str, **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "name": "mini_en_all",
        "flavour": None,
        "title": "Mini",
        "language": "eng",
        "category": "wikipedia",
        "size": FIXTURE_SIZE,
        "sha256": FIXTURE_SHA,
        "zim_url": url,
        "mirror_urls": [url],
        "license": "CC0-1.0",
        "status": "verified",
        "verify_error": None,
    }
    entry.update(overrides)
    return entry


# -- catalog ----------------------------------------------------------------


def test_catalog_lists_all_verified_entries(client) -> None:
    resp = client.get("/api/library/catalog")
    assert resp.status_code == 200
    body = resp.json()
    entries = body["entries"]
    assert len(entries) >= 15
    by_key = {(e["name"], e.get("flavour")): e for e in entries}
    tiers = {
        flavor: by_key[("wikipedia_en_all", flavor)]["tier"] for flavor in ("mini", "nopic", "maxi")
    }
    assert tiers == {"mini": 1, "nopic": 2, "maxi": 3}
    sample = by_key[("wikipedia_en_all", "mini")]
    assert len(sample["sha256"]) == 64
    assert sample["size"] > 0
    assert sample["status"] == "verified"
    assert sample["downloadable"] is True
    assert sample["installed"] is False
    assert sample["active_job"] is None
    # license warnings stay visible (iFixit was never authoritatively verified)
    ifixit = next(e for e in entries if e["name"] == "ifixit_en_all")
    assert ifixit["license_note"]


def test_items_empty(client) -> None:
    resp = client.get("/api/library/items")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


def test_health_includes_library_module(client) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    lib = next(m for m in resp.json()["modules"] if m["name"] == "library")
    assert lib["healthy"] is True
    assert lib["entries"] >= 15
    assert lib["installed"] == 0
    assert lib["kiwix_binary"] is False  # tmp ARK_HOME has no bin/kiwix-serve


# -- install endpoint -------------------------------------------------------


def test_install_requires_admin(client) -> None:
    resp = client.post("/api/library/install", json={"name": "wikipedia_en_all"})
    assert resp.status_code == 401


def test_install_unknown_entry_404(admin_client) -> None:
    resp = admin_client.post("/api/library/install", json={"name": "does_not_exist"})
    assert resp.status_code == 404
    assert "does_not_exist" in resp.json()["detail"]


def test_install_payload_validation(admin_client) -> None:
    resp = admin_client.post("/api/library/install", json={"name": "../etc/passwd"})
    assert resp.status_code == 422


def test_install_rejects_unverified_entry(admin_client, paths) -> None:
    _write_catalog(
        paths,
        [
            _entry(
                "http://127.0.0.1:1/x.zim", status="unverified", verify_error="connection refused"
            )
        ],
    )
    resp = admin_client.post("/api/library/install", json={"name": "mini_en_all"})
    assert resp.status_code == 409
    assert "not verified" in resp.json()["detail"]


def test_full_install_flow(admin_client, paths, file_server) -> None:
    url, _cfg = file_server()
    _write_catalog(paths, [_entry(url)])
    resp = admin_client.post("/api/library/install", json={"name": "mini_en_all"})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["job_id"] and body["item_id"]

    # Wait for the job worker to finish the download.
    deadline = time.monotonic() + 20
    item: dict[str, Any] = {}
    while time.monotonic() < deadline:
        items = admin_client.get("/api/library/items").json()["items"]
        item = next(i for i in items if i["id"] == body["item_id"])
        if item["status"] in ("installed", "error", "paused"):
            break
        time.sleep(0.05)
    assert item["status"] == "installed", item.get("error")
    assert item["size"] == FIXTURE_SIZE
    assert item["sha256"] == FIXTURE_SHA
    assert item["installed_at"]

    final = paths.library_dir / "mini_en_all.zim"
    assert final.is_file()
    assert hashlib.sha256(final.read_bytes()).hexdigest() == FIXTURE_SHA
    assert not list(paths.tmp_dir.glob("*.part"))

    # Catalog reflects the install; reinstall is refused until deleted.
    catalog = admin_client.get("/api/library/catalog").json()["entries"]
    mini = next(e for e in catalog if e["name"] == "mini_en_all")
    assert mini["installed"] is True
    assert mini["downloadable"] is False
    again = admin_client.post("/api/library/install", json={"name": "mini_en_all"})
    assert again.status_code == 409
    assert "already installed" in again.json()["detail"]

    # Delete removes row + file.
    deleted = admin_client.delete(f"/api/library/items/{body['item_id']}")
    assert deleted.status_code == 200
    assert not final.exists()
    assert admin_client.get("/api/library/items").json()["items"] == []


def test_delete_unknown_item_404(admin_client) -> None:
    resp = admin_client.delete("/api/library/items/nope")
    assert resp.status_code == 404


# -- download engine (direct calls) -----------------------------------------


def test_download_success_direct(paths, config, file_server) -> None:
    url, _cfg = file_server()
    ctx, _db = _engine(paths, config, _payload([url]))
    result = asyncio.run(download_zim(ctx, paths, config))
    assert result["sha256"] == FIXTURE_SHA
    assert result["size"] == FIXTURE_SIZE
    final = paths.library_dir / "ark-fixture.zim"
    assert final.read_bytes() == FIXTURE_BYTES
    assert not (paths.tmp_dir / "ark-fixture.zim.part").exists()


def test_download_resumes_from_partial(paths, config, file_server) -> None:
    url, cfg = file_server()
    part = paths.tmp_dir / "ark-fixture.zim.part"
    prefix = FIXTURE_BYTES[:20_000]
    part.write_bytes(prefix)
    ctx, _db = _engine(paths, config, _payload([url]))
    result = asyncio.run(download_zim(ctx, paths, config))
    assert result["sha256"] == FIXTURE_SHA
    assert cfg.ranges == [f"bytes={len(prefix)}-"]
    final = paths.library_dir / "ark-fixture.zim"
    assert final.read_bytes() == FIXTURE_BYTES


def test_download_mirror_fallback(paths, config, file_server, caplog) -> None:
    url, _cfg = file_server()
    dead = "http://127.0.0.1:1/ark-fixture.zim"  # connection refused
    ctx, _db = _engine(paths, config, _payload([dead, url]))
    with caplog.at_level(logging.WARNING, logger="ark.library"):
        result = asyncio.run(download_zim(ctx, paths, config))
    assert result["sha256"] == FIXTURE_SHA
    assert any("mirror failed" in r.message for r in caplog.records)


def test_download_checksum_mismatch_deletes_partial(paths, config, file_server) -> None:
    url, _cfg = file_server()
    ctx, _db = _engine(paths, config, _payload([url], sha256="0" * 64))
    with pytest.raises(LibraryError, match="checksum mismatch"):
        asyncio.run(download_zim(ctx, paths, config))
    assert not (paths.tmp_dir / "ark-fixture.zim.part").exists()
    assert not (paths.library_dir / "ark-fixture.zim").exists()


def test_download_all_mirrors_fail_keeps_partial(paths, config) -> None:
    part = paths.tmp_dir / "ark-fixture.zim.part"
    prefix = FIXTURE_BYTES[:10_000]
    part.write_bytes(prefix)
    dead = ["http://127.0.0.1:1/a.zim", "http://127.0.0.1:2/b.zim"]
    ctx, _db = _engine(paths, config, _payload(dead))
    with pytest.raises(LibraryError, match="all 2 mirror"):
        asyncio.run(download_zim(ctx, paths, config))
    assert part.read_bytes() == prefix  # resume data survives a failed attempt


def test_download_paused_keeps_partial(paths, config, file_server) -> None:
    url, _cfg = file_server()
    part = paths.tmp_dir / "ark-fixture.zim.part"
    prefix = FIXTURE_BYTES[:20_000]
    part.write_bytes(prefix)
    ctx, _db = _engine(paths, config, _payload([url]))
    with ctx.db.session() as session:
        session.get(Job, ctx.job_id).status = "cancelled"
    result = asyncio.run(download_zim(ctx, paths, config))
    assert result == {"cancelled": True, "bytes_done": len(prefix)}
    assert part.read_bytes() == prefix


def test_download_mid_stream_cancel(paths, config, file_server) -> None:
    config.library.download_chunk_bytes = 4096
    url, _cfg = file_server(piece_sleep=0.15)
    ctx, db = _engine(paths, config, _payload([url]))

    async def scenario() -> dict[str, Any]:
        task = asyncio.create_task(download_zim(ctx, paths, config))
        cancelled = False
        for _ in range(200):  # wait for real bytes to flow, then cancel
            await asyncio.sleep(0.05)
            with db.session() as session:
                job = session.get(Job, ctx.job_id)
                status = job.status if job else "?"
                bytes_done = job.bytes_done if job else 0
            if status == "cancelled":
                break
            if bytes_done > 0 and not cancelled:
                with db.session() as session:
                    session.get(Job, ctx.job_id).status = "cancelled"
                cancelled = True
        return await asyncio.wait_for(task, timeout=15)

    result = asyncio.run(scenario())
    assert result["cancelled"] is True
    part = paths.tmp_dir / "ark-fixture.zim.part"
    assert part.exists() and part.stat().st_size < FIXTURE_SIZE
    assert not (paths.library_dir / "ark-fixture.zim").exists()


def test_download_updates_library_item(paths, config, file_server) -> None:
    url, _cfg = file_server()
    run_migrations(paths)
    db = Database(paths.db_file)
    with db.session() as session:
        item = LibraryItem(
            id="item-1",
            kind="zim",
            name="mini_en_all:ark-fixture.zim",
            title="Mini",
            filename="ark-fixture.zim",
            relpath="library/ark-fixture.zim",
            status="queued",
        )
        session.add(item)
    job = enqueue(db, "library.download", _payload([url], item_id="item-1"))
    ctx = JobContext(
        job_id=job.id, job_type="library.download", payload=_payload([url], item_id="item-1"), db=db
    )
    asyncio.run(download_zim(ctx, paths, config))
    with db.session() as session:
        row = session.get(LibraryItem, "item-1")
        assert row.status == "installed"
        assert row.sha256 == FIXTURE_SHA
        assert row.installed_at is not None


# -- manuals ----------------------------------------------------------------


def test_manual_upload_list_serve_delete(admin_client) -> None:
    content = b"%PDF-1.4 test manual content"
    resp = admin_client.post(
        "/api/library/manuals",
        files={"file": ("My Field Manual.pdf", content, "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    item = resp.json()
    assert item["kind"] == "manual"
    assert item["filename"] == "My_Field_Manual.pdf"
    assert item["size"] == len(content)
    assert item["file_url"]

    items = admin_client.get("/api/library/items").json()["items"]
    assert [i["id"] for i in items] == [item["id"]]

    file_resp = admin_client.get(item["file_url"])
    assert file_resp.status_code == 200
    assert file_resp.content == content
    assert file_resp.headers["content-disposition"].startswith("inline")

    deleted = admin_client.delete(f"/api/library/items/{item['id']}")
    assert deleted.status_code == 200
    assert admin_client.get("/api/library/items").json()["items"] == []


def test_manual_upload_sanitizes_traversal_name(admin_client, paths) -> None:
    resp = admin_client.post(
        "/api/library/manuals",
        files={"file": ("../../evil.pdf", b"payload", "application/pdf")},
    )
    assert resp.status_code == 201
    stored = paths.manuals_dir / resp.json()["filename"]
    assert stored.parent == paths.manuals_dir
    assert stored.is_file()
    assert not (paths.data.parent / "evil.pdf").exists()
    assert not (paths.home / "evil.pdf").exists()


def test_manual_upload_rejects_empty(admin_client) -> None:
    resp = admin_client.post(
        "/api/library/manuals", files={"file": ("empty.pdf", b"", "application/pdf")}
    )
    assert resp.status_code == 422


def test_manual_file_requires_existing_manual(admin_client) -> None:
    resp = admin_client.get("/api/library/manuals/nope/file")
    assert resp.status_code == 404


def test_manuals_requires_admin(client) -> None:
    resp = client.post("/api/library/manuals", files={"file": ("x.pdf", b"x", "application/pdf")})
    assert resp.status_code == 401
    assert client.delete("/api/library/items/anything").status_code == 401
