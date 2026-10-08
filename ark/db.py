"""SQLite database: engine with WAL pragmas, session management, migrations."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger("ark.db")


def make_engine(db_file: Path, *, echo: bool = False) -> Engine:
    """Create a SQLite engine with WAL + sane concurrency pragmas."""
    db_file.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_file}",
        echo=echo,
        future=True,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn: object, _record: object) -> None:
        cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return engine


class Database:
    """Engine + session factory bound to one SQLite file."""

    def __init__(self, db_file: Path, *, echo: bool = False) -> None:
        self.db_file = db_file
        self.engine = make_engine(db_file, echo=echo)
        self.session_factory = sessionmaker(
            self.engine, expire_on_commit=False, autoflush=False, future=True
        )

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Transactional session: commits on success, rolls back on error."""
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()


def run_migrations(paths: object) -> None:
    """Apply all pending Alembic migrations (idempotent, in-process).

    ``paths`` is an :class:`ark.paths.Paths`. Typed loosely to avoid a circular
    import at module load; callers always pass a real Paths.
    """
    from alembic import command
    from alembic.config import Config

    from ark.paths import Paths as _Paths

    assert isinstance(paths, _Paths)
    cfg = Config()  # no ini file: env.py derives everything from ARK_HOME
    cfg.set_main_option("script_location", str(paths.home / "migrations"))
    logger.info(
        "running migrations",
        extra={"script_location": str(paths.home / "migrations"), "db": str(paths.db_file)},
    )
    command.upgrade(cfg, "head")
