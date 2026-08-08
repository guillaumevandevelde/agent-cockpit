"""Agent Cockpit MCP Server — exposes cockpit data via MCP protocol."""
from mcp.server.fastmcp import FastMCP

from .tools import register_all_tools

mcp = FastMCP(
    "agent-cockpit",
    instructions=(
        "Agent Cockpit management server. Provides read access to "
        "agent sessions, recurring triggers, MCP server configs, "
        "projects, and merged configuration settings."
    ),
)

register_all_tools(mcp)
