"""Edge cases for MCPService.test_connection: unknown server, misconfigured
stdio/http servers, and network timeouts talking to remote MCP servers."""
import httpx
import pytest

from app.services import mcp_service as mcp_mod
from app.models.schemas import MCPServer
from app.services.mcp_service import MCPService


def _server(**kw) -> MCPServer:
    base = dict(name="srv", type="stdio", scope="user")
    base.update(kw)
    return MCPServer(**base)


def _patch_get_server(monkeypatch, svc, server):
    async def _fake(name, scope):
        return server
    monkeypatch.setattr(svc, "get_server", _fake)


class _TimeoutClient:
    """httpx.AsyncClient stand-in whose requests always time out."""
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *a, **k):
        raise httpx.TimeoutException("timed out")

    async def get(self, *a, **k):
        raise httpx.TimeoutException("timed out")


@pytest.mark.asyncio
async def test_test_connection_unknown_server(monkeypatch):
    svc = MCPService()
    _patch_get_server(monkeypatch, svc, None)
    result = await svc.test_connection("ghost", "user")
    assert result["success"] is False
    assert "not found" in result["message"]


@pytest.mark.asyncio
async def test_test_connection_stdio_without_command(monkeypatch):
    svc = MCPService()
    _patch_get_server(monkeypatch, svc, _server(type="stdio", command=None))
    result = await svc.test_connection("srv", "user")
    assert result["success"] is False
    assert "No command" in result["message"]


@pytest.mark.asyncio
async def test_test_connection_stdio_command_not_in_path(monkeypatch):
    svc = MCPService()
    _patch_get_server(monkeypatch, svc,
                      _server(type="stdio", command="definitely-not-a-real-binary-xyz"))
    result = await svc.test_connection("srv", "user")
    assert result["success"] is False
    assert "not found in PATH" in result["message"]


@pytest.mark.asyncio
async def test_test_connection_http_without_url(monkeypatch):
    svc = MCPService()
    _patch_get_server(monkeypatch, svc, _server(type="http", url=None))
    result = await svc.test_connection("srv", "user")
    assert result["success"] is False
    assert "No URL" in result["message"]


@pytest.mark.asyncio
async def test_test_connection_http_times_out(monkeypatch):
    svc = MCPService()
    _patch_get_server(monkeypatch, svc, _server(type="http", url="https://mcp.example/api"))
    monkeypatch.setattr(mcp_mod.CredentialsService, "get_mcp_token",
                        lambda self, name, url: None)
    monkeypatch.setattr(mcp_mod.httpx, "AsyncClient", _TimeoutClient)
    result = await svc.test_connection("srv", "user")
    assert result["success"] is False
    assert result["message"] == "Connection timeout"


@pytest.mark.asyncio
async def test_test_connection_sse_times_out(monkeypatch):
    svc = MCPService()
    _patch_get_server(monkeypatch, svc, _server(type="sse", url="https://mcp.example/sse"))
    monkeypatch.setattr(mcp_mod.CredentialsService, "get_mcp_token",
                        lambda self, name, url: None)
    monkeypatch.setattr(mcp_mod.httpx, "AsyncClient", _TimeoutClient)
    result = await svc.test_connection("srv", "user")
    assert result["success"] is False
    assert result["message"] == "Connection timeout"
