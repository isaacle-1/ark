#!/usr/bin/env python3
"""UI design-pass screenshot harness.

Boots a throwaway ARK server (temp ARK_HOME, real catalog + migrations, built
frontend symlinked in) and captures Dashboard, Library, Logs, Login and
Settings in every theme x viewport combination, so the redesign can be
reviewed as screenshots and iterated.

Usage (called by `make screenshots`, or directly):
    python scripts/screenshots.py [run-dir]       # default .screenshots/run1

Artifacts land under <run-dir>/<theme>/<viewport>/<page>.png. The final
approved set is copied into docs/screenshots/ afterwards.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

THEMES = ["dark", "light", "rednight"]
VIEWPORTS = {
    "desktop": {"width": 1280, "height": 800},
    "phone": {"width": 390, "height": 844},
}
PAGES = [
    ("Dashboard", "/"),
    ("Library", "/library"),
    ("Logs", "/logs"),
    ("Settings", "/settings"),
]

BASE = "http://127.0.0.1:8138"
ROOT = Path(__file__).resolve().parents[1]


class _GuardedProc:
    """Kill the throwaway server on interpreter exit (atexit) so no orphan
    keeps the port bound for the next run."""

    def __init__(self, proc: subprocess.Popen, log: object) -> None:
        self.proc = proc
        self.log = log
        import atexit

        atexit.register(self.cleanup)

    def cleanup(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        try:
            self.log.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def spin_server() -> Path:
    import tempfile

    py = shutil.which("python") or sys.executable
    venv = ROOT / ".venv" / "bin" / "python"
    if venv.is_file():
        py = str(venv)
    home = Path(tempfile.mkdtemp(prefix="ark-shots-", dir="/tmp"))
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
    proc = subprocess.Popen(
        [py, "-m", "ark", "serve", "--host", "127.0.0.1", "--port", "8138"],
        env=env,
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    _guarded_proc = _GuardedProc(proc, log)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/healthz", timeout=1) as r:
                if r.status == 200:
                    return home
        except Exception:
            pass
        time.sleep(0.3)
    _guarded_proc.cleanup()
    raise SystemExit("server did not become healthy:\n" + (home / "server.log").read_text()[-1500:])


def admin_creds(home: Path) -> tuple[str, str]:
    text = (home / "data" / "config" / "initial-credentials.txt").read_text(encoding="utf-8")
    creds = {}
    for ln in text.splitlines():
        if ln.startswith("  username:") or ln.startswith("  password:"):
            key, _, value = ln.strip().partition(":")
            creds[key] = value.strip()
    return creds["username"], creds["password"]


def main(argv: list[str]) -> int:
    run_dir = Path(argv[0] if argv else ".screenshots/run1")
    home = spin_server()
    user, password = admin_creds(home)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for theme in THEMES:
            for size_name, size in VIEWPORTS.items():
                out = run_dir / theme / size_name
                out.mkdir(parents=True, exist_ok=True)
                page = browser.new_page(viewport=size, device_scale_factor=2)
                page.add_init_script(f"window.localStorage.setItem('ark-theme', '{theme}')")

                # Login (logged out).
                page.goto(BASE + "/login", wait_until="networkidle")
                page.screenshot(path=str(out / "login.png"))

                # Sign in.
                page.fill("input[placeholder='username']", user)
                page.fill("input[placeholder='password']", password)
                page.click("button[data-testid=login-btn]")
                page.wait_for_url(lambda u: u.rstrip("/") == BASE, timeout=10_000)

                for label, path in PAGES:
                    page.goto(BASE + path, wait_until="networkidle")
                    page.screenshot(path=str(out / f"{label.lower()}.png"))

                page.close()
                print(f"  {theme}/{size_name} done")
        browser.close()
    print(f"Screenshots → {run_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
