"""Bug #1: the kanban DB must live at a fixed, absolute, CWD-independent path
so there is one board per machine (not one per worktree / per launch CWD), and
an existing legacy ./kanban.db is migrated into it without data loss.
"""
import sqlite3
from pathlib import Path

from app.config import _default_kanban_database_url
from app.kanban.db import _migrate_legacy_sqlite


def test_default_kanban_db_url_is_absolute_and_machine_global():
    url = _default_kanban_database_url()
    # Not CWD-relative (the bug): the old default was sqlite+aiosqlite:///./kanban.db
    assert ":///./" not in url
    # Absolute sqlite URL uses four slashes after the scheme.
    assert url.startswith("sqlite+aiosqlite:////")
    # Anchored beside the existing backups dir, independent of CWD/worktree.
    expected = Path.home() / ".claude-registry" / "kanban.db"
    assert str(expected) in url


def test_migrate_legacy_sqlite_copies_when_target_absent(tmp_path):
    legacy = tmp_path / "backend" / "kanban.db"
    legacy.parent.mkdir(parents=True)
    src = sqlite3.connect(legacy)
    src.execute("CREATE TABLE kanban_cards (id TEXT)")
    src.execute("INSERT INTO kanban_cards VALUES ('card-1')")
    src.commit()
    src.close()

    target = tmp_path / "home" / ".claude-registry" / "kanban.db"
    assert _migrate_legacy_sqlite(target, legacy) is True
    assert target.exists()

    dst = sqlite3.connect(target)
    rows = dst.execute("SELECT id FROM kanban_cards").fetchall()
    dst.close()
    assert rows == [("card-1",)]


def test_migrate_legacy_sqlite_skips_when_target_exists(tmp_path):
    legacy = tmp_path / "legacy.db"
    sqlite3.connect(legacy).close()
    target = tmp_path / "target.db"
    target.write_bytes(b"existing")

    assert _migrate_legacy_sqlite(target, legacy) is False
    assert target.read_bytes() == b"existing"  # untouched


def test_migrate_legacy_sqlite_skips_when_no_legacy(tmp_path):
    legacy = tmp_path / "does-not-exist.db"
    target = tmp_path / "target.db"
    assert _migrate_legacy_sqlite(target, legacy) is False
    assert not target.exists()
