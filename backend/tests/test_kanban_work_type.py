"""Tests for the structured `work_type` field on KanbanCard.

Covers the additive migration in `_ensure_card_columns`, the create/update
materialization, and the HTTP round-trip. See
docs/cockpit/work-type-routing-analysis.md §5 (bouwsteen a).
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.kanban.db import _ensure_card_columns
from app.kanban.schemas import WORK_TYPES
from app.main import app
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _make_legacy_engine():
    """Schema as it existed before work_type was added: real, older kanban.db."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
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


async def test_ensure_card_columns_adds_work_type():
    engine = await _make_legacy_engine()
    try:
        async with engine.begin() as conn:
            rows = (await conn.exec_driver_sql("PRAGMA table_info(kanban_cards)")).fetchall()
            before = {r[1] for r in rows}
            assert "work_type" not in before

            await _ensure_card_columns(conn)

            rows = (await conn.exec_driver_sql("PRAGMA table_info(kanban_cards)")).fetchall()
            after = {r[1] for r in rows}
        assert "work_type" in after
    finally:
        await engine.dispose()


async def test_ensure_card_columns_is_idempotent_on_work_type():
    engine = await _make_legacy_engine()
    try:
        async with engine.begin() as conn:
            await _ensure_card_columns(conn)
        async with engine.begin() as conn:
            await _ensure_card_columns(conn)  # must not raise on a second run
    finally:
        await engine.dispose()


def test_work_types_constant_matches_doc():
    # Pin the four values defined in §5.1 of the routing analysis. Adding a new
    # value here is intentional; the frontend WORK_TYPES mirror must move in
    # lock-step (types.ts) or the Select dropdown silently drops the new value.
    assert WORK_TYPES == ["analysis", "feature", "bug", "chore"]


@pytest.mark.asyncio
async def test_create_card_with_work_type_round_trips():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "Investigate X", "work_type": "analysis",
                  "confirm_new_project": True})
        assert r.status_code == 201, r.text
        cid = r.json()["id"]
        assert r.json()["work_type"] == "analysis"

        # Labels and work_type are independent (per §5.2: both, not either/or).
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "Tagged card",
                  "labels": ["investigate"], "work_type": "bug"})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["labels"] == ["investigate"]
        assert body["work_type"] == "bug"

        # List also surfaces it
        r = await ac.get("/api/v1/kanban/cards", params={"project_key": "P"})
        items = r.json()["items"]
        by_id = {c["id"]: c for c in items}
        assert by_id[cid]["work_type"] == "analysis"
        assert by_id[body["id"]]["work_type"] == "bug"


@pytest.mark.asyncio
async def test_update_card_can_set_and_clear_work_type():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "t", "work_type": "feature",
                  "confirm_new_project": True})).json()["id"]
        assert (await ac.get(f"/api/v1/kanban/cards/{cid}")).json()["work_type"] == "feature"

        # Change to a different value
        r = await ac.patch(f"/api/v1/kanban/cards/{cid}", json={"work_type": "chore"})
        assert r.status_code == 200, r.text
        assert r.json()["work_type"] == "chore"

        # Clear it back to null
        r = await ac.patch(f"/api/v1/kanban/cards/{cid}", json={"work_type": None})
        assert r.status_code == 200, r.text
        assert r.json()["work_type"] is None


@pytest.mark.asyncio
async def test_work_type_survives_rematerialize():
    """rematerialize() rebuilds kanban_cards from the op-log. work_type must
    survive the replay, otherwise a DB rebuild silently drops the routing hint."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.operations import rematerialize

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "t",
                  "labels": ["x"], "work_type": "bug",
                  "confirm_new_project": True})).json()["id"]

        async with KanbanSessionLocal() as s:
            await rematerialize(s)
            await s.commit()

        r = await ac.get(f"/api/v1/kanban/cards/{cid}")
        body = r.json()
        assert body["work_type"] == "bug"
        assert body["labels"] == ["x"]