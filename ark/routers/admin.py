"""Admin API: background jobs (list/create/cancel) and support helpers."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ark.auth import require_admin
from ark.jobs import JOB_STATUSES, cancel_job, enqueue
from ark.models import Job

logger = logging.getLogger("ark.jobs")

router = APIRouter(prefix="/api/admin/jobs", tags=["jobs"], dependencies=[Depends(require_admin)])


class JobCreate(BaseModel):
    type: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_.-]+$")
    payload: dict[str, Any] = Field(default_factory=dict)


def _job_dict(job: Job) -> dict[str, Any]:
    try:
        payload = json.loads(job.payload) if job.payload else {}
    except json.JSONDecodeError:
        payload = {"_unparseable": job.payload[:200]}
    result: Any = None
    if job.result:
        try:
            result = json.loads(job.result)
        except json.JSONDecodeError:
            result = job.result[:500]
    return {
        "id": job.id,
        "type": job.type,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "bytes_done": job.bytes_done,
        "bytes_total": job.bytes_total,
        "speed_bps": job.speed_bps,
        "error": job.error,
        "payload": payload,
        "result": result,
        "created_at": job.created_at.isoformat() + "Z" if job.created_at else None,
        "started_at": job.started_at.isoformat() + "Z" if job.started_at else None,
        "finished_at": job.finished_at.isoformat() + "Z" if job.finished_at else None,
    }


@router.get("")
async def list_jobs(
    request: Request,
    status: str | None = Query(default=None, pattern=f"^({'|'.join(JOB_STATUSES)})$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    with request.app.state.db.session() as db:
        stmt = select(Job)
        count_stmt = select(func.count()).select_from(Job)
        if status:
            stmt = stmt.where(Job.status == status)
            count_stmt = count_stmt.where(Job.status == status)
        total = int(db.scalar(count_stmt) or 0)
        rows = db.scalars(stmt.order_by(Job.created_at.desc()).limit(limit).offset(offset)).all()
    return {"items": [_job_dict(j) for j in rows], "total": total}


@router.get("/{job_id}")
async def get_job(job_id: str, request: Request) -> dict[str, Any]:
    with request.app.state.db.session() as db:
        job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return _job_dict(job)


@router.post("", status_code=202)
async def create_job(payload: JobCreate, request: Request) -> dict[str, Any]:
    worker = request.app.state.worker
    if payload.type not in worker.handlers:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown job type {payload.type!r}. Registered: {sorted(worker.handlers)}",
        )
    job = enqueue(request.app.state.db, payload.type, payload.payload)
    worker.notify()
    logger.info("job enqueued", extra={"job_id": job.id, "job_type": job.type})
    return _job_dict(job)


@router.post("/{job_id}/cancel")
async def cancel(job_id: str, request: Request) -> dict[str, Any]:
    with request.app.state.db.session() as db:
        job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if not cancel_job(request.app.state.db, job_id):
        raise HTTPException(status_code=409, detail=f"Job is already {job.status}; cannot cancel")
    logger.info("job cancelled", extra={"job_id": job_id})
    return {"ok": True, "id": job_id, "status": "cancelled"}
