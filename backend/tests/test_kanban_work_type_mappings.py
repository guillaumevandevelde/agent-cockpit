# backend/tests/test_kanban_work_type_mappings.py
"""Tests for the per-project work_type → persona mapping.

Covers the service layer (CRUD + default-fallback) and the REST endpoints.
The mapping is read at create_card time (and any future dispatch-time work)
to auto-fill card.agent, so a wrong default or missing-row behaviour would
silently send cards to the wrong persona.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.kanban import service
from app.kanban.schemas import (
    WORK_TYPE_PERSONA_DEFAULTS,
    WORK_TYPES,
)
from app.main import app
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


# ---- service-layer tests -----------------------------------------------------


@pytest.mark.asyncio
async def test_get_work_type_persona_returns_default_when_no_override():
    async with KanbanSessionLocal() as s:
        assert await service.get_work_type_persona(s, "PROJ", "analysis") == "analyst"
        assert await service.get_work_type_persona(s, "PROJ", "feature") == "engineer"
        assert await service.get_work_type_persona(s, "PROJ", "bug") == "engineer"
        assert await service.get_work_type_persona(s, "PROJ", "chore") == "engineer"


@pytest.mark.asyncio
async def test_get_work_type_persona_uses_override_when_present():
    async with KanbanSessionLocal() as s:
        await service.upsert_work_type_mapping(s, "PROJ", "analysis", "engineer")
        await s.commit()
        assert await service.get_work_type_persona(s, "PROJ", "analysis") == "engineer"
        # Other work_types still use their defaults.
        assert await service.get_work_type_persona(s, "PROJ", "feature") == "engineer"


@pytest.mark.asyncio
async def test_get_work_type_persona_unknown_work_type_falls_back_to_engineer():
    """Legacy cards with a work_type predating the enum must still resolve
    to a sane persona rather than crashing the dispatcher."""
    async with KanbanSessionLocal() as s:
        assert await service.get_work_type_persona(s, "PROJ", "unknown") == "engineer"


@pytest.mark.asyncio
async def test_work_type_mapping_for_project_merges_defaults_and_overrides():
    async with KanbanSessionLocal() as s:
        await service.upsert_work_type_mapping(s, "PROJ", "bug", "analyst")
        await s.commit()
        merged = await service.work_type_mapping_for_project(s, "PROJ")
        # Every WORK_TYPES entry is present, with the bug override applied.
        assert set(merged.keys()) == set(WORK_TYPES)
        assert merged["analysis"] == "analyst"
        assert merged["bug"] == "analyst"
        assert merged["feature"] == "engineer"
        assert merged["chore"] == "engineer"


@pytest.mark.asyncio
async def test_upsert_work_type_mapping_rejects_unknown_work_type():
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError, match="work_type must be one of"):
            await service.upsert_work_type_mapping(s, "PROJ", "exploration", "engineer")


@pytest.mark.asyncio
async def test_upsert_work_type_mapping_rejects_empty_persona():
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError, match="persona must be a non-empty"):
            await service.upsert_work_type_mapping(s, "PROJ", "bug", "")


@pytest.mark.asyncio
async def test_upsert_overwrites_existing_row():
    async with KanbanSessionLocal() as s:
        first = await service.upsert_work_type_mapping(s, "PROJ", "bug", "analyst")
        await s.commit()
        second = await service.upsert_work_type_mapping(s, "PROJ", "bug", "engineer")
        await s.commit()
        # Same primary key (project_key, work_type), different persona.
        assert first.id == second.id
        assert second.persona == "engineer"


@pytest.mark.asyncio
async def test_bulk_replace_keeps_unmentioned_work_types_on_default():
    async with KanbanSessionLocal() as s:
        # Pre-seed an override on `chore`.
        await service.upsert_work_type_mapping(s, "PROJ", "chore", "analyst")
        await s.commit()

        # Bulk-replace only `bug`. `chore` must keep its override.
        await service.bulk_replace_work_type_mappings(
            s, "PROJ", [{"work_type": "bug", "persona": "analyst"}]
        )
        await s.commit()

        assert await service.get_work_type_persona(s, "PROJ", "bug") == "analyst"
        assert await service.get_work_type_persona(s, "PROJ", "chore") == "analyst"


@pytest.mark.asyncio
async def test_delete_work_type_mapping_resets_to_default():
    async with KanbanSessionLocal() as s:
        await service.upsert_work_type_mapping(s, "PROJ", "bug", "analyst")
        await s.commit()
        assert await service.get_work_type_persona(s, "PROJ", "bug") == "analyst"

        removed = await service.delete_work_type_mapping(s, "PROJ", "bug")
        await s.commit()
        assert removed is True
        assert await service.get_work_type_persona(s, "PROJ", "bug") == "engineer"


@pytest.mark.asyncio
async def test_delete_work_type_mapping_is_idempotent_when_no_row():
    async with KanbanSessionLocal() as s:
        removed = await service.delete_work_type_mapping(s, "PROJ", "bug")
        await s.commit()
        assert removed is False


@pytest.mark.asyncio
async def test_mappings_are_isolated_per_project():
    async with KanbanSessionLocal() as s:
        await service.upsert_work_type_mapping(s, "A", "bug", "analyst")
        await s.commit()
        assert await service.get_work_type_persona(s, "A", "bug") == "analyst"
        # B never had an override; should fall back to the default.
        assert await service.get_work_type_persona(s, "B", "bug") == "engineer"


# ---- REST endpoint tests -----------------------------------------------------


@pytest.mark.asyncio
async def test_get_endpoint_returns_merged_map_for_project():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        # Seed an override via the bulk endpoint so we know both paths agree.
        await ac.post(
            "/api/v1/kanban/work-type-mappings/bulk",
            json={
                "project_key": "PROJ",
                "mappings": [{"work_type": "bug", "persona": "analyst"}],
            },
        )
        r = await ac.get(
            "/api/v1/kanban/work-type-mappings",
            params={"project_key": "PROJ"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["project_key"] == "PROJ"
        mappings = body["mappings"]
        assert set(mappings.keys()) == set(WORK_TYPES)
        assert mappings["bug"] == "analyst"
        assert mappings["analysis"] == WORK_TYPE_PERSONA_DEFAULTS["analysis"]


@pytest.mark.asyncio
async def test_get_endpoint_returns_all_defaults_when_no_overrides():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get(
            "/api/v1/kanban/work-type-mappings",
            params={"project_key": "EMPTY"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["mappings"] == dict(WORK_TYPE_PERSONA_DEFAULTS)


@pytest.mark.asyncio
async def test_bulk_endpoint_creates_overrides():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post(
            "/api/v1/kanban/work-type-mappings/bulk",
            json={
                "project_key": "PROJ",
                "mappings": [
                    {"work_type": "analysis", "persona": "engineer"},
                    {"work_type": "chore", "persona": "analyst"},
                ],
            },
        )
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 2
        assert {row["work_type"] for row in rows} == {"analysis", "chore"}

        # GET reflects both overrides plus the defaults for the rest.
        r = await ac.get(
            "/api/v1/kanban/work-type-mappings",
            params={"project_key": "PROJ"},
        )
        merged = r.json()["mappings"]
        assert merged["analysis"] == "engineer"
        assert merged["chore"] == "analyst"
        assert merged["feature"] == WORK_TYPE_PERSONA_DEFAULTS["feature"]
        assert merged["bug"] == WORK_TYPE_PERSONA_DEFAULTS["bug"]


@pytest.mark.asyncio
async def test_bulk_endpoint_rejects_unknown_work_type():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post(
            "/api/v1/kanban/work-type-mappings/bulk",
            json={
                "project_key": "PROJ",
                "mappings": [{"work_type": "exploration", "persona": "engineer"}],
            },
        )
        assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_delete_endpoint_resets_a_single_override():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        # Seed two overrides.
        await ac.post(
            "/api/v1/kanban/work-type-mappings/bulk",
            json={
                "project_key": "PROJ",
                "mappings": [
                    {"work_type": "analysis", "persona": "engineer"},
                    {"work_type": "chore", "persona": "analyst"},
                ],
            },
        )

        r = await ac.delete(
            "/api/v1/kanban/work-type-mappings/analysis",
            params={"project_key": "PROJ"},
        )
        assert r.status_code == 204, r.text

        r = await ac.get(
            "/api/v1/kanban/work-type-mappings",
            params={"project_key": "PROJ"},
        )
        merged = r.json()["mappings"]
        # analysis back to its default; chore still overridden.
        assert merged["analysis"] == WORK_TYPE_PERSONA_DEFAULTS["analysis"]
        assert merged["chore"] == "analyst"


@pytest.mark.asyncio
async def test_delete_endpoint_is_idempotent_when_no_row():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.delete(
            "/api/v1/kanban/work-type-mappings/analysis",
            params={"project_key": "PROJ"},
        )
        assert r.status_code == 204, r.text


@pytest.mark.asyncio
async def test_delete_endpoint_rejects_unknown_work_type():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.delete(
            "/api/v1/kanban/work-type-mappings/exploration",
            params={"project_key": "PROJ"},
        )
        assert r.status_code == 422, r.text