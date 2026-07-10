# backend/tests/test_kanban_model_options_api.py
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.kanban import dispatch
from app.main import app
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_get_model_options_returns_seed_before_any_refresh():
    async with await _client() as c:
        r = await c.get("/api/v1/kanban/model-options")
    assert r.status_code == 200
    assert r.json() == {"provider": "claude-code", "options": list(dispatch.MODEL_OPTIONS_SEED)}


@pytest.mark.asyncio
async def test_refresh_model_options_updates_cache():
    with patch.object(dispatch, "refresh_claude_model_options_sync",
                      return_value=["sonnet", "opus", "haiku", "fable"]):
        async with await _client() as c:
            r = await c.post("/api/v1/kanban/model-options/refresh")
    assert r.status_code == 200
    assert r.json() == {"provider": "claude-code",
                        "options": ["sonnet", "opus", "haiku", "fable"]}


@pytest.mark.asyncio
async def test_refresh_model_options_502_when_cli_unavailable():
    with patch.object(dispatch, "refresh_claude_model_options_sync",
                      side_effect=FileNotFoundError("claude not found")):
        async with await _client() as c:
            r = await c.post("/api/v1/kanban/model-options/refresh")
    assert r.status_code == 502
