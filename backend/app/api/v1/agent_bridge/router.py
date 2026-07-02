"""Agent Bridge endpoints: mixed provider session discovery and terminal access."""
from __future__ import annotations

import logging
import secrets
import time
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket
from pydantic import BaseModel

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.schemas import ResumableSessionListResponse
from app.services.agent_bridge import git_status as git_status_service
from app.services.agent_bridge.discovery import capture_pane_preview, discover_agent_sessions
from app.services.agent_bridge.pty_relay import PtyRelay
from app.services.agent_bridge.resumable import list_resumable_sessions
from app.services.agent_bridge.spawn import kill_session, rename_session, spawn_session
from app.services.host_service import HostNotFoundError
from app.services.providers import get_provider
from app.services.providers.base import SpawnCommandOptions

logger = logging.getLogger(__name__)

router = APIRouter()

_tokens: dict[str, float] = {}
_TOKEN_TTL = 30


class SpawnRequest(BaseModel):
    provider: str = "claude-code"
    session_name: str | None = None
    directory: str
    mode: Literal["plain", "worktree", "resume", "fork"] = "plain"
    worktree_name: str | None = None
    session_id: str | None = None
    project_folder: str | None = None
    skip_permissions: bool = False
    prompt: str | None = None
    model: str | None = None
    profile: str | None = None
    profile_v2: str | None = None
    sandbox: str | None = None
    approval_policy: str | None = None
    search: bool | None = None
    no_alt_screen: bool = False
    dangerously_bypass_approvals_and_sandbox: bool = False
    use_last: bool = False
    platform: str = "anthropic"
    aws_region: str | None = None
    aws_profile: str | None = None
    bedrock_model: str | None = None
    host_id: int | None = None


@router.get("/sessions")
def list_sessions(provider: str | None = Query(default=None)):
    try:
        sessions = discover_agent_sessions(provider)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"sessions": sessions, "count": len(sessions)}


@router.get("/resumable-sessions", response_model=ResumableSessionListResponse)
async def list_resumable_sessions_endpoint(
    directory: str = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    try:
        sessions = await list_resumable_sessions(directory, limit, db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ResumableSessionListResponse(sessions=sessions)


@router.get("/sessions/{target:path}/preview")
def get_session_preview(target: str):
    content = capture_pane_preview(target)
    if not content:
        raise HTTPException(status_code=404, detail="Could not capture pane")
    return {"target": target, "content": content}


class GitStatusResponse(BaseModel):
    is_git_repo: bool
    branch: str | None = None
    detached: bool = False
    upstream: str | None = None
    ahead: int = 0
    behind: int = 0
    dirty: bool = False


@router.get("/sessions/{target:path}/git-status", response_model=GitStatusResponse)
async def get_session_git_status(target: str):
    """Live git status of a running session's working directory (on demand)."""
    status = await git_status_service.get_session_git_status(target)
    if status is None:
        raise HTTPException(status_code=404, detail="Session pane not found")
    return status


@router.get("/token")
async def get_terminal_token():
    now = time.time()
    expired = [token for token, issued_at in _tokens.items() if now - issued_at > _TOKEN_TTL]
    for token in expired:
        _tokens.pop(token, None)

    token = secrets.token_urlsafe(32)
    _tokens[token] = now
    return {"token": token}


def _is_same_origin(origin: str, websocket: WebSocket) -> bool:
    try:
        origin_host = urlparse(origin).netloc.lower()
    except ValueError:
        return False
    if not origin_host:
        return False

    request_host = (websocket.headers.get("host") or "").lower()
    if request_host and origin_host == request_host:
        return True
    return origin in settings.cors_origins


def _validate_token(token: str) -> bool:
    issued_at = _tokens.pop(token, None)
    return issued_at is not None and (time.time() - issued_at) <= _TOKEN_TTL


@router.websocket("/sessions/{target:path}/terminal")
async def session_terminal(
    websocket: WebSocket,
    target: str,
    token: str = "",
    mode: str = "readonly",
):
    origin = websocket.headers.get("origin", "")
    if origin and not _is_same_origin(origin, websocket):
        await websocket.close(code=4403, reason="Invalid origin")
        return

    if not _validate_token(token):
        await websocket.close(code=4401, reason="Invalid or expired token")
        return

    relay = PtyRelay(target=target, read_only=mode != "interactive")
    await relay.run(websocket)


@router.post("/sessions")
async def spawn_session_endpoint(request: SpawnRequest, db: AsyncSession = Depends(get_db)):
    try:
        get_provider(request.provider)

        host_data = None
        if request.host_id is not None:
            from app.services.host_service import get_host as get_host_data
            host_data = await get_host_data(db, request.host_id)

        options = SpawnCommandOptions(
            directory=request.directory,
            mode=request.mode,
            worktree_name=request.worktree_name,
            session_id=request.session_id,
            project_folder=request.project_folder,
            skip_permissions=request.skip_permissions,
            prompt=request.prompt,
            model=request.model,
            profile=request.profile,
            profile_v2=request.profile_v2,
            sandbox=request.sandbox,
            approval_policy=request.approval_policy,
            search=request.search,
            no_alt_screen=request.no_alt_screen,
            dangerously_bypass_approvals_and_sandbox=request.dangerously_bypass_approvals_and_sandbox,
            use_last=request.use_last,
            platform=request.platform,
            aws_region=request.aws_region,
            aws_profile=request.aws_profile,
            bedrock_model=request.bedrock_model,
            host_id=request.host_id,
        )
        return spawn_session(request.provider, options, session_name=request.session_name, host_data=host_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class BulkResumeItem(BaseModel):
    session_id: str
    project_folder: str


class BulkResumeRequest(BaseModel):
    provider: str = "claude-code"
    directory: str = ""
    sessions: list[BulkResumeItem]
    skip_permissions: bool = False
    platform: str = "anthropic"
    aws_region: str | None = None
    aws_profile: str | None = None
    bedrock_model: str | None = None


class BulkResumeResult(BaseModel):
    session_id: str
    project_folder: str
    ok: bool
    tmux_target: str | None = None
    session_name: str | None = None
    error: str | None = None


class BulkResumeResponse(BaseModel):
    results: list[BulkResumeResult]
    spawned: int
    failed: int


@router.post("/sessions/bulk-resume", response_model=BulkResumeResponse)
def bulk_resume_endpoint(request: BulkResumeRequest):
    if not request.sessions:
        raise HTTPException(status_code=400, detail="No sessions provided")
    try:
        get_provider(request.provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    results: list[BulkResumeResult] = []
    for item in request.sessions:
        # Each session resolves its own launch directory from its project_folder,
        # so a batch can span multiple worktrees. One failure never aborts the rest.
        options = SpawnCommandOptions(
            directory=request.directory,
            mode="resume",
            session_id=item.session_id,
            project_folder=item.project_folder,
            skip_permissions=request.skip_permissions,
            platform=request.platform,
            aws_region=request.aws_region,
            aws_profile=request.aws_profile,
            bedrock_model=request.bedrock_model,
        )
        try:
            spawned = spawn_session(request.provider, options)
            results.append(
                BulkResumeResult(
                    session_id=item.session_id,
                    project_folder=item.project_folder,
                    ok=True,
                    tmux_target=spawned["tmux_target"],
                    session_name=spawned["session_name"],
                )
            )
        except ValueError as exc:
            results.append(
                BulkResumeResult(
                    session_id=item.session_id,
                    project_folder=item.project_folder,
                    ok=False,
                    error=str(exc),
                )
            )

    spawned_count = sum(1 for r in results if r.ok)
    return BulkResumeResponse(
        results=results,
        spawned=spawned_count,
        failed=len(results) - spawned_count,
    )


@router.delete("/sessions/{target}")
def kill_session_endpoint(target: str, cleanup_worktree: bool = False):
    return kill_session(session_name=target, cleanup_worktree=cleanup_worktree)


class RenameRequest(BaseModel):
    name: str


@router.post("/sessions/{target}/rename")
def rename_session_endpoint(target: str, request: RenameRequest):
    try:
        return rename_session(old_name=target, new_name=request.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

