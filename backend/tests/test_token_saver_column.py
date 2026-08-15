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


# De idempotentie-test voor `_ensure_column_table` is op 2026-08-15 verwijderd
# samen met die functie. Een oudere database naar de huidige vorm brengen is nu
# alembic's werk; gedekt door
# tests/test_db_bootstrap.py::test_pre_alembic_database_is_adopted.
