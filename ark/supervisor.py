"""Sidecar supervisor: run kiwix-serve & co. as managed child processes.

- One asyncio task per sidecar: spawn, capture stdout+stderr into
  ``data/logs/<name>.log``, restart with capped backoff on crash.
- Status feeds /api/health and `ark doctor`.
- The main app proxies ``/svc/<name>/...`` to the sidecar's local port.

Phase 0 ships the framework with no sidecars registered; later phases add
specs via :func:`get_sidecar_specs` (driven by config + installed binaries).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from ark.config import ArkConfig
from ark.paths import Paths

logger = logging.getLogger("ark.supervisor")


@dataclass(frozen=True)
class SidecarSpec:
    name: str
    command: tuple[str, ...]
    port: int
    env: dict[str, str] = field(default_factory=dict)
    cwd: Path | None = None
    auto_restart: bool = True
    max_restarts_per_minute: int = 30


@dataclass
class SidecarStatus:
    name: str
    running: bool
    port: int
    pid: int | None = None
    restarts: int = 0
    started_at: float | None = None
    last_exit_code: int | None = None
    last_error: str | None = None
    log_file: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "running": self.running,
            "port": self.port,
            "pid": self.pid,
            "restarts": self.restarts,
            "uptime_seconds": (
                round(time.time() - self.started_at, 1) if self.started_at else None
            ),
            "last_exit_code": self.last_exit_code,
            "last_error": self.last_error,
            "log_file": self.log_file,
        }


class Supervisor:
    def __init__(self, paths: Paths) -> None:
        self.paths = paths
        self.specs: dict[str, SidecarSpec] = {}
        self.statuses: dict[str, SidecarStatus] = {}
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stopping = False

    def register(self, spec: SidecarSpec) -> None:
        self.specs[spec.name] = spec
        self.statuses[spec.name] = SidecarStatus(name=spec.name, running=False, port=spec.port)

    @property
    def log_dir(self) -> Path:
        return self.paths.logs_dir

    async def start_all(self) -> None:
        self._stopping = False
        for spec in self.specs.values():
            self._tasks[spec.name] = asyncio.create_task(
                self._supervise(spec), name=f"ark-sidecar-{spec.name}"
            )
        if self.specs:
            logger.info("supervisor started", extra={"sidecars": sorted(self.specs)})

    async def stop_all(self) -> None:
        self._stopping = True
        for name in list(self._tasks):
            task = self._tasks[name]
            task.cancel()
        for name, task in list(self._tasks.items()):
            try:
                await task
            except asyncio.CancelledError:
                pass
            self._tasks.pop(name, None)
        for name, proc in list(self._procs.items()):
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except (TimeoutError, ProcessLookupError):
                    try:
                        proc.kill()
                        await proc.wait()
                    except ProcessLookupError:
                        pass
            self._procs.pop(name, None)
            st = self.statuses.get(name)
            if st:
                st.running = False
                st.pid = None
        if self.specs:
            logger.info("supervisor stopped")

    def status(self) -> list[dict[str, object]]:
        return [self.statuses[n].as_dict() for n in sorted(self.statuses)]

    def is_running(self, name: str) -> bool:
        proc = self._procs.get(name)
        return proc is not None and proc.returncode is None

    def port_for(self, name: str) -> int | None:
        spec = self.specs.get(name)
        return spec.port if spec and self.is_running(name) else None

    async def restart(self, name: str, spec: SidecarSpec | None = None) -> None:
        """Stop a sidecar now; re-register and start it again when a spec is given.

        ``spec=None`` stops it and unregisters it (used when its content
        disappears, e.g. the last ZIM is deleted).
        """
        task = self._tasks.pop(name, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        proc = self._procs.pop(name, None)
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except (TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
        st = self.statuses.get(name)
        if st is not None:
            st.running = False
            st.pid = None
        if spec is None:
            self.specs.pop(name, None)
            self.statuses.pop(name, None)
            logger.info("sidecar stopped and unregistered", extra={"sidecar": name})
            return
        self.register(spec)
        if not self._stopping:
            self._tasks[name] = asyncio.create_task(
                self._supervise(spec), name=f"ark-sidecar-{name}"
            )
            logger.info("sidecar restarted", extra={"sidecar": name, "port": spec.port})

    async def _supervise(self, spec: SidecarSpec) -> None:
        st = self.statuses[spec.name]
        backoff = 1.0
        restart_times: list[float] = []
        while not self._stopping:
            log_path = self.log_dir / f"{spec.name}.log"
            self.log_dir.mkdir(parents=True, exist_ok=True)
            try:
                log_fh = log_path.open("a", encoding="utf-8")
            except OSError as exc:
                st.last_error = f"cannot open log file {log_path}: {exc}"
                logger.error(st.last_error, extra={"sidecar": spec.name})
                return
            env = {**os.environ, **spec.env}
            try:
                proc = await asyncio.create_subprocess_exec(
                    *spec.command,
                    cwd=str(spec.cwd) if spec.cwd else str(self.paths.home),
                    env=env,
                    stdout=log_fh,
                    stderr=asyncio.subprocess.STDOUT,
                )
            except (OSError, ValueError) as exc:
                log_fh.close()
                st.last_error = f"failed to start: {exc}"
                st.running = False
                logger.error(
                    "sidecar failed to start",
                    extra={"sidecar": spec.name, "command": list(spec.command), "error": str(exc)},
                )
                if not spec.auto_restart or self._stopping:
                    return
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue

            log_fh.close()
            st.running = True
            st.pid = proc.pid
            st.started_at = time.time()
            st.last_error = None
            logger.info(
                "sidecar started",
                extra={"sidecar": spec.name, "pid": proc.pid, "port": spec.port},
            )
            self._procs[spec.name] = proc
            code = await proc.wait()
            self._procs.pop(spec.name, None)
            st.running = False
            st.pid = None
            st.last_exit_code = code
            logger.warning(
                "sidecar exited",
                extra={"sidecar": spec.name, "exit_code": code, "log_file": str(log_path)},
            )
            if self._stopping or not spec.auto_restart:
                return
            # Rate-limit crash loops: keep only restarts from the last minute.
            now = time.monotonic()
            restart_times = [t for t in restart_times if now - t < 60]
            if len(restart_times) >= spec.max_restarts_per_minute:
                st.last_error = (
                    f"restarted {len(restart_times)}x in the last minute; giving up "
                    f"(see {log_path.name})"
                )
                logger.error(
                    "sidecar crash-looping, giving up",
                    extra={"sidecar": spec.name, "log_file": str(log_path)},
                )
                return
            restart_times.append(now)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


def build_kiwix_spec(paths: Paths, config: ArkConfig) -> SidecarSpec | None:
    """kiwix-serve spec for the currently installed ZIMs, or None if it should
    not run (module disabled, binary missing, or no ZIMs installed yet) —
    a missing binary/content degrades to a clear "not running" state, never a
    crash or a crash-loop.
    """
    lib = config.library
    if not (lib.enabled and lib.kiwix_enabled):
        return None
    binary = paths.kiwix_binary
    if not binary.is_file():
        return None
    zims = sorted(p for p in paths.library_dir.glob("*.zim") if p.is_file())
    if not zims:
        return None
    command = (
        str(binary),
        "-i",
        "127.0.0.1",  # only reachable through the /svc/kiwix/ proxy
        "-p",
        str(lib.kiwix_port),
        "-b",  # block external links (offline-first)
        "-s",
        "10",  # fulltext search across installed ZIMs
        "-L",
        "8",  # connection limit per IP (kiwix-serve recommendation)
        "-k",  # skip invalid ZIMs instead of refusing to start
        *(str(z) for z in zims),
    )
    return SidecarSpec(
        name="kiwix",
        command=command,
        port=lib.kiwix_port,
        auto_restart=True,
        max_restarts_per_minute=6,
    )


def get_sidecar_specs(paths: Paths, config: ArkConfig) -> list[SidecarSpec]:
    """Sidecars to supervise for this installation.

    Each spec is guarded by "module enabled / binary exists / content
    present" checks — a missing dependency must degrade to a clear
    "not installed / not reached" state, never a crash.
    """
    spec = build_kiwix_spec(paths, config)
    return [spec] if spec is not None else []
