"""MCP tools for MCP server management."""
import json
from typing import Optional

from mcp.server.fastmcp import FastMCP

from app.database import AsyncSessionLocal
from app.services.mcp_service import MCPService


def register_mcp_tools(mcp: FastMCP) -> None:
    """Register MCP server management tools."""

    @mcp.tool()
    async def list_mcp_servers(
        project_path: Optional[str] = None,
    ) -> str:
        """List all configured MCP servers with their status.

        Args:
            project_path: Optional project path to include project-scoped servers.
        """
        service = MCPService()
        async with AsyncSessionLocal() as db:
            servers = await service.list_servers(project_path, db)

        items = []
        for s in servers:
            items.append({
                "name": s.name,
                "type": s.type,
                "scope": s.scope,
                "is_connected": s.is_connected,
                "disabled": s.disabled,
                "tool_count": s.tool_count or 0,
                "resource_count": s.resource_count or 0,
                "prompt_count": s.prompt_count or 0,
                "last_error": s.last_error,
                "source": s.source,
            })

        return json.dumps({"servers": items, "total": len(items)}, indent=2)

    @mcp.tool()
    async def get_mcp_server(
        name: str,
        scope: str = "user",
    ) -> str:
        """Get detailed information about a specific MCP server.

        Args:
            name: Server name.
            scope: Server scope (user, project, plugin, managed).
        """
        service = MCPService()
        server = await service.get_server(name, scope)

        if not server:
            return json.dumps({"error": f"Server '{name}' not found in '{scope}' scope"})

        return json.dumps({
            "name": server.name,
            "type": server.type,
            "scope": server.scope,
            "command": server.command,
            "args": server.args,
            "url": server.url,
            "is_connected": server.is_connected,
            "disabled": server.disabled,
            "tool_count": server.tool_count or 0,
            "resource_count": server.resource_count or 0,
            "prompt_count": server.prompt_count or 0,
            "tools": [{"name": t.name, "description": t.description} for t in (server.tools or [])],
            "last_error": server.last_error,
            "source": server.source,
        }, indent=2)
