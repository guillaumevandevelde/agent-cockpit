"""Tests for the bulk session resume endpoint."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.api.v1.runs.router as bridge_router
from app.database import Base, engine
from app.main import app


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_bulk_resume_spawns_each_session_in_resume_mode(monkeypatch):
    spawned = []

    def fake_spawn(cli_id, options, session_name=None):
        spawned.append((cli_id, options.mode, options.session_id, options.project_folder))
        return {"tmux_target": f"{options.session_id}:0.0", "session_name": options.session_id}

    monkeypatch.setattr(bridge_router, "spawn_session", fake_spawn)

    payload = {
        "sessions": [
            {"session_id": "s1", "project_folder": "-a"},
            {"session_id": "s2", "project_folder": "-b"},
        ],
    }
    async with _client() as ac:
        r = await ac.post("/api/v1/agent-bridge/sessions/bulk-resume", json=payload)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spawned"] == 2
    assert body["failed"] == 0
    assert [res["tmux_target"] for res in body["results"]] == ["s1:0.0", "s2:0.0"]
    assert all(res["ok"] for res in body["results"])
    # Every item is spawned in resume mode with its own project_folder.
    assert spawned == [
        ("claude-code", "resume", "s1", "-a"),
        ("claude-code", "resume", "s2", "-b"),
    ]


@pytest.mark.asyncio
async def test_bulk_resume_reports_partial_failure(monkeypatch):
    def fake_spawn(cli_id, options, session_name=None):
        if options.session_id == "bad":
            raise ValueError("boom")
        return {"tmux_target": f"{options.session_id}:0.0", "session_name": options.session_id}

    monkeypatch.setattr(bridge_router, "spawn_session", fake_spawn)

    payload = {
        "sessions": [
            {"session_id": "ok", "project_folder": "-a"},
            {"session_id": "bad", "project_folder": "-b"},
        ],
    }
    async with _client() as ac:
        r = await ac.post("/api/v1/agent-bridge/sessions/bulk-resume", json=payload)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spawned"] == 1
    assert body["failed"] == 1
    failed = next(res for res in body["results"] if not res["ok"])
    assert failed["session_id"] == "bad"
    assert failed["error"] == "boom"
    assert failed["tmux_target"] is None


@pytest.mark.asyncio
async def test_bulk_resume_rejects_empty_list():
    async with _client() as ac:
        r = await ac.post("/api/v1/agent-bridge/sessions/bulk-resume", json={"sessions": []})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_bulk_resume_rejects_unknown_provider():
    async with _client() as ac:
        r = await ac.post(
            "/api/v1/agent-bridge/sessions/bulk-resume",
            json={"provider": "nope", "sessions": [{"session_id": "s1", "project_folder": "-a"}]},
        )
    assert r.status_code == 400
