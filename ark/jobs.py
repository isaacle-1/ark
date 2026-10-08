"""In-process background job queue backed by the SQLite ``jobs`` table.

No Redis: a single asyncio task polls for queued jobs, claims them atomically
and runs the registered async handler. Progress/bytes/speed/error are written
to the row so the UI (and /api/health) can observe them. Jobs survive restarts:
rows left in ``running`` are marked failed with "interrupted by restart".
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, update

from ark.config import ArkConfig
from ark.db import Database
from ark.models import Job, utcnow
from ark.paths import Paths

logger = logging.getLogger("ark.jobs")

JOB_STATUSES = ("queued", "running", "paused", "done", "failed", "cancelled")


@dataclass
class JobContext:
    """Passed to job handlers; lets them report progress and observe cancel."""

    job_id: str
    job_type: str
    payload: dict[str, Any]
    db: Database
    _cancelled: bool = field(default=False, repr=False)

    def set_progress(
        self,
        *,
        progress: float | None = None,
        message: str | None = None,
        bytes_done: int | None = None,
        bytes_total: int | None = None,
        speed_bps: float | None = None,
    ) -> None:
        with self.db.session() as session:
            job = session.get(Job, self.job_id)
            if job is None:
                return
            if progress is not None:
                job.progress = max(0.0, min(1.0, progress))
            if message is not None:
                job.message = message[:512]
            if bytes_done is not None:
                job.bytes_done = bytes_done
            if bytes_total is not None:
                job.bytes_total = bytes_total
            if speed_bps is not None:
                job.speed_bps = speed_bps

    def cancelled(self) -> bool:
        """True if the job was cancelled/paused externally (cheap DB read)."""
        with self.db.session() as session:
            job = session.get(Job, self.job_id)
            return job is None or job.status in {"cancelled", "paused"}


JobHandler = Callable[[JobContext], Awaitable[dict[str, Any] | None]]


class JobWorker:
    """Polls the jobs table and executes handlers one at a time."""

    def __init__(self, db: Database, config: ArkConfig, paths: Paths) -> None:
        self.db = db
        self.config = config
        self.paths = paths
        self.handlers: dict[str, JobHandler] = {}
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._stopping = False

    def register(self, job_type: str, handler: JobHandler) -> None:
        self.handlers[job_type] = handler

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        self._stopping = False
        self._mark_interrupted()
        if self.config.jobs.enabled:
            self._task = asyncio.create_task(self._loop(), name="ark-job-worker")
            logger.info(
                "job worker started", extra={"poll_interval": self.config.jobs.poll_interval}
            )

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("job worker stopped")

    def notify(self) -> None:
        """Wake the worker immediately (new job enqueued)."""
        self._wake.set()

    def _mark_interrupted(self) -> None:
        with self.db.session() as session:
            result = session.execute(
                update(Job)
                .where(Job.status == "running")
                .values(
                    status="failed",
                    error="interrupted by restart",
                    finished_at=utcnow(),
                )
            )
            rowcount = int(getattr(result, "rowcount", 0) or 0)
            if rowcount:
                logger.warning(
                    "marked interrupted jobs as failed",
                    extra={"count": rowcount},
                )

    # -- execution ---------------------------------------------------------

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                job_row = self._claim_next()
                if job_row is None:
                    try:
                        await asyncio.wait_for(
                            self._wake.wait(), timeout=self.config.jobs.poll_interval
                        )
                    except TimeoutError:
                        pass
                    self._wake.clear()
                    continue
                await self._run_job(job_row)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("job worker loop error")
                await asyncio.sleep(self.config.jobs.poll_interval)

    def _claim_next(self) -> tuple[str, str, str] | None:
        with self.db.session() as session:
            job = session.scalar(
                select(Job).where(Job.status == "queued").order_by(Job.created_at).limit(1)
            )
            if job is None:
                return None
            result = session.execute(
                update(Job)
                .where(Job.id == job.id, Job.status == "queued")
                .values(status="running", started_at=utcnow())
            )
            if int(getattr(result, "rowcount", 0) or 0) != 1:
                return None
            return job.id, job.type, job.payload

    async def _run_job(self, claim: tuple[str, str, str]) -> None:
        job_id, job_type, payload_json = claim
        handler = self.handlers.get(job_type)
        if handler is None:
            self._finish(
                job_id, status="failed", error=f"no handler registered for type {job_type!r}"
            )
            logger.error("no handler for job type", extra={"job_type": job_type, "job_id": job_id})
            return
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except json.JSONDecodeError:
            payload = {}
        ctx = JobContext(job_id=job_id, job_type=job_type, payload=payload, db=self.db)
        from ark.logging_setup import job_id_var

        token = job_id_var.set(job_id)
        logger.info("job started", extra={"job_type": job_type})
        try:
            result = await handler(ctx)
        except asyncio.CancelledError:
            self._finish(job_id, status="failed", error="worker stopped")
            raise
        except Exception as exc:
            logger.exception("job failed", extra={"job_type": job_type})
            self._finish(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")
        else:
            self._finish(job_id, status="done", result=result, progress=1.0)
            logger.info("job finished", extra={"job_type": job_type})
        finally:
            job_id_var.reset(token)

    def _finish(
        self,
        job_id: str,
        *,
        status: str,
        error: str | None = None,
        result: dict[str, Any] | None = None,
        progress: float | None = None,
    ) -> None:
        with self.db.session() as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            if status == "done" and job.status in {"cancelled", "paused"}:
                # Explicit cancel/pause wins over completion (cooperative).
                return
            job.status = status
            job.error = error
            job.finished_at = utcnow()
            if result is not None:
                job.result = json.dumps(result, default=str)
            if progress is not None:
                job.progress = progress


def enqueue(db: Database, job_type: str, payload: dict[str, Any] | None = None) -> Job:
    """Insert a queued job and return the ORM row (id assigned)."""
    job = Job(
        id=str(uuid.uuid4()),
        type=job_type,
        status="queued",
        payload=json.dumps(payload or {}, default=str),
    )
    with db.session() as session:
        session.add(job)
        session.commit()
        session.refresh(job)
        return job


def cancel_job(db: Database, job_id: str) -> bool:
    """Cancel a queued job (immediate) or a running one (cooperative)."""
    with db.session() as session:
        job = session.get(Job, job_id)
        if job is None or job.status in {"done", "failed", "cancelled"}:
            return False
        job.status = "cancelled"
        job.finished_at = utcnow()
        return True


async def _noop_handler(ctx: JobContext) -> dict[str, Any]:
    """Test/demo handler: steps 0..5, cancellable, ~0.2s total."""
    for step in range(6):
        if ctx.cancelled():
            return {"cancelled_at": step}
        ctx.set_progress(progress=step / 5, message=f"step {step + 1}/6")
        await asyncio.sleep(0.05)
    return {"ok": True}


def create_worker(
    db: Database,
    config: ArkConfig,
    paths: Paths,
    supervisor: Any = None,
) -> JobWorker:
    worker = JobWorker(db, config, paths)
    worker.register("noop", _noop_handler)
    if config.library.enabled:
        from ark.library import make_download_handler

        worker.register("library.download", make_download_handler(paths, config, supervisor))
    return worker
