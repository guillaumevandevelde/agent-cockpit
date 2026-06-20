"""MCP tools for project management."""
import json

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.database import Project


def register_project_tools(mcp: FastMCP) -> None:
    """Register project-related MCP tools."""

    @mcp.tool()
    async def list_projects(
        limit: int = 50,
    ) -> str:
        """List all registered Claude Code projects.

        Args:
            limit: Maximum number of projects to return (default 50).
        """
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(Project).order_by(Project.last_accessed.desc()).limit(limit)
            )).scalars().all()

        items = []
        for p in rows:
            items.append({
                "id": p.id,
                "name": p.name,
                "path": p.path,
                "is_active": p.is_active,
                "last_accessed": p.last_accessed.isoformat() if p.last_accessed else None,
                "created_at": p.created_at.isoformat() if p.created_at else None,
            })

        return json.dumps({"projects": items, "total": len(items)}, indent=2)
