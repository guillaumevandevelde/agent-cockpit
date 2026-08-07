"""Tests for refuse-while-running guard on kanban DB restore.

Background (kanban card 141f2eba42444ddebc821d4182dd4cea, follow-up
on kaart 18984c63a…):

    The opt-in restore of the kanban SQLite DB (``skip_kanban_db=False``)
    silently overwrites ``~/.claude-registry/kanban.db`` while the
    cockpit backend holds the file open in WAL mode. The freshly
    extracted primary file then sits next to the live ``-wal`` / ``-shm``
    sidecars which still belong to the OLD generation — exactly the
    failure mode the operator can no longer detect.

    Human direction (kaart 141f2eba…): refuse the restore while the
    backend is running. The fix must:
      1. detect that the kanban DB is held open by another process,
      2. refuse the entire restore when the opt-in path would touch
         the kanban DB file in that state,
      3. return an explicit message that names the operator's only
         path forward: "stop the cockpit and retry".

These tests pin the contract end-to-end: the live DB must NOT be
overwritten, the sidecars must NOT be removed by the restore itself,
and the failure message must name the stop-backend step so the
operator doesn't have to guess.
"""
import sqlite3
import zipfile
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings as live_settings
from app.database import Base
from app.models.database import Backup
from app.models.schemas import RestoreOptions
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


def _open_live_kanban_db(path: Path) -> sqlite3.Connection:
    """Create a WAL-mode kanban DB at ``path`` and return an OPEN
    connection that the caller MUST hold onto for the lifetime of
    the test.

    Without a persistent open connection, ``sqlite3.connect`` followed
    by ``close()`` runs the final auto-checkpoint on the last open
    connection, which empties the WAL and SQLite then DELETES the
    sidecar — and then the refused-restore test can no longer observe
    the "stale sidecar after overwrite" failure mode the human wants
    to prevent. The pattern mirrors
    ``test_backup_includes_kanban_db._make_wal_mode_db_with_committed_data``
    which has to keep its connection open for the same reason.
    """
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE kanban_cards (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO kanban_cards (id) VALUES (1)")
    wal = path.with_name(path.name + "-wal")
    shm = path.with_name(path.name + "-shm")
    assert wal.exists(), "WAL sidecar should exist after write"
    assert wal.stat().st_size > 0, "WAL should contain the committed frame"
    assert shm.exists(), "SHM sidecar should exist in WAL mode"
    return conn


def _make_free_kanban_db(path: Path) -> None:
    """Create a closed WAL-mode kanban DB on disk. The refuse-while-
    running guard must report "free" against this fixture (no open
    connection, no live process holding the file).
    """
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE kanban_cards (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO kanban_cards (id) VALUES (1)")
    conn.commit()
    conn.close()


def _make_backup_zip_with_kanban_entry(
    archive_path: Path,
    kanban_entry_content: bytes,
    arcname: str = ".claude-registry/kanban.db",
) -> None:
    """Write a structurally valid backup ZIP containing a kanban DB entry
    plus a manifest. The restore flow requires both: the archive must
    parse and the manifest must be parseable so the destructive loop
    actually reaches the kanban entry.
    """
    manifest = (
        b'{"version":"1.0","created_at":"2026-08-05T00:00:00",'
        b'"platform":"linux","scope":"user","contents":{"files":[],'
        b'"skills":[],"plugins":[],"mcp_servers":[]}}'
    )
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("manifest.json", manifest)
        zf.writestr(arcname, kanban_entry_content)


async def _register_backup(db, archive: Path) -> Backup:
    """Insert a backup row pointing at ``archive`` so restore_backup finds it."""
    row = Backup(
        name="with-kanban",
        file_path=str(archive),
        scope="user",
        size_bytes=archive.stat().st_size,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def test_opt_in_kanban_restore_refused_while_backend_holds_db(
    db, tmp_path, monkeypatch,
):
    """The opt-in restore MUST refuse when the live DB is held open.

    Failure scenario: the cockpit backend runs and holds the kanban
    SQLite file open in WAL mode (every board read/write goes through
    that connection pool). An operator clicks "Restore" with the kanban
    toggle ON. Without the guard, the restore overwrites the primary
    file and leaves the live ``-wal``/``-shm`` sidecars behind — the
    primary file and the sidecars now belong to different DB
    generations. Acceptance: the restore returns failure with an
    explicit stop-the-cockpit message, and the live primary file
    content is byte-identical to what the running backend committed.

    The detection helper is patched directly so the test exercises
    the restore-service branch without spawning a subprocess (the
    real /proc/*/fd check has its own dedicated tests in
    ``test_backup_service_kanban_lock.py``).
    """
    live_db = tmp_path / "kanban.db"
    live_conn = _open_live_kanban_db(live_db)
    try:
        # Snapshot the live file's primary bytes — what the restore
        # MUST NOT have overwritten if it behaved correctly.
        live_primary_before = live_db.read_bytes()

        # Build a backup ZIP whose kanban entry has different content
        # so a successful overwrite would be detectable.
        archive = tmp_path / "backup.zip"
        _make_backup_zip_with_kanban_entry(
            archive,
            kanban_entry_content=b"this is the snapshot, not the live db",
        )
        backup_row = await _register_backup(db, archive)

        # Redirect kanban_db_path() to our tmp_path DB so the restore
        # guard sees the live WAL-mode DB.
        monkeypatch.setattr(
            "app.services.backup_service.kanban_db_path",
            lambda: live_db,
        )

        # The kanban engine reads the URL from settings too. Redirect
        # settings.kanban_database_url so any path resolution in the
        # restore service lands on our tmp_path DB.
        monkeypatch.setattr(
            live_settings,
            "kanban_database_url",
            f"sqlite+aiosqlite:///{live_db}",
        )

        # Simulate "backend holds the DB open" by patching the detector.
        # The detector's own logic (proc/*/fd walk) is covered by its
        # own unit test; here we want to pin the restore-service branch.
        from app.services import restore_service as rs
        monkeypatch.setattr(
            rs,
            "_check_kanban_db_in_use",
            lambda _path: True,
        )

        svc = BackupService(db)
        result = await svc.restore_backup(
            backup_row.id,
            options=RestoreOptions(skip_kanban_db=False),
        )

        assert result.success is False, (
            "opt-in kanban restore must refuse while the live DB is held open; "
            f"got result={result!r}"
        )
        assert "stop the cockpit" in result.message.lower(), (
            "refusal message must name the operator's only path forward — "
            f"got: {result.message!r}"
        )
        assert "kanban" in result.message.lower(), (
            "refusal message must name the kanban DB so the operator knows "
            f"which archive entry is the blocker; got: {result.message!r}"
        )

        # The live primary file MUST be byte-identical to its pre-restore
        # state — the guard's whole point is to prevent overwrite.
        assert live_db.read_bytes() == live_primary_before, (
            "opt-in kanban restore overwrote the live DB primary file "
            "while the backend was still holding it open"
        )

        # The live sidecars MUST still be there — the guard does not
        # touch them (the operator will stop the cockpit, which lets
        # the WAL autocheckpoint + close cleanly before the next
        # restore attempt).
        wal = live_db.with_name(live_db.name + "-wal")
        shm = live_db.with_name(live_db.name + "-shm")
        assert wal.exists(), "live WAL sidecar disappeared during refused restore"
        assert shm.exists(), "live SHM sidecar disappeared during refused restore"
    finally:
        live_conn.close()


async def test_opt_in_kanban_restore_proceeds_when_db_is_free(
    db, tmp_path, monkeypatch, restore_home,
):
    """Negative control: when the kanban DB is not held open (no other
    connection), the opt-in restore MUST proceed and overwrite the
    primary file. The guard must not block legitimate restore-then-
    restart workflows where the operator already stopped the cockpit.

    Takes ``restore_home`` explicitly (it is autouse anyway) so the
    containment is visible at the call site: this test writes a real
    ``.claude-registry/kanban.db`` member, and without that fixture the
    bytes land on the **live board**. See the closing assertion.
    """
    live_db = tmp_path / "kanban.db"
    _make_free_kanban_db(live_db)

    archive = tmp_path / "backup.zip"
    _make_backup_zip_with_kanban_entry(
        archive,
        kanban_entry_content=b"restore-this-content",
    )
    backup_row = await _register_backup(db, archive)

    monkeypatch.setattr(
        "app.services.backup_service.kanban_db_path",
        lambda: live_db,
    )
    monkeypatch.setattr(
        live_settings,
        "kanban_database_url",
        f"sqlite+aiosqlite:///{live_db}",
    )

    from app.services import restore_service as rs
    monkeypatch.setattr(
        rs,
        "_check_kanban_db_in_use",
        lambda _path: False,
    )

    svc = BackupService(db)
    result = await svc.restore_backup(
        backup_row.id,
        options=RestoreOptions(skip_kanban_db=False),
    )

    assert result.success is True, (
        f"opt-in kanban restore must proceed when no one holds the DB; "
        f"got result={result!r}"
    )
    assert result.files_restored >= 1

    # Containment, not decoration. The extraction destination is
    # `get_user_home() / member` — patching `backup_service.kanban_db_path`
    # above only steers the in-use *guard*. Assert the bytes actually landed
    # inside the sandboxed home so this can never silently revert to
    # overwriting the live board (2026-08-06 + 2026-08-07 incidents).
    extracted = restore_home / ".claude-registry" / "kanban.db"
    assert extracted.read_bytes() == b"restore-this-content", (
        "restore extraction did not land in the contained home — the "
        "`restore_home` fixture is not steering `get_user_home`, which means "
        "this test is overwriting the real ~/.claude-registry/kanban.db"
    )
    assert not str(extracted).startswith(str(Path.home())), (
        f"restore extraction root is inside the real home ({Path.home()}); "
        "the containment fixture is not in effect"
    )


async def test_default_restore_skips_kanban_entry_unconditionally(
    db, tmp_path, monkeypatch,
):
    """Default path: skip_kanban_db defaults to True. The kanban entry
    must be skipped even when the live DB is held open — the operator
    is not opting into the destructive overwrite, so there is nothing
    to refuse. This is the steady-state guarantee from
    kaart 18984c63a… that the guard must not regress.
    """
    live_db = tmp_path / "kanban.db"
    # Default path: the DB doesn't need to be held open — the
    # default skip_kanban_db=True short-circuits before the
    # detection helper runs.
    _make_free_kanban_db(live_db)

    archive = tmp_path / "backup.zip"
    _make_backup_zip_with_kanban_entry(
        archive,
        kanban_entry_content=b"snapshot",
    )
    backup_row = await _register_backup(db, archive)

    monkeypatch.setattr(
        "app.services.backup_service.kanban_db_path",
        lambda: live_db,
    )
    monkeypatch.setattr(
        live_settings,
        "kanban_database_url",
        f"sqlite+aiosqlite:///{live_db}",
    )

    svc = BackupService(db)
    # No options → default skip_kanban_db=True.
    result = await svc.restore_backup(backup_row.id)

    assert result.success is True, (
        f"default restore must skip the kanban entry silently; got {result!r}"
    )
    # The kanban entry must NOT have been counted as restored.
    assert result.files_restored == 0, (
        f"default restore must skip the kanban entry; restored="
        f"{result.files_restored}, skipped={result.files_skipped}"
    )
    assert result.files_skipped >= 1


def test_is_kanban_db_held_open_returns_false_for_missing_path(tmp_path):
    """``_is_kanban_db_held_open`` returns ``False`` when the path
    doesn't exist — there is nothing to corrupt and the restore
    should proceed (the ZIP entry creates a fresh DB on disk).
    """
    from app.services.backup_service import _is_kanban_db_held_open
    assert _is_kanban_db_held_open(tmp_path / "nope.db") is False


def test_is_kanban_db_held_open_detects_open_connection_in_other_thread(tmp_path):
    """When another thread (in this test, the test fixture itself
    simulates the live backend by holding an open connection) has
    the DB file open via a different file descriptor, the helper
    must report ``True``. The test process' own ``/proc/self/fd``
    entries are excluded, so we deliberately keep the connection
    open past the detection call.
    """
    from app.services.backup_service import _is_kanban_db_held_open

    db_path = tmp_path / "kanban.db"
    live_conn = _open_live_kanban_db(db_path)
    try:
        assert _is_kanban_db_held_open(db_path) is True, (
            "detection missed an open file descriptor on the kanban DB"
        )
    finally:
        live_conn.close()


def test_is_kanban_db_held_open_returns_false_when_no_connection(tmp_path):
    """Negative control: when no other process/connection holds the
    file open, the helper must report ``False``. Without a persistent
    open connection the sidecar still exists, but the file descriptor
    is gone — the helper must distinguish "file exists" from "file is
    open".
    """
    from app.services.backup_service import _is_kanban_db_held_open

    db_path = tmp_path / "kanban.db"
    _make_free_kanban_db(db_path)
    assert _is_kanban_db_held_open(db_path) is False
