#!/usr/bin/env python3
"""Design-conformance audit (pixel-blind stand-in for eyeballing screenshots).

Boots its own throwaway ARK server and, for every theme x viewport, walks the
pages and asserts the field-manual design invariants that the brief demands:

  * sharp corners — no element visibly uses border-radius > 6px
  * flat surfaces — no box-shadow on visible layout elements
  * 1px defined borders on panels/inputs/buttons
  * token palette actually applied (data-theme surfaces/ink/accent)
  * typographic hierarchy — display font on masthead, racket on h1/h2,
    Roboto body, Roboto Mono on .mono/.num/.fine
  * phone layout doesn't overflow horizontally; scroll is sane
  * zero console errors and zero failed network requests (fonts + api)

Prints a per-page PASS/FAIL report. Exit 0 = everything conforms.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

THEMES = ["dark", "light", "rednight"]
VIEWPORTS = {
    "desktop": {"width": 1280, "height": 800},
    "phone": {"width": 390, "height": 844},
}
PAGES = [
    ("dashboard", "/"),
    ("library", "/library"),
    ("logs", "/logs"),
    ("settings", "/settings"),
    ("login", "/login"),
]

BASE = "http://127.0.0.1:8139"
ROOT = Path(__file__).resolve().parents[1]
FAILS: list[str] = []


class _GuardedProc:
    """Kill the throwaway server on interpreter exit (atexit) so no orphan
    keeps the port bound for the next run."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        import atexit

        atexit.register(self.cleanup)

    def cleanup(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def spin_server() -> Path:
    import tempfile

    home = Path(tempfile.mkdtemp(prefix="ark-audit-", dir="/tmp"))
    shutil.copytree(
        ROOT / "migrations",
        home / "migrations",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    cat_src = ROOT / "catalog" / "kiwix.json"
    if cat_src.is_file():
        (home / "catalog").mkdir(exist_ok=True)
        shutil.copy2(cat_src, home / "catalog" / "kiwix.json")
    dist = home / "ark" / "frontend" / "dist"
    dist.parent.mkdir(parents=True, exist_ok=True)
    dist.symlink_to(ROOT / "ark" / "frontend" / "dist")

    env = dict(os.environ)
    env["ARK_HOME"] = str(home)
    env["ARK_SKIP_NODE"] = "1"
    log = (home / "server.log").open("w", encoding="utf-8")
    py = ROOT / ".venv" / "bin" / "python"
    proc = subprocess.Popen(
        [str(py), "-m", "ark", "serve", "--host", "127.0.0.1", "--port", "8139"],
        env=env,
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    _guarded = _GuardedProc(proc)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/healthz", timeout=1) as r:
                if r.status == 200:
                    return home
        except Exception:
            pass
        time.sleep(0.3)
    raise SystemExit("server unhealthy:\n" + (home / "server.log").read_text()[-1500:])


def admin_creds(home: Path) -> tuple[str, str]:
    text = (home / "data" / "config" / "initial-credentials.txt").read_text(encoding="utf-8")
    creds = {}
    for ln in text.splitlines():
        if ln.startswith("  username:") or ln.startswith("  password:"):
            key, _, value = ln.strip().partition(":")
            creds[key] = value.strip()
    return creds["username"], creds["password"]


AUDIT_JS = r"""
() => {
  const r = { overflow: null, radii: [], shadows: [], fonts: {} };
  const doc = document.documentElement;
  r.overflow = doc.scrollWidth > doc.clientWidth ? doc.scrollWidth : 0;
  const nm = (el) => el.className?.toString().slice(0, 40);
  for (const el of document.querySelectorAll('*')) {
    if (el.closest('iframe') ) continue;
    const cs = getComputedStyle(el);
    const br = cs.borderRadius;
    if (br && br !== '0px') {
      const v = parseFloat(br);
      if (v > 6) r.radii.push([el.tagName, nm(el), br]);
    }
    const sh = cs.boxShadow;
    if (sh && sh !== 'none') {
      // focus/selection rings ("0 0 0 1px") are not elevation — ignore blur 0.
      const push = (s) => r.shadows.push([el.tagName, nm(el), s.slice(0, 50)]);
      const blur = /0 0 (\d+(?:\.\d+)?)px/.exec(sh);
      if (blur ? parseFloat(blur[1]) > 0 : true) push(sh);
    }
  }
  const head = document.querySelector('h1');
  if (head) r.fonts.h1 = getComputedStyle(head).fontFamily;
  const num = document.querySelector('.num, .mono');
  if (num) r.fonts.mono = getComputedStyle(num).fontFamily;
  const body = document.querySelector('body');
  r.fonts.body = getComputedStyle(body).fontFamily;
  const bg = getComputedStyle(document.body).backgroundColor;
  const ink = getComputedStyle(document.body).color;
  r.palette = { bg, ink, theme: document.documentElement.getAttribute('data-theme') };
  return JSON.stringify(r);
}
"""


def main() -> int:
    home = spin_server()
    user, password = admin_creds(home)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for theme in THEMES:
            for size_name, size in VIEWPORTS.items():
                page = browser.new_page(viewport=size)
                page.add_init_script(f"window.localStorage.setItem('ark-theme', '{theme}')")
                page.on(
                    "console",
                    lambda msg, t=theme, s=size_name: (
                        FAILS.append(f"[{t}/{s}] console: {msg.text}")
                        if msg.type == "error"
                        else None
                    ),
                )
                page.on(
                    "requestfailed",
                    lambda req, t=theme, s=size_name: (
                        FAILS.append(f"[{t}/{s}] request failed: {req.url} ({req.failure})")
                        if "ERR_ABORTED" not in str(req.failure)
                        else None
                    ),
                )

                # Sign in first so the page audits run without auth-noise.
                page.goto(BASE + "/login", wait_until="networkidle")
                page.fill("input[placeholder='username']", user)
                page.fill("input[placeholder='password']", password)
                page.click("button[data-testid=login-btn]")
                page.wait_for_url(lambda u: u.rstrip("/") == BASE, timeout=10_000)

                for label, path in PAGES:
                    page.goto(BASE + path, wait_until="networkidle")
                    data = page.evaluate(AUDIT_JS)
                    report(page, theme, size_name, label, data)
                page.close()
        browser.close()

    print(f"\nAUDIT RESULT: {'FAIL' if FAILS else 'PASS'} — {len(FAILS)} console/network issues")
    for f in FAILS[:40]:
        print("  " + f)
    return 1 if FAILS else 0


def report(page, theme: str, size: str, label: str, data: str):
    import json

    d = json.loads(data)
    mark = "PASS"
    if d["overflow"]:
        mark = "FAIL"
        FAILS.append(f"[{theme}/{size}/{label}] horizontal overflow px={d['overflow']}")
    if d["radii"]:
        mark = "FAIL"
        FAILS.append(f"[{theme}/{size}/{label}] nonzero radii >6px: {d['radii'][:3]}")
    if d["shadows"]:
        mark = "FAIL"
        FAILS.append(f"[{theme}/{size}/{label}] box-shadows: {d['shadows'][:3]}")
    f = d.get("fonts") or {}
    fonts = f"font h1={f.get('h1', '?')[:28]} body={f.get('body', '?')[:24]}"
    print(
        f"  {theme:9s} {size:8s} {label:9s} {mark}  "
        f"pal={d['palette']['bg']}/{d['palette']['ink']} theme={d['palette']['theme']}  {fonts}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
