"""Tests for the public PATCH /api/v1/kanban/columns/{id} contract.

Specifically: the endpoint must distinguish 'field not sent' from 'field set to
null', so a column-update PATCH carrying `max_sessions: null` actually clears
the existing cap (the column-pause UI's ∞ button). The same applies to the
nullable default_agent / default_provider / default_model fields.

The latently-broken shape lived at `service.update_column` doing
`if v is not None: setattr(...)` — every explicit null was silently dropped.
The new shape uses `payload.model_dump(exclude_unset=True)` (matches the rest
of the codebase, e.g. PATCH /cards/{cid}, scheduled_messages PATCH, security
PATCH, project_service.update).
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.mark.asyncio
async def test_patch_column_can_clear_max_sessions_with_null():
    """`∞` in the UI PATCHes {max_sessions: null} — that null must land."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
            "max_sessions": 2,
        })).json()["id"]
        assert (await ac.get("/api/v1/kanban/columns",
                             params={"project_key": "PROJ"})
                ).json()["columns"][0]["max_sessions"] == 2

        r = await ac.patch(f"/api/v1/kanban/columns/{cid}",
                           json={"max_sessions": None})
        assert r.status_code == 200, r.text
        assert r.json()["max_sessions"] is None

        # And the persisted value (re-GET) is null — not the old cap.
        listing = (await ac.get("/api/v1/kanban/columns",
                                params={"project_key": "PROJ"})
                   ).json()["columns"]
        assert listing[0]["max_sessions"] is None


@pytest.mark.asyncio
async def test_patch_column_can_clear_default_agent_with_null():
    """`null` for default_agent is honoured end-to-end (same exclude_unset gap)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
            "default_agent": "engineer",
        })).json()["id"]
        assert (await ac.get("/api/v1/kanban/columns",
                             params={"project_key": "PROJ"})
                ).json()["columns"][0]["default_agent"] == "engineer"

        r = await ac.patch(f"/api/v1/kanban/columns/{cid}",
                           json={"default_agent": None})
        assert r.status_code == 200, r.text
        assert r.json()["default_agent"] is None


@pytest.mark.asyncio
async def test_patch_column_can_clear_default_provider_with_null():
    """`null` for default_provider is honoured end-to-end."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
            "default_provider": "minimax",
        })).json()["id"]
        assert (await ac.get("/api/v1/kanban/columns",
                             params={"project_key": "PROJ"})
                ).json()["columns"][0]["default_provider"] == "minimax"

        r = await ac.patch(f"/api/v1/kanban/columns/{cid}",
                           json={"default_provider": None})
        assert r.status_code == 200, r.text
        assert r.json()["default_provider"] is None


@pytest.mark.asyncio
async def test_patch_column_can_clear_default_model_with_null():
    """`null` for default_model is honoured end-to-end."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
            "default_model": "opus",
        })).json()["id"]
        assert (await ac.get("/api/v1/kanban/columns",
                             params={"project_key": "PROJ"})
                ).json()["columns"][0]["default_model"] == "opus"

        r = await ac.patch(f"/api/v1/kanban/columns/{cid}",
                           json={"default_model": None})
        assert r.status_code == 200, r.text
        assert r.json()["default_model"] is None


@pytest.mark.asyncio
async def test_patch_column_omitted_fields_are_left_alone():
    """A PATCH that only mentions max_sessions must not touch default_agent."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
            "default_agent": "engineer",
            "default_model": "opus",
            "max_sessions": 3,
        })).json()["id"]

        # Only change max_sessions.
        r = await ac.patch(f"/api/v1/kanban/columns/{cid}",
                           json={"max_sessions": 7})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["max_sessions"] == 7
        assert body["default_agent"] == "engineer"
        assert body["default_model"] == "opus"


@pytest.mark.asyncio
async def test_patch_column_can_set_pause_via_zero():
    """`0` (Pause) and `null` (∞) are distinct values — both must round-trip.

    The column-pause UI sends `max_sessions: 0` to pause the column. The
    pause is interpreted by the dispatcher as 'no new sessions'; the value
    itself is persisted verbatim. This test guards against a regression where
    `0` is treated like `null` (or vice versa) at the API layer.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
        })).json()["id"]
        # Pause
        r = await ac.patch(f"/api/v1/kanban/columns/{cid}",
                           json={"max_sessions": 0})
        assert r.status_code == 200, r.text
        assert r.json()["max_sessions"] == 0

        # ∞ (clear the cap)
        r = await ac.patch(f"/api/v1/kanban/columns/{cid}",
                           json={"max_sessions": None})
        assert r.status_code == 200, r.text
        assert r.json()["max_sessions"] is None

        # A real cap (still works alongside the null-path)
        r = await ac.patch(f"/api/v1/kanban/columns/{cid}",
                           json={"max_sessions": 4})
        assert r.status_code == 200, r.text
        assert r.json()["max_sessions"] == 4