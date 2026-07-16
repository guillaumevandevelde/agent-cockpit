"""Shared app.database DB for tests.

Single source of truth — ``tests/conftest.py`` imports the engine and
``TestSessionLocal`` from here, and the ``_patch_app_database`` autouse
fixture identity-swaps ``app.database.engine`` / ``AsyncSessionLocal`` in
every module that imported them at import time.

Mirrors ``tests/kanban_test_db.py`` (which does the same for the kanban
board's ``KanbanBase``); only difference is the ``Base`` used for
``drop_all`` / ``create_all`` is ``app.database.Base`` (the device-local
``claude_registry.db`` base) instead of ``app.kanban.db.KanbanBase``.

The temp-file + ``NullPool`` rationale is identical: pytest-asyncio gives
each test its own event loop, and aiosqlite binds every connection to the
loop that created it, so a shared in-memory DB would deadlock as soon as
the second test opened a connection on a new loop. A file DB needs no
shared pooled connection, so each test gets a fresh connection bound to
its own loop, and the schema survives across loops in the file.
"""
import atexit
import gc
import os
import tempfile

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.config import settings
from app.database import Base

_fd, _db_path = tempfile.mkstemp(prefix="app_database_test_", suffix=".db")
os.close(_fd)


@atexit.register
def _cleanup_test_db_file() -> None:
    for p in (_db_path, _db_path + "-wal", _db_path + "-shm"):
        try:
            os.unlink(p)
        except OSError:
            pass


test_engine = create_async_engine(
    f"sqlite+aiosqlite:///{_db_path}", future=True, poolclass=NullPool,
)


@event.listens_for(test_engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    # Same WAL + busy_timeout + foreign_keys pragmas as the prod
    # ``app.database`` engine, so a test that writes a tight loop of
    # commits (agent_mail inbox fan-out, runs bulk resume, etc.) doesn't
    # see "database is locked" on the *next* test's reset pass under
    # NullPool's per-checkout connection churn.
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute(f"PRAGMA busy_timeout={settings.sqlite_busy_timeout_ms}")
    cur.close()


_test_session_factory = async_sessionmaker(
    test_engine, class_=AsyncSession, expire_on_commit=False,
    autocommit=False, autoflush=False,
)


class TestSessionLocal:
    def __call__(self):
        return _test_session_factory()


async def reset_test_tables():
    # Reclaim any leftover aiosqlite connections the cyclic GC hasn't
    # collected yet (see ``kanban_test_db.reset_test_tables`` for the
    # same rationale + the comment block explaining the gen-1 choice).
    gc.collect(1)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
