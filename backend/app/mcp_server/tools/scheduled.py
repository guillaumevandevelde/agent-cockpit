"""MCP tools for scheduled messages."""
import json
from typing import Optional

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.scheduled_message import ScheduledMessage


def register_scheduled_tools(mcp: FastMCP) -> None:
    """Register scheduled message MCP tools."""

    @mcp.tool()
    async def list_scheduled_messages(
        enabled_only: bool = False,
        limit: int = 50,
    ) -> str:
        """List all scheduled messages.

        Args:
            enabled_only: If true, only return enabled messages.
            limit: Maximum number of messages to return (default 50).
        """
        async with AsyncSessionLocal() as db:
            stmt = select(ScheduledMessage).order_by(ScheduledMessage.id.desc())
            if enabled_only:
                stmt = stmt.where(ScheduledMessage.enabled == True)
            rows = (await db.execute(stmt.limit(limit))).scalars().all()

        items = []
        for m in rows:
            items.append({
                "id": m.id,
                "target_project": m.target_project,
                "message": m.message[:200] + ("..." if len(m.message) > 200 else ""),
                "trigger_type": m.trigger_type,
                "fire_at": m.fire_at,
                "cron_expr": m.cron_expr,
                "timezone": m.timezone,
                "permission_mode": m.permission_mode,
                "enabled": m.enabled,
                "status": m.status,
                "on_missing_session": m.on_missing_session,
                "when_busy": m.when_busy,
                "last_fired_at": m.last_fired_at.isoformat() if m.last_fired_at else None,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            })

        return json.dumps({"items": items, "total": len(items)}, indent=2)

    @mcp.tool()
    async def get_scheduled_message(message_id: int) -> str:
        """Get detailed information about a specific scheduled message.

        Args:
            message_id: The ID of the scheduled message.
        """
        async with AsyncSessionLocal() as db:
            msg = await db.get(ScheduledMessage, message_id)

        if not msg:
            return json.dumps({"error": f"Scheduled message {message_id} not found"})

        return json.dumps({
            "id": msg.id,
            "target_project": msg.target_project,
            "message": msg.message,
            "trigger_type": msg.trigger_type,
            "fire_at": msg.fire_at,
            "cron_expr": msg.cron_expr,
            "timezone": msg.timezone,
            "permission_mode": msg.permission_mode,
            "enabled": msg.enabled,
            "status": msg.status,
            "on_missing_session": msg.on_missing_session,
            "when_busy": msg.when_busy,
            "target_kind": msg.target_kind,
            "target_session_id": msg.target_session_id,
            "project_folder": msg.project_folder,
            "last_fired_at": msg.last_fired_at.isoformat() if msg.last_fired_at else None,
            "created_at": msg.created_at.isoformat() if msg.created_at else None,
            "updated_at": msg.updated_at.isoformat() if msg.updated_at else None,
        }, indent=2)
