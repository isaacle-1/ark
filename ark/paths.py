"""Filesystem layout for ARK.

Everything ARK writes lives under ``ARK_HOME`` (default: the directory that
contains this repository). All other modules derive their paths from here so
the whole installation stays portable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Data subfolders that must exist inside data/ (created by ensure_data_dirs).
DATA_SUBDIRS: tuple[str, ...] = (
    "config",
    "db",
    "logs",
    "library",
    "manuals",
    "videos",
    "maps",
    "notes",
    "docs",
    "radio",
    "translate",
    "rag",
    "uploads",
    "tmp",
    "cache",
    "backups",
    "home",  # $HOME for the ark service user (must stay inside ARK_HOME)
)


def get_home() -> Path:
    """Return the ARK_HOME root, re-reading the environment each call.

    Tests point ``ARK_HOME`` at a temp directory before importing/creating the
    app, so we must not cache this at import time.
    """
    env = os.environ.get("ARK_HOME")
    if env:
        return Path(env).expanduser().resolve()
    # Default: the repository root (the parent of the ark/ package).
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Paths:
    """Derived paths for one ARK_HOME."""

    home: Path

    @classmethod
    def for_home(cls, home: Path) -> Paths:
        return cls(home=home.resolve())

    @classmethod
    def current(cls) -> Paths:
        return cls.for_home(get_home())

    @property
    def data(self) -> Path:
        return self.home / "data"

    @property
    def config_dir(self) -> Path:
        return self.data / "config"

    @property
    def config_file(self) -> Path:
        return self.config_dir / "ark.toml"

    @property
    def db_dir(self) -> Path:
        return self.data / "db"

    @property
    def db_file(self) -> Path:
        return self.db_dir / "ark.db"

    @property
    def logs_dir(self) -> Path:
        return self.data / "logs"

    @property
    def tmp_dir(self) -> Path:
        return self.data / "tmp"

    @property
    def cache_dir(self) -> Path:
        return self.data / "cache"

    @property
    def backups_dir(self) -> Path:
        return self.data / "backups"

    @property
    def uploads_dir(self) -> Path:
        return self.data / "uploads"

    @property
    def frontend_dist(self) -> Path:
        return self.home / "ark" / "frontend" / "dist"

    @property
    def alembic_ini(self) -> Path:
        return self.home / "alembic.ini"

    def data_subdir(self, name: str) -> Path:
        return self.data / name


def ensure_data_dirs(paths: Paths | None = None) -> None:
    """Create data/ and all known subfolders if missing (idempotent)."""
    p = paths or Paths.current()
    p.data.mkdir(parents=True, exist_ok=True)
    for name in DATA_SUBDIRS:
        p.data_subdir(name).mkdir(parents=True, exist_ok=True)
