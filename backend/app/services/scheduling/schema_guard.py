"""Add new scheduled_messages columns in place, without dropping the DB.

The project has no migration framework (schema is created via create_all). When
we add columns to an existing install, we ALTER the table at startup so the
user's existing data survives. SQLite supports ADD COLUMN with a default.
"""
import logging
from sqlalchemy.ext.asyncio import AsyncEngine


logger = logging.getLogger(__name__)
_NEW_COLUMNS = {
    "target_kind": "VARCHAR(16) DEFAULT 'project'",
    "target_session_id": "VARCHAR(128)",
    "project_folder": "VARCHAR(255)",
    "session_preview": "TEXT",
    "sandcastle_config_id": "INTEGER",
}


_NEW_BACKUP_COLUMNS = {
    "is_automatic": "BOOLEAN DEFAULT 0 NOT NULL",
}


async def ensure_scheduled_message_columns(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        result = await conn.exec_driver_sql("PRAGMA table_info(scheduled_messages)")
        existing = {row[1] for row in result.fetchall()}
        for column, ddl in _NEW_COLUMNS.items():
            if column not in existing:
                await conn.exec_driver_sql(
                    f"ALTER TABLE scheduled_messages ADD COLUMN {column} {ddl}"
                )


async def ensure_backup_columns(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        result = await conn.exec_driver_sql("PRAGMA table_info(backups)")
        existing = {row[1] for row in result.fetchall()}
        for column, ddl in _NEW_BACKUP_COLUMNS.items():
            if column not in existing:
                await conn.exec_driver_sql(
                    f"ALTER TABLE backups ADD COLUMN {column} {ddl}"
                )
