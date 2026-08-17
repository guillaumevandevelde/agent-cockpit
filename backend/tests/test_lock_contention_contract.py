"""Pin the `lock_contention` agent-failure contract.

Contract: docs/cockpit/agent-failure-response.md.
* REST: 503 with `detail = {reason, retry_after_ms, attempts}`.
* MCP: `{"error": "lock_contention", retry_after_ms, attempts, operation}`.
"""
import sqlite3

import pytest
from sqlalchemy.exc import OperationalError

from app.kanban.db import LockContention, run_write_with_retry


def _locked_error() -> OperationalError:
    return OperationalError("stmt", {}, sqlite3.OperationalError("database is locked"))


@pytest.mark.asyncio
async def test_exhausted_retries_raise_lock_contention():
    async def factory():
        raise _locked_error()

    with pytest.raises(LockContention) as exc_info:
        await run_write_with_retry(
            factory, max_retries=3, backoff_base_ms=1, total_budget_ms=100,
        )
    assert exc_info.value.reason == "lock_contention"
    assert exc_info.value.attempts == 3
    assert exc_info.value.retry_after_ms == 500
    # Still an OperationalError, so callers catching the SQLAlchemy class keep working.
    assert isinstance(exc_info.value, OperationalError)


@pytest.mark.asyncio
async def test_rest_route_returns_503_contract(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app import main as main_module

    async def _boom(*args, **kwargs):
        raise LockContention(_locked_error(), attempts=3)

    monkeypatch.setattr(
        "app.api.v1.kanban.router.run_write_with_retry", _boom, raising=True,
    )
    transport = ASGITransport(app=main_module.app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.post(
            "/api/v1/kanban/cards",
            json={"project_key": "slug:test", "title": "x", "column": "Backlog"},
        )
    assert resp.status_code == 503
    assert resp.json()["detail"] == {
        "reason": "lock_contention", "retry_after_ms": 500, "attempts": 3,
    }
    assert resp.headers["Retry-After"] == "1"


@pytest.mark.asyncio
async def test_mcp_tool_returns_error_dict(monkeypatch):
    from app.kanban import mcp_server

    async def _boom(*args, **kwargs):
        raise LockContention(_locked_error(), attempts=3)

    monkeypatch.setattr(mcp_server, "run_write_with_retry", _boom, raising=True)
    result = await mcp_server.ping()
    assert result == {
        "error": "lock_contention",
        "retry_after_ms": 500,
        "attempts": 3,
        "operation": "ping",
    }
