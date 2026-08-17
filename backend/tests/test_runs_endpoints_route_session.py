"""The endpoint-registry routes must read the *kanban* store, not the registry store.

``app/services/agentic_cli/endpoints.py`` persists endpoints as ``KanbanMeta``
rows, which live in the kanban DB (``~/.claude-registry/kanban.db``). The
routes in ``app/api/v1/runs/router.py`` used to inject ``Depends(get_db)`` —
the *device-local registry* session — and hand it straight to those helpers,
so every call died with ``no such table: kanban_meta`` (observed 12x in
``logs/backend/run-20260817-082951-3592-0.log``; it 500s the Agent-Bridge
NewSessionDialog).

The existing ``test_provider_endpoints.py`` never caught this because it calls
the helpers with a hand-built kanban session and so bypasses the route's own
dependency wiring. These tests drive the real ASGI route instead, which is the
only surface where the wrong-store mistake is observable.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_list_endpoints_route_reads_the_kanban_store():
    """A clean board answers with an empty list, not a 500.

    Asserting the exact clean-state shape (200 + ``endpoints == []``) rather
    than ``status_code != 500``: the latter would also pass on a 404 from a
    renamed route.
    """
    async with _client() as c:
        r = await c.get("/api/v1/agent-bridge/platforms/endpoints")
    assert r.status_code == 200, r.text
    assert r.json() == {"endpoints": []}


@pytest.mark.asyncio
async def test_endpoint_upsert_list_delete_roundtrip_through_the_route():
    """Write and read must land in the same store, or the row vanishes."""
    payload = {
        "name": "probe-endpoint",
        "base_url": "https://example.invalid/anthropic",
        "model": "probe-model",
        "credential_name": None,
    }
    async with _client() as c:
        r = await c.post("/api/v1/agent-bridge/platforms/endpoints", json=payload)
        assert r.status_code == 200, r.text

        r = await c.get("/api/v1/agent-bridge/platforms/endpoints")
        assert r.status_code == 200, r.text
        names = [e["name"] for e in r.json()["endpoints"]]
        assert names == ["probe-endpoint"], r.text

        r = await c.delete("/api/v1/agent-bridge/platforms/endpoints/probe-endpoint")
        assert r.status_code == 200, r.text

        r = await c.get("/api/v1/agent-bridge/platforms/endpoints")
        assert r.json() == {"endpoints": []}


@pytest.mark.asyncio
async def test_catalog_seed_route_reads_the_kanban_store():
    """The bulk-seed route shares the same wrong-store hazard."""
    async with _client() as c:
        r = await c.post("/api/v1/agent-bridge/platforms/endpoints-catalog/seed")
        assert r.status_code == 200, r.text
        assert r.json()["installed"] > 0, r.text

        r = await c.get("/api/v1/agent-bridge/platforms/endpoints")
        assert r.status_code == 200, r.text
        assert len(r.json()["endpoints"]) > 0, r.text
