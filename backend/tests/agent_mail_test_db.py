"""Isolated DB for Agent Mail tests.

``app.database.AsyncSessionLocal``/``engine`` point at the real, device-local
``claude_registry.db`` (there is no test-only swap for them the way
``tests/kanban_test_db.py`` provides for the kanban board). Agent Mail tests
that imported those names directly and registered members with ``cwd=str(tmp_path)``
were writing a permanent ``mail_team_members`` row per test run straight into
the production DB -- see docs/cockpit/spawn-test-bridge-sessions-analyse.md
bevinding 7. This module gives them their own temp-file engine instead, same
``Base.metadata`` (so ``MailTeamMember`` etc. still get created), same
NullPool-over-a-temp-file rationale as ``kanban_test_db.py`` (aiosqlite binds
a connection to the event loop that opened it, and pytest-asyncio gives each
test a fresh loop).
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

from app.config import settings

_fd, _db_path = tempfile.mkstemp(prefix="agent_mail_test_", suffix=".db")
os.close(_fd)


@atexit.register
def _cleanup_test_db_file() -> None:
    for p in (_db_path, _db_path + "-wal", _db_path + "-shm"):
        try:
            os.unlink(p)
        except OSError:
            pass


engine = create_async_engine(
    f"sqlite+aiosqlite:///{_db_path}", future=True, poolclass=NullPool,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute(f"PRAGMA busy_timeout={settings.sqlite_busy_timeout_ms}")
    cur.close()


_test_session_factory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False,
    autocommit=False, autoflush=False,
)


class AsyncSessionLocal:
    def __call__(self):
        return _test_session_factory()


AsyncSessionLocal = AsyncSessionLocal()
