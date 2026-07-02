"""Edge cases for BackupService: corrupt archives, invalid manifest JSON,
missing files, and restore/delete of unknown backups. The service must report
failure cleanly rather than raise into the API layer."""
import zipfile

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models.database import Backup
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


async def _add_backup(db, file_path, name="b", scope="user"):
    backup = Backup(name=name, file_path=str(file_path), scope=scope, size_bytes=0)
    db.add(backup)
    await db.commit()
    await db.refresh(backup)
    return backup


@pytest.mark.asyncio
async def test_validate_backup_not_found(db):
    svc = BackupService(db)
    ok, issues = await svc.validate_backup(9999)
    assert ok is False
    assert issues == ["Backup not found"]


@pytest.mark.asyncio
async def test_validate_backup_file_missing_on_disk(db, tmp_path):
    svc = BackupService(db)
    backup = await _add_backup(db, tmp_path / "gone.zip")
    ok, issues = await svc.validate_backup(backup.id)
    assert ok is False
    assert issues == ["Backup file not found on disk"]


@pytest.mark.asyncio
async def test_validate_backup_corrupt_archive(db, tmp_path):
    bad = tmp_path / "corrupt.zip"
    bad.write_bytes(b"this is definitely not a zip archive")
    svc = BackupService(db)
    backup = await _add_backup(db, bad)
    ok, issues = await svc.validate_backup(backup.id)
    assert ok is False
    assert issues == ["Backup file is corrupted"]


@pytest.mark.asyncio
async def test_validate_backup_flags_missing_manifest(db, tmp_path):
    # A structurally valid zip that lacks manifest.json is an older-format backup.
    archive = tmp_path / "nomanifest.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("some/file.txt", "data")
    svc = BackupService(db)
    backup = await _add_backup(db, archive)
    ok, issues = await svc.validate_backup(backup.id)
    assert ok is False
    assert any("manifest.json" in i for i in issues)


def test_get_manifest_returns_none_on_invalid_json(tmp_path):
    archive = tmp_path / "badmanifest.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("manifest.json", "{ not valid json")
    svc = BackupService(db=None)  # get_manifest_from_backup does not touch the db
    assert svc.get_manifest_from_backup(str(archive)) is None


def test_get_manifest_returns_none_for_missing_file(tmp_path):
    svc = BackupService(db=None)
    assert svc.get_manifest_from_backup(str(tmp_path / "nope.zip")) is None


@pytest.mark.asyncio
async def test_create_backup_raises_when_no_config_paths(db):
    # project scope without a project_path yields no files to back up.
    svc = BackupService(db)
    with pytest.raises(ValueError, match="No configuration files"):
        await svc.create_backup(name="empty", scope="project", project_path=None)


@pytest.mark.asyncio
async def test_restore_unknown_backup_reports_failure(db):
    svc = BackupService(db)
    result = await svc.restore_backup(9999)
    assert result.success is False
    assert result.message == "Backup not found"


@pytest.mark.asyncio
async def test_delete_unknown_backup_returns_false(db):
    svc = BackupService(db)
    assert await svc.delete_backup(9999) is False


@pytest.mark.asyncio
async def test_restore_plan_none_for_unknown_backup(db):
    svc = BackupService(db)
    assert await svc.get_restore_plan(9999) is None


@pytest.mark.asyncio
async def test_restore_plan_none_when_archive_missing(db, tmp_path):
    svc = BackupService(db)
    backup = await _add_backup(db, tmp_path / "vanished.zip")
    assert await svc.get_restore_plan(backup.id) is None


@pytest.mark.asyncio
async def test_export_config_raises_with_only_nonexistent_paths(db, tmp_path):
    svc = BackupService(db)
    with pytest.raises(ValueError, match="No valid paths"):
        await svc.export_config([str(tmp_path / "missing-a"), str(tmp_path / "missing-b")])
