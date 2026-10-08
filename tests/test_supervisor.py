"""Sidecar supervisor: spawn, capture output, stop, crash-loop give-up."""

from __future__ import annotations

import asyncio
import sys

from ark.paths import Paths
from ark.supervisor import SidecarSpec, Supervisor


async def _wait_running(sup: Supervisor, name: str, timeout: float = 5.0) -> None:
    import time

    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if sup.is_running(name):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("sidecar did not start in time")


def test_sidecar_spawn_log_stop(paths: Paths) -> None:
    spec = SidecarSpec(
        name="dummy-test",
        command=(sys.executable, "-c", "import time; time.sleep(30); print('never')"),
        port=12399,
    )
    sup = Supervisor(paths)

    async def run():
        sup.register(spec)
        await sup.start_all()
        await _wait_running(sup, "dummy-test")
        st = sup.statuses["dummy-test"]
        assert st.running is True
        assert st.pid is not None
        assert sense_status(sup.is_running("dummy-test")) is True
        await sup.stop_all()

    asyncio.run(run())


def test_sidecar_output_to_log_file(paths: Paths) -> None:
    spec = SidecarSpec(
        name="dummy-out",
        command=(
            sys.executable,
            "-c",
            "print('sidecar says hi', flush=True); import time; time.sleep(20)",
        ),
        port=12398,
    )
    sup = Supervisor(paths)

    async def run():
        sup.register(spec)
        await sup.start_all()
        await _wait_running(sup, "dummy-out")
        await sup.stop_all()

    asyncio.run(run())
    log = (paths.logs_dir / "dummy-out.log").read_text(encoding="utf-8")
    assert "sidecar says hi" in log


def test_crash_loop_gives_up(paths: Paths) -> None:
    spec = SidecarSpec(
        name="dummy-crash",
        command=(sys.executable, "-c", "raise SystemExit(1)"),
        port=12397,
        max_restarts_per_minute=2,
    )
    sup = Supervisor(paths)

    async def run():
        sup.register(spec)
        await sup.start_all()
        import time

        start = time.monotonic()
        while time.monotonic() - start < 10:
            await asyncio.sleep(0.1)
            if sup.statuses["dummy-crash"].last_error:
                break
        await sup.stop_all()
        assert sup.statuses["dummy-crash"].last_error, "expected crash-loop give-up"
        assert "giving up" in sup.statuses["dummy-crash"].last_error

    asyncio.run(run())


def sense_status(value: bool) -> bool:
    return value
