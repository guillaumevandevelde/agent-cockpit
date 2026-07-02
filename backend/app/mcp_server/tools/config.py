"""MCP tools for Claude Code configuration."""
import json

from mcp.server.fastmcp import FastMCP

from app.services.config_service import ConfigService


def register_config_tools(mcp: FastMCP) -> None:
    """Register configuration-related MCP tools."""

    @mcp.tool()
    async def get_config(
        project_path: str = "",
    ) -> str:
        """Get the current Claude Code configuration.

        Shows merged settings from user, project, and managed scopes.

        Args:
            project_path: Optional project directory path for project-scoped config.
        """
        service = ConfigService()
        path = project_path if project_path else None
        settings = service.get_merged_settings(path)

        return json.dumps({
            "settings": settings,
            "project_path": project_path or None,
        }, indent=2)

    @mcp.tool()
    async def list_config_files(
        project_path: str = "",
    ) -> str:
        """List all Claude Code configuration files and their status.

        Args:
            project_path: Optional project directory path.
        """
        service = ConfigService()
        path = project_path if project_path else None
        files = service.get_all_config_files(path)

        return json.dumps({"files": files, "total": len(files)}, indent=2)
