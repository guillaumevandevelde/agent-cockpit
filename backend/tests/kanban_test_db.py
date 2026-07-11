"""Shared kanban DB for tests.

Single source of truth — both conftest.py and test files import from here.

Uses a temp *file* DB with ``NullPool`` rather than ``:memory:`` on purpose.
pytest-asyncio gives each test its own event loop, and aiosqlite binds every
connection to the loop that created it. A shared in-memory DB has to keep one
pooled connection alive (that is the only way the in-memory data survives between
sessions), so a later test on a new loop reuses a connection bound to a closed
loop and **deadlocks** — this used to hang the whole suite, and with it every
dispatched session's session-end pytest. A file DB needs no shared connection, so
``NullPool`` can hand each session a fresh connection on the current loop and
close it afterwards; the schema lives in the file and survives across sessions.
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
from app.kanban.db import KanbanBase

_fd, _db_path = tempfile.mkstemp(prefix="kanban_test_", suffix=".db")
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
    # Mirrors app.kanban.db's connect listener in full, not just foreign_keys:
    # NullPool opens/closes a brand-new connection per checkout (see module
    # docstring), so a test that does several commits in a tight loop (e.g. a
    # MAX_DISPATCH_FAILURES retry loop) churns through many connections in a
    # few milliseconds. Without WAL + busy_timeout, the *next* test's
    # `_reset_test_db` autouse fixture can open its DROP TABLE connection
    # before the previous one's lock has fully cleared under SQLite's default
    # rollback-journal mode + zero busy_timeout, failing immediately with
    # "database is locked" at test setup instead of waiting a few ms.
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
    # A prior test's AsyncSession/Connection can form a reference cycle (session
    # -> connection -> ORM object -> session) that plain refcounting can't
    # break -- only the cyclic GC can, and pytest never forces a collection
    # between tests. Until that GC pass runs, the dbapi connection stays
    # checked out of the NullPool and its SQLite lock stays held, so this
    # DROP TABLE can hit "database is locked" even with a busy_timeout (see
    # the connect listener above) if the GC pass just hasn't happened yet.
    # Forcing one here reclaims any such connection before the DDL runs. Gen-1
    # is enough -- the cycle is created and abandoned within a single just-
    # finished test, so it's still young; a full gen-2 sweep would also walk
    # every long-lived object in the process and cost far more per call.
    gc.collect(1)
    async with test_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.drop_all)
        await conn.run_sync(KanbanBase.metadata.create_all)
