"""Pytest isolation: every test runs in a throwaway ARK_HOME.

We never touch the real /opt data. The env var is set before the app factory
runs, and ark.paths resolves ARK_HOME at call time (never at import).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("ARK_SKIP_NODE", "1")

_REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def ark_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, ArkConfig]:  # noqa: F821
    """Point ARK_HOME at a temp dir and return (home_dir, config)."""
    monkeypatch.setenv("ARK_HOME", str(tmp_path))
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("ARK_ADMIN_PASSWORD", raising=False)

    # Alembic scripts live in the repo; give the temp ARK_HOME its own copy
    # so in-process migrations (run_migrations -> ARK_HOME/migrations) work.
    shutil.copytree(
        _REPO / "migrations", tmp_path / "migrations", ignore=shutil.ignore_patterns("__pycache__")
    )
    # Same for the Kiwix catalog (library endpoints/health read ARK_HOME/catalog).
    (tmp_path / "catalog").mkdir(exist_ok=True)
    shutil.copy2(_REPO / "catalog" / "kiwix.json", tmp_path / "catalog" / "kiwix.json")

    from ark.config import ArkConfig
    from ark.paths import Paths, ensure_data_dirs

    ensure_data_dirs(Paths.for_home(tmp_path))
    config = ArkConfig(
        server={"host": "127.0.0.1", "port": 8081},
        logging={"level": "DEBUG", "console": False},
        auth={"mode": "open"},
        jobs={"poll_interval": 0.05},
    )
    return tmp_path, config


@pytest.fixture()
def home(ark_env: tuple[Path, ArkConfig]) -> Path:  # noqa: F821
    return ark_env[0]


@pytest.fixture()
def paths(home: Path):
    from ark.paths import Paths

    return Paths.for_home(home)


@pytest.fixture()
def config(ark_env: tuple[Path, ArkConfig]) -> ArkConfig:  # noqa: F821
    return ark_env[1]


@pytest.fixture()
def app(paths: Path, config: ArkConfig):  # noqa: F821
    from ark.server import create_app

    return create_app(config, paths)


@pytest.fixture()
def client(app):
    """TestClient with lifespan running (migrations, admin, worker, supervisor)."""
    from fastapi.testclient import TestClient

    from ark.auth import reset_rate_limiter

    reset_rate_limiter()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def admin_creds(client, paths: Path) -> tuple[str, str]:
    """Return the bootstrap admin username/password written at lifespan start.

    Depends on ``client`` so the lifespan (and bootstrap) has already run.
    """
    creds = paths.config_dir / "initial-credentials.txt"
    lines = creds.read_text(encoding="utf-8").splitlines()
    user = next(ln.split(":", 1)[1].strip() for ln in lines if ln.startswith("  username:"))
    password = next(ln.split(":", 1)[1].strip() for ln in lines if ln.startswith("  password:"))
    return user, password


@pytest.fixture()
def admin_client(client, admin_creds):
    """client already logged-in as admin (posts login + keeps cookie)."""
    user, password = admin_creds
    resp = client.post("/api/auth/login", json={"username": user, "password": password})
    assert resp.status_code == 200, resp.text
    return client
