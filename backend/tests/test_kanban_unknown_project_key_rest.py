# backend/tests/test_kanban_unknown_project_key_rest.py
#
# REST-side companion to the MCP `unknown_project_key` guard added by
# kanban card 91c85199. The MCP tools were the primary failure surface; the
# dispatch prompt also documents the `GET /api/v1/kanban/cards` /
# `POST /api/v1/kanban/cards` endpoints as the MCP-`-32602` fallback, so a
# hand-typed `project_key` from a dispatched agent could re-trigger the same
# silent-orphan-bucket incident through REST. These tests pin the REST guard
# and the `confirm_new_project` opt-in on `POST /cards` so a future refactor
# can't quietly drop either half.
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_list_cards_unknown_project_key_returns_404_not_empty_list():
    """A typo'd / guessed `project_key` must surface as a structured 404
    instead of the previous false-empty `[]` (kanban card 91c85199 incident).
    """
    async with _client() as ac:
        r = await ac.get("/api/v1/kanban/cards",
                         params={"project_key": "git:github.com/typo-org/claude-cockpit"})
    assert r.status_code == 404, r.text
    body = r.json()["detail"]
    assert body["error"] == "unknown_project_key"
    assert body["project_key"] == "git:github.com/typo-org/claude-cockpit"
    # The `known_project_keys_sample` lets the caller self-correct without a
    # second round-trip to discover what projects do exist.
    assert "known_project_keys_sample" in body
    assert isinstance(body["known_project_keys_sample"], list)


@pytest.mark.asyncio
async def test_create_card_unknown_project_key_is_refused_without_confirm():
    """POST /cards must reject an unknown key — a typo here used to silently
    create an orphaned card in a bucket auto-dispatch never sees (the
    MCP-side equivalent was closed in kanban card 91c85199).
    """
    async with _client() as ac:
        r = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "git:github.com/typo-org/claude-cockpit",
            "title": "Should not be created",
        })
    assert r.status_code == 404, r.text
    body = r.json()["detail"]
    assert body["error"] == "unknown_project_key"

    # And confirm the refused create did NOT silently seed the project — a
    # follow-up list_cards must still error, not return the would-be card.
    async with _client() as ac:
        r2 = await ac.get("/api/v1/kanban/cards",
                          params={"project_key": "git:github.com/typo-org/claude-cockpit"})
    assert r2.status_code == 404
    assert r2.json()["detail"]["error"] == "unknown_project_key"


@pytest.mark.asyncio
async def test_create_card_new_project_allowed_with_explicit_confirm():
    """Passing `confirm_new_project=true` is the explicit opt-in for the rare
    legitimate "first card of a brand-new project via REST" path. The card
    must then be visible to a subsequent `list_cards` for the same key (which
    proves it didn't land in an unrelated bucket).
    """
    async with _client() as ac:
        r = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "BRAND-NEW-REST",
            "title": "First card",
            "confirm_new_project": True,
        })
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["project_key"] == "BRAND-NEW-REST"

    async with _client() as ac:
        listed = (await ac.get("/api/v1/kanban/cards",
                               params={"project_key": "BRAND-NEW-REST"})).json()
    assert any(c["id"] == cid for c in listed["items"])


@pytest.mark.asyncio
async def test_create_card_after_enable_does_not_need_confirm(tmp_path):
    """The normal REST onboarding path is `POST /kanban/enable` — that seeds
    the columns, which puts the key into `known_project_keys`. After enable,
    a follow-up `POST /cards` must work without `confirm_new_project` (the
    opt-in is reserved for the "I deliberately know this key is new" path,
    not the standard flow).
    """
    async with _client() as ac:
        r = await ac.post("/api/v1/kanban/enable",
                          json={"project_path": str(tmp_path)})
        assert r.status_code == 200, r.text
        onboarded_key = r.json()["project_key"]

        # Now the key is "known" via columns — POST /cards should succeed
        # without `confirm_new_project`.
        r2 = await ac.post("/api/v1/kanban/cards", json={
            "project_key": onboarded_key,
            "title": "First card after enable",
        })
    assert r2.status_code == 201, r2.text


@pytest.mark.asyncio
async def test_create_card_existing_project_no_confirm_unchanged_behavior():
    """Once a project has ≥1 card, subsequent create_card calls for the same
    key don't need `confirm_new_project` — only the very first card does.
    Mirrors the existing MCP contract (test_kanban_mcp.py:1007).
    """
    async with _client() as ac:
        first = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "ALREADY-KNOWN-REST",
            "title": "First",
            "confirm_new_project": True,
        })
        assert first.status_code == 201, first.text

        second = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "ALREADY-KNOWN-REST",
            "title": "Second",
        })
    assert second.status_code == 201, second.text
    assert second.json()["id"] != first.json()["id"]
