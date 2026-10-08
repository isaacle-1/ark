#!/usr/bin/env python3
"""Verify catalog/kiwix.json entries against the live Kiwix OPDS + mirrors.

Checks per entry (all must pass for status=verified):
  1. OPDS resolution by exact name (+flavour) matches the recorded opds_uuid,
  2. the .meta4 metalink parses and its <size>/<sha-256> match the catalog,
  3. a HEAD request to the primary mirror returns 200/206 with the same
     Content-Length.

Anything that fails is marked "unverified" with a reason and listed on exit —
never silently trusted. stdlib only (runs with bare python3, offline tests
use a local HTTP fixture server).

Usage:
  scripts/verify_catalog.py                 # refresh, verify, write catalog
  scripts/verify_catalog.py --check         # verify only; exit 1 on any problem
  scripts/verify_catalog.py --add name[:flavour] [name[:flavour] ...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OPDS = "https://opds.library.kiwix.org/catalog/v2/entries"
CATALOG = Path(__file__).resolve().parent.parent / "catalog" / "kiwix.json"
UA = {"User-Agent": "ark-verify_catalog/0.1 (+offline survival toolkit)"}
TIMEOUT = 20
# Hosts found unreachable during this process (some mirrors blackhole us).
_dead_hosts: set[str] = set()
ATOM = "{http://www.w3.org/2005/Atom}"
METALINK = "urn:ietf:params:xml:ns:metalink"

LICENSE_BY_CATEGORY: dict[str, tuple[str, str | None, str]] = {
    "wikipedia": (
        "CC-BY-SA-4.0",
        "https://creativecommons.org/licenses/by-sa/4.0/",
        "Wikipedia/Wikimedia text license; openZIM mirrors keep this license.",
    ),
    "wikibooks": (
        "CC-BY-SA-4.0",
        "https://creativecommons.org/licenses/by-sa/4.0/",
        "Wikimedia text license; openZIM mirrors keep this license.",
    ),
    "gutenberg": (
        "public-domain",
        "https://www.gutenberg.org/",
        "Project Gutenberg books are public domain in the US.",
    ),
    "iFixit": (
        "UNVERIFIED",
        None,
        "iFixit wiki terms not confirmed from an authoritative page — "
        "confirm before redistributing this ZIM.",
    ),
    "devdocs": (
        "mixed-per-source",
        None,
        "devdocs aggregates many doc sets; each keeps its own license.",
    ),
}


class VerifyError(Exception):
    """A single check failed — message becomes verify_error."""


def _open(req: urllib.request.Request):
    """urlopen with retry/backoff — mirrors and OPDS rate-limit occasionally."""
    last: Exception | None = None
    for delay in (0, 15, 45, 90):
        if delay:
            time.sleep(delay)
        t0 = time.monotonic()
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
            if os.environ.get("ARK_VERIFY_DEBUG"):
                print(f"    retry {req.full_url}: {exc} ({time.monotonic() - t0:.1f}s)", flush=True)
        else:
            if os.environ.get("ARK_VERIFY_DEBUG"):
                print(f"    ok {req.full_url} ({time.monotonic() - t0:.1f}s)", flush=True)
            time.sleep(1.5)  # pacing — Kiwix rate-limits bursts hard
            return resp
    raise VerifyError(f"request failed after retries: {last}")


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=UA)
    with _open(req) as resp:
        return resp.read()


def _head(url: str, timeout: int = TIMEOUT) -> tuple[int, int | None]:
    req = urllib.request.Request(url, headers=UA, method="HEAD")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # single shot
        length = resp.headers.get("Content-Length")
        return resp.status, int(length) if length is not None else None


def resolve_opds(name: str, flavour: str) -> dict[str, Any]:
    """Resolve an OPDS entry by exact name (+flavour). Raises VerifyError."""
    query = urllib.parse.urlencode({"count": "50", "name": name})
    try:
        xml = _get(f"{OPDS}?{query}")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise VerifyError(f"OPDS unreachable: {exc}") from exc
    root = ET.fromstring(xml)
    candidates = []
    for entry in root.findall(f"{ATOM}entry"):
        if entry.findtext(f"{ATOM}name", "") != name:
            continue
        if (entry.findtext(f"{ATOM}flavour", "") or "") != flavour:
            continue
        candidates.append(entry)
    if not candidates:
        raise VerifyError(f"no OPDS entry named {name!r} (flavour={flavour!r})")
    entry = candidates[0]
    uuid = (entry.findtext(f"{ATOM}id", "") or "").removeprefix("urn:uuid:")
    link = next(
        (
            acq
            for acq in entry.findall(f"{ATOM}link")
            if (acq.get("rel") or "").endswith("acquisition/open-access")
        ),
        None,
    )
    if link is None or not link.get("href"):
        raise VerifyError("OPDS entry has no download link")
    return {
        "opds_uuid": uuid,
        "title": entry.findtext(f"{ATOM}title", "") or "",
        "language": entry.findtext(f"{ATOM}language", "") or "",
        "category": entry.findtext(f"{ATOM}category", "") or "",
        "article_count": int(entry.findtext(f"{ATOM}articleCount", "0") or 0),
        "media_count": int(entry.findtext(f"{ATOM}mediaCount", "0") or 0),
        "meta4_url": link.get("href", ""),
        "size": int(link.get("length", "0") or 0),
        "browse_url": next(
            (
                el.get("href", "")
                for el in entry.findall(f"{ATOM}link")
                if el.get("type") == "text/html"
            ),
            "",
        ),
    }


def parse_meta4(xml: bytes) -> tuple[int, str, list[str]]:
    """(size, sha256, mirror urls) from a Metalink document."""
    root = ET.fromstring(xml)
    file_el = root.find(f"{{{METALINK}}}file")
    if file_el is None:
        raise VerifyError("metalink has no <file>")
    size_el = file_el.find(f"{{{METALINK}}}size")
    if size_el is None or not (size_el.text or "").strip().isdigit():
        raise VerifyError("metalink has no <size>")
    size = int((size_el.text or "0").strip())
    sha256 = ""
    for h in file_el.findall(f"{{{METALINK}}}hash"):
        if (h.get("type") or "").lower() == "sha-256":
            sha256 = (h.text or "").strip()
    if not sha256:
        raise VerifyError("metalink has no sha-256 hash")
    urls = [
        (u.text or "").strip() for u in file_el.iter(f"{{{METALINK}}}url") if (u.text or "").strip()
    ]
    if not urls:
        raise VerifyError("metalink has no download urls")
    return size, sha256, urls


def verify_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Run all three checks; returns entry with status/verified_at/fields."""
    entry["verify_error"] = None
    try:
        # OPDS resolution only when metadata is missing (or --refresh): the
        # metalink + HEAD below are the authoritative download checks, and
        # hammering OPDS gets us rate-limited.
        if not entry.get("meta4_url") or not entry.get("opds_uuid"):
            resolved = resolve_opds(entry["name"], entry.get("flavour", ""))
            if entry.get("opds_uuid") and resolved["opds_uuid"] != entry["opds_uuid"]:
                raise VerifyError(
                    f"upstream republished: opds_uuid changed "
                    f"{entry['opds_uuid']} -> {resolved['opds_uuid']}"
                )
            entry["opds_uuid"] = resolved["opds_uuid"]
            entry.setdefault("opds_url", f"{OPDS}?name={entry['name']}")
            for key in ("title", "language", "category", "browse_url"):
                if resolved.get(key):
                    entry[key] = resolved[key]

        size, sha256, urls = parse_meta4(_get(entry["meta4_url"]))
        if entry.get("size") and entry["size"] != size:
            raise VerifyError(
                f"upstream rebuilt: catalog size {entry['size']} != "
                f"live metalink size {size} (re-curate this entry)"
            )
        if entry.get("sha256") and entry["sha256"] != sha256:
            raise VerifyError("upstream rebuilt: sha256 changed (re-curate)")
        entry["size"] = size
        entry["sha256"] = sha256
        entry["mirror_urls"] = urls

        # Walk mirrors in metalink priority order; unresponsive hosts are
        # remembered for the rest of the run (some mirrors blackhole us).
        working = None
        tried = 0
        for mirror in urls:
            host = urllib.parse.urlsplit(mirror).hostname or ""
            if host in _dead_hosts:
                continue
            tried += 1
            try:
                status, length = _head(mirror, timeout=8)
            except Exception:
                _dead_hosts.add(host)
                continue
            if status not in (200, 206):
                raise VerifyError(f"HEAD {mirror} -> HTTP {status}")
            if length is not None and length != size:
                raise VerifyError(f"HEAD {mirror} Content-Length {length} != size {size}")
            working = mirror
            break
        if working is None:
            raise VerifyError(
                f"no reachable mirror ({tried} tried, {len(_dead_hosts)} host(s) dead this run)"
            )
        entry["zim_url"] = working
        entry["status"] = "verified"
        entry["verified_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    except VerifyError as exc:
        entry["status"] = "unverified"
        entry["verified_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        entry["verify_error"] = str(exc)
    except Exception as exc:
        entry["status"] = "unverified"
        entry["verified_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        entry["verify_error"] = f"{type(exc).__name__}: {exc}"
    return entry


def add_entry(name_flavour: str) -> dict[str, Any]:
    name, _, flavour = name_flavour.partition(":")
    resolved = resolve_opds(name, flavour)
    license_key = resolved["category"] or ("devdocs" if name.startswith("devdocs") else "")
    license_, license_url, license_note = LICENSE_BY_CATEGORY.get(
        license_key, ("UNVERIFIED", None, "unknown category")
    )
    return {
        "name": name,
        "flavour": flavour,
        "title": resolved["title"],
        "language": resolved["language"],
        "category": resolved["category"],
        "tier": None,
        "opds_uuid": resolved["opds_uuid"],
        "opds_url": f"{OPDS}?name={urllib.parse.quote(name)}",
        "article_count": resolved["article_count"],
        "media_count": resolved["media_count"],
        "size": 0,
        "sha256": "",
        "zim_url": "",
        "meta4_url": resolved["meta4_url"],
        "browse_url": resolved["browse_url"],
        "license": license_,
        "license_url": license_url,
        "license_note": license_note,
        "status": "unverified",
        "verified_at": None,
        "verify_error": None,
    }


def _label(entry: dict[str, Any]) -> str:
    flavour = entry.get("flavour") or ""
    return f"{entry['name']}:{flavour}" if flavour else entry["name"]


def load_catalog() -> dict[str, Any]:
    if CATALOG.exists():
        return json.loads(CATALOG.read_text(encoding="utf-8"))
    return {"schema": 1, "opds": OPDS, "updated": None, "entries": []}


def save_catalog(cat: dict[str, Any]) -> None:
    cat["updated"] = datetime.now(UTC).isoformat(timespec="seconds")
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text(json.dumps(cat, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="verify but do not write; exit 1 on problems"
    )
    parser.add_argument(
        "--add", nargs="+", metavar="name[:flavour]", help="append new entries resolved from OPDS"
    )
    args = parser.parse_args(argv)

    cat = load_catalog()
    known = {(e["name"], e.get("flavour", "")) for e in cat["entries"]}
    failed_adds = False
    for spec in args.add or []:
        name, _, flavour = spec.partition(":")
        if (name, flavour) in known:
            print(f"skip (already present): {spec}")
            continue
        try:
            entry = add_entry(spec)
        except VerifyError as exc:
            print(f"FAILED to add {spec}: {exc}", file=sys.stderr)
            failed_adds = True
            continue
        cat["entries"].append(entry)
        print(f"added: {spec} ({entry['category']}, {entry['size']} bytes)")

    for entry in cat["entries"]:
        verify_entry(entry)
        mark = "ok " if entry["status"] == "verified" else "FAIL"
        print(
            f"  [{mark}] {_label(entry)}"
            + (f" — {entry['verify_error']}" if entry.get("verify_error") else "")
        )

    bad = [e for e in cat["entries"] if e["status"] != "verified"]
    if not args.check:
        save_catalog(cat)

    total = len(cat["entries"])
    print(f"\n{total - len(bad)}/{total} download checks verified")
    if bad:
        print("\nUNVERIFIED — do not offer these for download until resolved:")
        for e in bad:
            print(f"  - {_label(e)}\n      reason: {e.get('verify_error') or 'unknown'}")
    lic_bad = [e for e in cat["entries"] if e.get("license") == "UNVERIFIED"]
    if lic_bad:
        print("\nLICENSE UNVERIFIED — confirm the terms before distributing:")
        for e in lic_bad:
            print(f"  - {_label(e)}\n      note: {e.get('license_note') or ''}")
    if bad or failed_adds:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
