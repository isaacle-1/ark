"""E2E smoke test: real server + real browser.

Boots `python -m ark serve` against a temp ARK_HOME (with the built frontend
symlinked in), then drives the dashboard, login and the Logs page live tail.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def server_proc(tmp_path_factory: pytest.TempPathFactory):
    import shutil

    home = tmp_path_factory.mktemp("ark-e2e")
    # migrations must live inside ARK_HOME (alembic script_location).
    shutil.copytree(
        Path(os.getcwd()) / "migrations",
        home / "migrations",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    dist = home / "ark" / "frontend" / "dist"
    dist.parent.mkdir(parents=True, exist_ok=True)
    real_dist = Path(os.getcwd()) / "ark" / "frontend" / "dist"
    if not real_dist.is_dir():
        pytest.skip("frontend not built — run `make frontend` first")
    dist.symlink_to(real_dist)

    env = dict(os.environ)
    env["ARK_HOME"] = str(home)
    env["ARK_SKIP_NODE"] = "1"
    server_log = (home / "server.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "ark", "serve", "--host", "127.0.0.1", "--port", "8137"],
        env=env,
        cwd=os.getcwd(),
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )

    import urllib.request

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8137/healthz", timeout=1) as r:
                if r.status == 200:
                    break
        except Exception:
            pass
        time.sleep(0.3)
    else:
        proc.kill()
        server_log.close()
        tail = (home / "server.log").read_text(encoding="utf-8")[-1500:]
        pytest.fail(f"server did not become healthy\n--- server.log tail ---\n{tail}")

    yield home
    proc.terminate()
    proc.wait(timeout=10)
    server_log.close()


def _admin_creds(home: Path) -> tuple[str, str]:
    text = (home / "data" / "config" / "initial-credentials.txt").read_text(encoding="utf-8")
    user = next(
        ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("  username:")
    )
    password = next(
        ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("  password:")
    )
    return user, password


def test_dashboard_login_logs_live_tail(server_proc):
    from playwright.sync_api import sync_playwright

    home = server_proc
    base = "http://127.0.0.1:8137"
    user, password = _admin_creds(home)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        # Dashboard (auth open in phase 0 → visible without login).
        page.goto(base + "/", wait_until="networkidle")
        assert page.locator("text=Dashboard").first.is_visible()

        # Logs page requires admin → redirected to login.
        page.goto(base + "/logs", wait_until="networkidle")
        assert page.locator("input[placeholder='username']").is_visible()

        page.fill("input[placeholder='username']", user)
        page.fill("input[placeholder='password']", password)
        page.click("button[data-testid=login-btn]")
        from urllib.parse import urlparse

        page.wait_for_url(lambda u: urlparse(u).path == "/", timeout=10_000)
        page.goto(base + "/logs", wait_until="networkidle")

        assert page.locator("text=Logs").first.is_visible()
        # The live stream badge should reach "live" at least once.
        page.wait_for_function(
            "() => document.querySelector('[data-testid=log-status]')"
            "?.textContent.trim() === 'live'",
            timeout=15_000,
        )

        # Pause/resume controls exist and rows render (server logs lines always).
        page.click("[data-testid=pause-btn]")
        assert page.locator("[data-testid=log-status]").inner_text().lower() == "paused"
        page.click("[data-testid=pause-btn]")

        page.screenshot(path=str(home / "logs-page.png"))
        browser.close()
