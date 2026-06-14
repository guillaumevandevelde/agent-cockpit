"""Separate SQLAlchemy store for the kanban board domain.

Intentionally independent from app.database: the board is portable and
sync-able, whereas app.database holds device-local data (tmux targets,
absolute paths, scheduled deliveries).
"""
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession, create_async_engine, async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class KanbanBase(DeclarativeBase):
    """Base for all kanban-domain models."""
    pass


kanban_engine = create_async_engine(settings.kanban_database_url, future=True)

if settings.kanban_database_url.startswith("sqlite"):
    @event.listens_for(kanban_engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

KanbanSessionLocal = async_sessionmaker(
    kanban_engine, class_=AsyncSession, expire_on_commit=False,
    autocommit=False, autoflush=False,
)


async def init_kanban_db() -> None:
    """Create kanban tables. Import models so they register on KanbanBase."""
    from app.kanban import models  # noqa: F401
    async with kanban_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.create_all)
