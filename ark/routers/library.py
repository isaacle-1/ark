"""Library API: Kiwix catalog, ZIM installs, installed items, manual uploads."""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ark.auth import require_admin, require_read
from ark.jobs import enqueue
from ark.library import (
    LibraryError,
    build_catalog_plan,
    entry_key,
    find_entry,
    find_entry_by_tier,
    job_brief,
    load_catalog,
    preflight_disk,
    refresh_kiwix,
    safe_manual_name,
    selection_totals,
    unique_dest,
    zim_filename,
)
from ark.models import Job, LibraryItem, utcnow

logger = logging.getLogger("ark.library")

router = APIRouter(prefix="/api/library", tags=["library"], dependencies=[Depends(require_read)])

_ZIM_STATUSES = ("queued", "downloading", "installed", "error", "paused")


class InstallRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9._-]+$")
    flavour: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9._-]*$")
    tier: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-z0-9._-]+$")


class PreflightSelection(BaseModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9._-]+$")
    tier: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9._-]+$")


class PreflightRequest(BaseModel):
    selections: list[PreflightSelection] = Field(min_length=0, max_length=200)


def _require_enabled(request: Request) -> None:
    if not request.app.state.config.library.enabled:
        raise HTTPException(
            status_code=403,
            detail="Library module is disabled (set [library] enabled = true in ark.toml)",
        )


def _iso(dt: Any) -> str | None:
    return dt.isoformat() + "Z" if dt is not None else None


def _effective_status(item: LibraryItem, job: Job | None) -> tuple[str, str | None]:
    """Status shown to the API, reconciled with the backing job's fate."""
    if item.status in ("queued", "downloading"):
        if job is None:
            return "error", "download job is missing; retry the install"
        if job.status in ("queued", "running"):
            return item.status, None
        if job.status == "cancelled":
            return "paused", None
        if job.status == "failed":
            return "error", job.error or "download failed"
        if job.status == "done":
            return item.status, item.error
    return item.status, item.error


def _item_dict(item: LibraryItem, job: Job | None = None) -> dict[str, Any]:
    status, error = _effective_status(item, job)
    active = job if job is not None and job.status in ("queued", "running") else None
    return {
        "id": item.id,
        "kind": item.kind,
        "name": item.name,
        "title": item.title,
        "filename": item.filename,
        "size": item.size,
        "sha256": item.sha256,
        "status": status,
        "error": error,
        "job_id": item.job_id,
        "active_job": job_brief(active) if active is not None else None,
        "file_url": f"/api/library/manuals/{item.id}/file" if item.kind == "manual" else None,
        "created_at": _iso(item.created_at),
        "installed_at": _iso(item.installed_at),
    }


def _load_items(
    session: Session,
) -> tuple[dict[str, LibraryItem], dict[str, Job], list[LibraryItem]]:
    """All items, zims keyed by catalog key, plus jobs referenced by any item."""
    all_items = list(
        session.scalars(select(LibraryItem).order_by(LibraryItem.created_at.desc())).all()
    )
    zim_items = {item.name: item for item in reversed(all_items) if item.kind == "zim"}
    job_ids = {i.job_id for i in all_items if i.job_id}
    jobs: dict[str, Job] = {}
    if job_ids:
        for job in session.scalars(select(Job).where(Job.id.in_(job_ids))).all():
            jobs[job.id] = job
    return zim_items, jobs, all_items


# -- catalog / install ------------------------------------------------------


@router.get("/catalog")
async def get_catalog(request: Request) -> dict[str, Any]:
    _require_enabled(request)
    paths = request.app.state.paths
    try:
        catalog = load_catalog(paths)
    except LibraryError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    with request.app.state.db.session() as db:
        zim_items, jobs, _ = _load_items(db)
    entries: list[dict[str, Any]] = []
    views: dict[str, dict[str, Any]] = {}
    for entry in catalog["entries"]:
        key = entry_key(entry.get("name", ""), entry.get("flavour"))
        item = zim_items.get(key)
        job = jobs.get(item.job_id) if item is not None and item.job_id else None
        status, error = _effective_status(item, job) if item else (None, None)
        active = job if job is not None and job.status in ("queued", "running") else None
        out = dict(entry)
        out["item"] = _item_dict(item, job) if item else None
        out["item_status"] = status
        out["item_error"] = error
        out["active_job"] = job_brief(active) if active else None
        out["installed"] = status == "installed"
        out["downloadable"] = entry.get("status") == "verified" and status not in (
            "queued",
            "downloading",
            "installed",
        )
        entries.append(out)
        views[key] = out
    plan = build_catalog_plan(catalog, views)
    return {"updated": catalog.get("updated"), "entries": entries, "plan": plan}


@router.post("/preflight", dependencies=[Depends(require_admin)])
async def preflight(body: PreflightRequest, request: Request) -> dict[str, Any]:
    """Disk math + combined totals for a mixed tier selection (does not start anything)."""
    _require_enabled(request)
    paths = request.app.state.paths
    try:
        catalog = load_catalog(paths)
    except LibraryError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    picks = [(s.name, s.tier) for s in body.selections]
    total, excluded = selection_totals(catalog, picks)
    disk = preflight_disk(paths, request.app.state.config, total)
    disk["excluded"] = excluded
    return {"selections": len(picks), "total_bytes": total, **disk}


@router.post("/install", status_code=202, dependencies=[Depends(require_admin)])
async def install(body: InstallRequest, request: Request) -> dict[str, Any]:
    _require_enabled(request)
    paths = request.app.state.paths
    try:
        catalog = load_catalog(paths)
    except LibraryError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if body.tier is not None:
        entry = find_entry_by_tier(catalog, body.name, body.tier)
        if entry is None:
            raise HTTPException(
                status_code=404, detail=f"Catalog entry not found: {body.name}:{body.tier}"
            )
    else:
        entry = find_entry(catalog, body.name, body.flavour)
        if entry is None:
            raise HTTPException(
                status_code=404, detail=f"Catalog entry not found: {entry_key(body.name, body.flavour)}"
            )
    if entry.get("status") != "verified":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Catalog entry {entry_key(entry['name'], entry.get('flavour'))} is not verified "
                f"({entry.get('verify_error') or 'run scripts/verify_catalog.py'})"
            ),
        )
    urls = [u for u in (entry.get("mirror_urls") or []) if isinstance(u, str) and u]
    if not urls and isinstance(entry.get("zim_url"), str):
        urls = [entry["zim_url"]]
    if not urls:
        raise HTTPException(status_code=409, detail="Catalog entry has no download URLs")

    key = entry_key(entry["name"], entry.get("flavour"))
    filename = zim_filename(entry["name"], entry.get("flavour"))
    size = entry.get("size")
    sha256 = entry.get("sha256")
    if not isinstance(size, int) or size <= 0 or not isinstance(sha256, str):
        raise HTTPException(status_code=409, detail="Catalog entry has no valid size/sha256")

    db = request.app.state.db
    with db.session() as session:
        item = session.scalar(
            select(LibraryItem).where(LibraryItem.kind == "zim", LibraryItem.name == key)
        )
        if item is not None and item.job_id:
            job = session.get(Job, item.job_id)
            if (
                job is not None
                and job.status in ("queued", "running")
                and item.status
                in (
                    "queued",
                    "downloading",
                )
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"{key} is already queued/downloading (job {job.id}); "
                        "wait or cancel it first"
                    ),
                )
        if item is not None:
            existing_job = session.get(Job, item.job_id) if item.job_id else None
            if _effective_status(item, existing_job)[0] == "installed":
                raise HTTPException(
                    status_code=409,
                    detail=f"{key} is already installed; delete it first to reinstall",
                )
        if item is None:
            item = LibraryItem(
                id=str(uuid.uuid4()),
                kind="zim",
                name=key,
                title=str(entry.get("title") or key),
                filename=filename,
                relpath=f"library/{filename}",
                size=size,
                sha256=sha256,
                status="queued",
            )
            session.add(item)
        else:
            item.status = "queued"
            item.error = None
            item.job_id = None
            item.filename = filename
            item.relpath = f"library/{filename}"
            item.size = size
            item.sha256 = sha256
            item.title = str(entry.get("title") or key)
        session.flush()
        item_id = item.id

    job = enqueue(
        db,
        "library.download",
        {
            "item_id": item_id,
            "name": entry["name"],
            "flavour": entry.get("flavour"),
            "title": entry.get("title"),
            "filename": filename,
            "size": size,
            "sha256": sha256,
            "urls": urls,
        },
    )
    with db.session() as session:
        pending = session.get(LibraryItem, item_id)
        if pending is not None:
            pending.job_id = job.id
    worker = request.app.state.worker
    worker.notify()
    logger.info(
        "install enqueued",
        extra={"job_id": job.id, "item_id": item_id, "entry": key, "size": size},
    )
    return {"job_id": job.id, "item_id": item_id, "name": key}


# -- items ------------------------------------------------------------------


@router.get("/items")
async def list_items(request: Request) -> dict[str, Any]:
    _require_enabled(request)
    with request.app.state.db.session() as db:
        _, jobs, all_items = _load_items(db)
        items = [
            _item_dict(item, jobs.get(item.job_id) if item.job_id else None)
            for item in reversed(all_items)
        ]
    return {"items": items, "total": len(items)}


@router.delete("/items/{item_id}", dependencies=[Depends(require_admin)])
async def delete_item(item_id: str, request: Request) -> dict[str, Any]:
    _require_enabled(request)
    paths = request.app.state.paths
    db = request.app.state.db
    with db.session() as session:
        item = session.get(LibraryItem, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail=f"Library item not found: {item_id}")
        job = session.get(Job, item.job_id) if item.job_id else None
        status, _ = _effective_status(item, job)
        if status in ("queued", "downloading"):
            raise HTTPException(
                status_code=409,
                detail=f"Item is {status}; cancel the download before deleting it",
            )
        kind, name, relpath = item.kind, item.name, item.relpath
        session.delete(item)

    target = (paths.data / relpath).resolve()
    allowed = {paths.library_dir.resolve(), paths.manuals_dir.resolve()}
    if target.parent not in allowed:
        logger.error(
            "refusing to delete file outside library/manuals",
            extra={"item_id": item_id, "path": str(target)},
        )
        raise HTTPException(status_code=400, detail="Invalid library item path")
    if target.is_file():
        target.unlink()
        logger.info(
            "library item deleted",
            extra={"item_id": item_id, "kind": kind, "entry": name, "path": str(target)},
        )
    else:
        logger.warning(
            "library item file already missing",
            extra={"item_id": item_id, "kind": kind, "path": str(target)},
        )

    if kind == "zim":
        await refresh_kiwix(request.app.state.supervisor, paths, request.app.state.config)
    return {"ok": True, "id": item_id}


# -- manuals ----------------------------------------------------------------


@router.post("/manuals", status_code=201, dependencies=[Depends(require_admin)])
async def upload_manual(
    request: Request,
    file: UploadFile = File(...),  # noqa: B008 — FastAPI idiom
) -> dict[str, Any]:
    _require_enabled(request)
    paths = request.app.state.paths
    config = request.app.state.config
    original = file.filename or "manual"
    limit = config.library.max_manual_mb * 1024 * 1024
    dest = unique_dest(paths.manuals_dir, safe_manual_name(original))
    digest = hashlib.sha256()
    total = 0
    too_big = False
    with dest.open("wb") as fh:
        while chunk := await file.read(1_048_576):
            total += len(chunk)
            if total > limit:
                too_big = True
                break
            digest.update(chunk)
            fh.write(chunk)
    if too_big:
        dest.unlink(missing_ok=True)
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {config.library.max_manual_mb} MB manual upload limit",
        )
    if total == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="Uploaded file is empty")

    item = LibraryItem(
        id=str(uuid.uuid4()),
        kind="manual",
        name=original,
        title=original,
        filename=dest.name,
        relpath=f"manuals/{dest.name}",
        size=total,
        sha256=digest.hexdigest(),
        status="installed",
        installed_at=utcnow(),
    )
    with request.app.state.db.session() as session:
        session.add(item)
    logger.info(
        "manual uploaded",
        extra={"item_id": item.id, "file_name": dest.name, "size": total, "sha256": item.sha256},
    )
    return _item_dict(item)


@router.get("/manuals/{item_id}/file")
async def manual_file(item_id: str, request: Request) -> FileResponse:
    _require_enabled(request)
    paths = request.app.state.paths
    with request.app.state.db.session() as session:
        item = session.get(LibraryItem, item_id)
    if item is None or item.kind != "manual":
        raise HTTPException(status_code=404, detail=f"Manual not found: {item_id}")
    target = (paths.data / item.relpath).resolve()
    if target.parent != paths.manuals_dir.resolve():
        logger.error(
            "refusing to serve file outside manuals dir",
            extra={"item_id": item_id, "path": str(target)},
        )
        raise HTTPException(status_code=400, detail="Invalid manual file path")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"Manual file missing on disk: {item.filename}")
    return FileResponse(target, filename=item.filename, content_disposition_type="inline")
