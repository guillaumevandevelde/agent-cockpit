"""Tests for the portfolio ``kind``/``priority`` project tag.

Covers the Pydantic enum boundary, the ORM default, the ``ALTER TABLE``
migration for pre-existing DBs, the service round-trip, and the PATCH route.
"""
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1 import projects as projects_api
from app.database import Base
from app.main import app
from app.models.schemas import ProjectCreate, ProjectResponse, ProjectUpdate
from app.services.project_service import ProjectService

# ---------------------------------------------------------------- schema layer


def test_project_create_defaults():
    p = ProjectCreate(name="demo", path="/tmp/demo")
    assert p.kind == "product"
    assert p.priority is None


def test_project_create_accepts_valid_kinds():
    for kind in ("meta", "product", "archived"):
        assert ProjectCreate(name="d", path=f"/tmp/{kind}", kind=kind).kind == kind


def test_project_create_rejects_invalid_kind():
    with pytest.raises(ValidationError):
        ProjectCreate(name="demo", path="/tmp/demo", kind="bogus")


def test_project_update_rejects_invalid_kind():
    with pytest.raises(ValidationError):
        ProjectUpdate(kind="bogus")


def test_project_response_carries_kind_and_priority():
    r = ProjectResponse(
        id=1,
        name="demo",
        path="/tmp/demo",
        kind="meta",
        priority=5,
        is_active=False,
        last_accessed="2026-01-01T00:00:00",
        created_at="2026-01-01T00:00:00",
    )
    assert r.kind == "meta"
    assert r.priority == 5


# ---------------------------------------------------------------- db fixtures


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    """Isolated in-memory SQLite session so we never touch the real DB."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------- ORM default


@pytest.mark.asyncio
async def test_orm_default_kind_is_product(db_session):
    service = ProjectService(db_session)
    created = await service.add_project(ProjectCreate(name="demo", path="/tmp/demo"))
    assert created.kind == "product"
    assert created.priority is None


@pytest.mark.asyncio
async def test_add_project_persists_kind_and_priority(db_session):
    service = ProjectService(db_session)
    created = await service.add_project(
        ProjectCreate(name="meta-app", path="/tmp/meta", kind="meta", priority=3)
    )
    assert created.kind == "meta"
    assert created.priority == 3


@pytest.mark.asyncio
async def test_update_project_changes_kind_and_priority(db_session):
    service = ProjectService(db_session)
    created = await service.add_project(ProjectCreate(name="demo", path="/tmp/demo"))

    updated = await service.update_project(
        created.id, ProjectUpdate(kind="archived", priority=9)
    )
    assert updated.kind == "archived"
    assert updated.priority == 9
    # name untouched by a partial patch
    assert updated.name == "demo"


@pytest.mark.asyncio
async def test_update_project_missing_returns_none(db_session):
    service = ProjectService(db_session)
    assert await service.update_project(999, ProjectUpdate(kind="meta")) is None


# ---------------------------------------------------------------- migration


# The legacy-table migration test that used to sit here was removed on
# 2026-08-15 together with `_migrate_project_columns`. Bringing an older
# database up to the current shape is alembic's job now; see
# tests/test_db_bootstrap.py::test_pre_alembic_database_is_adopted.

# ---------------------------------------------------------------- API route


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


class _FakeProjectService:
    pass


@pytest.mark.asyncio
async def test_patch_project_updates_kind(monkeypatch):
    fake = _FakeProjectService()
    fake.update_project = AsyncMock(
        return_value=ProjectResponse(
            id=1,
            name="demo",
            path="/tmp/demo",
            kind="meta",
            priority=2,
            is_active=False,
            last_accessed="2026-01-01T00:00:00",
            created_at="2026-01-01T00:00:00",
        )
    )
    monkeypatch.setattr(projects_api, "ProjectService", lambda db: fake)

    async with _client() as ac:
        r = await ac.patch("/api/v1/projects/1", json={"kind": "meta", "priority": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "meta"
    assert body["priority"] == 2


@pytest.mark.asyncio
async def test_patch_project_rejects_invalid_kind(monkeypatch):
    fake = _FakeProjectService()
    fake.update_project = AsyncMock(return_value=None)
    monkeypatch.setattr(projects_api, "ProjectService", lambda db: fake)

    async with _client() as ac:
        r = await ac.patch("/api/v1/projects/1", json={"kind": "bogus"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patch_project_404_when_missing(monkeypatch):
    fake = _FakeProjectService()
    fake.update_project = AsyncMock(return_value=None)
    monkeypatch.setattr(projects_api, "ProjectService", lambda db: fake)

    async with _client() as ac:
        r = await ac.patch("/api/v1/projects/999", json={"kind": "meta"})
    assert r.status_code == 404
