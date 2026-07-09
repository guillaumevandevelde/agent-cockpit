"""Agent Bridge endpoints: mixed provider session discovery, terminal access, and team grouping."""
from __future__ import annotations

import logging
import secrets
import time
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.schemas import (
    BridgeAttachmentDeleteResponse,
    BridgeAttachmentListResponse,
    BridgeAttachmentPasteRequest,
    BridgeAttachmentPasteResponse,
    BridgeAttachmentResponse,
    ResumableSessionListResponse,
)
from app.services.agent_bridge import git_status as git_status_service
from app.services.agent_bridge import minimax_credentials
from app.services.agent_bridge import teams as teams_service
from app.services.agent_bridge.attachments import agent_bridge_attachment_service
from app.services.agent_bridge.discovery import capture_pane_preview, discover_agent_sessions
from app.services.agent_bridge.pty_relay import PtyRelay, is_target_interactive
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
    minimax_base_url: str | None = None
    host_id: int | None = None
    agent: str | None = None
    context_tier: str | None = None
    reasoning_effort: str | None = None
    plan: bool = False
    remote: bool | None = None
    allow_all: bool = False
    no_ask_user: bool = False


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


class PlatformStatusResponse(BaseModel):
    configured: bool


@router.get("/platforms/minimax/status", response_model=PlatformStatusResponse)
def get_minimax_platform_status():
    """Whether MINIMAX_API_KEY is set in the backend environment.

    Never returns the key itself: Cockpit resolves it server-side in
    spawn_session and it must never reach the browser.
    """
    return {"configured": bool(settings.minimax_api_key)}


class MinimaxCredentialsRequest(BaseModel):
    minimax_api_key: str


@router.post("/platforms/minimax/credentials", response_model=PlatformStatusResponse)
def set_minimax_credentials(request: MinimaxCredentialsRequest):
    """Set the MiniMax API key from the UI: writes it to the backend .env file
    and updates the running Settings immediately. Never returns the key."""
    try:
        minimax_credentials.set_minimax_api_key(request.minimax_api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"configured": True}


@router.delete("/platforms/minimax/credentials", response_model=PlatformStatusResponse)
def clear_minimax_credentials():
    minimax_credentials.clear_minimax_api_key()
    return {"configured": False}


@router.get("/token")
async def get_terminal_token():
    now = time.time()
    expired = [token for token, issued_at in _tokens.items() if now - issued_at > _TOKEN_TTL]
    for token in expired:
        _tokens.pop(token, None)

    token = secrets.token_urlsafe(32)
    _tokens[token] = now
    return {"token": token}


def _is_same_origin_host(origin: str, request_host: str) -> bool:
    try:
        origin_host = urlparse(origin).netloc.lower()
    except ValueError:
        return False
    if not origin_host:
        return False

    if request_host and origin_host == request_host:
        return True
    return origin in settings.cors_origins


def _is_same_origin(origin: str, websocket: WebSocket) -> bool:
    return _is_same_origin_host(origin, (websocket.headers.get("host") or "").lower())


def _validate_token(token: str) -> bool:
    issued_at = _tokens.pop(token, None)
    return issued_at is not None and (time.time() - issued_at) <= _TOKEN_TTL


def _require_attachment_access(
    request: Request,
    token: str,
) -> None:
    origin = request.headers.get("origin", "")
    if origin and not _is_same_origin_host(origin, (request.headers.get("host") or "").lower()):
        raise HTTPException(status_code=403, detail="Invalid origin")
    if not _validate_token(token):
        raise HTTPException(status_code=401, detail="Invalid or expired token")


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


@router.post("/sessions/{target:path}/attachments", response_model=BridgeAttachmentResponse)
async def upload_session_attachment(
    target: str,
    request: Request,
    file: UploadFile = File(...),
    template: str | None = Form(default=None),
    prompt: str | None = Form(default=None),
    created_by: str | None = Form(default="deck-ui"),
    token: str = Header(default="", alias="X-Claude-Cockpit-Terminal-Token"),
    db: AsyncSession = Depends(get_db),
):
    _require_attachment_access(request, token)
    try:
        content = await file.read(settings.bridge_attachment_max_bytes + 1)
        return await agent_bridge_attachment_service.create_attachment(
            db,
            target=target,
            content=content,
            original_filename=file.filename,
            prompt=prompt,
            template=template,
            created_by=created_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sessions/{target:path}/attachments", response_model=BridgeAttachmentListResponse)
async def list_session_attachments(
    target: str,
    request: Request,
    token: str = Header(default="", alias="X-Claude-Cockpit-Terminal-Token"),
    db: AsyncSession = Depends(get_db),
):
    _require_attachment_access(request, token)
    attachments = await agent_bridge_attachment_service.list_attachments(db, target=target)
    return BridgeAttachmentListResponse(attachments=attachments)


@router.post(
    "/sessions/{target:path}/attachments/{attachment_id}/paste",
    response_model=BridgeAttachmentPasteResponse,
)
async def paste_session_attachment(
    target: str,
    attachment_id: int,
    paste_request: BridgeAttachmentPasteRequest,
    request: Request,
    token: str = Header(default="", alias="X-Claude-Cockpit-Terminal-Token"),
    db: AsyncSession = Depends(get_db),
):
    _require_attachment_access(request, token)
    if paste_request.require_interactive_relay and not is_target_interactive(target):
        raise HTTPException(status_code=409, detail="Terminal relay is read-only or not attached")
    try:
        return await agent_bridge_attachment_service.paste_attachment(
            db,
            target=target,
            attachment_id=attachment_id,
            request=paste_request,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete(
    "/sessions/{target:path}/attachments/{attachment_id}",
    response_model=BridgeAttachmentDeleteResponse,
)
async def delete_session_attachment(
    target: str,
    attachment_id: int,
    request: Request,
    token: str = Header(default="", alias="X-Claude-Cockpit-Terminal-Token"),
    db: AsyncSession = Depends(get_db),
):
    _require_attachment_access(request, token)
    try:
        return await agent_bridge_attachment_service.delete_attachment(
            db,
            target=target,
            attachment_id=attachment_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
            minimax_base_url=request.minimax_base_url,
            host_id=request.host_id,
            agent=request.agent,
            context_tier=request.context_tier,
            reasoning_effort=request.reasoning_effort,
            plan=request.plan,
            remote=request.remote,
            allow_all=request.allow_all,
            no_ask_user=request.no_ask_user,
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
    minimax_base_url: str | None = None


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
            minimax_base_url=request.minimax_base_url,
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


# ── Agent Team endpoints ──────────────────────────────────────────────────


class TeamMemberInfo(BaseModel):
    session_name: str
    pane_id: str | None = None
    tmux_target: str = ""


class AgentTeamResponse(BaseModel):
    team_id: str
    name: str
    provider: str
    cwd: str
    is_auto_detected: bool
    lead: dict[str, Any] | None = None
    members: list[dict[str, Any]]


class AgentTeamsResponse(BaseModel):
    teams: list[AgentTeamResponse]
    ungrouped: list[dict[str, Any]]
    total_teams: int
    total_sessions: int


@router.get("/teams")
async def list_teams(db: AsyncSession = Depends(get_db)):
    """List all agent teams (auto-detected + manual) with their sessions."""
    sessions = discover_agent_sessions()
    manual_teams = await teams_service.get_manual_teams(db)
    teams = teams_service.discover_teams(sessions, manual_teams)
    ungrouped = teams_service.get_ungrouped_sessions(sessions, teams)
    return AgentTeamsResponse(
        teams=[AgentTeamResponse(**t) for t in teams],
        ungrouped=ungrouped,
        total_teams=len(teams),
        total_sessions=len(sessions),
    )


class CreateTeamRequest(BaseModel):
    name: str
    provider: str = ""
    cwd: str = ""
    lead_session_name: str | None = None
    members: list[TeamMemberInfo] = []


@router.post("/teams")
async def create_team(request: CreateTeamRequest, db: AsyncSession = Depends(get_db)):
    """Create a new manual agent team."""
    try:
        team = await teams_service.create_manual_team(
            db,
            name=request.name,
            provider=request.provider,
            cwd=request.cwd,
            lead_session_name=request.lead_session_name,
            member_sessions=[m.model_dump() for m in request.members],
        )
        return team
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/teams/{team_id}")
async def delete_team(team_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a manual agent team."""
    success = await teams_service.delete_manual_team(db, team_id)
    if not success:
        raise HTTPException(status_code=404, detail="Team not found")
    return {"deleted": True}


class AddMemberRequest(BaseModel):
    session_name: str
    pane_id: str | None = None
    tmux_target: str = ""


@router.post("/teams/{team_id}/members")
async def add_team_member(
    team_id: int, request: AddMemberRequest, db: AsyncSession = Depends(get_db)
):
    """Add a member to a manual team."""
    success = await teams_service.add_team_member(
        db, team_id, request.session_name, request.pane_id, request.tmux_target
    )
    if not success:
        raise HTTPException(status_code=404, detail="Team not found")
    return {"added": True}


@router.delete("/teams/{team_id}/members/{member_id}")
async def remove_team_member(
    team_id: int, member_id: int, db: AsyncSession = Depends(get_db)
):
    """Remove a member from a manual team."""
    success = await teams_service.remove_team_member(db, member_id)
    if not success:
        raise HTTPException(status_code=404, detail="Member not found")
    return {"removed": True}

