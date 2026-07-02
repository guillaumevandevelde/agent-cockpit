"""Auth tests for the kanban MCP endpoint and the enable endpoint."""
import json
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app


@pytest.mark.asyncio
async def test_kanban_mcp_sse_blocked_without_token_when_api_token_set(monkeypatch):
    """/kanban-mcp/sse returns 401 when api_token is configured but not provided."""
    monkeypatch.setattr(settings, "api_token", "test-secret")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/kanban-mcp/sse")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_kanban_mcp_passes_with_correct_token(monkeypatch):
    """Kanban MCP paths are not blocked when the correct api_token is provided."""
    monkeypatch.setattr(settings, "api_token", "test-secret")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Use a non-existent path so FastMCP returns 404 fast (no SSE stream).
        # We only care that the middleware does NOT return 401.
        r = await client.get(
            "/kanban-mcp/nonexistent",
            headers={"Authorization": "Bearer test-secret"},
        )
    assert r.status_code != 401


@pytest.mark.asyncio
async def test_kanban_mcp_accessible_without_api_token(monkeypatch):
    """Kanban MCP paths are accessible without auth when api_token is not configured."""
    monkeypatch.setattr(settings, "api_token", None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/kanban-mcp/nonexistent")
    assert r.status_code != 401


@pytest.mark.asyncio
async def test_enable_writes_auth_header_when_api_token_set(tmp_path, monkeypatch):
    """enable writes Authorization header into .mcp.json when api_token is configured."""
    monkeypatch.setattr(settings, "api_token", "my-token")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/v1/kanban/enable",
            json={"project_path": str(tmp_path)},
            headers={"Authorization": "Bearer my-token"},
        )
    assert r.status_code == 200, r.text
    data = json.loads((tmp_path / ".mcp.json").read_text())
    entry = data["mcpServers"]["cockpit-kanban"]
    assert "headers" in entry
    assert entry["headers"]["Authorization"] == "Bearer my-token"


@pytest.mark.asyncio
async def test_enable_omits_auth_header_when_no_api_token(tmp_path, monkeypatch):
    """enable does not write Authorization header into .mcp.json when api_token is not set."""
    monkeypatch.setattr(settings, "api_token", None)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post(
            "/api/v1/kanban/enable",
            json={"project_path": str(tmp_path)},
        )
    assert r.status_code == 200, r.text
    data = json.loads((tmp_path / ".mcp.json").read_text())
    entry = data["mcpServers"]["cockpit-kanban"]
    assert "headers" not in entry
