"""Library module: Kiwix ZIM catalog, resumable verified downloads, manuals.

- ``catalog/kiwix.json`` (produced by scripts/verify_catalog.py) is the source
  of truth for downloadable archives: every entry carries a verified sha256,
  size and a mirror list.
- Downloads run as the ``library.download`` job: resumable (.part file in
  data/tmp), sha256-verified against the catalog, written atomically into
  data/library. Progress/milestones are logged with the job id.
- Manuals are uploaded into data/manuals and served with path-safe FileResponses.
- kiwix-serve (supervised sidecar) serves installed ZIMs via /svc/kiwix/.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import urllib.request
from pathlib import Path
from typing import Any

from ark.config import ArkConfig
from ark.db import Database
from ark.jobs import JobContext, JobHandler
from ark.models import Job, LibraryItem, utcnow
from ark.paths import Paths

logger = logging.getLogger("ark.library")

USER_AGENT = "ark-library/1.0 (offline survival toolkit)"
_MILESTONES = (10, 25, 50, 75, 90)
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_ACTIVE_JOB_STATUSES = ("queued", "running")


def _next_milestone(done: int, size: int) -> int | None:
    percent = done * 100 // size if size else 100
    return next((m for m in _MILESTONES if percent < m), None)


class LibraryError(Exception):
    """An actionable library failure (bad catalog entry, checksum, I/O…)."""


# -- catalog ----------------------------------------------------------------


def entry_key(name: str, flavour: str | None) -> str:
    return f"{name}:{flavour or ''}"


def load_catalog(paths: Paths) -> dict[str, Any]:
    """Read and minimally validate catalog/kiwix.json."""
    path = paths.catalog_file
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LibraryError(
            f"catalog not found at {path} (expected the repo's catalog/kiwix.json)"
        ) from exc
    except OSError as exc:
        raise LibraryError(f"cannot read catalog {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LibraryError(f"catalog {path} is not valid JSON: {exc}") from exc
    entries = raw.get("entries")
    if not isinstance(entries, list):
        raise LibraryError(f"catalog {path} has no 'entries' list")
    return raw


def find_entry(catalog: dict[str, Any], name: str, flavour: str | None) -> dict[str, Any] | None:
    key = entry_key(name, flavour)
    for entry in catalog.get("entries", []):
        if entry_key(entry.get("name", ""), entry.get("flavour")) == key:
            return entry
    return None


def zim_filename(name: str, flavour: str | None) -> str:
    """Deterministic, filesystem-safe file name for a catalog entry."""
    base = f"{name}_{flavour}" if flavour else name
    base = _UNSAFE.sub("_", base).strip(".") or "archive"
    return f"{base}.zim"


# -- catalog plan (items/groups, per-item tiers, curated bundles) ------------
#
# catalog/kiwix.json stays the verified machine truth (one row per archive).
# Two optional top-level keys make it a "plan":
#   "tiers"  — {tier-id: {label, description}} presentation overrides (the
#              defaults below are used for anything else).
#   "bundles" — curated sets of {name, tier} pick-lists defined as data
#               (never in code). Bundles containing an unverified item are
#               reported as incomplete and the item is excluded from the
#               computed total.
# The API arranges entries into logical items (same ``name``) whose tiers are
# the size variants (``flavour``), so the UI shows tiers side by side.

LATER_PHASE_HINT = (
    "RAG search indexes, offline maps, and video content will need more disk "
    "in later phases. Keep a safety margin when planning selections."
)

_CATEGORY_PRIORITY = ("wikipedia", "wikibooks", "ifixit", "gutenberg", "devdocs")

TIER_META_DEFAULT: dict[str, dict[str, str]] = {
    "full": {"label": "Full", "description": "Complete archive, images included"},
    "maxi": {"label": "Maxi", "description": "With images; largest size"},
    "nopic": {"label": "No pictures", "description": "All articles, no images"},
    "mini": {"label": "Mini", "description": "All articles, no images; smallest size"},
    "top": {"label": "Top", "description": "Most-read articles only"},
}


def tier_id_of(entry: dict[str, Any]) -> str:
    """The logical tier id of a catalog entry (flavour; 'full' when none)."""
    return (entry.get("flavour") or "").strip() or "full"


def iter_entries(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    entries = catalog.get("entries")
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _tier_meta(catalog: dict[str, Any], tier_id: str) -> dict[str, str]:
    override = (catalog.get("tiers") or {}).get(tier_id) or {}
    base = dict(TIER_META_DEFAULT.get(tier_id, {"label": tier_id, "description": "Variant"}))
    base.update({k: v for k, v in override.items() if isinstance(v, str)})
    return base


def find_entry_by_tier(catalog: dict[str, Any], name: str, tier: str) -> dict[str, Any] | None:
    """Resolve an entry by its logical tier id (name + tier)."""
    for entry in iter_entries(catalog):
        if entry.get("name") == name and tier_id_of(entry) == tier:
            return entry
    return None


def _group_sort_key(group: dict[str, Any]) -> tuple[int, str]:
    name = str(group["name"])
    if name == "wikipedia_en_all":
        return (-1, "")
    priority = _CATEGORY_PRIORITY.index(group["category"]) if group["category"] in _CATEGORY_PRIORITY else 99
    return (priority, name)


def build_catalog_plan(
    catalog: dict[str, Any],
    view_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Group entries into items with tiers, and expand curated bundles.

    ``view_map`` maps ``entry_key(name, flavour)`` to the annotated entry view
    already built by the API (adds item/status/job fields per tier).
    """
    by_name: dict[str, list[dict[str, Any]]] = {}
    for entry in iter_entries(catalog):
        by_name.setdefault(str(entry.get("name")), []).append(entry)

    tiers_meta: dict[str, dict[str, str]] = {}
    groups: list[dict[str, Any]] = []
    for name, entry_list in by_name.items():
        entry_list.sort(key=lambda e: e.get("size") or 0)
        first = entry_list[0]
        tiers: list[dict[str, Any]] = []
        for entry in entry_list:
            tid = tier_id_of(entry)
            meta = _tier_meta(catalog, tid)
            key = entry_key(name, entry.get("flavour"))
            view = view_map.get(key, {})
            tiers.append(
                {
                    "id": tid,
                    "label": meta["label"],
                    "description": meta["description"],
                    "flavour": entry.get("flavour"),
                    "title": str(entry.get("title") or name),
                    "size": entry.get("size"),
                    "license": entry.get("license"),
                    "license_url": entry.get("license_url"),
                    "license_note": entry.get("license_note"),
                    "status": entry.get("status"),
                    "verified_at": entry.get("verified_at"),
                    "source_url": entry.get("browse_url") or entry.get("zim_url"),
                    "downloadable": bool(view.get("downloadable")),
                    "installed": bool(view.get("installed")),
                    "item_status": view.get("item_status"),
                    "active_job": view.get("active_job"),
                    "item": view.get("item"),
                }
            )
        for tid in tiers:
            tiers_meta[tid["id"]] = {"id": tid["id"], "label": tid["label"], "description": tid["description"]}
        groups.append(
            {
                "name": name,
                "title": str(first.get("title") or name),
                "category": str(first.get("category") or ""),
                "language": first.get("language"),
                "tiers": tiers,
            }
        )
    groups.sort(key=_group_sort_key)

    bundles: list[dict[str, Any]] = []
    for bundle in catalog.get("bundles") or []:
        members = []
        total = 0
        excluded = False
        reasons: list[str] = []
        for spec in bundle.get("spec") or []:
            name = str(spec.get("name", ""))
            tier = str(spec.get("tier", ""))
            entry = find_entry_by_tier(catalog, name, tier)
            if entry is None:
                excluded = True
                reasons.append(f"{name}:{tier}: not in catalog")
                members.append({"name": name, "tier": tier, "title": name, "size": 0, "verified": False})
                continue
            verified = entry.get("status") == "verified"
            if not verified:
                excluded = True
                reasons.append(
                    f"{name}:{tier}: not verified "
                    f"({entry.get('verify_error') or 'run scripts/verify_catalog.py'})"
                )
            size = entry.get("size") if verified else 0
            total += size
            members.append(
                {
                    "name": name,
                    "tier": tier,
                    "title": str(entry.get("title") or name),
                    "size": size,
                    "verified": verified,
                }
            )
        bundles.append(
            {
                "id": str(bundle.get("id")),
                "title": str(bundle.get("title")),
                "description": str(bundle.get("description") or ""),
                "members": members,
                "total_bytes": total,
                "incomplete": excluded,
                "incomplete_reasons": reasons,
            }
        )

    return {"tiers": tiers_meta, "groups": groups, "bundles": bundles}


def selection_totals(
    catalog: dict[str, Any],
    selections: list[tuple[str, str]],
) -> tuple[int, list[str]]:
    """Sum verified sizes for ``(name, tier)`` picks; report any that are skipped."""
    excluded: list[str] = []
    total = 0
    for name, tier in selections:
        entry = find_entry_by_tier(catalog, name, tier)
        if entry is None or entry.get("status") != "verified":
            excluded.append(f"{name}:{tier}")
            continue
        total += int(entry.get("size") or 0)
    return total, excluded


def preflight_disk(
    paths: Paths,
    config: ArkConfig,
    selection_bytes: int,
) -> dict[str, Any]:
    """Disk math for a selection: what it needs, what survives, does it fit."""
    import shutil

    usage = shutil.disk_usage(paths.home)
    overhead = int(
        max(
            selection_bytes * config.library.disk_overhead_pct / 100.0,
            config.library.disk_min_margin_mb * 1024 * 1024,
        )
    )
    needed = selection_bytes + overhead
    free_after = usage.free - needed if selection_bytes else usage.free
    fits = selection_bytes == 0 or free_after >= 0
    blocked_reason = (
        None
        if fits
        else (
            f"Not enough free space: needs {needed} bytes (selection + temp/resume "
            f"margin), only {usage.free} free."
        )
    )
    return {
        "selection_bytes": selection_bytes,
        "overhead_bytes": overhead,
        "needed_bytes": needed,
        "free_bytes": usage.free,
        "free_after_bytes": max(0, free_after),
        "total_bytes": usage.total,
        "used_pct": round((needed / usage.total) * 100, 1) if usage.total else 0.0,
        "fits": bool(fits),
        "blocked_reason": blocked_reason,
        "hint": LATER_PHASE_HINT,
    }


# -- files ------------------------------------------------------------------


def sha256_file(path: Path, chunk_bytes: int = 1_048_576) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_bytes):
            digest.update(chunk)
    return digest.hexdigest()


def safe_manual_name(original: str) -> str:
    """Sanitize an uploaded filename to a safe basename (keeps the extension)."""
    base = Path(original.replace("\\", "/")).name
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    stem = _UNSAFE.sub("_", stem).strip(".") or "manual"
    ext = _UNSAFE.sub("", ext).lower()[:16]
    stem = stem[:180]
    return f"{stem}.{ext}" if ext else stem


def unique_dest(directory: Path, filename: str) -> Path:
    """Return directory/filename, adding -2, -3… if it already exists."""
    dest = directory / filename
    if not dest.exists():
        return dest
    stem, dot, ext = filename.rpartition(".")
    if not dot:
        stem, ext = filename, ""
    for n in range(2, 1000):
        candidate = directory / (f"{stem}-{n}.{ext}" if ext else f"{stem}-{n}")
        if not candidate.exists():
            return candidate
    raise LibraryError(f"too many files named {filename!r} in {directory}")


# -- library item rows ------------------------------------------------------


def set_item(db: Database, item_id: str | None, **fields: Any) -> None:
    """Update a library_items row (no-op when the id is unknown/absent)."""
    if not item_id:
        return
    with db.session() as session:
        item = session.get(LibraryItem, item_id)
        if item is None:
            return
        for key, value in fields.items():
            setattr(item, key, value)


def get_active_job(db: Database, job_id: str | None) -> Job | None:
    if not job_id:
        return None
    with db.session() as session:
        job = session.get(Job, job_id)
        if job is None or job.status not in _ACTIVE_JOB_STATUSES:
            return None
        return job


def job_brief(job: Job) -> dict[str, Any]:
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
        "created_at": job.created_at.isoformat() + "Z" if job.created_at else None,
        "started_at": job.started_at.isoformat() + "Z" if job.started_at else None,
    }


# -- download engine --------------------------------------------------------


def _truncate(path: Path) -> None:
    with path.open("wb"):
        pass


def _truncate_to(path: Path, size: int) -> None:
    if size == 0:
        _truncate(path)
    else:
        with path.open("r+b") as fh:
            fh.truncate(size)


async def download_zim(ctx: JobContext, paths: Paths, config: ArkConfig) -> dict[str, Any]:
    """Run one ``library.download`` job (resumable, hash-verified, cancellable)."""
    payload = ctx.payload
    urls = [u for u in payload.get("urls", []) if isinstance(u, str) and u]
    filename = payload.get("filename")
    size = payload.get("size")
    expected = str(payload.get("sha256") or "").lower()
    item_id = payload.get("item_id")
    name = payload.get("name")
    flavour = payload.get("flavour")

    if not urls:
        raise LibraryError("no download URLs in job payload")
    if not isinstance(filename, str) or Path(filename).name != filename or not filename:
        raise LibraryError(f"unsafe or missing filename in payload: {filename!r}")
    if not isinstance(size, int) or size <= 0:
        raise LibraryError(f"invalid size in payload: {size!r}")
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise LibraryError(f"invalid sha256 in payload: {expected!r}")

    part = paths.tmp_dir / f"{filename}.part"
    final = paths.library_dir / filename
    paths.tmp_dir.mkdir(parents=True, exist_ok=True)
    paths.library_dir.mkdir(parents=True, exist_ok=True)

    resume = part.stat().st_size if part.exists() else 0
    if resume > size:
        logger.warning(
            "partial file larger than expected size; discarding",
            extra={"file": str(part), "partial_bytes": resume, "size": size},
        )
        part.unlink(missing_ok=True)
        resume = 0

    logger.info(
        "download started",
        extra={
            "item_id": item_id,
            "entry": name,
            "flavour": flavour,
            "file": filename,
            "size": size,
            "sha256": expected,
            "mirrors": len(urls),
            "resume_bytes": resume,
        },
    )
    set_item(
        ctx.db,
        item_id,
        status="downloading",
        error=None,
        filename=filename,
        size=size,
        sha256=expected,
    )
    ctx.set_progress(
        progress=resume / size if size else 0.0,
        bytes_done=resume,
        bytes_total=size,
        message="starting" if resume == 0 else f"resuming at {resume} bytes",
    )

    if resume:
        digest = hashlib.sha256()
        with part.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1_048_576), b""):
                digest.update(chunk)
        logger.info("resuming download", extra={"file": str(part), "offset": resume})
    else:
        digest = hashlib.sha256()
        _truncate(part)

    done = resume
    next_milestone = _next_milestone(done, size)
    started = time.monotonic()
    last_report = started
    report_bytes = done
    url_index = 0
    chunk_bytes = config.library.download_chunk_bytes
    timeout = config.library.request_timeout

    while done < size:
        if ctx.cancelled():
            logger.info(
                "download paused by user",
                extra={"item_id": item_id, "bytes_done": done, "file": str(part)},
            )
            set_item(ctx.db, item_id, status="paused")
            ctx.set_progress(message="paused — partial download kept for resume")
            return {"cancelled": True, "bytes_done": done}

        if url_index >= len(urls):
            break
        url = urls[url_index]
        # Trusted on-disk state before this attempt (a failed attempt rolls
        # back to it so a resumed prefix is never lost or mixed).
        base = done
        base_digest = digest.copy()
        resp: Any = None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            if done > 0:
                req.add_header("Range", f"bytes={done}-")
            resp = await asyncio.to_thread(urllib.request.urlopen, req, timeout=timeout)
            status = getattr(resp, "status", 200)
            mode = "ab"
            if status == 206:
                content_range = resp.headers.get("Content-Range", "")
                match = re.match(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", content_range)
                if match:
                    start = int(match.group(1))
                    total = match.group(3)
                    if start != done:
                        raise LibraryError(
                            f"unexpected Content-Range {content_range!r} (wanted start {done})"
                        )
                    if total.isdigit() and int(total) != size:
                        raise LibraryError(f"mirror serves {total} bytes, catalog says {size}")
            elif status == 200:
                if done > 0:
                    logger.info(
                        "mirror ignored Range header; restarting download",
                        extra={"url": url, "discarded_bytes": done},
                    )
                    digest = hashlib.sha256()
                    done = 0
                    report_bytes = 0
                    next_milestone = _next_milestone(0, size)
                content_length = resp.headers.get("Content-Length")
                if content_length and content_length.isdigit() and int(content_length) != size:
                    raise LibraryError(
                        f"mirror serves {content_length} bytes, catalog says {size} ({url})"
                    )
                mode = "wb"
            else:
                raise LibraryError(f"HTTP {status} from {url}")
            base = done
            base_digest = digest.copy()

            with await asyncio.to_thread(part.open, mode) as fh:
                while True:
                    if ctx.cancelled():
                        await asyncio.to_thread(fh.flush)
                        logger.info(
                            "download paused by user",
                            extra={"item_id": item_id, "bytes_done": done, "file": str(part)},
                        )
                        set_item(ctx.db, item_id, status="paused")
                        ctx.set_progress(message="paused — partial download kept for resume")
                        return {"cancelled": True, "bytes_done": done}
                    chunk = await asyncio.to_thread(resp.read, chunk_bytes)
                    if not chunk:
                        break
                    await asyncio.to_thread(fh.write, chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if now - last_report >= 1.0 or done >= size:
                        dt = now - last_report
                        speed = (done - report_bytes) / dt if dt > 0 else None
                        ctx.set_progress(
                            progress=min(1.0, done / size),
                            bytes_done=done,
                            bytes_total=size,
                            speed_bps=speed,
                            message=f"{done} / {size} bytes",
                        )
                        last_report = now
                        report_bytes = done
                    percent = done * 100 // size
                    if next_milestone is not None and percent >= next_milestone:
                        logger.info(
                            "download progress",
                            extra={
                                "item_id": item_id,
                                "percent": next_milestone,
                                "bytes_done": done,
                                "bytes_total": size,
                            },
                        )
                        next_milestone = next((m for m in _MILESTONES if percent < m), None)
            if done < size:
                logger.warning(
                    "connection ended before the file was complete",
                    extra={"url": url, "bytes_done": done, "size": size},
                )
                digest = base_digest.copy()
                done = base
                report_bytes = done
                next_milestone = _next_milestone(done, size)
                _truncate_to(part, base)
        except Exception as exc:  # network/HTTP/OS problems → try next mirror
            logger.warning(
                "mirror failed",
                extra={"url": url, "error": f"{type(exc).__name__}: {exc}"},
            )
            digest = base_digest.copy()
            done = base
            report_bytes = done
            next_milestone = _next_milestone(done, size)
            _truncate_to(part, base)
        finally:
            if resp is not None:
                await asyncio.to_thread(resp.close)
        url_index += 1

    if done < size:
        raise LibraryError(
            f"all {len(urls)} mirror(s) failed before {size} bytes were downloaded "
            f"({done} bytes kept in data/tmp/{filename}.part for resume)"
        )

    actual = digest.hexdigest()
    if actual != expected:
        part.unlink(missing_ok=True)
        logger.error(
            "checksum mismatch — deleting partial file",
            extra={
                "item_id": item_id,
                "file": filename,
                "expected_sha256": expected,
                "actual_sha256": actual,
                "bytes": done,
            },
        )
        raise LibraryError(
            f"checksum mismatch for {filename}: expected {expected}, got {actual} "
            "(file deleted; retry the download)"
        )
    logger.info("checksum ok", extra={"file": filename, "sha256": actual, "bytes": done})

    os.replace(part, final)
    elapsed = round(time.monotonic() - started, 1)
    set_item(
        ctx.db,
        item_id,
        status="installed",
        error=None,
        size=done,
        sha256=actual,
        filename=filename,
        installed_at=utcnow(),
    )
    ctx.set_progress(progress=1.0, message=f"installed {filename}")
    logger.info(
        "download complete",
        extra={
            "item_id": item_id,
            "file": str(final),
            "bytes": done,
            "elapsed_s": elapsed,
        },
    )
    return {
        "item_id": item_id,
        "filename": filename,
        "size": done,
        "sha256": actual,
        "elapsed_s": elapsed,
    }


def make_download_handler(
    paths: Paths,
    config: ArkConfig,
    supervisor: Any = None,
) -> JobHandler:
    """Build the ``library.download`` handler bound to this installation."""

    async def handler(ctx: JobContext) -> dict[str, Any]:
        item_id = ctx.payload.get("item_id")
        try:
            result = await download_zim(ctx, paths, config)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            set_item(ctx.db, item_id, status="error", error=f"{type(exc).__name__}: {exc}")
            logger.error(
                "download failed",
                extra={
                    "item_id": item_id,
                    "file": ctx.payload.get("filename"),
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            raise
        if supervisor is not None and not result.get("cancelled"):
            await refresh_kiwix(supervisor, paths, config)
        return result

    return handler


# -- kiwix-serve sidecar ----------------------------------------------------


async def refresh_kiwix(supervisor: Any, paths: Paths, config: ArkConfig) -> None:
    """Restart kiwix-serve so it picks up the current data/library/*.zim set."""
    from ark.supervisor import build_kiwix_spec

    spec = build_kiwix_spec(paths, config)
    registered = "kiwix" in supervisor.specs
    if spec is None and not registered:
        return
    await supervisor.restart("kiwix", spec)
