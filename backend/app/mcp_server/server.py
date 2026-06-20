"""Claude Cockpit MCP Server — exposes cockpit data via MCP protocol."""
from mcp.server.fastmcp import FastMCP

from .tools import register_all_tools

mcp = FastMCP(
    "claude-cockpit",
    instructions=(
        "Claude Cockpit management server. Provides read access to "
        "Claude Code sessions, scheduled messages, MCP server configs, "
        "projects, and merged configuration settings."
    ),
)

register_all_tools(mcp)
