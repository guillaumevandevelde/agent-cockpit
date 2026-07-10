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
import os
import tempfile

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

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
    # Mirrors app.kanban.db's connect listener so tests enforce the same FK
    # constraints as production (SQLite defaults foreign_keys to OFF per
    # connection otherwise).
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()
_test_session_factory = async_sessionmaker(
    test_engine, class_=AsyncSession, expire_on_commit=False,
    autocommit=False, autoflush=False,
)


class TestSessionLocal:
    def __call__(self):
        return _test_session_factory()


async def reset_test_tables():
    async with test_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.drop_all)
        await conn.run_sync(KanbanBase.metadata.create_all)
