"""Tests for the bulk session resume endpoint."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import app.api.v1.runs.router as bridge_router
from app.database import Base, engine
from app.main import app
from tests.kanban_test_db import TestSessionLocal, reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Anthropic-compatible endpoint rows live in the kanban DB
    # (``kanban_meta`` table) — keep its schema in sync so the resume
    # handler can resolve ``endpoint_name`` like the non-bulk spawn
    # path does.
    await reset_test_tables()
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


@pytest.mark.asyncio
async def test_bulk_resume_propagates_anthropic_compatible_endpoint(monkeypatch):
    """AC (kaart 7ab0fc0038c…): a bulk-resume with
    ``provider="anthropic-compatible"`` + ``endpoint_name`` must
    resolve the endpoint like the non-bulk spawn path does and thread
    ``endpoint_base_url`` + ``endpoint_auth_token`` into the per-item
    ``SpawnCommandOptions``. Without this, the resumed sessions silently
    fall back to plain Anthropic — the bug this card fixes.

    The resolver is monkeypatched to a fake so the test stays in-process
    and doesn't depend on the (separate) kanban DB; the contract under
    test is the handler's *use* of the resolver's return shape, not
    resolver semantics (covered by test_provider_endpoints)."""
    from app.services.agentic_cli import endpoints as endpoints_mod

    async def fake_resolve(session, project_key, endpoint_name, **kwargs):
        return {
            "name": endpoint_name,
            "base_url": "https://api.groq.example/anthropic",
            "auth_token": None,  # ambient credential
            "model": "claude-sonnet-4-6",
        }

    monkeypatch.setattr(endpoints_mod, "resolve_compatible_endpoint", fake_resolve)

    spawned: list = []

    def fake_spawn(cli_id, options, session_name=None):
        spawned.append(options)
        return {"tmux_target": f"{options.session_id}:0.0", "session_name": options.session_id}

    monkeypatch.setattr(bridge_router, "spawn_session", fake_spawn)

    payload = {
        "provider": "anthropic-compatible",
        "endpoint_name": "groq-resume",
        "sessions": [
            {"session_id": "s1", "project_folder": "-a"},
            {"session_id": "s2", "project_folder": "-b"},
        ],
    }
    async with _client() as ac:
        r = await ac.post("/api/v1/agent-bridge/sessions/bulk-resume", json=payload)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spawned"] == 2 and body["failed"] == 0
    assert all(res["ok"] for res in body["results"])
    # Both items got the resolved base_url + auth_token threaded into
    # SpawnCommandOptions so the provider-env builder can stamp
    # ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN on the spawned CLI.
    assert len(spawned) == 2
    for opts in spawned:
        assert opts.provider == "anthropic-compatible"
        assert opts.endpoint_name == "groq-resume"
        assert opts.endpoint_base_url == "https://api.groq.example/anthropic"
        # ambient credential_name=None → auth_token stays None
        assert opts.endpoint_auth_token is None
        # mode stays "resume" for every item in the batch
        assert opts.mode == "resume"


@pytest.mark.asyncio
async def test_bulk_resume_rejects_anthropic_compatible_without_endpoint_name(monkeypatch):
    """Symmetric to the non-bulk spawn path: a bulk-resume that picks
    the ``anthropic-compatible`` vendor without an ``endpoint_name`` is
    a configuration error and must surface as a clean 400 naming the
    missing field, instead of silently spawning on plain Anthropic."""
    payload = {
        "provider": "anthropic-compatible",
        "sessions": [{"session_id": "s1", "project_folder": "-a"}],
    }
    async with _client() as ac:
        r = await ac.post("/api/v1/agent-bridge/sessions/bulk-resume", json=payload)
    assert r.status_code == 400
    assert "endpoint_name" in r.text
