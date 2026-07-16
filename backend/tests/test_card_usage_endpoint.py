"""Integration tests for GET /api/v1/kanban/cards/{cid}/usage.

Covers kanban card 8a2ad986 acceptance criterion #1: per dispatched card,
the token usage of the matching session is queryable end-to-end.
"""
import json
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


def _write_jsonl(path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def _assistant_line(*, sid: str, ts: str, model: str,
                    input_tokens: int, output_tokens: int) -> dict:
    return {
        "type": "assistant",
        "sessionId": sid,
        "timestamp": ts,
        "message": {"model": model, "usage": {
            "input_tokens": input_tokens, "output_tokens": output_tokens,
        }},
    }


@pytest.mark.asyncio
async def test_card_usage_returns_404_for_missing_card():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get("/api/v1/kanban/cards/does-not-exist/usage")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_card_usage_returns_null_for_card_without_dispatch_breadcrumbs():
    """Legacy card (dispatched before this feature landed, or freshly created)
    has no dispatch_started_at → response is {usage: null}, not 404."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
                          json={"project_key": "P", "title": "legacy"})
        cid = r.json()["id"]

        r = await ac.get(f"/api/v1/kanban/cards/{cid}/usage")
        assert r.status_code == 200
        assert r.json() == {"usage": None}


@pytest.mark.asyncio
async def test_card_usage_aggregates_from_dispatch_project_folder(tmp_path, monkeypatch):
    """End-to-end: PATCH dispatch fields onto a card → drop a JSONL into
    the matching folder → endpoint must aggregate + return the totals."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        # 1) Create a card
        r = await ac.post("/api/v1/kanban/cards",
                          json={"project_key": "P", "title": "telemetry-test"})
        cid = r.json()["id"]

        # 2) Write dispatch breadcrumbs via PATCH (the same path
        # dispatch.py takes; routing it through the API keeps the test
        # honest about the public write surface).
        dispatch_started = datetime(2026, 7, 15, 9, 0, 0, tzinfo=UTC).isoformat()
        r = await ac.patch(f"/api/v1/kanban/cards/{cid}",
                           json={
                               "dispatch_started_at": dispatch_started,
                               "dispatch_project_folder": "-home-telemetry-worktree",
                               "dispatch_model": "claude-sonnet-4-5",
                           })
        assert r.status_code == 200, r.text

        # 3) Drop a synthetic JSONL into the projects folder the service
        # will scan. Pin projects_dir to a tmp dir so the test is hermetic.
        projects_dir = tmp_path / "projects"
        folder = projects_dir / "-home-telemetry-worktree"
        _write_jsonl(
            folder / "session-abc.jsonl",
            [
                _assistant_line(sid="session-abc", ts="2026-07-15T09:01:00Z",
                                model="claude-sonnet-4-5",
                                input_tokens=1000, output_tokens=200),
                _assistant_line(sid="session-abc", ts="2026-07-15T09:05:00Z",
                                model="claude-sonnet-4-5",
                                input_tokens=500, output_tokens=100),
                # A line from a *different* session in the same worktree
                # — defensive filter must drop this so concurrent-session
                # contamination can't inflate the per-card totals.
                _assistant_line(sid="OTHER-session", ts="2026-07-15T09:10:00Z",
                                model="claude-opus-4-8",
                                input_tokens=99999, output_tokens=99999),
            ],
        )
        import os
        # mtime must be after dispatch_started_at so the file passes the
        # discovery filter
        new_time = datetime(2026, 7, 15, 9, 0, 0, tzinfo=UTC).timestamp() + 60
        os.utime(folder / "session-abc.jsonl", (new_time, new_time))

        monkeypatch.setattr(
            "app.services.dispatch_usage_service.get_claude_projects_dir",
            lambda: projects_dir,
        )

        # 4) Query the endpoint
        r = await ac.get(f"/api/v1/kanban/cards/{cid}/usage")
        assert r.status_code == 200, r.text
        body = r.json()
        usage = body["usage"]
        assert usage is not None
        assert usage["session_id"] == "session-abc"
        assert usage["recorded_model"] == "claude-sonnet-4-5"
        # 1500 + 300 only — the OTHER-session line is filtered out
        assert usage["input_tokens"] == 1500
        assert usage["output_tokens"] == 300
        assert usage["total_tokens"] == 1800
        # Single breakdown row (sonnet), no opus leak from OTHER-session
        assert len(usage["model_breakdowns"]) == 1
        assert usage["model_breakdowns"][0]["model"] == "claude-sonnet-4-5"
        assert usage["model_breakdowns"][0]["input_tokens"] == 1500


@pytest.mark.asyncio
async def test_card_usage_returns_empty_when_transcript_not_yet_written(tmp_path, monkeypatch):
    """Card has dispatch breadcrumbs but no JSONL has been written (session
    was spawned <1s ago, or it failed before any model call). Endpoint
    returns a CardUsage with zero tokens + session_id=None — NOT null, NOT
    404 — so the UI can render 'Awaiting first response…' without a
    card-level error."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
                          json={"project_key": "P", "title": "no-transcript"})
        cid = r.json()["id"]
        dispatch_started = datetime(2026, 7, 15, 9, 0, 0, tzinfo=UTC).isoformat()
        r = await ac.patch(f"/api/v1/kanban/cards/{cid}",
                           json={
                               "dispatch_started_at": dispatch_started,
                               "dispatch_project_folder": "-home-no-tr-yet",
                           })
        assert r.status_code == 200

        projects_dir = tmp_path / "projects"
        (projects_dir / "-home-no-tr-yet").mkdir(parents=True)  # folder exists, no JSONL
        monkeypatch.setattr(
            "app.services.dispatch_usage_service.get_claude_projects_dir",
            lambda: projects_dir,
        )

        r = await ac.get(f"/api/v1/kanban/cards/{cid}/usage")
        assert r.status_code == 200
        usage = r.json()["usage"]
        assert usage is not None
        assert usage["session_id"] is None
        assert usage["input_tokens"] == 0
        assert usage["output_tokens"] == 0
        assert usage["model_breakdowns"] == []
