"""Tests for the scheduled automatic-backup service."""
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
from app.models.database import Backup
from app.models.schemas import AutoBackupSettingsUpdate
from app.services import auto_backup_service as svc


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
async def test_get_or_create_returns_defaults(db):
    settings = await svc.get_or_create_settings(db)
    assert settings.enabled is False
    assert settings.scope == "user"
    assert settings.retention_days == 7
    assert settings.time_of_day == "03:00"


@pytest.mark.asyncio
async def test_update_settings_validates_and_persists(db):
    settings = await svc.update_settings(
        db,
        AutoBackupSettingsUpdate(enabled=True, time_of_day="02:30", retention_days=14),
    )
    assert settings.enabled is True
    assert settings.time_of_day == "02:30"
    assert settings.retention_days == 14


@pytest.mark.asyncio
async def test_update_settings_rejects_bad_time(db):
    with pytest.raises(ValueError):
        await svc.update_settings(db, AutoBackupSettingsUpdate(time_of_day="25:00"))


@pytest.mark.asyncio
async def test_update_settings_full_scope_requires_project_path(db):
    with pytest.raises(ValueError):
        await svc.update_settings(
            db, AutoBackupSettingsUpdate(enabled=True, scope="full")
        )


@pytest.mark.asyncio
async def test_run_auto_backup_disabled_returns_none(db):
    result = await svc.run_auto_backup(db)
    assert result is None


@pytest.mark.asyncio
async def test_run_auto_backup_creates_automatic_backup(db, monkeypatch, tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text("{}", encoding="utf-8")
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()

    from app.services import backup_service as bs
    monkeypatch.setattr(bs, "get_backup_storage_dir", lambda: backups_dir)
    monkeypatch.setattr(
        bs.BackupService, "_get_user_config_paths", lambda self: [config_file]
    )

    await svc.update_settings(db, AutoBackupSettingsUpdate(enabled=True))
    backup = await svc.run_auto_backup(db)

    assert backup is not None
    assert backup.is_automatic is True
    assert backup.name == svc.AUTO_BACKUP_NAME

    settings = await svc.get_or_create_settings(db)
    assert settings.last_backup_id == backup.id
    assert settings.last_run_at is not None
    assert "success" in settings.last_status


@pytest.mark.asyncio
async def test_create_backup_survives_info_logging(db, monkeypatch, tmp_path, caplog):
    """create_backup must not crash when INFO logging is actually emitted.

    backup_service logged with extra={"name": ...}; "name" is a reserved LogRecord
    field, so makeRecord raises KeyError -- but only once INFO is enabled, which it
    is in production (structured logging at INFO). In an isolated test the logger
    sits at WARNING so logger.info is a no-op and the bug hides; this test forces
    INFO so the regression is caught deterministically, not by test ordering.
    """
    import logging

    config_file = tmp_path / "config.json"
    config_file.write_text("{}", encoding="utf-8")
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()

    from app.services import backup_service as bs
    monkeypatch.setattr(bs, "get_backup_storage_dir", lambda: backups_dir)
    monkeypatch.setattr(
        bs.BackupService, "_get_user_config_paths", lambda self: [config_file]
    )

    await svc.update_settings(db, AutoBackupSettingsUpdate(enabled=True))
    with caplog.at_level(logging.INFO, logger="app.services.backup_service"):
        backup = await svc.run_auto_backup(db)

    assert backup is not None
    assert "success" in (await svc.get_or_create_settings(db)).last_status


@pytest.mark.asyncio
async def test_rotation_deletes_old_automatic_only(db):
    now = datetime.now(UTC)
    old_auto = Backup(
        name="auto-backup", file_path="/nonexistent/old_auto.zip", scope="user",
        size_bytes=1, is_automatic=True, created_at=now - timedelta(days=10),
    )
    recent_auto = Backup(
        name="auto-backup", file_path="/nonexistent/recent_auto.zip", scope="user",
        size_bytes=1, is_automatic=True, created_at=now - timedelta(days=2),
    )
    old_manual = Backup(
        name="manual", file_path="/nonexistent/old_manual.zip", scope="user",
        size_bytes=1, is_automatic=False, created_at=now - timedelta(days=30),
    )
    db.add_all([old_auto, recent_auto, old_manual])
    await db.commit()

    deleted = await svc.apply_rotation(db, retention_days=7)
    assert deleted == 1

    from sqlalchemy import select
    remaining = (await db.execute(select(Backup))).scalars().all()
    names = sorted(b.name for b in remaining)
    assert names == ["auto-backup", "manual"]  # recent auto + manual survive
