"""MCP tools for Claude Code sessions."""
import json

from mcp.server.fastmcp import FastMCP

from app.database import AsyncSessionLocal
from app.services.presence_service import PresenceService


def register_session_tools(mcp: FastMCP) -> None:
    """Register session-related MCP tools."""

    @mcp.tool()
    async def list_sessions(
        status: str | None = None,
        limit: int = 50,
    ) -> str:
        """List all Claude Code sessions with their current status.

        Args:
            status: Filter by status (active, idle, error, ended). Omit for all.
            limit: Maximum number of sessions to return (default 50).
        """
        async with AsyncSessionLocal() as db:
            service = PresenceService()
            sessions = await service.get_all_sessions(db)

        if status:
            sessions = [s for s in sessions if s.status == status]

        result = []
        for s in sessions[:limit]:
            result.append({
                "session_id": s.session_id,
                "label": s.label,
                "status": s.status,
                "project_path": s.project_path,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "last_event_at": s.last_event_at.isoformat() if s.last_event_at else None,
                "total_events": s.total_events,
                "error_count": s.error_count,
                "last_command": s.last_command,
                "tmux_pane": s.tmux_pane,
            })

        return json.dumps({"sessions": result, "total": len(result)}, indent=2)

    @mcp.tool()
    async def get_session(session_id: str) -> str:
        """Get detailed information about a specific Claude Code session.

        Args:
            session_id: The session ID to look up.
        """
        async with AsyncSessionLocal() as db:
            service = PresenceService()
            sessions = await service.get_all_sessions(db)

        for s in sessions:
            if s.session_id == session_id:
                return json.dumps({
                    "session_id": s.session_id,
                    "label": s.label,
                    "status": s.status,
                    "status_text": s.status_text,
                    "project_path": s.project_path,
                    "last_narrative": s.last_narrative,
                    "modified_files": s.modified_files,
                    "last_command": s.last_command,
                    "last_command_exit": s.last_command_exit,
                    "total_events": s.total_events,
                    "error_count": s.error_count,
                    "started_at": s.started_at.isoformat() if s.started_at else None,
                    "last_event_at": s.last_event_at.isoformat() if s.last_event_at else None,
                    "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                    "tmux_pane": s.tmux_pane,
                }, indent=2)

        return json.dumps({"error": f"Session '{session_id}' not found"})
