#!/usr/bin/env python3
"""CI gate — fail if the built frontend references any external URL.

Scans every text file under the build directory for ``http(s)://`` URLs
(and protocol-relative ``//host/…`` in HTML/SVG, where browsers will happily
fetch) and flags hosts outside a tiny allowlist of protocol-independent
identifiers or provably-unfetched documentation strings. Offline-first rule #1.

Usage: python scripts/check_no_external_urls.py <build-dir>
Exit 0 = clean, 1 = violations found (printed).
"""

import re
import sys
from pathlib import Path

# Hosts that are NOT an external fetchable resource:
# - localhost/loopback: runtime self-references (dev proxies, callbacks)
# - www.w3.org + schemas: XML/SVG namespace identifiers (never loaded)
# - reactjs.org: React's minified invariant message text ("visit …/error-decoder")
# - tailwindcss.com: a comment inside the generated CSS
# Both of the above are plain strings in the bundle; nothing ever fetches them.
ALLOWED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "[::1]",
    "www.w3.org",
    "w3.org",
    "schemas.microsoft.com",
    "json-schema.org",
    "xmlns.com",
    "purl.org",
    "reactjs.org",
    "tailwindcss.com",
}

ABS_URL_RE = re.compile(r"(?:https?://)([^/\"'()\s<>\\]+)")
PROTO_REL_RE = re.compile(r"//([A-Za-z0-9][A-Za-z0-9.-]+(?::\d+)?)(?=[/\"'\s]|$)")

# Some files are legitimately binary-ish (fonts/images) — only scan text-ish
# extensions; binary blobs would give false positives from byte sequences.
TEXT_EXTS = {".html", ".js", ".css", ".json", ".svg", ".webmanifest", ".xml", ".txt"}
# Protocol-relative URLs only matter where a browser parses them as a URL.
PROTO_REL_EXTS = {".html", ".svg"}


def _host_of(match_host: str) -> str:
    host = match_host
    if host.startswith("[") and "]" in host:
        host = host.split("]")[0] + "]"
    elif ":" in host:
        host = host.split(":", 1)[0]
    return host.lower()


def scan(build_dir: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(build_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found: list[tuple[int, str]] = []
        for m in ABS_URL_RE.finditer(text):
            found.append((m.start(), _host_of(m.group(1))))
        if path.suffix.lower() in PROTO_REL_EXTS:
            for m in PROTO_REL_RE.finditer(text):
                found.append((m.start(), _host_of(m.group(1))))
        for pos, host in found:
            if host not in ALLOWED_HOSTS:
                line_no = text.count("\n", 0, pos) + 1
                violations.append(f"{path}:{line_no}: external URL host {host!r}")
    return violations


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: check_no_external_urls.py <frontend-dist-dir>")
        return 2
    dist = Path(args[0])
    if not dist.is_dir():
        print(f"build dir not found: {dist} (run the frontend build first)")
        return 2
    violations = scan(dist)
    if not violations:
        print(f"no-external-URL check PASS ({dist})")
        return 0
    print(f"no-external-URL check FAIL — {len(violations)} external URL(s):")
    for v in violations[:50]:
        print("  " + v)
    print("Offline-first principle: the frontend must not load anything from the internet.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
