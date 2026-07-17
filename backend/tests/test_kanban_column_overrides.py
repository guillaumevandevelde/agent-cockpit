"""Tests for the optional `column_overrides` JSON field on KanbanCard.

Per-agent-column (persona) model+provider override, shape:
    { "<column-name>": {"model": str|null, "provider": str|null} }

The dispatch-side precedence is covered in test_kanban_dispatch.py; this file
concentrates on the create/patch REST round-trip + rematerialize persistence,
mirroring test_kanban_metadata.py.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

SAMPLE = {
    "engineer": {"model": "sonnet-5", "provider": "anthropic"},
    "analyst": {"model": "opus", "provider": "anthropic"},
}


@pytest.mark.asyncio
async def test_create_card_with_column_overrides_round_trips():
    """POSTing column_overrides stores it and GET surfaces it unchanged."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Override card",
            "column_overrides": SAMPLE,
            "confirm_new_project": True,
        })
        assert r.status_code == 201, r.text
        assert r.json()["column_overrides"] == SAMPLE

        cid = r.json()["id"]
        r = await ac.get(f"/api/v1/kanban/cards/{cid}")
        assert r.json()["column_overrides"] == SAMPLE


@pytest.mark.asyncio
async def test_update_card_can_set_replace_and_clear_column_overrides():
    """PATCH column_overrides sets, replaces, and clears (None) end-to-end."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Plain card",
            "confirm_new_project": True,
        })).json()["id"]
        assert (await ac.get(f"/api/v1/kanban/cards/{cid}")).json()["column_overrides"] is None

        r = await ac.patch(f"/api/v1/kanban/cards/{cid}",
                           json={"column_overrides": {"engineer": {"provider": "minimax"}}})
        assert r.status_code == 200, r.text
        assert r.json()["column_overrides"] == {"engineer": {"provider": "minimax"}}

        r = await ac.patch(f"/api/v1/kanban/cards/{cid}",
                           json={"column_overrides": SAMPLE})
        assert r.status_code == 200, r.text
        assert r.json()["column_overrides"] == SAMPLE

        r = await ac.patch(f"/api/v1/kanban/cards/{cid}",
                           json={"column_overrides": None})
        assert r.status_code == 200, r.text
        assert r.json()["column_overrides"] is None


@pytest.mark.asyncio
async def test_column_overrides_defaults_to_null_when_omitted():
    """Cards created without column_overrides keep it null (backwards compatible)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
                          json={"project_key": "PROJ", "title": "No overrides",
                                "confirm_new_project": True})
        assert r.status_code == 201, r.text
        assert r.json()["column_overrides"] is None

        cid = r.json()["id"]
        r = await ac.get(f"/api/v1/kanban/cards/{cid}")
        assert r.json()["column_overrides"] is None


@pytest.mark.asyncio
async def test_column_overrides_survive_rematerialize():
    """rematerialize() rebuilds kanban_cards from the op-log; column_overrides
    must survive the replay so a DB rebuild doesn't silently drop it."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.operations import rematerialize

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Replay me",
            "column_overrides": SAMPLE,
            "confirm_new_project": True,
        })).json()["id"]
        assert (await ac.get(f"/api/v1/kanban/cards/{cid}")).json()["column_overrides"] == SAMPLE

        async with KanbanSessionLocal() as s:
            await rematerialize(s)
            await s.commit()

        r = await ac.get(f"/api/v1/kanban/cards/{cid}")
        assert r.json()["column_overrides"] == SAMPLE
