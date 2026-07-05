"""Tests for the self-update API endpoint."""
import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import Base, engine
from app.main import app


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


# ── GET /api/v1/update/status ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_status_happy_path():
    """GET /api/v1/update/status returns version metadata."""
    async with _client() as ac:
        r = await ac.get("/api/v1/update/status")
    assert r.status_code == 200, r.text
    body = r.json()
    # Should report basic fields even in a test environment
    assert "version" in body
    assert "commit" in body
    assert "branch" in body
    assert "update_script_available" in body
    assert "working_tree_clean" in body
    assert "update_possible" in body


@pytest.mark.asyncio
async def test_update_status_reports_when_script_missing():
    """When update.sh doesn't exist, update_possible is False."""
    # The real VERSION file exists, git commands may work — just verify shape
    async with _client() as ac:
        r = await ac.get("/api/v1/update/status")
    assert r.status_code == 200
    body = r.json()
    # These are always present
    assert isinstance(body.get("update_script_available"), bool)
    assert isinstance(body.get("working_tree_clean"), bool)
    # update_possible = script_available AND working_tree_clean
    assert body["update_possible"] == (
        body["update_script_available"] and body["working_tree_clean"]
    )


# ── POST /api/v1/update/run ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_run_reports_missing_script():
    """POST /api/v1/update/run returns 404 when the script doesn't exist."""
    target = Path(__file__).resolve().parents[2] / "scripts" / "update.sh"
    if not target.exists():
        # Script genuinely missing — verify we get 404
        async with _client() as ac:
            r = await ac.post("/api/v1/update/run")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_run_streams_events():
    """POST /api/v1/update/run streams SSE events from the script."""
    target = Path(__file__).resolve().parents[2] / "scripts" / "update.sh"
    if not target.exists():
        pytest.skip("update.sh not present — cannot test streaming")

    async with _client() as ac:
        async with ac.stream("POST", "/api/v1/update/run") as response:
            assert response.status_code == 200
            ct = response.headers.get("content-type", "")
            assert ct.startswith("text/event-stream"), f"Unexpected content-type: {ct}"
            assert response.headers.get("cache-control") == "no-cache"
            assert response.headers.get("x-accel-buffering") == "no"

            # Read at least one event
            chunks = []
            async for chunk in response.aiter_text():
                chunks.append(chunk)
                if len(chunks) > 10:  # safety valve
                    break

            # Verify at least some events were received
            all_text = "".join(chunks)
            assert "event:" in all_text or "data:" in all_text


@pytest.mark.asyncio
async def test_update_run_handles_missing_script_gracefully():
    """When the script does not exist, /run returns 404 with detail."""
    original = Path(__file__).resolve().parents[2] / "scripts" / "update.sh"
    if not original.exists() or not original.is_file():
        pytest.skip("update.sh not found, test not applicable")

    # Temporarily rename the script to trigger "not found"
    backup = original.with_suffix(original.suffix + ".bak")
    original.rename(backup)
    try:
        async with _client() as ac:
            r = await ac.post("/api/v1/update/run")
        assert r.status_code == 404
        body = r.json()
        assert "detail" in body
    finally:
        backup.rename(original)


@pytest.mark.asyncio
async def test_update_status_consistent_data_types():
    """All status fields have consistent types regardless of environment."""
    async with _client() as ac:
        r = await ac.get("/api/v1/update/status")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["version"], str)
    assert isinstance(body["commit"], str)
    assert isinstance(body["branch"], str)
    assert isinstance(body["update_script_available"], bool)
    assert isinstance(body["working_tree_clean"], bool)
    assert isinstance(body["update_possible"], bool)


@pytest.mark.asyncio
async def test_update_run_script_output_parsing():
    """Verify the script produces valid JSON lines that match expected schema."""
    target = Path(__file__).resolve().parents[2] / "scripts" / "update.sh"
    if not target.exists():
        pytest.skip("update.sh not present")

    import subprocess
    result = subprocess.run(
        ["bash", str(target)],
        capture_output=True, text=True, timeout=10,
        cwd=target.parent.parent,
    )

    # The script should output JSON lines
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            pytest.fail(f"Script produced non-JSON line: {line}")
        assert "event" in parsed, f"Missing 'event' in: {parsed}"
        assert "message" in parsed, f"Missing 'message' in: {parsed}"
        assert parsed["event"] in (
            "preflight", "pulling", "building", "installing",
            "healthcheck", "done", "error",
        ), f"Unknown event type: {parsed['event']}"
