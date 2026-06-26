"""Tests for sandcastle run deletion / bulk cleanup.

These use a throwaway in-memory DB monkeypatched into the service, so they never
touch the production claude_registry.db (the shared conftest only patches kanban).
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.services.sandcastle_service as svc_mod
from app.database import Base
from app.models.sandcastle import SandcastleConfig, SandcastleRun
from app.services.sandcastle_service import SandcastleService


@pytest_asyncio.fixture
async def session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(svc_mod, "AsyncSessionLocal", sf)
    yield sf
    await engine.dispose()


async def _seed(sf, statuses):
    """Create a config + one run per given status; return the run ids."""
    async with sf() as s:
        cfg = SandcastleConfig(project_path="/p", enabled=True)
        s.add(cfg)
        await s.commit()
        await s.refresh(cfg)
        ids = []
        for st in statuses:
            run = SandcastleRun(project_path="/p", config_id=cfg.id, prompt="x", status=st)
            s.add(run)
            await s.commit()
            await s.refresh(run)
            ids.append(run.id)
        return ids


@pytest.mark.asyncio
async def test_delete_run_removes_record(session_factory):
    [rid] = await _seed(session_factory, ["completed"])
    svc = SandcastleService()
    assert await svc.delete_run(rid) is True
    assert await svc.get_run(rid) is None


@pytest.mark.asyncio
async def test_delete_run_returns_false_for_missing(session_factory):
    svc = SandcastleService()
    assert await svc.delete_run(99999) is False


@pytest.mark.asyncio
async def test_clear_runs_deletes_only_terminal_by_default(session_factory):
    await _seed(session_factory, ["completed", "failed", "cancelled", "running", "pending"])
    svc = SandcastleService()
    deleted = await svc.clear_runs(project_path="/p")
    assert deleted == 3  # completed + failed + cancelled
    remaining = {r.status for r in await svc.list_runs(project_path="/p")}
    assert remaining == {"running", "pending"}


@pytest.mark.asyncio
async def test_clear_runs_include_running_deletes_everything(session_factory):
    await _seed(session_factory, ["completed", "running", "pending"])
    svc = SandcastleService()
    deleted = await svc.clear_runs(project_path="/p", include_running=True)
    assert deleted == 3
    assert await svc.list_runs(project_path="/p") == []


@pytest.mark.asyncio
async def test_clear_runs_scoped_to_project(session_factory):
    # Seed a second project; clearing /p must not touch /other.
    async with session_factory() as s:
        cfg = SandcastleConfig(project_path="/other", enabled=True)
        s.add(cfg)
        await s.commit()
        await s.refresh(cfg)
        s.add(SandcastleRun(project_path="/other", config_id=cfg.id, prompt="x", status="completed"))
        await s.commit()
    await _seed(session_factory, ["completed"])
    svc = SandcastleService()
    deleted = await svc.clear_runs(project_path="/p")
    assert deleted == 1
    assert len(await svc.list_runs(project_path="/other")) == 1
