"""Separate SQLAlchemy store for the kanban board domain.

Intentionally independent from app.database: the board is portable and
sync-able, whereas app.database holds device-local data (tmux targets,
absolute paths, scheduled deliveries).
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
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


_T = TypeVar("_T")

# Backoff before each retry, in seconds; four attempts total.
#
# These sleeps are NOT the whole wait. Each attempt first burns the driver's
# own ``busy_timeout`` (``sqlite_busy_timeout_ms``, 5s) inside SQLite before it
# reports the lock, so the real worst case is ~4x that plus these backoffs —
# roughly 20s, not 0.35s. Measured against the live backend: a writer holding
# the lock for 8s used to produce a hard 500 after 5.03s and now yields a 201
# after 8.05s.
#
# That budget is deliberate but it is a *safety net*, not a licence: a writer
# that holds the lock for tens of seconds is a bug in that writer (it happened
# — the dispatch tick held one transaction across its whole resolution phase,
# fixed in ``dispatch_project``). Fix such a writer rather than widening this.
_WRITE_RETRY_BACKOFF_S = (0.05, 0.1, 0.2)


def is_sqlite_locked_error(exc: BaseException) -> bool:
    """True for the SQLITE_BUSY family: "database is locked" / "table is locked".

    Matches on the message because SQLAlchemy wraps the driver error and
    ``sqlite3.OperationalError`` carries no error code, so the message is the
    only discriminator available. Deliberately narrow: any *other*
    ``OperationalError`` (a missing table, a bad column) is a real defect and
    must surface on the first attempt instead of being retried three times.
    """
    if not isinstance(exc, OperationalError):
        return False
    return "is locked" in str(exc.orig if exc.orig is not None else exc).lower()


async def run_write_with_retry(
    fn: Callable[[AsyncSession], Awaitable[_T]],
    *,
    label: str,
) -> _T:
    """Run ``fn`` in a fresh kanban session, retrying on SQLITE_BUSY.

    SQLite allows one writer at a time. When a concurrent writer holds the
    lock for longer than ``sqlite_busy_timeout_ms``, the driver raises
    ``database is locked`` and — without this helper — the request dies as an
    unhandled 500. That is what made ``POST /kanban/cards`` fail while the
    auto-dispatch tick was mid-transaction.

    ``fn`` receives the session and is responsible for its own ``commit()``.
    It must be **idempotent-on-failure**: a retry re-runs it from the top with
    a brand-new session, so a partially-applied first attempt would double up.
    That holds for the op-log writers here because a failed attempt commits
    nothing — the session is discarded unrolled.

    Raises the last ``OperationalError`` when every attempt is exhausted; the
    API layer turns that into a 503 (retryable) rather than a 500.
    """
    attempts = len(_WRITE_RETRY_BACKOFF_S) + 1
    for attempt in range(attempts):
        try:
            async with KanbanSessionLocal() as session:
                return await fn(session)
        except OperationalError as exc:
            if not is_sqlite_locked_error(exc) or attempt == attempts - 1:
                raise
            delay = _WRITE_RETRY_BACKOFF_S[attempt]
            logger.warning(
                "kanban write %s hit a locked DB (attempt %d/%d); retrying in %.2fs",
                label, attempt + 1, attempts, delay,
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


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
