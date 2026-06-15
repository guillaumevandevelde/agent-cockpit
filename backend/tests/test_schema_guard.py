import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from app.services.scheduling.schema_guard import ensure_scheduled_message_columns

NEW = {"target_kind", "target_session_id", "project_folder", "session_preview"}


@pytest.mark.asyncio
async def test_adds_missing_columns_idempotently(tmp_path):
    db = tmp_path / "old.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    # Simulate an old DB whose table predates the new columns.
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE scheduled_messages (id INTEGER PRIMARY KEY, message TEXT)"
        )
    await ensure_scheduled_message_columns(engine)
    await ensure_scheduled_message_columns(engine)  # second run must not error
    async with engine.begin() as conn:
        result = await conn.exec_driver_sql("PRAGMA table_info(scheduled_messages)")
        cols = {row[1] for row in result.fetchall()}
    await engine.dispose()
    assert NEW <= cols
