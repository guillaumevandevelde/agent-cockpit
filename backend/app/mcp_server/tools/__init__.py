"""MCP tools package — registers all tools on the server."""
from mcp.server.fastmcp import FastMCP

from .sessions import register_session_tools
from .scheduled import register_scheduled_tools
from .mcp import register_mcp_tools
from .config import register_config_tools
from .projects import register_project_tools


def register_all_tools(mcp: FastMCP) -> None:
    """Register all MCP tools on the given server."""
    register_session_tools(mcp)
    register_scheduled_tools(mcp)
    register_mcp_tools(mcp)
    register_config_tools(mcp)
    register_project_tools(mcp)
