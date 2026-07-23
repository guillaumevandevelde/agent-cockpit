# backend/tests/test_backup_includes_kanban_db.py
"""Test that the canonical `~/.claude-registry/kanban.db` (the durable store
for the kanban board) is included in the backup zip.

Closes the "borddata overleeft de applicatie" gap from kanban card
39d2d54a… acceptance criterion #3: today, the backup set covers Claude/
Codex config files only, so a future schema-rot that requires deleting the
DB wipes the institutional memory of the project even when a fresh backup
is taken an hour earlier. The DB must ride along with the config files.

The kanban engine runs in WAL mode (`PRAGMA journal_mode=WAL`,
``backend/app/kanban/db.py:34-42``), so copying the primary ``kanban.db``
file directly with ``ZipFile.write`` silently omits any frames still
sitting in ``kanban.db-wal``. The acceptance contract — *"borddata moet
de applicatie overleven"* — fails in exactly that scenario: the ZIP
contains a file named ``kanban.db`` whose reader sees a board that is
hours behind. The fix uses SQLite's online backup API to take a
transactionally-consistent snapshot before the ZIP write.
"""
import sqlite3
import zipfile

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.services.backup_service import BackupService


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


def _make_wal_mode_db_with_committed_data(path) -> sqlite3.Connection:
    """Create a real WAL-mode SQLite DB at ``path`` and COMMIT a row.

    Returns the live connection (still open) — closing it would
    auto-checkpoint the WAL, which defeats the purpose of the test. The
    caller must close it after the backup completes. SQLite normally
    checkpoints on close, so the row in this scenario lives ONLY in the
    ``<path>-wal`` sidecar until something explicitly reads through the
    WAL or issues a checkpoint. A naive ``zf.write(path)`` copies only
    the primary file and would therefore miss the row; only a proper
    backup snapshot through SQLite's online backup API sees it.
    """
    from pathlib import Path

    path = Path(path)
    # ``isolation_level=None`` opens in autocommit mode — committing a
    # write leaves the frame in WAL without forcing an implicit
    # checkpoint. Disable auto-checkpointing entirely so the WAL frame
    # survives until the test deliberately inspects it.
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE board (note TEXT)")
    conn.execute("INSERT INTO board VALUES ('live committed data')")

    # Sanity: assert the WAL sidecar exists and is non-empty so the test
    # really does exercise the WAL-only-frame scenario. If a future
    # SQLite silently checkpoints on close, the assertion below would
    # catch it and we'd want to know.
    wal = path.with_name(path.name + "-wal")
    assert wal.exists(), f"WAL sidecar {wal} should exist after write"
    assert wal.stat().st_size > 0, "WAL should contain the un-checkpointed frame"
    return conn


@pytest.mark.asyncio
async def test_project_backup_includes_kanban_db(db, tmp_path, monkeypatch):
    """A project-scope backup must include the kanban SQLite file **and** the
    ZIP must contain a transactionally-consistent snapshot — not just a
    raw copy of the primary file, which would silently miss any frames
    still resident in the WAL sidecar.

    Acceptance criterion #3 of kanban card 39d2d54a…. Without the DB in
    the backup, deleting the live DB to recover from a schema mismatch
    silently destroys every `**Summary:**` / `**Impediment:**` / Done
    summary on the board — even if a backup was taken an hour ago. With
    WAL-mode + a naive copy, the ZIP contains a board that is hours
    behind, and the restore replays a stale snapshot.
    """
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / "CLAUDE.md").write_text("# project\n")

    # Real WAL-mode SQLite DB with a committed row that lives only in
    # the WAL sidecar until a checkpoint happens. Keep the connection
    # open across the backup so the WAL is NOT auto-checkpointed.
    live_db = tmp_path / "kanban.db"
    wal_conn = _make_wal_mode_db_with_committed_data(live_db)

    monkeypatch.setattr(
        "app.services.backup_service.kanban_db_path",
        lambda: live_db,
    )

    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    from app.services import backup_service as bs
    monkeypatch.setattr(bs, "get_backup_storage_dir", lambda: backups_dir)

    svc = BackupService(db)
    backup, _manifest = await svc.create_backup(
        name="with-kanban",
        scope="project",
        project_path=str(project_path),
    )
    wal_conn.close()

    # The ZIP must contain the kanban DB at its canonical path.
    with zipfile.ZipFile(backup.file_path) as zf:
        names = zf.namelist()
    assert any(
        name.endswith("kanban.db") for name in names
    ), f"kanban DB missing from backup; archive entries: {names}"

    # The crucial check: the snapshotted DB in the ZIP must contain the
    # row that was committed into the WAL. Reading the primary file
    # directly with sqlite3 will fail (it returns no rows because the
    # frame is still in -wal), so a naive ZipFile.write path leaves
    # exactly this kind of incomplete snapshot.
    with zipfile.ZipFile(backup.file_path) as zf:
        candidates = [n for n in zf.namelist() if n.endswith("kanban.db")]
        assert candidates, "no kanban.db entry to inspect"
        archive_name = candidates[0]
        snap_path = tmp_path / "snap.db"
        with zipfile.ZipFile(backup.file_path).open(archive_name) as src, \
                open(snap_path, "wb") as dst:
            dst.write(src.read())

    conn = sqlite3.connect(str(snap_path))
    try:
        rows = list(conn.execute("SELECT note FROM board"))
    finally:
        conn.close()
    assert rows == [("live committed data",)], (
        f"snapshotted DB missing the WAL-resident row; got {rows!r}. "
        "This means the backup copied the primary file instead of "
        "taking a transactionally-consistent snapshot — recent board "
        "data would be lost on restore."
    )


@pytest.mark.asyncio
async def test_full_backup_includes_kanban_db(db, tmp_path, monkeypatch):
    """Full scope mirrors project scope: the kanban DB is a per-machine
    store, so a full backup that misses it loses the same institutional
    memory. Regression guard for the path added by kanban card 39d2d54a….
    """
    user_cfg = tmp_path / "user"
    user_cfg.mkdir()
    (user_cfg / "settings.json").write_text("{}")

    live_db = tmp_path / "kanban.db"
    wal_conn = _make_wal_mode_db_with_committed_data(live_db)

    monkeypatch.setattr(
        "app.services.backup_service.kanban_db_path",
        lambda: live_db,
    )
    monkeypatch.setattr(
        "app.services.backup_service.get_user_home",
        lambda: user_cfg,
    )

    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    from app.services import backup_service as bs
    monkeypatch.setattr(bs, "get_backup_storage_dir", lambda: backups_dir)

    svc = BackupService(db)
    backup, _manifest = await svc.create_backup(
        name="full-with-kanban",
        scope="full",
    )
    wal_conn.close()

    with zipfile.ZipFile(backup.file_path) as zf:
        names = zf.namelist()
    assert any(
        name.endswith("kanban.db") for name in names
    ), f"full backup missing kanban DB; archive entries: {names}"


@pytest.mark.asyncio
async def test_kanban_db_path_resolves_from_settings(monkeypatch):
    """The kanban-DB path helper must read the canonical kanban URL setting
    so a non-default KANBAN_DATABASE_URL (e.g. a mounted volume in Docker)
    is honored by the backup instead of silently falling back to the
    default `~/.claude-registry/kanban.db` that the test machine may not
    even have."""
    from app.config import settings as live_settings
    from app.services import backup_service as bs

    fake_uri = "sqlite+aiosqlite:////tmp/some-other-kanban.db"
    monkeypatch.setattr(live_settings, "kanban_database_url", fake_uri)
    resolved = bs.kanban_db_path()
    assert str(resolved).endswith("some-other-kanban.db")
