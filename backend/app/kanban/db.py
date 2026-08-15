"""Separate SQLAlchemy store for the kanban board domain.

Intentionally independent from app.database: the board is portable and
sync-able, whereas app.database holds device-local data (tmux targets,
absolute paths, scheduled deliveries).
"""
import logging
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import PROJECT_ROOT, settings

logger = logging.getLogger(__name__)


class KanbanBase(DeclarativeBase):
    """Base for all kanban-domain models."""
    pass


kanban_engine = create_async_engine(settings.kanban_database_url, future=True)

# SQLite + SQLAlchemy drops ``tzinfo`` on write, so every
# ``DateTime(timezone=True)`` column reads back as a naive ``datetime`` here.
# Use ``app.utils.timeutils.ensure_aware`` before comparing against
# ``datetime.now(UTC)`` — inline ``replace(tzinfo=UTC)`` guards have caused
# multiple ``can't compare offset-naive and offset-aware datetimes`` bugs.

if settings.kanban_database_url.startswith("sqlite"):
    @event.listens_for(kanban_engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute(f"PRAGMA busy_timeout={settings.sqlite_busy_timeout_ms}")
        cur.close()

KanbanSessionLocal = async_sessionmaker(
    kanban_engine, class_=AsyncSession, expire_on_commit=False,
    autocommit=False, autoflush=False,
)


def _migrate_legacy_sqlite(target_path: Path, legacy_path: Path) -> bool:
    """One-time, non-destructive copy of a legacy CWD-relative kanban.db into the
    new machine-global location. Returns True iff a copy happened.

    No-op if the target already exists (don't clobber a live board) or there is
    no legacy file. Uses SQLite's online backup API, which is WAL-safe and leaves
    the legacy file untouched.
    """
    import sqlite3

    if target_path.exists() or not legacy_path.exists():
        return False
    target_path.parent.mkdir(parents=True, exist_ok=True)
    # Connect normally (not mode=ro): legacy is WAL-mode and a read-only handle
    # may miss un-checkpointed WAL frames. The backup only reads the source.
    src = sqlite3.connect(str(legacy_path))
    try:
        dst = sqlite3.connect(str(target_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return True


def _sqlite_path(database_url: str) -> Path | None:
    """Filesystem path of a sqlite SQLAlchemy URL, or None for non-sqlite/memory."""
    if not database_url.startswith("sqlite"):
        return None
    db = make_url(database_url).database
    if not db or db == ":memory:":
        return None
    return Path(db)


async def init_kanban_db() -> None:
    """Create kanban tables. Import models so they register on KanbanBase."""
    from app.kanban import models  # noqa: F401

    target = _sqlite_path(settings.kanban_database_url)
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        _migrate_legacy_sqlite(target, PROJECT_ROOT / "backend" / "kanban.db")

    async with kanban_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.create_all)
