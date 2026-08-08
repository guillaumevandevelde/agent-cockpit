"""MCP tools package — registers all tools on the server."""
from mcp.server.fastmcp import FastMCP

from .agent_mail import register_agent_mail_tools
from .config import register_config_tools
from .mcp import register_mcp_tools
from .projects import register_project_tools
from .sessions import register_session_tools


def register_all_tools(mcp: FastMCP) -> None:
    """Register all MCP tools on the given server."""
    register_session_tools(mcp)
    register_mcp_tools(mcp)
    register_config_tools(mcp)
    register_project_tools(mcp)
    register_agent_mail_tools(mcp)
