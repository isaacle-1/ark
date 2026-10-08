"""Job queue: enqueue, worker completion, progress, cancel, interrupted jobs."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ark.db import Database, run_migrations
from ark.jobs import JobContext, cancel_job, create_worker, enqueue
from ark.models import Job


async def _wait_status(
    db: Database, job_id: str, *wanted: str, rounds: int = 400, delay: float = 0.02
):
    state = None
    for _ in range(rounds):
        with db.session() as s:
            state = s.get(Job, job_id)
        if state is not None and state.status in wanted:
            return state
        await asyncio.sleep(delay)
    raise AssertionError(
        f"job {job_id} never reached {wanted}; last={state.status if state else None}"
    )


async def _run_with_worker(
    paths, config, check: Callable[[Database, object], Awaitable[None]]
) -> None:
    run_migrations(paths)
    db = Database(paths.db_file)
    worker = create_worker(db, config, paths)
    await worker.start()
    try:
        await check(db, worker)
    finally:
        await worker.stop()
        db.dispose()


def test_noop_job_completes(paths, config) -> None:
    async def check(db: Database, worker) -> None:
        job = enqueue(db, "noop", {})
        assert job.id, "job id assigned"
        state = await _wait_status(db, job.id, "done")
        assert state.status == "done", state.error
        assert state.progress == 1.0
        assert "cancelled_at" not in (state.result or "")

    asyncio.run(_run_with_worker(paths, config, check))


def test_handler_can_report_progress_and_cancel(paths, config) -> None:
    async def slow_handler(ctx: JobContext) -> dict:
        for step in range(100):
            ctx.set_progress(progress=step / 99, message=f"step {step}")
            await asyncio.sleep(0.005)
        return {"ok": True}

    async def check(db: Database, worker) -> None:
        worker.register("slow", slow_handler)
        job = enqueue(db, "slow", {})
        state = await _wait_status(db, job.id, "done")
        assert state.status == "done"
        assert float(state.progress) >= 0.99
        # cancel an in-flight job: the cooperative cancel must stick.
        job2 = enqueue(db, "slow", {})
        assert cancel_job(db, job2.id) is True
        state = await _wait_status(db, job2.id, "cancelled", "done", "failed")
        assert state.status == "cancelled", "cooperative cancel must not be overwritten"

    asyncio.run(_run_with_worker(paths, config, check))


def test_unknown_job_type_fails_fast(paths, config) -> None:
    async def check(db: Database, worker) -> None:
        job = enqueue(db, "never-registered", {})
        state = await _wait_status(db, job.id, "done", "failed")
        assert state.status == "failed"
        assert "no handler" in (state.error or "")

    asyncio.run(_run_with_worker(paths, config, check))


def test_interrupted_jobs_marked_failed_on_start(paths, config) -> None:
    run_migrations(paths)
    db = Database(paths.db_file)
    job = enqueue(db, "noop", {})
    with db.session() as s:
        state = s.get(Job, job.id)
        state.status = "running"  # simulate a crashed worker mid-job
    db.dispose()

    # Starting any worker marks stale running jobs as failed.
    async def idle(db_: Database, worker) -> None:
        await asyncio.sleep(0.05)

    asyncio.run(_run_with_worker(paths, config, idle))

    db = Database(paths.db_file)
    try:
        with db.session() as s:
            state = s.get(Job, job.id)
        assert state.status == "failed"
        assert state.error == "interrupted by restart"
    finally:
        db.dispose()
