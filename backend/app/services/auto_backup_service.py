"""Service for scheduled automatic backups with a retention/rotation policy."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import AutoBackupSettings, Backup
from app.models.schemas import AutoBackupSettingsUpdate
from app.services.backup_service import BackupService

logger = logging.getLogger(__name__)

AUTO_BACKUP_NAME = "auto-backup"
_VALID_SCOPES = {"user", "full"}


def _parse_time_of_day(value: str) -> Tuple[int, int]:
    """Parse "HH:MM" into (hour, minute), raising ValueError when invalid."""
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError("time_of_day must be in HH:MM format")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time_of_day must be a valid 24h time")
    return hour, minute


async def get_or_create_settings(db: AsyncSession) -> AutoBackupSettings:
    """Return the singleton settings row, creating it with defaults if absent."""
    settings = (
        await db.execute(select(AutoBackupSettings).where(AutoBackupSettings.id == 1))
    ).scalar_one_or_none()
    if settings is None:
        settings = AutoBackupSettings(id=1)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return settings


async def update_settings(
    db: AsyncSession, update: AutoBackupSettingsUpdate
) -> AutoBackupSettings:
    """Apply a partial update to the auto-backup settings, validating fields."""
    settings = await get_or_create_settings(db)

    if update.scope is not None:
        if update.scope not in _VALID_SCOPES:
            raise ValueError("scope must be 'user' or 'full'")
        settings.scope = update.scope
    if update.time_of_day is not None:
        _parse_time_of_day(update.time_of_day)  # validate
        settings.time_of_day = update.time_of_day
    if update.timezone is not None:
        settings.timezone = update.timezone
    if update.retention_days is not None:
        if update.retention_days < 1:
            raise ValueError("retention_days must be at least 1")
        settings.retention_days = update.retention_days
    if update.project_path is not None:
        settings.project_path = update.project_path or None
    if update.enabled is not None:
        settings.enabled = update.enabled

    if settings.enabled and settings.scope == "full" and not settings.project_path:
        raise ValueError("project_path is required for 'full' scope")

    await db.commit()
    await db.refresh(settings)
    return settings


async def apply_rotation(db: AsyncSession, retention_days: int) -> int:
    """Delete automatic backups older than retention_days. Returns count removed."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    rows = (
        await db.execute(
            select(Backup).where(
                Backup.is_automatic.is_(True),
                Backup.created_at < cutoff,
            )
        )
    ).scalars().all()

    service = BackupService(db)
    deleted = 0
    for backup in rows:
        if await service.delete_backup(backup.id):
            deleted += 1
    return deleted


async def run_auto_backup(db: AsyncSession) -> Optional[Backup]:
    """Create an automatic backup (if enabled) and apply the rotation policy.

    Returns the created Backup, or None when auto-backups are disabled.
    Records the outcome on the settings row.
    """
    settings = await get_or_create_settings(db)
    if not settings.enabled:
        return None

    backup: Optional[Backup] = None
    try:
        service = BackupService(db)
        backup, _ = await service.create_backup(
            name=AUTO_BACKUP_NAME,
            scope=settings.scope,
            project_path=settings.project_path,
            description="Automatic scheduled backup",
            is_automatic=True,
        )
        deleted = await apply_rotation(db, settings.retention_days)
        settings.last_status = (
            f"success (created #{backup.id}, removed {deleted} old)"
        )
        settings.last_backup_id = backup.id
    except Exception as exc:  # noqa: BLE001 - surfaced via last_status
        logger.exception("automatic backup failed")
        settings.last_status = f"error: {exc}"
    finally:
        settings.last_run_at = datetime.now(timezone.utc)
        await db.commit()

    return backup


async def run_auto_backup_job() -> None:
    """Scheduler entrypoint: open a session and run the automatic backup."""
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await run_auto_backup(db)
