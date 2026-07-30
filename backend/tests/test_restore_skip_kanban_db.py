"""Tests for ``skip_kanban_db`` on ``RestoreOptions`` and the manifest
arcname contract that ships the kanban SQLite snapshot under its
canonical name.

Background (kanban card 18984c63a…):
    Since ``ae6a76d`` the kanban DB rides along in every
    ``project`` / ``full`` scope backup as a WAL-safe snapshot. A
    blind restore that unpacked every ZIP entry to the user home would
    therefore silently overwrite the live board with the snapshot —
    the institutional memory of every `**Summary:**`,
    ``**Impediment:**``, deliverable and dependency graph. The fix
    introduces an opt-in ``skip_kanban_db`` flag on ``RestoreOptions``
    that defaults to skip (restore is opt-in for the live board), and
    aligns the manifest ``contents.files`` list with the actual ZIP
    arcname (the snapshot's ``kanban.db.snap-…db`` source path was
    leaking into the manifest while the ZIP entry was already renamed
    to the canonical ``kanban.db``).
"""
import sqlite3
import zipfile
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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


async def _register_backup(db, archive: Path, *, name: str = "b") -> Backup:
    """Insert a Backup row pointing at the archive on disk. The restore
    service reads the row to find the archive; without this the
    restore path would 404 on ``backup not found``."""
    backup = Backup(
        name=name,
        file_path=str(archive),
        scope="user",
        size_bytes=archive.stat().st_size,
    )
    db.add(backup)
    await db.commit()
    await db.refresh(backup)
    return backup


def _seed_backup_with_kanban_db(
    archive: Path,
    *,
    live_kanban_content: bytes = b"snapshot-bytes",
) -> None:
    """Write a minimal backup ZIP that contains a kanban-DB entry plus a
    sentinel skill file. The skill file is the "would-be-touched"
    indicator that proves restore ran at all when ``skip_kanban_db``
    is False — without it the test can't distinguish "restore ran but
    skipped the DB" from "restore ran and overwrote it".
    """
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", "{}")
        zf.writestr(".claude-registry/kanban.db", live_kanban_content)
        zf.writestr(".claude/skills/example/SKILL.md", b"skill-content")


# --- RestoreOptions default + flag shape -------------------------------------


def test_restore_options_skip_kanban_db_defaults_to_true():
    """The destructive item must be skipped by default. Restoring the
    live kanban-DB is an explicit, conscious action — never the path
    the wizard lands on when the user just clicks through."""
    options = RestoreOptions()
    assert options.skip_kanban_db is True, (
        "skip_kanban_db must default to True so the live board is "
        "protected unless the operator actively opts in. "
        "Convention: skip_plugins / skip_skills / skip_mcp_servers "
        "default to False because overwriting them is benign; the "
        "kanban DB is the destructive item, so its skip defaults "
        "to True."
    )


def test_restore_options_skip_kanban_db_is_overridable():
    """Operators who DO want to roll back the board must be able to
    set ``skip_kanban_db = False``. Without this, the wizard can
    only ever skip the file — losing the recovery use-case from
    kanban card 39d2d54a… where the whole point of having the DB in
    the backup was the ability to restore it after a schema-rot."""
    options = RestoreOptions(skip_kanban_db=False)
    assert options.skip_kanban_db is False


# --- restore_backup honors skip_kanban_db ------------------------------------


@pytest.mark.asyncio
async def test_restore_default_does_not_overwrite_live_kanban_db(
    db, tmp_path, monkeypatch
):
    """The default restore (no options) must leave the live kanban DB
    on disk untouched. This is the regression guard for kanban card
    18984c63a… — the original bug was a silent overwrite of the live
    board on every Restore click.

    The sentinel skill file restores successfully, proving the loop
    ran — only the kanban DB is held back, exactly as requested.
    """
    home = tmp_path / "home"
    home.mkdir()
    live_db_dir = home / ".claude-registry"
    live_db_dir.mkdir()
    live_db_path = live_db_dir / "kanban.db"
    live_db_bytes = b"live board - must NOT be replaced"
    live_db_path.write_bytes(live_db_bytes)

    # ``restore_backup`` resolves ``target_path = project_path`` (when
    # supplied) or ``get_user_home()``; this fixture uses
    # ``project_path=home`` so the ZIP entries extract relative to it.
    monkeypatch.setattr(
        "app.services.restore_service.get_user_home", lambda: home
    )

    archive = tmp_path / "backup.zip"
    _seed_backup_with_kanban_db(
        archive,
        live_kanban_content=b"snapshot-from-yesterday",
    )
    backup = await _register_backup(db, archive)

    svc = BackupService(db)
    result = await svc.restore_backup(
        backup_id=backup.id,
        project_path=str(home),
        # No options → uses RestoreOptions() defaults → skip_kanban_db=True.
    )

    assert result.success is True, f"restore reported failure: {result.message}"
    assert live_db_path.read_bytes() == live_db_bytes, (
        "live kanban DB was overwritten despite skip_kanban_db defaulting "
        "to True — the destructive item was not held back."
    )
    # Sentinel proves the loop actually ran.
    sentinel = home / ".claude" / "skills" / "example" / "SKILL.md"
    assert sentinel.read_bytes() == b"skill-content", (
        "restore did not extract the sentinel skill file — the loop "
        "didn't run, so this test is not actually exercising "
        "skip_kanban_db."
    )


@pytest.mark.asyncio
async def test_restore_with_skip_kanban_db_false_overwrites_live_kanban_db(
    db, tmp_path, monkeypatch
):
    """Opting in to restore the kanban DB must actually overwrite the
    live board. This is the recovery path that card 39d2d54a…
    intentionally enabled — without it, the snapshot is dead weight."""
    home = tmp_path / "home"
    home.mkdir()
    live_db_dir = home / ".claude-registry"
    live_db_dir.mkdir()
    live_db_path = live_db_dir / "kanban.db"
    live_db_path.write_bytes(b"old live board")

    monkeypatch.setattr(
        "app.services.restore_service.get_user_home", lambda: home
    )

    snapshot_bytes = b"snapshot-from-yesterday"
    archive = tmp_path / "backup.zip"
    _seed_backup_with_kanban_db(
        archive,
        live_kanban_content=snapshot_bytes,
    )
    backup = await _register_backup(db, archive)

    svc = BackupService(db)
    result = await svc.restore_backup(
        backup_id=backup.id,
        project_path=str(home),
        options=RestoreOptions(skip_kanban_db=False),
    )

    assert result.success is True, f"restore reported failure: {result.message}"
    assert live_db_path.read_bytes() == snapshot_bytes, (
        "explicit skip_kanban_db=False did not restore the snapshot — "
        "the recovery path is broken."
    )


@pytest.mark.asyncio
async def test_restore_reports_skipped_kanban_db_in_files_skipped(
    db, tmp_path, monkeypatch
):
    """The skipped kanban-DB entry must show up in
    ``RestoreResult.files_skipped`` so the wizard / log shows the
    operator exactly what was held back. Without this, "I clicked
    Restore, did it work?" is unanswerable."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude-registry").mkdir()
    (home / ".claude-registry" / "kanban.db").write_bytes(b"live")

    monkeypatch.setattr(
        "app.services.restore_service.get_user_home", lambda: home
    )

    archive = tmp_path / "backup.zip"
    _seed_backup_with_kanban_db(archive)
    backup = await _register_backup(db, archive)

    svc = BackupService(db)
    result = await svc.restore_backup(
        backup_id=backup.id,
        project_path=str(home),
        options=RestoreOptions(),  # default skip_kanban_db=True
    )

    assert result.success is True
    # The kanban DB is the new skip path; ``manifest.json`` is
    # always bypassed by the restore loop without being counted
    # in ``files_skipped`` (see ``restore_service.restore_backup``'
    # pre-emptive ``continue``). The two restores (skill + DB)
    # became: skill restored, DB held back.
    assert result.files_skipped == 1, (
        f"expected exactly the kanban.db entry to be skipped, "
        f"got files_skipped={result.files_skipped}; message={result.message}"
    )
    assert result.files_restored == 1, (
        f"expected exactly the sentinel skill to be restored, "
        f"got files_restored={result.files_restored}"
    )


# --- Manifest arcname matches ZIP entry --------------------------------------


@pytest.mark.asyncio
async def test_backup_manifest_lists_canonical_arcname_for_kanban_snapshot(
    db, tmp_path, monkeypatch
):
    """The manifest's ``contents.files`` must list the arcname that
    actually lands in the ZIP — the canonical ``.claude-registry/kanban.db``
    — not the snapshot source path ``kanban.db.snap-<ts>-<id>.db``.
    Before the fix, anyone inspecting the manifest and trying to read
    those files out of the archive hit a path that wasn't there, and
    the actual ZIP entry was the one the manifest didn't list.
    """
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / "CLAUDE.md").write_text("# project\n")

    # Place the kanban DB inside a synthetic ``~/.claude-registry/`` so
    # the canonical-arcname computation (``kanban_path.relative_to(home)``)
    # yields the canonical ``.claude-registry/kanban.db`` form a real
    # deploy would use. Without this, the production fallback
    # (``canonical_arcname = str(kanban_path)``) leaks an absolute
    # path into the manifest and the round-trip assertion below would
    # pass for the wrong reason.
    home = tmp_path / "home"
    (home / ".claude-registry").mkdir(parents=True)
    live_db = home / ".claude-registry" / "kanban.db"
    conn = sqlite3.connect(str(live_db))
    try:
        conn.execute("CREATE TABLE board (note TEXT)")
        conn.execute("INSERT INTO board VALUES ('row')")
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(
        "app.services.backup_service.kanban_db_path", lambda: live_db
    )
    monkeypatch.setattr(
        "app.services.backup_service.get_user_home", lambda: home
    )

    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    from app.services import backup_service as bs
    monkeypatch.setattr(bs, "get_backup_storage_dir", lambda: backups_dir)

    svc = BackupService(db)
    _backup, manifest = await svc.create_backup(
        name="manifest-arcname-check",
        scope="project",
        project_path=str(project_path),
    )

    # The manifest must list the canonical arcname — never the
    # ephemeral snapshot path. Both formats would appear if a future
    # refactor reintroduced the mismatch; this assertion catches it.
    assert ".claude-registry/kanban.db" in manifest.contents.files, (
        f"manifest must list the canonical arcname "
        f"'.claude-registry/kanban.db'; got {manifest.contents.files!r}"
    )
    assert not any(
        ".snap-" in name for name in manifest.contents.files
    ), (
        f"manifest leaked the snapshot source path "
        f"(kanban.db.snap-…db); got {manifest.contents.files!r}"
    )

    # And the kanban-DB arcname listed in the manifest must exist as a
    # ZIP entry — round-trip contract for the snapshot path. (The
    # project-scoped manifest/arcname mismatch on the project_path
    # files is a separate, pre-existing concern outside this card's
    # scope — the snapshot path is what this fix owns.)
    with zipfile.ZipFile(_backup.file_path) as zf:
        archive_names = set(zf.namelist())
    assert ".claude-registry/kanban.db" in archive_names, (
        f"manifest-listed kanban arcname not in archive; "
        f"manifest={manifest.contents.files!r}, archive={archive_names!r}"
    )
