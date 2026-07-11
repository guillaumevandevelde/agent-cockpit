"""Parity check: tests/kanban_test_db.py's connect listener claims to mirror
app.kanban.db's connect listener in full, but nothing enforces that beyond a
code comment. A prior drift (test listener only set foreign_keys, silently
dropping journal_mode/synchronous/busy_timeout) caused a real ~25-40% flake
in test_kanban_dispatch.py under NullPool's rapid connection churn. This test
turns a future silent drift into an immediate red test instead.

Calls both real listener functions directly against plain sqlite3 connections
(not the async engines) so the test has no event-loop or shared-connection
surface of its own.
"""
import os
import sqlite3
import tempfile

from app.kanban.db import _set_sqlite_pragma as _prod_set_sqlite_pragma
from tests.kanban_test_db import _set_sqlite_pragma as _test_set_sqlite_pragma

PRAGMAS = ("journal_mode", "synchronous", "foreign_keys", "busy_timeout")


def _pragma_values(listener) -> dict:
    fd, path = tempfile.mkstemp(prefix="kanban_pragma_parity_", suffix=".db")
    os.close(fd)
    try:
        conn = sqlite3.connect(path)
        try:
            listener(conn, None)
            cur = conn.cursor()
            values = {}
            for name in PRAGMAS:
                cur.execute(f"PRAGMA {name}")
                values[name] = cur.fetchone()[0]
            cur.close()
        finally:
            conn.close()
    finally:
        for p in (path, path + "-wal", path + "-shm"):
            try:
                os.unlink(p)
            except OSError:
                pass
    return values


def test_test_db_pragmas_match_production_pragmas():
    assert _pragma_values(_test_set_sqlite_pragma) == _pragma_values(_prod_set_sqlite_pragma)
