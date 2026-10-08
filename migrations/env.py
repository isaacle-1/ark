"""Alembic environment: derives everything from ARK_HOME, never touches logging."""

from __future__ import annotations

from alembic import context

from ark.db import make_engine
from ark.models import Base
from ark.paths import Paths

config = context.config
target_metadata = Base.metadata

paths = Paths.current()


def _run_migrations_online() -> None:
    engine = make_engine(paths.db_file)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,  # SQLite: recreate-table approach
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


def _run_migrations_offline() -> None:
    url = f"sqlite:///{paths.db_file}"
    context.configure(
        url=url,
        target_metadata=target_metadata,
        render_as_batch=True,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    _run_migrations_offline()
else:
    _run_migrations_online()
