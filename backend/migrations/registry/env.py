"""Alembic environment for the registry store (app.database.Base).

The URL comes from ALEMBIC_DATABASE_URL when set, so tests can point a run at
a tmp_path file without touching the developer's real claude_registry.db.
Falls back to the app's configured URL, with the async driver stripped:
alembic runs synchronously, and the aiosqlite driver would fail to connect.
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.models  # noqa: F401  (register every table on Base)
import app.models.database  # noqa: F401  (core tables predate the eager-import convention)
from app.config import settings
from app.database import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("ALEMBIC_DATABASE_URL") or settings.database_url
    return url.replace("+aiosqlite", "")


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        # render_as_batch is mandatory: SQLite cannot ALTER a column, so alembic
        # has to rebuild the table. Without this, every column change fails.
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
