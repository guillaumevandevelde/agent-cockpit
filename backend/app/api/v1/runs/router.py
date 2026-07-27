"""Run endpoints: mixed agentic-CLI run discovery, terminal access, and run-group endpoints.

The wire-format (URL prefix, JSON keys, and team/group shape) is preserved for
back-compat with the frontend; the underlying Python class names follow the
canonical "Run" terminology from ``docs/cockpit/terminology.md``.
"""
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
from app.services.agentic_cli import get_agentic_cli
from app.services.agentic_cli.base import SpawnCommandOptions
from app.services.host_service import HostNotFoundError
from app.services.runs import git_status as git_status_service
from app.services.runs import groups as groups_service
from app.services.runs import minimax_credentials
from app.services.runs.attachments import run_attachment_service
from app.services.runs.discovery import capture_pane_preview, discover_agent_sessions
from app.services.runs.pty_relay import PtyRelay, is_target_interactive
from app.services.runs.resumable import list_resumable_sessions
from app.services.runs.spawn import kill_session, rename_session, spawn_session

logger = logging.getLogger(__name__)

router = APIRouter()

_tokens: dict[str, float] = {}
_TOKEN_TTL = 30


class SpawnRequest(BaseModel):
    cli: str = "claude-code"
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
    provider: str = "anthropic"
    aws_region: str | None = None
    aws_profile: str | None = None
    bedrock_model: str | None = None
    minimax_base_url: str | None = None
    endpoint_name: str | None = None
    host_id: int | None = None
    agent: str | None = None
    context_tier: str | None = None
    reasoning_effort: str | None = None
    plan: bool = False
    remote: bool | None = None
    allow_all: bool = False
    no_ask_user: bool = False


@router.get("/sessions")
def list_sessions(cli: str | None = Query(default=None)):
    try:
        sessions = discover_agent_sessions(cli)
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


class ProviderStatusResponse(BaseModel):
    configured: bool


@router.get("/platforms/minimax/status", response_model=ProviderStatusResponse)
def get_minimax_platform_status():
    """Whether MINIMAX_API_KEY is set in the backend environment.

    Never returns the key itself: Cockpit resolves it server-side in
    spawn_session and it must never reach the browser.
    """
    return {"configured": bool(settings.minimax_api_key)}


class MinimaxCredentialsRequest(BaseModel):
    minimax_api_key: str


@router.post("/platforms/minimax/credentials", response_model=ProviderStatusResponse)
def set_minimax_credentials(request: MinimaxCredentialsRequest):
    """Set the MiniMax API key from the UI: writes it to the backend .env file
    and updates the running Settings immediately. Never returns the key."""
    try:
        minimax_credentials.set_minimax_api_key(request.minimax_api_key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"configured": True}


@router.delete("/platforms/minimax/credentials", response_model=ProviderStatusResponse)
def clear_minimax_credentials():
    minimax_credentials.clear_minimax_api_key()
    return {"configured": False}


# ---- Anthropic-compatible endpoint registry ------------------------------
#
# ``PROVIDER_COMPATIBLE`` (see ``app.services.agentic_cli.provider_env``) is
# the data-driven branch: a row of {name, base_url, model, credential_name}
# stored per project_key, no provider-side code changes per new upstream.
# The list endpoint feeds the NewSessionDialog's vendor-provider select
# (which used to hardcode ``'anthropic' | 'bedrock' | 'minimax'``). The
# actual credential lookup stays server-side; the response deliberately
# omits any secret and only echoes back the *name* of the credential so
# the UI can render a "configured / not configured" hint without ever
# receiving the key.


class EndpointResponse(BaseModel):
    name: str
    base_url: str
    model: str
    credential_name: str | None = None
    credential_configured: bool = False


class EndpointListResponse(BaseModel):
    endpoints: list[EndpointResponse]


class EndpointUpsertRequest(BaseModel):
    name: str
    base_url: str
    model: str
    credential_name: str | None = None


def _secret_store():
    """Factory for the project-scoped ``SecretStore`` used by
    ``_credential_configured`` to look up non-MiniMax endpoint credentials.

    Mirrors the same factory pattern in
    ``backend/app/services/agentic_cli/endpoints.py`` so tests can
    monkeypatch a fake implementation by reassigning the module-level
    attribute. Kept lazy so the router's import-time stays free of the
    secrets_store module + its scrypt/keyring transitive dependencies
    (the status endpoint is hit on every dropdown render of the
    NewSessionDialog vendor-provider select — keep the cold path
    cheap).
    """
    from app.services.secrets_store import AGESecretStore

    return AGESecretStore()


def _credential_configured(credential_name: str | None, project_key: str) -> bool:
    """Is the named credential resolvable for this server?

    Mirrors the spawn-path contract in
    ``backend/app/services/agentic_cli/endpoints.py`` (``resolve_compatible_endpoint``)
    so the status indicator stays honest:

    - ``credential_name is None`` → ``False`` (ambient-credential
      endpoints intentionally have no SecretStore row; the UI hides
      the warning for them).
    - ``credential_name == "minimax"`` → legacy ``settings.minimax_api_key``
      escape-hatch. Preserved so the existing MiniMax flow keeps working
      without forcing every operator to migrate their key into the
      SecretStore.
    - Anything else → ``_secret_store().get(project_key, credential_name)``.
      ``SecretNotFound`` (no file at all) and ``None`` (file exists,
      name absent) both surface as ``False`` — the UI keeps showing
      "Credential X is not configured" until the operator PUTs the key
      via ``POST /api/v1/secrets``.

    Any ``SecretStoreError`` other than ``SecretNotFound`` (e.g.
    ``AuthenticationError`` from a corrupt file) is swallowed and
    reported as ``False`` — the status endpoint must never 500 the
    NewSessionDialog on a secretary-side problem; the spawn path will
    surface the real error if the operator actually tries to launch.
    """
    if not credential_name:
        return False
    if credential_name == "minimax":
        return bool(settings.minimax_api_key)
    # Anything else: project SecretStore. Match the spawn-path lookup
    # so the UI's "configured" hint is consistent with what
    # ``resolve_compatible_endpoint`` will actually use at spawn time.
    from app.services.secrets_store import SecretNotFound, SecretStoreError

    try:
        stored = _secret_store().get(project_key, credential_name)
    except SecretNotFound:
        return False
    except SecretStoreError:
        # Defensive: a corrupt / undecryptable file must not 500 the
        # status endpoint. The spawn path will surface the real error
        # when the operator actually tries to launch with this row.
        return False
    return bool(stored)


@router.get("/platforms/endpoints", response_model=EndpointListResponse)
async def list_endpoints_endpoint(
    project_key: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Return every configured Anthropic-compatible endpoint for the project.

    ``credential_configured`` is computed server-side so the UI never has
    to ask the user to paste a key — it only needs a "not configured"
    hint, identical to the MiniMax ``configured`` boolean.

    ``project_key`` is optional: callers without a project context (e.g.
    the NewSessionDialog before the user picks a directory) pass nothing
    and read the shared default bucket. Project-scoped callers pass
    their resolved project_key and see only that project's endpoints.
    """
    from app.services.agentic_cli.endpoints import (
        DEFAULT_PROJECT_KEY,
    )
    from app.services.agentic_cli.endpoints import (
        list_endpoints as _list,
    )

    key = project_key or DEFAULT_PROJECT_KEY
    endpoints = await _list(db, key)
    return EndpointListResponse(
        endpoints=[
            EndpointResponse(
                name=e.name,
                base_url=e.base_url,
                model=e.model,
                credential_name=e.credential_name,
                credential_configured=_credential_configured(e.credential_name, key),
            )
            for e in endpoints
        ],
    )


@router.post("/platforms/endpoints", response_model=EndpointResponse)
async def upsert_endpoint_endpoint(
    request: EndpointUpsertRequest,
    project_key: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Insert or overwrite a single endpoint. Validation lives at the
    storage boundary (``endpoints.serialize_endpoint``) so a corrupt row
    is refused with a 400 instead of wedging the dispatcher on a bad
    KanbanMeta row."""
    from app.services.agentic_cli.endpoints import (
        DEFAULT_PROJECT_KEY,
        Endpoint,
    )
    from app.services.agentic_cli.endpoints import (
        upsert_endpoint as _upsert,
    )
    try:
        ep = Endpoint(
            name=request.name,
            base_url=request.base_url,
            model=request.model,
            credential_name=request.credential_name,
        )
        key = project_key or DEFAULT_PROJECT_KEY
        await _upsert(db, key, ep)
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return EndpointResponse(
        name=ep.name,
        base_url=ep.base_url,
        model=ep.model,
        credential_name=ep.credential_name,
        credential_configured=_credential_configured(ep.credential_name, key),
    )


@router.delete("/platforms/endpoints/{name}", response_model=dict)
async def delete_endpoint_endpoint(
    name: str,
    project_key: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    from app.services.agentic_cli.endpoints import (
        DEFAULT_PROJECT_KEY,
    )
    from app.services.agentic_cli.endpoints import (
        delete_endpoint as _delete,
    )
    try:
        await _delete(db, project_key or DEFAULT_PROJECT_KEY, name)
        await db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"deleted": True}


class CatalogLitellmUpstreamResponse(BaseModel):
    model: str
    api_base: str
    api_key_env: str


class CatalogEntryResponse(BaseModel):
    name: str
    display_name: str
    provider: str
    base_url: str
    model: str
    credential_name: str | None
    free_tier_kind: str
    free_evidence_url: str
    free_measured_on: str
    free_notes: str
    litellm_upstream: CatalogLitellmUpstreamResponse | None


class CatalogListResponse(BaseModel):
    entries: list[CatalogEntryResponse]


@router.get("/platforms/endpoints-catalog", response_model=CatalogListResponse)
async def list_free_endpoint_catalog():
    """Return the curated free-tier seed catalog (kaart 8222fee8…).

    The catalog is a repo file (``backend/data/free_endpoint_catalog.toml``)
    that pairs each Anthropic-format endpoint with a free-tier annotation
    (``free_evidence_url`` + ``free_measured_on``) so an operator can decide
    which entries are worth installing into their project's
    endpoint-registry. No secrets: ``credential_name`` is a SecretStore
    key name, not the key value.
    """
    from app.services.agentic_cli.free_endpoint_catalog import load_catalog

    entries = load_catalog()
    return CatalogListResponse(
        entries=[
            CatalogEntryResponse(
                name=e.name,
                display_name=e.display_name,
                provider=e.provider,
                base_url=e.base_url,
                model=e.model,
                credential_name=e.credential_name,
                free_tier_kind=e.free_tier_kind,
                free_evidence_url=e.free_evidence_url,
                free_measured_on=e.free_measured_on,
                free_notes=e.free_notes,
                litellm_upstream=(
                    CatalogLitellmUpstreamResponse(
                        model=e.litellm_upstream.model,
                        api_base=e.litellm_upstream.api_base,
                        api_key_env=e.litellm_upstream.api_key_env,
                    )
                    if e.litellm_upstream is not None
                    else None
                ),
            )
            for e in entries
        ],
    )


class SeedCatalogResponse(BaseModel):
    installed: int
    skipped: int
    skipped_names: list[str]


@router.post("/platforms/endpoints-catalog/seed", response_model=SeedCatalogResponse)
async def seed_free_endpoint_catalog(
    project_key: str | None = Query(default=None),
    overwrite: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
):
    """Bulk-install the seed catalog into the project's endpoint registry.

    By default, entries that already exist under the same ``name`` are left
    alone — the operator's runtime-config wins over the catalog. Pass
    ``?overwrite=true`` to replace existing rows with the catalog values
    (intended for re-seeding after a catalog bump; it will NOT touch any
    SecretStore entries, so credentials survive intact).
    """
    from app.services.agentic_cli.endpoints import DEFAULT_PROJECT_KEY
    from app.services.agentic_cli.free_endpoint_catalog import seed_catalog

    try:
        installed, skipped, skipped_names = await seed_catalog(
            db,
            project_key or DEFAULT_PROJECT_KEY,
            overwrite=overwrite,
        )
        await db.commit()
    except ValueError as exc:
        # tomllib.TOMLDecodeError subclasses ValueError on every supported
        # Python build, so a corrupt catalog lands here too.
        raise HTTPException(status_code=400, detail=str(exc))
    return SeedCatalogResponse(
        installed=installed, skipped=skipped, skipped_names=skipped_names,
    )


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
        return await run_attachment_service.create_attachment(
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
    attachments = await run_attachment_service.list_attachments(db, target=target)
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
        return await run_attachment_service.paste_attachment(
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
        return await run_attachment_service.delete_attachment(
            db,
            target=target,
            attachment_id=attachment_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/sessions")
async def spawn_session_endpoint(
    request: SpawnRequest,
    project_key: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    try:
        get_agentic_cli(request.cli)

        host_data = None
        if request.host_id is not None:
            from app.services.host_service import get_host as get_host_data
            host_data = await get_host_data(db, request.host_id)

        # Resolve the named Anthropic-compatible endpoint (if any) into
        # the explicit fields the provider-env builder expects. We do
        # this here (and not inside ``spawn_session``) so the spawn
        # service stays DB-free and the DB session lifetime stays in
        # the request handler. The same helper serves the auto-
        # dispatch path so both surfaces share their error messages
        # and validation — kaart 293d1faa…
        #
        # ``project_key`` honours the same query parameter as
        # ``GET/POST/DELETE /platforms/endpoints`` so an endpoint
        # registered under a project resolves in the same bucket
        # (kaart 333af652… blocker: the spawn path used to always
        # read the shared ``_default`` bucket, so project-scoped
        # rows 404'd at spawn time even though list/upsert could
        # see them).
        endpoint_base_url: str | None = None
        endpoint_auth_token: str | None = None
        endpoint_name = request.endpoint_name
        if request.provider == "anthropic-compatible":
            if not endpoint_name:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "anthropic-compatible provider requires endpoint_name; "
                        "configure one via /api/v1/agent-bridge/platforms/endpoints"
                    ),
                )
            from app.services.agentic_cli.endpoints import (
                DEFAULT_PROJECT_KEY,
            )
            from app.services.agentic_cli.endpoints import (
                resolve_compatible_endpoint as _resolve_compatible,
            )
            try:
                resolved = await _resolve_compatible(
                    db, project_key or DEFAULT_PROJECT_KEY, endpoint_name,
                    requested_model=request.model,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if resolved is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"unknown endpoint {endpoint_name!r}",
                )
            endpoint_base_url = resolved["base_url"]
            endpoint_auth_token = resolved["auth_token"]
            # The endpoint's model is the per-endpoint default; the
            # request-level ``model`` is the optional per-session override.
            # Fall through to the endpoint default when the caller didn't
            # pin one — keeps the provider-env builder happy (it requires
            # a non-empty model) without forcing every client to repeat
            # the endpoint's model on every spawn.
            if not request.model:
                request.model = resolved["model"]

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
            provider=request.provider,
            aws_region=request.aws_region,
            aws_profile=request.aws_profile,
            bedrock_model=request.bedrock_model,
            minimax_base_url=request.minimax_base_url,
            endpoint_name=endpoint_name,
            endpoint_base_url=endpoint_base_url,
            endpoint_auth_token=endpoint_auth_token,
            host_id=request.host_id,
            agent=request.agent,
            context_tier=request.context_tier,
            reasoning_effort=request.reasoning_effort,
            plan=request.plan,
            remote=request.remote,
            allow_all=request.allow_all,
            no_ask_user=request.no_ask_user,
        )
        return spawn_session(request.cli, options, session_name=request.session_name, host_data=host_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except HostNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class BulkResumeItem(BaseModel):
    session_id: str
    project_folder: str


class BulkResumeRequest(BaseModel):
    cli: str = "claude-code"
    directory: str = ""
    sessions: list[BulkResumeItem]
    skip_permissions: bool = False
    provider: str = "anthropic"
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
        get_agentic_cli(request.cli)
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
            provider=request.provider,
            aws_region=request.aws_region,
            aws_profile=request.aws_profile,
            bedrock_model=request.bedrock_model,
            minimax_base_url=request.minimax_base_url,
        )
        try:
            spawned = spawn_session(request.cli, options)
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


# ── Run Group endpoints ──────────────────────────────────────────────
# The wire-format is preserved (URL `/teams`, JSON keys `team_id`/`members`)
# so the frontend card can switch in lockstep; the Python class names follow
# the canonical Run terminology.


class GroupMemberInfo(BaseModel):
    run_name: str
    pane_id: str | None = None
    tmux_target: str = ""


class RunGroupResponse(BaseModel):
    team_id: str
    name: str
    cli: str
    cwd: str
    is_auto_detected: bool
    lead: dict[str, Any] | None = None
    members: list[dict[str, Any]]


class RunGroupsResponse(BaseModel):
    teams: list[RunGroupResponse]
    ungrouped: list[dict[str, Any]]
    total_teams: int
    total_sessions: int


def _group_dict_to_team_response(group: dict[str, Any]) -> RunGroupResponse:
    """Adapt the service-layer group dict (key: ``runs``) to the legacy wire
    shape (key: ``members``)."""
    return RunGroupResponse(
        team_id=group["group_id"],
        name=group["name"],
        cli=group["cli"],
        cwd=group["cwd"],
        is_auto_detected=group["is_auto_detected"],
        lead=group.get("lead"),
        members=group.get("runs", group.get("members", [])),
    )


@router.get("/teams")
async def list_teams(db: AsyncSession = Depends(get_db)):
    """List all run groups (auto-detected + manual) with their runs."""
    runs = discover_agent_sessions()
    manual_groups = await groups_service.get_manual_groups(db)
    groups = groups_service.discover_groups(runs, manual_groups)
    ungrouped = groups_service.get_ungrouped_runs(runs, groups)
    return RunGroupsResponse(
        teams=[_group_dict_to_team_response(g) for g in groups],
        ungrouped=ungrouped,
        total_teams=len(groups),
        total_sessions=len(runs),
    )


class CreateGroupRequest(BaseModel):
    name: str
    cli: str = ""
    cwd: str = ""
    lead_run_name: str | None = None
    members: list[GroupMemberInfo] = []


@router.post("/teams")
async def create_group(request: CreateGroupRequest, db: AsyncSession = Depends(get_db)):
    """Create a new manual run group."""
    try:
        group = await groups_service.create_manual_group(
            db,
            name=request.name,
            cli=request.cli,
            cwd=request.cwd,
            lead_run_name=request.lead_run_name,
            member_runs=[m.model_dump() for m in request.members],
        )
        return group
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/teams/{team_id}")
async def delete_team(team_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a manual run group."""
    success = await groups_service.delete_manual_group(db, team_id)
    if not success:
        raise HTTPException(status_code=404, detail="Team not found")
    return {"deleted": True}


class AddMemberRequest(BaseModel):
    run_name: str
    pane_id: str | None = None
    tmux_target: str = ""


@router.post("/teams/{team_id}/members")
async def add_team_member(
    team_id: int, request: AddMemberRequest, db: AsyncSession = Depends(get_db)
):
    """Add a run to a manual group."""
    success = await groups_service.add_group_membership(
        db, team_id, request.run_name, request.pane_id, request.tmux_target
    )
    if not success:
        raise HTTPException(status_code=404, detail="Team not found")
    return {"added": True}


@router.delete("/teams/{team_id}/members/{member_id}")
async def remove_team_member(
    team_id: int, member_id: int, db: AsyncSession = Depends(get_db)
):
    """Remove a run from a manual group."""
    success = await groups_service.remove_group_membership(db, member_id)
    if not success:
        raise HTTPException(status_code=404, detail="Member not found")
    return {"removed": True}
