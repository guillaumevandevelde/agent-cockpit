"""End-to-end self-check for the kanban MCP wiring.

check_mcp_health() must report ok=True with the real tool list when the mount is
sound, and ok=False (not raise, not hang) when the advertised message endpoint is
mis-wired -- the doubled-mount_path class of bug that silently strands agents.
This is the signal the UI surfaces.
"""
import asyncio

from starlette.applications import Starlette
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app.kanban.mcp_health import check_mcp_health


def test_mcp_health_is_ok_for_the_real_app():
    result = asyncio.run(check_mcp_health())
    assert result["ok"] is True, result
    assert result["advertised_endpoint"] == "/kanban-mcp/messages/", result
    assert result["routes_to_mount"] is True, result
    assert "list_cards" in result["tools"], result
    assert result["db_ok"] is True, result


def _doubled_app():
    """A deliberately mis-wired mount: mount_path doubles the prefix, exactly the
    regression that broke agent tool calls."""
    ts = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    mcp = FastMCP("probe", transport_security=ts)
    app = Starlette()
    app.mount("/kanban-mcp", mcp.sse_app(mount_path="/kanban-mcp"))
    return app, mcp


def test_mcp_health_flags_a_doubled_mount_path():
    app, mcp = _doubled_app()
    result = asyncio.run(check_mcp_health(app=app, mcp=mcp))
    assert result["ok"] is False
    assert result["advertised_endpoint"] == "/kanban-mcp/kanban-mcp/messages/", result
    assert result["routes_to_mount"] is False
    assert result["error"]
