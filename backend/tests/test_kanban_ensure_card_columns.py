"""Bug: the kanban board failed to load with
``sqlite3.OperationalError: no such column: kanban_cards.analyst_agent_id``.

Root cause: analyst_agent_id, executor_agent_id, parent_card_id, analyst_run_id
and depends_on were added to the KanbanCard model (phase-aware analyst/dispatch
work) but ``_ensure_card_columns`` — the additive, idempotent migration that
patches a *pre-existing* kanban.db on startup (create_all never alters existing
tables) — was never updated to add them. Any board created before that change
kept the old schema forever and every query against kanban_cards broke.

These tests build a table with the pre-change schema (mirroring a real db that
predates the new columns) and assert ``_ensure_card_columns`` adds each new
column, the same way it already does for the older ones like ``agent``.
"""
from sqlalchemy.ext.asyncio import create_async_engine

from app.kanban.db import _ensure_card_columns

NEW_COLUMNS = {
    "analyst_agent_id",
    "executor_agent_id",
    "parent_card_id",
    "analyst_run_id",
    "depends_on",
}


async def _make_legacy_engine():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        # Schema as it existed before the phase-aware analyst/dispatch columns
        # were added to the model — i.e. what a real, older kanban.db has.
        await conn.exec_driver_sql(
            """
            CREATE TABLE kanban_cards (
                id VARCHAR(64) PRIMARY KEY,
                project_key VARCHAR(512) NOT NULL,
                title VARCHAR(512) NOT NULL,
                description TEXT NOT NULL,
                "column" VARCHAR(32) NOT NULL,
                rank VARCHAR(64) NOT NULL,
                priority VARCHAR(16),
                labels JSON,
                agent VARCHAR(64),
                transport VARCHAR(16),
                resume_session_id VARCHAR(256),
                resume_project_folder VARCHAR(512),
                scheduled_at VARCHAR(40),
                dispatch_failures INTEGER NOT NULL DEFAULT 0,
                claimed_by VARCHAR(256),
                claimed_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )
    return engine


async def test_ensure_card_columns_adds_missing_analyst_dispatch_columns():
    engine = await _make_legacy_engine()
    try:
        async with engine.begin() as conn:
            rows = (await conn.exec_driver_sql("PRAGMA table_info(kanban_cards)")).fetchall()
            before = {r[1] for r in rows}
            assert not (NEW_COLUMNS & before)  # sanity: legacy schema lacks them

            await _ensure_card_columns(conn)

            rows = (await conn.exec_driver_sql("PRAGMA table_info(kanban_cards)")).fetchall()
            after = {r[1] for r in rows}
        assert after >= NEW_COLUMNS
    finally:
        await engine.dispose()


async def test_ensure_card_columns_is_idempotent_on_new_schema():
    """Running it twice (e.g. two backend restarts) must not raise
    "duplicate column" errors."""
    engine = await _make_legacy_engine()
    try:
        async with engine.begin() as conn:
            await _ensure_card_columns(conn)
        async with engine.begin() as conn:
            await _ensure_card_columns(conn)  # must not raise
    finally:
        await engine.dispose()
