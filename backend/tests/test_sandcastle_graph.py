"""Tests for the sandcastle run-graph view.

There is no dependency/parent data on SandcastleRun, so the graph is inferred
from the log_file_path correlator that _execute_parallel_runs already stamps
onto every run started via one /runs/parallel call: runs sharing a log file
become a fan-out batch, everything else is a standalone node.

These use the same throwaway in-memory DB pattern as test_sandcastle_cleanup.py
so they never touch the production claude_registry.db.
"""
from datetime import datetime, timedelta, timezone

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


async def _seed_config(sf, project_path="/p"):
    async with sf() as s:
        cfg = SandcastleConfig(project_path=project_path, enabled=True)
        s.add(cfg)
        await s.commit()
        await s.refresh(cfg)
        return cfg.id


async def _add_run(sf, config_id, project_path="/p", **overrides):
    async with sf() as s:
        run = SandcastleRun(project_path=project_path, config_id=config_id, prompt="x", status="pending")
        for k, v in overrides.items():
            setattr(run, k, v)
        s.add(run)
        await s.commit()
        await s.refresh(run)
        return run.id


@pytest.mark.asyncio
async def test_solo_run_is_a_standalone_node_with_no_edges(session_factory):
    cfg_id = await _seed_config(session_factory)
    run_id = await _add_run(session_factory, cfg_id, prompt="do the thing", status="completed")

    svc = SandcastleService()
    graph = await svc.get_run_graph("/p")

    assert graph["edges"] == []
    assert len(graph["nodes"]) == 1
    node = graph["nodes"][0]
    assert node["type"] == "run"
    assert node["run_id"] == run_id
    assert node["status"] == "completed"


@pytest.mark.asyncio
async def test_runs_sharing_a_log_file_become_a_batch_fan_out(session_factory):
    cfg_id = await _seed_config(session_factory)
    ids = []
    for i in range(3):
        rid = await _add_run(
            session_factory, cfg_id,
            prompt=f"run {i}", status="running",
            log_file_path="/p/.sandcastle/logs/parallel-1.log",
        )
        ids.append(rid)

    svc = SandcastleService()
    graph = await svc.get_run_graph("/p")

    batch_nodes = [n for n in graph["nodes"] if n["type"] == "batch"]
    run_nodes = [n for n in graph["nodes"] if n["type"] == "run"]
    assert len(batch_nodes) == 1
    assert len(run_nodes) == 3
    assert {n["run_id"] for n in run_nodes} == set(ids)

    batch_id = batch_nodes[0]["id"]
    assert len(graph["edges"]) == 3
    assert all(e["source"] == batch_id for e in graph["edges"])
    assert {e["target"] for e in graph["edges"]} == {n["id"] for n in run_nodes}


@pytest.mark.asyncio
async def test_batch_status_is_running_if_any_child_is_active(session_factory):
    cfg_id = await _seed_config(session_factory)
    await _add_run(session_factory, cfg_id, status="completed", log_file_path="/p/logs/parallel-1.log")
    await _add_run(session_factory, cfg_id, status="running", log_file_path="/p/logs/parallel-1.log")

    svc = SandcastleService()
    graph = await svc.get_run_graph("/p")
    batch = next(n for n in graph["nodes"] if n["type"] == "batch")
    assert batch["status"] == "running"


@pytest.mark.asyncio
async def test_batch_status_is_failed_if_any_child_failed_and_none_active(session_factory):
    cfg_id = await _seed_config(session_factory)
    await _add_run(session_factory, cfg_id, status="completed", log_file_path="/p/logs/parallel-1.log")
    await _add_run(session_factory, cfg_id, status="failed", log_file_path="/p/logs/parallel-1.log")

    svc = SandcastleService()
    graph = await svc.get_run_graph("/p")
    batch = next(n for n in graph["nodes"] if n["type"] == "batch")
    assert batch["status"] == "failed"


@pytest.mark.asyncio
async def test_batch_status_completed_when_all_children_completed(session_factory):
    cfg_id = await _seed_config(session_factory)
    await _add_run(session_factory, cfg_id, status="completed", log_file_path="/p/logs/parallel-1.log")
    await _add_run(session_factory, cfg_id, status="completed", log_file_path="/p/logs/parallel-1.log")

    svc = SandcastleService()
    graph = await svc.get_run_graph("/p")
    batch = next(n for n in graph["nodes"] if n["type"] == "batch")
    assert batch["status"] == "completed"


@pytest.mark.asyncio
async def test_pending_run_without_log_file_is_standalone(session_factory):
    # A run that hasn't started yet has no log_file_path -- must not be grouped.
    cfg_id = await _seed_config(session_factory)
    await _add_run(session_factory, cfg_id, status="pending", log_file_path=None)

    svc = SandcastleService()
    graph = await svc.get_run_graph("/p")
    assert len(graph["nodes"]) == 1
    assert graph["nodes"][0]["type"] == "run"
    assert graph["edges"] == []


@pytest.mark.asyncio
async def test_independent_batches_do_not_cross_link(session_factory):
    cfg_id = await _seed_config(session_factory)
    for i in range(2):
        await _add_run(session_factory, cfg_id, status="completed", log_file_path="/p/logs/parallel-1.log")
    for i in range(2):
        await _add_run(session_factory, cfg_id, status="completed", log_file_path="/p/logs/parallel-2.log")

    svc = SandcastleService()
    graph = await svc.get_run_graph("/p")
    batch_nodes = [n for n in graph["nodes"] if n["type"] == "batch"]
    assert len(batch_nodes) == 2
    assert len(graph["edges"]) == 4


@pytest.mark.asyncio
async def test_graph_scoped_to_project(session_factory):
    cfg_a = await _seed_config(session_factory, "/a")
    cfg_b = await _seed_config(session_factory, "/b")
    await _add_run(session_factory, cfg_a, project_path="/a", status="completed")
    await _add_run(session_factory, cfg_b, project_path="/b", status="completed")

    svc = SandcastleService()
    graph = await svc.get_run_graph("/a")
    assert len(graph["nodes"]) == 1


@pytest.mark.asyncio
async def test_duration_survives_sqlite_datetime_round_trip(session_factory):
    # Regression: SQLite round-trips DateTime(timezone=True) as naive even
    # though it's always written as UTC -- the duration subtraction must not
    # blow up on offset-naive vs offset-aware datetimes after a real fetch.
    cfg_id = await _seed_config(session_factory)
    started = datetime.now(timezone.utc) - timedelta(minutes=5)
    completed = datetime.now(timezone.utc)
    await _add_run(
        session_factory, cfg_id, status="completed",
        started_at=started, completed_at=completed,
    )

    svc = SandcastleService()
    graph = await svc.get_run_graph("/p")
    node = graph["nodes"][0]
    assert node["duration_seconds"] == pytest.approx(300, abs=2)


@pytest.mark.asyncio
async def test_running_node_duration_computed_against_now(session_factory):
    cfg_id = await _seed_config(session_factory)
    started = datetime.now(timezone.utc) - timedelta(seconds=10)
    await _add_run(session_factory, cfg_id, status="running", started_at=started)

    svc = SandcastleService()
    graph = await svc.get_run_graph("/p")
    node = graph["nodes"][0]
    assert node["duration_seconds"] is not None
    assert node["duration_seconds"] >= 10


@pytest.mark.asyncio
async def test_node_includes_commits_count_and_prompt(session_factory):
    cfg_id = await _seed_config(session_factory)
    await _add_run(
        session_factory, cfg_id, prompt="fix the bug", status="completed",
        commits=[{"sha": "abc"}, {"sha": "def"}],
    )

    svc = SandcastleService()
    graph = await svc.get_run_graph("/p")
    node = graph["nodes"][0]
    assert node["prompt"] == "fix the bug"
    assert node["commits_count"] == 2
