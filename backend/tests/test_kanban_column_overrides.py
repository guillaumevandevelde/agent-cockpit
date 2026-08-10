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


# ---- subagent_caps: per-column Claude Code subagent caps ------------------
#
# Per-column knobs (kanban card aaa81b23…): the dispatcher translates
# ``column_overrides[col].subagent_caps`` into CLAUDE_CODE_MAX_* env vars so
# high-trust lanes can opt into deeper nesting while cost-sensitive lanes
# stay at depth 1. Allowed keys mirror Claude Code 2.1.217+
# (CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH, …_CONCURRENT_SUBAGENTS,
#  …_SUBAGENTS_PER_SESSION, …_WEB_SEARCHES_PER_SESSION). Unknown keys fail
# loud at the API boundary so a UI typo never silently spawns on platform
# defaults.

_SUBAGENT_CAPS_SAMPLE = {
    "engineer": {
        "model": "sonnet-5",
        "provider": "anthropic",
        "subagent_caps": {"max_spawn_depth": 3, "max_concurrent": 20},
    },
}


@pytest.mark.asyncio
async def test_subagent_caps_round_trip_when_keys_known():
    """A well-formed subagent_caps dict (allowed keys only) round-trips through
    the create/get REST path unchanged."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Caps card",
            "column_overrides": _SUBAGENT_CAPS_SAMPLE,
            "confirm_new_project": True,
        })
        assert r.status_code == 201, r.text
        assert r.json()["column_overrides"] == _SUBAGENT_CAPS_SAMPLE

        cid = r.json()["id"]
        r = await ac.get(f"/api/v1/kanban/cards/{cid}")
        assert r.json()["column_overrides"] == _SUBAGENT_CAPS_SAMPLE


@pytest.mark.asyncio
async def test_subagent_caps_rejects_unknown_keys():
    """An unknown key (e.g. max_banana) is rejected at the API boundary with a
    clear 422 pointing at the offending key — a silent pass would spawn on
    platform defaults and hide the bug until production."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Bad caps",
            "column_overrides": {
                "engineer": {"subagent_caps": {"max_banana": 5}},
            },
            "confirm_new_project": True,
        })
        assert r.status_code == 422, r.text
        body = r.text
        assert "max_banana" in body


@pytest.mark.asyncio
async def test_subagent_caps_rejects_non_integer_values():
    """Non-int values are rejected — passing a string would otherwise fall
    through and reach the spawned CLI as env value that the CLI itself then
    fails to parse."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "String depth",
            "column_overrides": {
                "engineer": {"subagent_caps": {"max_spawn_depth": "three"}},
            },
            "confirm_new_project": True,
        })
        assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_subagent_caps_depth_out_of_range_is_rejected():
    """max_spawn_depth > 3 is rejected — Claude Code 2.1.217 caps at 3 and a
    higher value silently falls back to the platform default rather than
    honouring the override (the env var is read as int with a max-3 clamp)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Too deep",
            "column_overrides": {
                "engineer": {"subagent_caps": {"max_spawn_depth": 99}},
            },
            "confirm_new_project": True,
        })
        assert r.status_code == 422, r.text
