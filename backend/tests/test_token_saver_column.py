"""Tests for the per-lane ``token_saver_enabled`` column on ``kanban_columns``.

Acceptance criterion #1 from
``docs/superpowers/specs/2026-07-24-token-saver-integration-design.md``:
the flag is opt-in, default off, additive-migrated without touching
existing rows.

This file covers only the schema + ORM surface. The dispatch-side
behaviour (helper, kill-switch, activity-feed comments) lives in
``test_token_saver.py`` and ``test_dispatch_token_saver_integration.py``.
"""
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.kanban.models import KanbanColumn
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.mark.asyncio
async def test_column_has_token_saver_enabled_attribute():
    """``KanbanColumn`` exposes the new boolean as a public ORM attribute.

    Surfaces as ``int`` (SQLite 0/1); the dispatch helper coerces to bool.
    """
    assert hasattr(KanbanColumn, "token_saver_enabled"), (
        "KanbanColumn.token_saver_enabled missing; "
        "add the Mapped[int] field to the model."
    )


@pytest.mark.asyncio
async def test_new_column_defaults_token_saver_enabled_to_false():
    """A freshly-created column has the flag off — never on by default.

    Pinning the default at the schema level means a pre-existing row that
    the migration back-fills with ``DEFAULT 0`` round-trips as ``False``
    without a per-row backfill script.
    """
    async with KanbanSessionLocal() as s:
        col = KanbanColumn(
            id="col1", project_key="PROJ", name="engineer", rank="0000",
        )
        s.add(col)
        await s.commit()

    async with KanbanSessionLocal() as s:
        row = (await s.execute(
            text("SELECT token_saver_enabled FROM kanban_columns WHERE id=:i"),
            {"i": "col1"},
        )).first()
    assert row is not None
    assert row[0] == 0


@pytest.mark.asyncio
async def test_token_saver_enabled_round_trips_through_orm():
    """Set the flag to True and read it back unchanged."""
    async with KanbanSessionLocal() as s:
        col = KanbanColumn(
            id="col1", project_key="PROJ", name="engineer", rank="0000",
            token_saver_enabled=1,
        )
        s.add(col)
        await s.commit()

    async with KanbanSessionLocal() as s:
        loaded = (await s.get(KanbanColumn, "col1"))
    assert loaded is not None
    assert loaded.token_saver_enabled == 1


@pytest.mark.asyncio
async def test_migration_is_additive_when_column_already_exists():
    """Calling ``_ensure_column_table`` twice does not raise on the second call.

    Mirrors the existing additive-migration contract for ``default_provider``,
    ``max_sessions``, etc. (see ``db.py``). A second invocation must hit
    the early-return path that the ``if "token_saver_enabled" not in cols``
    guard installs.
    """
    from app.kanban.db import _ensure_column_table
    from app.kanban.models import KanbanBase

    # Reset (clean baseline) then run twice.
    async with KanbanSessionLocal() as s:
        # The engine used by TestSessionLocal is shared; calling begin()
        # through the session-binding that fixtures establish requires us
        # to use the underlying connection. We pull it from the test engine
        # by name; kanban_test_db exposes it as ``test_engine``.
        from tests.kanban_test_db import test_engine
        async with test_engine.begin() as conn:
            await _ensure_column_table(conn)
            # The second call must not raise. If the migration is not
            # properly idempotent this is where an OperationalError appears.
            try:
                await _ensure_column_table(conn)
            except OperationalError as e:
                pytest.fail(
                    f"_ensure_column_table is not idempotent: {e}"
                )
