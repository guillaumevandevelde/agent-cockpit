"""Separate SQLAlchemy store for the kanban board domain.

Intentionally independent from app.database: the board is portable and
sync-able, whereas app.database holds device-local data (tmux targets,
absolute paths, scheduled deliveries).
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

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


class LockContention(OperationalError):
    """Raised when ``run_write_with_retry`` exhausts its retries on lock contention.

    Subclasses ``OperationalError`` so callers that only catch the SQLAlchemy
    class keep working; carries the structured fields of the
    ``lock_contention`` agent-failure contract
    (``docs/cockpit/agent-failure-response.md``): ``attempts`` and
    ``retry_after_ms``. The REST layer renders it as a 503 via the handler in
    ``app/main.py``; the MCP layer renders it as an ``{"error":
    "lock_contention", …}`` dict via the tool decorator in ``mcp_server.py``.
    """

    reason = "lock_contention"

    def __init__(
        self,
        cause: OperationalError,
        *,
        attempts: int,
        retry_after_ms: int = 500,
    ) -> None:
        super().__init__(cause.statement, cause.params, cause.orig)
        self.attempts = attempts
        self.retry_after_ms = retry_after_ms


def _is_lock_contention(exc: OperationalError) -> bool:
    """True iff ``exc`` is a SQLite "database is locked" raised by the busy_timeout.

    Matches on ``str(exc.orig)`` per the contract (§4 kind-2): a substring
    check against the wrapped cause. Non-lock ``OperationalError``
    (schema-mismatch, no such table, etc.) returns False, so the wrapper
    does not retry them.
    """
    orig = exc.orig
    if orig is None:
        return False
    return "database is locked" in str(orig)


async def run_write_with_retry(
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    max_retries: int = 3,
    backoff_base_ms: int = 200,
    total_budget_ms: int = 2000,
) -> Any:
    """Retry a kanban write coroutine on transient SQLite lock contention.

    The wrapper calls ``coro_factory()`` to obtain a fresh coroutine for each
    attempt, awaits it, and only retries on
    ``sqlalchemy.exc.OperationalError`` whose wrapped cause is
    ``"database is locked"``. Three error classes with distinct behaviour:

    * **Lock-contention ``OperationalError``**: slept with **exponential
      backoff** (``backoff_base_ms * 2**attempt``) and retried, bounded by
      both ``max_retries`` and ``total_budget_ms``. The remaining budget is
      checked *before* the sleep so the bound is honoured even when the
      configured ``backoff_base_ms`` alone would exceed it.
    * **Non-lock ``OperationalError``**: propagated unchanged. These are
      genuine bugs (schema-mismatch, no such table, etc.) and retrying
      would only mask the failure.
    * **Any other exception** (including ``ClaimRejected`` from
      ``apply_operation``): propagated unchanged. ``ClaimRejected`` is
      business logic — the caller checks claim ownership, not a lock — so
      a retry would be incorrect.

    ``coro_factory`` is invoked afresh on every attempt, so the caller can
    open a brand-new ``KanbanSessionLocal()`` inside it and the retry
    receives a session with no pre-mutation state. The wrapper does not
    manage session lifecycle itself; that stays with the caller.
    """
    # Lazy import: ``app.kanban.operations`` imports ``app.kanban.models``,
    # which in turn imports ``KanbanBase`` from this module. A top-level
    # import of ``ClaimRejected`` would form a cycle.
    from app.kanban.operations import ClaimRejected

    last_lock_error: OperationalError | None = None
    total_budget_s = total_budget_ms / 1000.0
    deadline = asyncio.get_event_loop().time() + total_budget_s
    # ``max_retries`` is the number of *retries* on top of the initial
    # attempt, so the loop runs at most ``max_retries + 1`` times.
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except OperationalError as exc:
            if not _is_lock_contention(exc):
                raise
            last_lock_error = exc
            # No more retries configured — bail with the last error.
            if attempt == max_retries:
                break
            # If the next backoff would exceed the remaining budget, bail
            # without sleeping. ``last_lock_error`` already holds the cause.
            sleep_s = (backoff_base_ms * (2 ** attempt)) / 1000.0
            if asyncio.get_event_loop().time() + sleep_s > deadline:
                break
            await asyncio.sleep(sleep_s)
        except ClaimRejected:
            raise

    # All attempts exhausted. The loop body never returns on this path.
    assert last_lock_error is not None
    raise LockContention(last_lock_error, attempts=attempt) from last_lock_error
