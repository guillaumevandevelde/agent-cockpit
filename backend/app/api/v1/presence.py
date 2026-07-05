"""API endpoints for Presence Dashboard — webhook receiver, REST, and WebSocket."""
import json
import secrets
import time

from fastapi import APIRouter, Depends, Query, Request, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.constants import SessionStatus
from app.models.schemas import (
    PresenceConfigSnippet,
    PresenceEventIn,
    PresenceSessionListResponse,
    PresenceSessionResponse,
    PresenceSessionUpdate,
)
from app.services.presence_service import PresenceService, manager
from app.services.scheduling.idle_state import idle_state
from app.utils.url_utils import resolve_base_url

router = APIRouter()
service = PresenceService()

_tokens: dict[str, float] = {}
_TOKEN_TTL = 30  # seconds


def _issue_token() -> str:
    now = time.time()
    expired = [t for t, ts in list(_tokens.items()) if now - ts > _TOKEN_TTL]
    for t in expired:
        _tokens.pop(t, None)
    token = secrets.token_urlsafe(32)
    _tokens[token] = now
    return token


def _consume_token(token: str) -> bool:
    issued_at = _tokens.pop(token, None)
    return issued_at is not None and (time.time() - issued_at) <= _TOKEN_TTL


@router.post("/events")
async def receive_event(
    payload: PresenceEventIn,
    db: AsyncSession = Depends(get_db),
):
    """Webhook receiver for Claude Code HTTP hooks. Always returns {} with 200."""
    dumped = payload.model_dump()
    updated_session = await service.process_event(dumped, db)

    # Feed the scheduled-messages idle detector from the same hook stream, so we
    # don't need a separate hook install. cwd identifies the project to deliver to.
    if payload.cwd:
        idle_state.record(payload.hook_event_name, cwd=payload.cwd, session_id=payload.session_id)

    # Broadcast to WebSocket clients
    msg = json.dumps({"type": "session_update", "session": updated_session.model_dump()})
    await manager.broadcast(msg)

    return {}


@router.get("/sessions", response_model=PresenceSessionListResponse)
async def list_sessions(db: AsyncSession = Depends(get_db)):
    """Return all presence sessions."""
    sessions = await service.get_all_sessions(db)
    active = sum(1 for s in sessions if s.status == SessionStatus.ACTIVE)
    error = sum(1 for s in sessions if s.status == SessionStatus.ERROR)
    return PresenceSessionListResponse(
        sessions=sessions, total=len(sessions), active=active, error=error
    )


@router.patch("/sessions/{session_id}", response_model=PresenceSessionResponse)
async def update_session_label(
    session_id: str,
    update: PresenceSessionUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update a session's label."""
    result = await service.update_label(session_id, update.label, db)
    if not result:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Session not found")
    return result


@router.delete("/sessions/{session_id}", status_code=204)
async def remove_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Remove a session from the dashboard."""
    removed = await service.remove_session(session_id, db)
    if not removed:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Session not found")

    # Broadcast removal
    msg = json.dumps({"type": "session_remove", "session_id": session_id})
    await manager.broadcast(msg)
    return None


@router.delete("/sessions", status_code=204)
async def clear_all_sessions(db: AsyncSession = Depends(get_db)):
    """Clear all sessions from the dashboard."""
    await service.clear_all_sessions(db)

    msg = json.dumps({"type": "sessions_cleared"})
    await manager.broadcast(msg)
    return None


@router.get("/config-snippet", response_model=PresenceConfigSnippet)
async def get_config_snippet(request: Request):
    """Generate the settings.json snippet for hooking up Claude Code."""
    url = f"{resolve_base_url(request)}/api/v1/presence/events"
    events = [
        "Notification", "PreToolUse", "PostToolUse", "Stop",
        "SessionStart", "SessionEnd", "UserPromptSubmit",
        "SubagentStart", "SubagentStop",
    ]
    # Command hook: forward the hook JSON from stdin plus $TMUX_PANE (the exact
    # join key to the Agent Bridge), then POST to the events endpoint. We also
    # normalise Claude Code's field names to what the events endpoint expects:
    # CC sends `tool_response`/`prompt`, while PresenceEventIn reads
    # `tool_result`/`user_prompt` — without this the error badge and prompt
    # capture stay empty.
    jq_filter = (
        ". + {tmux_pane:$p, "
        "tool_result:(.tool_response // .tool_result), "
        "user_prompt:(.prompt // .user_prompt)}"
    )
    auth_header = (
        f" -H 'Authorization: Bearer {settings.api_token}'"
        if settings.api_token
        else ""
    )
    command = (
        f"jq -c --arg p \"$TMUX_PANE\" '{jq_filter}' | "
        f"curl -sf -X POST {url} -H 'Content-Type: application/json'"
        f"{auth_header} -d @-"
    )
    snippet = {
        "hooks": {
            event: [{"hooks": [{"type": "command", "command": command}]}]
            for event in events
        }
    }
    instructions = (
        "Add this to your ~/.claude/settings.json (or merge into existing hooks). "
        "Requires `jq` and `curl` on PATH. Then restart any running Claude Code "
        "sessions for the hooks to take effect."
    )
    return PresenceConfigSnippet(snippet=snippet, instructions=instructions)


@router.get("/token")
async def get_presence_ws_token():
    """Issue a short-lived one-time token for WebSocket authentication."""
    return {"token": _issue_token()}


@router.websocket("/ws")
async def presence_websocket(
    ws: WebSocket,
    token: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
):
    """WebSocket for live presence updates. On connect, sends all current sessions."""
    if settings.api_token:
        if not _consume_token(token):
            await ws.close(code=4401, reason="Invalid or expired token")
            return
    await manager.connect(ws)
    try:
        # Send initial state
        sessions = await service.get_all_sessions(db)
        for s in sessions:
            await ws.send_text(
                json.dumps({"type": "session_update", "session": s.model_dump()})
            )

        # Keep connection alive — wait for disconnection
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(ws)
