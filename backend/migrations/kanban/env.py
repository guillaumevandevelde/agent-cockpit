"""Alembic environment for the board store (app.kanban.db.KanbanBase).

See migrations/registry/env.py for why ALEMBIC_DATABASE_URL exists and why
render_as_batch is mandatory. This environment targets the portable board DB
(~/.claude-registry/kanban.db), which holds production data — never point it
at that file from a test.
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

import app.kanban.models  # noqa: F401  (register every table on KanbanBase)
from app.config import settings
from app.kanban.db import KanbanBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = KanbanBase.metadata


def _database_url() -> str:
    url = os.environ.get("ALEMBIC_DATABASE_URL") or settings.kanban_database_url
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
