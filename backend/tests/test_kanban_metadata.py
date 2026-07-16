"""Tests for the optional `metadata` JSON bag on KanbanCard.

Free-form key/value bag for integration-specific data (external IDs,
workflow provenance, last-seen upstream commit sha, …). Mirrors the
claude-task-master task.metadata field. The card acceptance criteria ask
for create+patch round-trip coverage; this file concentrates on that.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

SAMPLE = {
    "external_id": "task-master:42",
    "source": "claude-task-master",
    "sha": "abc123",
}


@pytest.mark.asyncio
async def test_create_card_with_metadata_round_trips():
    """POSTing metadata stores it on the card and GET surfaces it unchanged."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Tagged card", "metadata": SAMPLE,
        })
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["metadata"] == SAMPLE

        cid = body["id"]
        r = await ac.get(f"/api/v1/kanban/cards/{cid}")
        assert r.json()["metadata"] == SAMPLE


@pytest.mark.asyncio
async def test_update_card_can_set_and_clear_metadata():
    """PATCH metadata sets, replaces, and clears (None) the bag end-to-end."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Plain card",
        })).json()["id"]
        assert (await ac.get(f"/api/v1/kanban/cards/{cid}")).json()["metadata"] is None

        # Set a bag.
        r = await ac.patch(f"/api/v1/kanban/cards/{cid}",
                           json={"metadata": {"external_id": "x"}})
        assert r.status_code == 200, r.text
        assert r.json()["metadata"] == {"external_id": "x"}

        # Replace with a different bag.
        r = await ac.patch(f"/api/v1/kanban/cards/{cid}",
                           json={"metadata": {"fresh": True, "n": 3}})
        assert r.status_code == 200, r.text
        assert r.json()["metadata"] == {"fresh": True, "n": 3}

        # Clear back to null via explicit None.
        r = await ac.patch(f"/api/v1/kanban/cards/{cid}",
                           json={"metadata": None})
        assert r.status_code == 200, r.text
        assert r.json()["metadata"] is None


@pytest.mark.asyncio
async def test_metadata_defaults_to_null_when_omitted():
    """Cards created without `metadata` keep it null on both stored and
    freshly-loaded views — the additive change must not retroactively
    coerce an existing-shaped payload."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
                          json={"project_key": "PROJ", "title": "No metadata"})
        assert r.status_code == 201, r.text
        assert r.json()["metadata"] is None

        cid = r.json()["id"]
        r = await ac.get(f"/api/v1/kanban/cards/{cid}")
        assert r.json()["metadata"] is None


@pytest.mark.asyncio
async def test_metadata_survives_rematerialize():
    """rematerialize() rebuilds kanban_cards from the op-log. metadata must
    survive the replay, otherwise a DB rebuild silently drops the bag."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.operations import rematerialize

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Replay me", "metadata": SAMPLE,
        })).json()["id"]
        assert (await ac.get(f"/api/v1/kanban/cards/{cid}")).json()["metadata"] == SAMPLE

        async with KanbanSessionLocal() as s:
            await rematerialize(s)
            await s.commit()

        r = await ac.get(f"/api/v1/kanban/cards/{cid}")
        assert r.json()["metadata"] == SAMPLE


# --- MCP layer ---------------------------------------------------------------
# The acceptance criteria also require that the MCP create_card / update_card
# tools expose the same field. The MCP layer round-trips through the same
# op-log + materialize path as the REST API, so the tests above already prove
# the persistence; these only assert the MCP wrapper accepts and surfaces it.

from app.kanban import mcp_server as m  # noqa: E402  (import after SAMPLE for grouping)


@pytest.mark.asyncio
async def test_mcp_create_card_accepts_metadata_and_round_trips():
    created = await m.create_card("PROJ", "Tagged via MCP", metadata=SAMPLE, confirm_new_project=True)
    assert created["metadata"] == SAMPLE
    fetched = await m.get_card(created["id"])
    assert fetched["metadata"] == SAMPLE


@pytest.mark.asyncio
async def test_mcp_update_card_accepts_metadata_and_round_trips():
    cid = (await m.create_card("PROJ", "Plain via MCP", confirm_new_project=True))["id"]
    assert (await m.get_card(cid))["metadata"] is None

    updated = await m.update_card(cid, metadata={"sha": "def456"})
    assert updated["metadata"] == {"sha": "def456"}

    # Replace it.
    replaced = await m.update_card(cid, metadata={"sha": "ghi789", "extra": 1})
    assert replaced["metadata"] == {"sha": "ghi789", "extra": 1}


@pytest.mark.asyncio
async def test_mcp_metadata_omitted_stays_none():
    """Omitting metadata on MCP create must leave the column None."""
    card = await m.create_card("PROJ", "Standalone", confirm_new_project=True)
    assert card["metadata"] is None
