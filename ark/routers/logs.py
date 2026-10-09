"""In-app log viewer API: history, SSE live tail, downloads, runtime level.

All admin-only except POST /api/client-log (public: frontend error reports).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from ark.auth import LoginRateLimiter, client_ip, require_admin
from ark.config import ArkConfig
from ark.logging_setup import get_log_bus, set_root_level, tail_log_file
from ark.models import Setting
from ark.paths import Paths

logger = logging.getLogger("ark.http")

router = APIRouter(prefix="/api", tags=["logs"])

_LEVEL_ORDER = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
_LEVEL_RANK = {name: i for i, name in enumerate(_LEVEL_ORDER)}
_SAFE_FILE = re.compile(r"^[A-Za-z0-9._-]+\.log(\.\d+)?$")


class LevelUpdate(BaseModel):
    level: str = Field(pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")


class ClientLogEntry(BaseModel):
    message: str = Field(default="client error", max_length=4000)
    stack: str | None = Field(default=None, max_length=16000)
    url: str | None = Field(default=None, max_length=2000)
    source: str | None = Field(default=None, max_length=200)


def _parse_line(raw: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _matches(
    entry: dict[str, Any],
    *,
    min_level: str | None,
    module: str | None,
    request_id: str | None,
    job_id: str | None,
    contains: str | None,
) -> bool:
    if min_level:
        rank = _LEVEL_RANK.get(str(entry.get("level", "INFO")), 1)
        if rank < _LEVEL_RANK[min_level]:
            return False
    if module:
        logger_name = str(entry.get("logger", ""))
        if not (logger_name == f"ark.{module}" or logger_name.startswith(f"ark.{module}.")):
            return False
    if request_id and entry.get("request_id") != request_id:
        return False
    if job_id and str(entry.get("job_id", "")) != job_id:
        return False
    return not (contains and contains.lower() not in str(entry.get("msg", "")).lower())


def _list_log_files(paths: Paths) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not paths.logs_dir.is_dir():
        return out
    for p in sorted(paths.logs_dir.iterdir()):
        if p.is_file() and (".log" in p.name):
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({"name": p.name, "size_bytes": st.st_size, "mtime": st.st_mtime})
    return out


def _resolve_log_file(paths: Paths, name: str) -> Path:
    """Resolve a log file name inside data/logs (path-traversal safe)."""
    if not _SAFE_FILE.match(name):
        raise HTTPException(status_code=400, detail=f"Invalid log file name: {name!r}")
    base = paths.logs_dir.resolve()
    target = (base / name).resolve()
    if target.parent != base or not target.is_file():
        raise HTTPException(status_code=404, detail=f"Log file not found: {name}")
    return target


@router.get("/admin/logs/history")
async def log_history(
    request: Request,
    lines: int = Query(default=200, ge=1, le=5000),
    file: str = Query(default="ark.log"),
    level: str | None = Query(default=None, pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$"),
    module: str | None = Query(default=None, max_length=64),
    request_id: str | None = Query(default=None, max_length=64),
    job_id: str | None = Query(default=None, max_length=64),
    contains: str | None = Query(default=None, max_length=200),
    _: object = Depends(require_admin),
) -> dict[str, Any]:
    paths: Paths = request.app.state.paths
    _resolve_log_file(paths, file)
    raw_lines = tail_log_file(paths, lines=lines + 500, filename=file)
    entries = [
        obj
        for raw in raw_lines
        if (obj := _parse_line(raw))
        and _matches(
            obj,
            min_level=level,
            module=module,
            request_id=request_id,
            job_id=job_id,
            contains=contains,
        )
    ]
    return {"items": entries[-lines:], "file": file, "total_in_file": len(raw_lines)}


@router.get("/admin/logs/files")
async def log_files(request: Request, _: object = Depends(require_admin)) -> dict[str, Any]:
    paths: Paths = request.app.state.paths
    return {"files": _list_log_files(paths)}


@router.get("/admin/logs/download")
async def log_download(
    request: Request,
    file: str = Query(default="ark.log"),
    _: object = Depends(require_admin),
) -> FileResponse:
    paths: Paths = request.app.state.paths
    target = _resolve_log_file(paths, file)
    logger.info("log file downloaded", extra={"file": file})
    return FileResponse(target, media_type="text/plain", filename=file)


def _level_and_source(config: ArkConfig, request: Request) -> tuple[str, str]:
    env = os.environ.get("LOG_LEVEL", "").strip().upper()
    if env:
        return env, "env"
    with request.app.state.db.session() as db:
        row = db.get(Setting, "log_level")
        if row is not None and row.value.upper() in _LEVEL_RANK:
            return row.value.upper(), "runtime"
    return config.logging.level, "config"


@router.get("/admin/logs/level")
async def get_level(request: Request, _: object = Depends(require_admin)) -> dict[str, Any]:
    config: ArkConfig = request.app.state.config
    level, source = _level_and_source(config, request)
    return {"level": level, "source": source}


@router.post("/admin/logs/level")
async def post_level(
    payload: LevelUpdate, request: Request, _: object = Depends(require_admin)
) -> dict[str, Any]:
    try:
        set_root_level(payload.level)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if os.environ.get("LOG_LEVEL", "").strip():
        source = "env"
        note = "LOG_LEVEL env var overrides runtime changes; unset it to apply this."
    else:
        source = "runtime"
        note = None
        with request.app.state.db.session() as db:
            row = db.get(Setting, "log_level")
            if row is None:
                db.add(Setting(key="log_level", value=payload.level))
            else:
                row.value = payload.level
    logger.info(
        "log level changed",
        extra={"level": payload.level, "source": source, "by": client_ip(request)},
    )
    return {"level": payload.level, "source": source, "note": note}


@router.post("/client-log")
async def client_log(payload: ClientLogEntry, request: Request) -> dict[str, Any]:
    """Frontend JS errors land in the same logs as everything else."""
    ip = client_ip(request)
    if _client_limiter.retry_after(ip, "") > 0:
        raise HTTPException(status_code=429, detail="client log rate limit exceeded")
    _client_limiter.record_failure(ip, "")
    extra = {
        "client_ip": ip,
        "client_url": payload.url,
        "client_source": payload.source,
    }
    if payload.stack:
        extra["client_stack"] = payload.stack[:8000]
    logger.error("client-side error: %s", payload.message[:1000], extra=extra)
    return {"ok": True}


_client_limiter = LoginRateLimiter(max_attempts=60, window_seconds=60)


@router.get("/admin/logs/stream")
async def log_stream(
    request: Request,
    history: int = Query(default=100, ge=0, le=2000),
    file: str = Query(default="ark.log"),
    level: str | None = Query(default=None, pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$"),
    module: str | None = Query(default=None, max_length=64),
    request_id: str | None = Query(default=None, max_length=64),
    job_id: str | None = Query(default=None, max_length=64),
    contains: str | None = Query(default=None, max_length=200),
    _: object = Depends(require_admin),
) -> StreamingResponse:
    """SSE live tail: history first (deduped), then every new matching line."""
    paths: Paths = request.app.state.paths
    _resolve_log_file(paths, file)
    bus = get_log_bus()

    def keep(raw: str) -> bool:
        obj = _parse_line(raw)
        if obj is None:
            return False
        return _matches(
            obj,
            min_level=level,
            module=module,
            request_id=request_id,
            job_id=job_id,
            contains=contains,
        )

    sent: set[str] = set()

    async def generate() -> Any:
        # Subscribe BEFORE reading history so no line is lost in between.
        sub = bus.subscribe()
        try:
            if history:
                for raw in tail_log_file(paths, lines=history, filename=file):
                    if keep(raw):
                        sent.add(raw)
                        yield f"data: {raw}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    raw = await asyncio.to_thread(sub.get, True, 15.0)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                if raw in sent:
                    sent.discard(raw)  # overlap between history and live window
                    continue
                if file == "ark.log" and keep(raw):
                    yield f"data: {raw}\n\n"
        finally:
            bus.unsubscribe(sub)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
