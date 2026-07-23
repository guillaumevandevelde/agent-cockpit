# backend/tests/test_backup_includes_kanban_db.py
"""Test that the canonical `~/.claude-registry/kanban.db` (the durable store
for the kanban board) is included in the backup zip.

Closes the "borddata overleeft de applicatie" gap from kanban card
39d2d54a… acceptance criterion #3: today, the backup set covers Claude/
Codex config files only, so a future schema-rot that requires deleting the
DB wipes the institutional memory of the project even when a fresh backup
is taken an hour earlier. The DB must ride along with the config files.
"""
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


@pytest.mark.asyncio
async def test_project_backup_includes_kanban_db(db, tmp_path, monkeypatch):
    """A project-scope backup must include the kanban SQLite file.

    Acceptance criterion #3 of kanban card 39d2d54a…. Without the DB in
    the backup, deleting the live DB to recover from a schema mismatch
    silently destroys every `**Summary:**` / `**Impediment:**` / Done
    summary on the board — even if a backup was taken an hour ago.
    """
    # Synthetic project dir + a representative project file so the
    # project-scope code path has at least one path to pick up.
    project_path = tmp_path / "project"
    project_path.mkdir()
    (project_path / "CLAUDE.md").write_text("# project\n")

    # Fake kanban DB file at the canonical location. Use a real file with
    # content so the zip entry is non-empty — otherwise an empty file
    # silently slips through the zip writer and the assertion still passes.
    fake_db = tmp_path / "kanban.db"
    fake_db.write_text("SQLite format stuff\n")
    # The backup picks the path via `kanban_db_path` (helper below); pin
    # it to the fake so the test does not depend on the host's actual
    # `~/.claude-registry/kanban.db` (which may not exist or be read-only).
    monkeypatch.setattr(
        "app.services.backup_service.kanban_db_path",
        lambda: fake_db,
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

    # The backup zip must contain the kanban DB at its canonical path.
    with zipfile.ZipFile(backup.file_path) as zf:
        names = zf.namelist()
    # The entry is named relative to $HOME (the convention for the zip
    # archive). The fake DB lives at tmp_path, so its archive name is
    # the absolute path — the test should accept EITHER the relative
    # `~/.claude-registry/kanban.db` form (the production layout) or
    # the absolute path the test produces, so a future change to the
    # archive layout doesn't break this assertion.
    assert any(
        name.endswith("kanban.db") for name in names
    ), f"kanban DB missing from backup; archive entries: {names}"

    # And the file content must match the live DB so a restore can
    # actually replay it.
    with zipfile.ZipFile(backup.file_path) as zf:
        candidates = [n for n in zf.namelist() if n.endswith("kanban.db")]
        written = zf.read(candidates[0])
    assert written == fake_db.read_bytes()


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
