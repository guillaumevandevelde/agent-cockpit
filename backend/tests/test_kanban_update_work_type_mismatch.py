# backend/tests/test_kanban_update_work_type_mismatch.py
"""Tests for surfacing a work_type/agent routing mismatch on `update_card`.

Root cause (see the "[problem] update_card laat work_type wijzigen zonder agent
te her-resolven" card): `resolve_create_agent`'s "explicit agent wins over
work_type" rule is correct for create, but the PATCH/update path applies the
same rule with no visibility. When a card's `work_type` is changed but its
pinned `agent` is left untouched, dispatch keeps honouring the stale agent over
the new work_type mapping — silently routing to the wrong persona.

Contract:
  * Dispatch behaviour is unchanged — the pinned `agent` still wins (that rule
    is intentional). This only makes the otherwise-silent decision visible.
  * When `work_type` changes to one whose persona differs from the card's
    current `agent`, and `agent` is not set in the same call, a visible
    "Routing mismatch" comment is posted to the card's activity feed.
  * No comment when the resulting persona already matches the pinned agent,
    when the card has no pinned agent, when `agent` is set in the same call,
    or when `work_type` is cleared to null.

The autouse `_reset_test_db` fixture lives in tests/conftest.py.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.kanban import service
from app.kanban.db import KanbanSessionLocal
from app.main import app

# ---- service-layer tests -----------------------------------------------------


@pytest.mark.asyncio
async def test_mismatch_comment_none_when_no_agent_pinned():
    async with KanbanSessionLocal() as s:
        assert await service.work_type_agent_mismatch_comment(
            s, "PROJ", new_work_type="analysis", current_agent=None,
        ) is None
        assert await service.work_type_agent_mismatch_comment(
            s, "PROJ", new_work_type="analysis", current_agent="   ",
        ) is None


@pytest.mark.asyncio
async def test_mismatch_comment_none_when_agent_matches_persona():
    async with KanbanSessionLocal() as s:
        # analysis → analyst by default; pinned agent already analyst → no warning
        assert await service.work_type_agent_mismatch_comment(
            s, "PROJ", new_work_type="analysis", current_agent="analyst",
        ) is None


@pytest.mark.asyncio
async def test_mismatch_comment_none_when_work_type_cleared():
    async with KanbanSessionLocal() as s:
        assert await service.work_type_agent_mismatch_comment(
            s, "PROJ", new_work_type=None, current_agent="engineer",
        ) is None


@pytest.mark.asyncio
async def test_mismatch_comment_returned_on_conflict():
    async with KanbanSessionLocal() as s:
        got = await service.work_type_agent_mismatch_comment(
            s, "PROJ", new_work_type="analysis", current_agent="engineer",
        )
        assert got is not None
        assert "analysis" in got
        assert "analyst" in got   # the persona the work_type maps to
        assert "engineer" in got  # the pinned agent that still wins


@pytest.mark.asyncio
async def test_mismatch_comment_honours_per_project_override():
    async with KanbanSessionLocal() as s:
        # Override bug → analyst for this project. A card pinned to engineer with
        # work_type=bug now mismatches (default would have matched).
        await service.upsert_work_type_mapping(s, "PROJ", "bug", "analyst")
        await s.commit()
        got = await service.work_type_agent_mismatch_comment(
            s, "PROJ", new_work_type="bug", current_agent="engineer",
        )
        assert got is not None and "analyst" in got
        # Same work_type, agent already analyst → the override makes them match.
        assert await service.work_type_agent_mismatch_comment(
            s, "PROJ", new_work_type="bug", current_agent="analyst",
        ) is None


# ---- REST endpoint tests -----------------------------------------------------


async def _activity_texts(ac: AsyncClient, cid: str) -> list[str]:
    r = await ac.get(f"/api/v1/kanban/cards/{cid}/activity")
    assert r.status_code == 200, r.text
    return [
        e["payload"].get("text", "")
        for e in r.json()
        if e["op_type"] == "comment"
    ]


@pytest.mark.asyncio
async def test_patch_work_type_posts_mismatch_comment():
    """The evidence scenario: card pinned to engineer, work_type PATCHed to
    analysis — a visible mismatch comment must appear, and agent stays engineer."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Onderzoek: reviewer-agent",
            "agent": "engineer",
            "confirm_new_project": True,
        })).json()["id"]

        r = await ac.patch(f"/api/v1/kanban/cards/{cid}", json={"work_type": "analysis"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["work_type"] == "analysis"
        assert body["agent"] == "engineer", "dispatch behaviour unchanged: agent still wins"

        texts = await _activity_texts(ac, cid)
        assert any("Routing mismatch" in t for t in texts), texts
        mismatch = next(t for t in texts if "Routing mismatch" in t)
        assert "analyst" in mismatch and "engineer" in mismatch


@pytest.mark.asyncio
async def test_patch_work_type_no_comment_when_agent_matches():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Feature card", "agent": "engineer",
            "confirm_new_project": True,
        })).json()["id"]

        # engineer + work_type=bug → bug maps to engineer → no mismatch.
        r = await ac.patch(f"/api/v1/kanban/cards/{cid}", json={"work_type": "bug"})
        assert r.status_code == 200, r.text
        assert not any("Routing mismatch" in t for t in await _activity_texts(ac, cid))


@pytest.mark.asyncio
async def test_patch_work_type_no_comment_when_agent_set_in_same_call():
    """Setting agent in the same PATCH is an explicit reconciliation — no warning."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Card", "agent": "engineer",
            "confirm_new_project": True,
        })).json()["id"]

        r = await ac.patch(f"/api/v1/kanban/cards/{cid}",
                           json={"work_type": "analysis", "agent": "analyst"})
        assert r.status_code == 200, r.text
        assert r.json()["agent"] == "analyst"
        assert not any("Routing mismatch" in t for t in await _activity_texts(ac, cid))


@pytest.mark.asyncio
async def test_patch_work_type_no_comment_when_no_agent_pinned():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Unpinned card",
            "confirm_new_project": True,
        })).json()["id"]

        r = await ac.patch(f"/api/v1/kanban/cards/{cid}", json={"work_type": "analysis"})
        assert r.status_code == 200, r.text
        assert not any("Routing mismatch" in t for t in await _activity_texts(ac, cid))
