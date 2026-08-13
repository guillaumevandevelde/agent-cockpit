"""Add new columns in place, without dropping the DB.

The project has no migration framework (schema is created via create_all). When
we add columns to an existing install, we ALTER the table at startup so the
user's existing data survives. SQLite supports ADD COLUMN with a default.
"""
import logging

from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


_NEW_BACKUP_COLUMNS = {
    "is_automatic": "BOOLEAN DEFAULT 0 NOT NULL",
}


async def ensure_backup_columns(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        result = await conn.exec_driver_sql("PRAGMA table_info(backups)")
        existing = {row[1] for row in result.fetchall()}
        for column, ddl in _NEW_BACKUP_COLUMNS.items():
            if column not in existing:
                await conn.exec_driver_sql(
                    f"ALTER TABLE backups ADD COLUMN {column} {ddl}"
                )


async def ensure_model_columns(engine: AsyncEngine) -> None:
    """Ensure every column defined in ORM models exists in the database.

    Runs at startup and patches any missing columns via ALTER TABLE ADD COLUMN.
    Currently handles DateTime columns with a CURRENT_TIMESTAMP default, which
    covers the common case of adding updated_at (or similar) to existing tables.
    """
    from sqlalchemy import DateTime

    # Trigger model registration (idempotent if already imported by main.py)
    import app.models.database  # noqa: F401
    import app.models.mcp_token  # noqa: F401
    import app.models.sandcastle  # noqa: F401
    from app.database import Base

    async with engine.begin() as conn:
        for table_name, table in Base.metadata.tables.items():
            result = await conn.exec_driver_sql(f'PRAGMA table_info("{table_name}")')
            existing_cols = {row[1] for row in result.fetchall()}
            if not existing_cols:
                # Table doesn't exist yet — create_all will handle it
                continue
            for col in table.columns:
                if col.name in existing_cols:
                    continue
                if isinstance(col.type, DateTime):
                    ddl = "DATETIME DEFAULT CURRENT_TIMESTAMP"
                else:
                    # Skip complex types (FK, JSON, etc.) — add specific guards above if needed
                    logger.warning(
                        "Skipping missing column %s.%s (type %s): add a specific guard",
                        table_name, col.name, type(col.type).__name__,
                    )
                    continue
                await conn.exec_driver_sql(
                    f'ALTER TABLE "{table_name}" ADD COLUMN {col.name} {ddl}'
                )
                logger.info("Added column %s.%s", table_name, col.name)
