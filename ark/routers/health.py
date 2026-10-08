"""Health endpoints: cheap liveness + full per-module diagnostics."""

from __future__ import annotations

import logging
import os
import shutil
import time
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import text

import ark
from ark.paths import Paths

logger = logging.getLogger("ark.health")

router = APIRouter(tags=["health"])

# TTL caches: health is polled by the dashboard; scanning data/ on every call
# would get expensive once content packs land.
_TTL = 60.0
_cache: dict[str, Any] = {"disk_at": 0.0, "db_at": 0.0, "disk": None, "db": None}


def _dir_sizes(paths: Paths) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not paths.data.is_dir():
        return out
    for entry in sorted(paths.data.iterdir(), key=lambda p: p.name):
        if not entry.is_dir() or entry.is_symlink():
            continue
        total = 0
        try:
            for root, _dirs, files in os.walk(entry, onerror=lambda _e: None):
                for fname in files:
                    try:
                        total += os.lstat(os.path.join(root, fname)).st_size
                    except OSError:
                        continue
        except OSError:
            continue
        out.append({"name": entry.name, "size_bytes": total})
    return out


def _disk_report(paths: Paths) -> dict[str, Any]:
    now = time.monotonic()
    cached = _cache.get("disk")
    if cached and now - float(_cache["disk_at"] or 0) < _TTL:
        return cached
    usage = shutil.disk_usage(paths.home)
    report = {
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_pct": round(usage.free / usage.total * 100, 1) if usage.total else 0.0,
        "data_subdirs": _dir_sizes(paths),
    }
    _cache["disk"] = report
    _cache["disk_at"] = now
    return report


def _db_report(request: Request) -> dict[str, Any]:
    now = time.monotonic()
    if _cache.get("db") and now - float(_cache["db_at"] or 0) < _TTL:
        return _cache["db"]
    db = request.app.state.db
    report: dict[str, Any] = {"ok": False}
    try:
        with db.engine.connect() as conn:
            result = conn.execute(text("PRAGMA quick_check")).scalar()
            journal = conn.execute(text("PRAGMA journal_mode")).scalar()
        report = {
            "ok": result == "ok",
            "integrity": str(result),
            "journal_mode": str(journal),
        }
    except Exception as exc:
        report = {"ok": False, "integrity": f"error: {exc}", "journal_mode": None}
        logger.error("db integrity check failed", exc_info=True)
    try:
        report["size_bytes"] = db.db_file.stat().st_size
    except OSError:
        report["size_bytes"] = None
    _cache["db"] = report
    _cache["db_at"] = now
    return report


def clear_health_cache() -> None:
    _cache["disk_at"] = 0.0
    _cache["db_at"] = 0.0
    _cache["disk"] = None
    _cache["db"] = None


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Minimal liveness probe (no dependencies)."""
    return {"status": "ok", "version": ark.__version__}


@router.get("/api/health")
async def api_health(request: Request) -> dict[str, Any]:
    """Full health: modules, sidecars, disk, DB integrity, versions."""
    paths: Paths = request.app.state.paths
    config = request.app.state.config
    started_at: float = request.app.state.started_at
    problems: list[str] = []

    modules: list[dict[str, Any]] = []

    db_rep = _db_report(request)
    modules.append(
        {
            "name": "database",
            "healthy": bool(db_rep["ok"]),
            "status": "ok" if db_rep["ok"] else "error",
            "reason": None if db_rep["ok"] else f"integrity: {db_rep['integrity']}",
        }
    )
    if not db_rep["ok"]:
        problems.append(f"database: {db_rep['integrity']}")

    logs_ok = os.access(paths.logs_dir, os.W_OK)
    modules.append(
        {
            "name": "logging",
            "healthy": logs_ok,
            "status": "ok" if logs_ok else "error",
            "reason": None if logs_ok else f"cannot write {paths.logs_dir}",
        }
    )
    if not logs_ok:
        problems.append("logging: logs directory not writable")

    frontend_ok = (paths.frontend_dist / "index.html").is_file()
    modules.append(
        {
            "name": "frontend",
            "healthy": frontend_ok,
            "status": "ok" if frontend_ok else "not_built",
            "reason": None if frontend_ok else "frontend not built (run: make frontend)",
        }
    )
    if not frontend_ok:
        problems.append("frontend: not built")

    jobs_enabled = config.jobs.enabled
    queued = running = 0
    if jobs_enabled:
        try:
            with request.app.state.db.session() as s:
                queued = int(
                    s.execute(text("SELECT count(*) FROM jobs WHERE status = 'queued'")).scalar()
                    or 0
                )
                running = int(
                    s.execute(text("SELECT count(*) FROM jobs WHERE status = 'running'")).scalar()
                    or 0
                )
        except Exception:
            logger.exception("failed to count jobs for health")
    modules.append(
        {
            "name": "jobs",
            "healthy": True,
            "status": "ok" if jobs_enabled else "disabled",
            "reason": None,
            "queued": queued,
            "running": running,
        }
    )

    from ark.logging_setup import get_log_bus

    modules.append(
        {
            "name": "log_stream",
            "healthy": True,
            "status": "ok",
            "reason": None,
            "subscribers": len(get_log_bus()._subs),
        }
    )

    # library (Phase 1): catalog readable, ZIM dir writable
    if not config.library.enabled:
        modules.append(
            {
                "name": "library",
                "healthy": True,
                "status": "disabled",
                "reason": "disabled in ark.toml [library]",
            }
        )
    else:
        lib_ok = True
        lib_reason: str | None = None
        entry_count = zim_count = 0
        try:
            from ark.library import LibraryError, load_catalog

            try:
                entry_count = len(load_catalog(paths)["entries"])
            except LibraryError as exc:
                lib_ok = False
                lib_reason = str(exc)
            zim_count = sum(1 for p in paths.library_dir.glob("*.zim") if p.is_file())
            if lib_ok and not os.access(paths.library_dir, os.W_OK):
                lib_ok = False
                lib_reason = f"cannot write {paths.library_dir}"
        except Exception:
            logger.exception("library health check failed")
            lib_ok = False
            lib_reason = "library check failed (see data/logs/ark.log)"
        modules.append(
            {
                "name": "library",
                "healthy": lib_ok,
                "status": "ok" if lib_ok else "error",
                "reason": lib_reason,
                "entries": entry_count,
                "installed": zim_count,
                "kiwix_binary": paths.kiwix_binary.is_file(),
            }
        )
        if not lib_ok:
            problems.append(f"library: {lib_reason}")

    # Module toggles from config (later phases add their own checks).
    for name, enabled in sorted(config.modules.items()):
        modules.append(
            {
                "name": name,
                "healthy": True,
                "status": "ok" if enabled else "disabled",
                "reason": None if enabled else "disabled in ark.toml [modules]",
            }
        )

    supervisor = getattr(request.app.state, "supervisor", None)
    sidecars = supervisor.status() if supervisor else []

    disk = _disk_report(paths)
    if disk["free_pct"] < 5:
        problems.append(f"disk: only {disk['free_pct']}% free")
    elif disk["free_pct"] < 10:
        problems.append(f"disk: {disk['free_pct']}% free (running low)")

    from logging import getLevelName

    root_level = logging.getLogger().level
    status = "degraded" if problems else "ok"

    try:
        import psutil

        mem = psutil.virtual_memory()
        system = {
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": mem.percent,
            "memory_used_bytes": mem.used,
            "memory_total_bytes": mem.total,
        }
    except Exception:
        system = {"error": "psutil unavailable"}

    return {
        "status": status,
        "version": ark.__version__,
        "ark_home": str(paths.home),
        "pid": os.getpid(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started_at)),
        "uptime_seconds": round(time.time() - started_at, 1),
        "problems": problems,
        "auth_mode": config.auth.mode,
        "log_level": str(getLevelName(root_level)),
        "modules": modules,
        "sidecars": sidecars,
        "disk": disk,
        "system": system,
        "db": db_rep,
    }
