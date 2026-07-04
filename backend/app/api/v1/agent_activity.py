"""Agent activity API — live status of running agent sessions."""
from __future__ import annotations

import asyncio
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services.agent_bridge.discovery import discover_agent_sessions, capture_pane_preview

router = APIRouter(prefix="/agent-activity", tags=["Agent Activity"])


class AgentActivity(BaseModel):
    tmux_target: str
    session_name: str
    cwd: str
    pid: str
    provider: str
    preview: str | None = None
    status: str = "active"


@router.get("/live")
async def get_live_agents(
    provider: str | None = Query(default=None),
    preview_lines: int = Query(default=5, ge=1, le=20),
) -> dict:
    """Return currently running agent sessions with optional pane preview."""
    sessions = await asyncio.to_thread(discover_agent_sessions, provider)
    agents: list[AgentActivity] = []
    for s in sessions:
        preview = None
        target = s.get("tmux_target", "")
        if target:
            raw = await asyncio.to_thread(capture_pane_preview, target)
            if raw:
                lines = raw.strip().splitlines()
                preview = "\n".join(lines[-preview_lines:])
        agents.append(AgentActivity(
            tmux_target=target,
            session_name=s.get("session_name", ""),
            cwd=s.get("cwd", ""),
            pid=s.get("pid", ""),
            provider=s.get("provider", "unknown"),
            preview=preview,
            status=_infer_status(raw if target else None, s),
        ))
    return {"agents": agents, "count": len(agents)}


def _infer_status(preview: str | None, session: dict) -> str:
    """Infer agent status from pane content."""
    if not preview:
        return "active"
    lower = preview.lower()
    if "waiting for" in lower or "permission" in lower or "approve" in lower:
        return "waiting"
    if "error" in lower or "failed" in lower:
        return "error"
    return "active"


@router.get("/summary")
async def get_activity_summary() -> dict:
    """Return a compact summary for the dashboard."""
    sessions = await asyncio.to_thread(discover_agent_sessions)
    providers: dict[str, int] = {}
    for s in sessions:
        p = s.get("provider", "unknown")
        providers[p] = providers.get(p, 0) + 1
    return {
        "total": len(sessions),
        "by_provider": providers,
        "has_active": len(sessions) > 0,
    }
