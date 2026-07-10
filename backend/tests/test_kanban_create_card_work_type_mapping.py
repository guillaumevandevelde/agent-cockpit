# backend/tests/test_kanban_create_card_work_type_mapping.py
"""Tests for auto-filling `card.agent` from work_type at create time.

Bouwsteen (c) from docs/cockpit/work-type-routing-analysis.md §2B/§5.

Contract:
  * `card.agent` is the highest-priority routing hint, set at create time.
  * Explicit `agent` value on the POST wins over the work_type mapping.
  * If only `work_type` is set, the per-project mapping (or
    `WORK_TYPE_PERSONA_DEFAULTS`) decides which persona to bind.
  * If neither is set, `card.agent` stays empty (dispatcher falls back to
    column-derived persona).
  * The resolved agent is written to the op-log so `rematerialize()` rebuilds
    it correctly.
  * Whitespace-padded explicit agent values are normalized before storage so
    the dispatcher's persona lookup sees the bare name.

Out of scope (deliberately, see §2B): changing `card.agent` when `work_type`
is updated AFTER creation. That would require a different hook in the PATCH
path and is explicitly deferred.

The autouse `_reset_test_db` fixture lives in tests/conftest.py — don't add
one here.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.kanban import service
from app.kanban.db import KanbanSessionLocal
from app.kanban.operations import rematerialize
from app.kanban.schemas import WORK_TYPE_PERSONA_DEFAULTS
from app.main import app

# ---- service-layer tests -----------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_create_agent_returns_explicit_when_set():
    """Explicit `agent` wins even when a work_type is also provided."""
    async with KanbanSessionLocal() as s:
        got = await service.resolve_create_agent(
            s, "PROJ", work_type="analysis", explicit_agent="custom-persona",
        )
        assert got == "custom-persona"


@pytest.mark.asyncio
async def test_resolve_create_agent_normalizes_whitespace_explicit():
    """Whitespace-padded explicit agent is stripped before being returned so the
    dispatcher's persona lookup sees the bare name (e.g. `engineer.md`)."""
    async with KanbanSessionLocal() as s:
        assert await service.resolve_create_agent(
            s, "PROJ", work_type="analysis", explicit_agent=" engineer ",
        ) == "engineer"


@pytest.mark.asyncio
async def test_resolve_create_agent_uses_default_mapping_when_work_type_set():
    """work_type set with no explicit agent → per-project default mapping."""
    async with KanbanSessionLocal() as s:
        # analysis → analyst by default
        assert await service.resolve_create_agent(
            s, "PROJ", work_type="analysis", explicit_agent=None,
        ) == "analyst"
        # feature/bug/chore → engineer by default
        assert await service.resolve_create_agent(
            s, "PROJ", work_type="feature", explicit_agent=None,
        ) == "engineer"
        assert await service.resolve_create_agent(
            s, "PROJ", work_type="bug", explicit_agent=None,
        ) == "engineer"
        assert await service.resolve_create_agent(
            s, "PROJ", work_type="chore", explicit_agent=None,
        ) == "engineer"


@pytest.mark.asyncio
async def test_resolve_create_agent_honours_per_project_override():
    """A stored (project_key, work_type) override must win over the default."""
    async with KanbanSessionLocal() as s:
        await service.upsert_work_type_mapping(s, "PROJ", "bug", "analyst")
        await s.commit()
        assert await service.resolve_create_agent(
            s, "PROJ", work_type="bug", explicit_agent=None,
        ) == "analyst"


@pytest.mark.asyncio
async def test_resolve_create_agent_returns_none_when_neither_set():
    """No work_type, no agent → leave card.agent empty."""
    async with KanbanSessionLocal() as s:
        assert await service.resolve_create_agent(
            s, "PROJ", work_type=None, explicit_agent=None,
        ) is None


@pytest.mark.asyncio
async def test_resolve_create_agent_treats_empty_explicit_as_no_explicit():
    """An empty/whitespace explicit value must not block the mapping lookup."""
    async with KanbanSessionLocal() as s:
        assert await service.resolve_create_agent(
            s, "PROJ", work_type="analysis", explicit_agent="",
        ) == "analyst"
        assert await service.resolve_create_agent(
            s, "PROJ", work_type="analysis", explicit_agent="   ",
        ) == "analyst"


@pytest.mark.asyncio
async def test_resolve_create_agent_isolated_per_project():
    """Override on PROJ must not bleed into PROJ-OTHER."""
    async with KanbanSessionLocal() as s:
        await service.upsert_work_type_mapping(s, "PROJ", "bug", "analyst")
        await s.commit()
        assert await service.resolve_create_agent(
            s, "PROJ", work_type="bug", explicit_agent=None,
        ) == "analyst"
        assert await service.resolve_create_agent(
            s, "PROJ-OTHER", work_type="bug", explicit_agent=None,
        ) == WORK_TYPE_PERSONA_DEFAULTS["bug"]


# ---- REST endpoint tests -----------------------------------------------------


@pytest.mark.asyncio
async def test_create_card_auto_fills_agent_from_default_mapping():
    """POSTing work_type without agent → card.agent equals the default persona."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Investigate X", "work_type": "analysis",
        })
        assert r.status_code == 201, r.text
        assert r.json()["agent"] == "analyst"

        r = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Fix Y", "work_type": "bug",
        })
        assert r.status_code == 201, r.text
        assert r.json()["agent"] == "engineer"


@pytest.mark.asyncio
async def test_create_card_auto_fills_agent_from_per_project_override():
    """A stored mapping override on the project wins over the default."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        # Seed an override: bug → analyst for this project.
        r = await ac.post("/api/v1/kanban/work-type-mappings/bulk", json={
            "project_key": "PROJ",
            "mappings": [{"work_type": "bug", "persona": "analyst"}],
        })
        assert r.status_code == 200, r.text

        r = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Investigate root cause",
            "work_type": "bug",
        })
        assert r.status_code == 201, r.text
        assert r.json()["agent"] == "analyst"


@pytest.mark.asyncio
async def test_create_card_keeps_explicit_agent_when_set():
    """Explicit agent wins over both default and override."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        await ac.post("/api/v1/kanban/work-type-mappings/bulk", json={
            "project_key": "PROJ",
            "mappings": [{"work_type": "bug", "persona": "analyst"}],
        })

        r = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Force to engineer",
            "work_type": "bug", "agent": "engineer",
        })
        assert r.status_code == 201, r.text
        assert r.json()["agent"] == "engineer"


@pytest.mark.asyncio
async def test_create_card_skips_mapping_when_no_work_type():
    """No work_type → card.agent stays empty regardless of any mapping."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Plain card",
        })
        assert r.status_code == 201, r.text
        assert r.json()["agent"] is None


@pytest.mark.asyncio
async def test_create_card_explicit_empty_string_falls_through_to_mapping():
    """Frontend sent `agent: ""` → treat as unset, mapping kicks in."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Investigate",
            "work_type": "analysis", "agent": "",
        })
        assert r.status_code == 201, r.text
        assert r.json()["agent"] == "analyst"


@pytest.mark.asyncio
async def test_create_card_explicit_whitespace_is_normalized():
    """Whitespace-padded explicit agent is stored stripped."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Force to engineer",
            "agent": " engineer ",
        })
        assert r.status_code == 201, r.text
        assert r.json()["agent"] == "engineer"


@pytest.mark.asyncio
async def test_create_card_resolved_agent_survives_rematerialize():
    """The op-log must carry the resolved agent so rematerialize rebuilds it."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Replay me",
            "work_type": "analysis",
        })).json()["id"]
        assert (await ac.get(f"/api/v1/kanban/cards/{cid}")).json()["agent"] == "analyst"

        # KanbanSessionLocal is patched by conftest to point at the test
        # engine; replay the op-log through it.
        async with KanbanSessionLocal() as s:
            await rematerialize(s)
            await s.commit()

        r = await ac.get(f"/api/v1/kanban/cards/{cid}")
        assert r.json()["agent"] == "analyst", (
            "rematerialize must replay the op-log's resolved agent"
        )


@pytest.mark.asyncio
async def test_create_card_known_limitation_work_type_after_create_does_not_change_agent():
    """Documented limitation: PATCHing work_type after create does NOT auto-update
    card.agent. The user must set agent explicitly if they want a different persona."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/cards", json={
            "project_key": "PROJ", "title": "Initially analysis",
            "work_type": "analysis",
        })).json()["id"]
        assert (await ac.get(f"/api/v1/kanban/cards/{cid}")).json()["agent"] == "analyst"

        # Switch work_type to bug; agent stays analyst (per §2B known limitation).
        r = await ac.patch(f"/api/v1/kanban/cards/{cid}", json={"work_type": "bug"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["work_type"] == "bug"
        assert body["agent"] == "analyst", (
            "§2B: work_type change after creation does NOT auto-update card.agent"
        )
