"""FastAPI application entry point."""
import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.logging_config import configure_logging

configure_logging()

from fastapi.staticfiles import StaticFiles

import app.models.agent_mail  # noqa: F401  (register tables for create_all)
import app.models.auto_resume  # noqa: F401  (register tables for create_all)
import app.models.host  # noqa: F401  (register tables for create_all)
import app.models.mcp_token  # noqa: F401  (register tables for create_all)
import app.models.recurring_trigger  # noqa: F401  (register tables for create_all)
import app.models.run_instance  # noqa: F401  (register tables for create_all)
import app.models.sandcastle  # noqa: F401  (register tables for create_all)
import app.models.security_audit  # noqa: F401  (register tables for create_all)
import app.models.security_profile  # noqa: F401  (register tables for create_all)
from app.api.v1.router import router as api_v1_router
from app.config import settings
from app.database import init_db
from app.middleware.correlation_id import CorrelationIdMiddleware

logger = logging.getLogger(__name__)


async def ensure_scheduling_hooks_installed() -> None:
    """Additively install the scheduling hooks into ~/.claude/settings.json.

    Non-fatal: a shared settings file some other process is mid-write to
    shouldn't block backend startup, it just means the hooks stay uninstalled
    until the next restart.
    """
    from app.services.scheduling.hook_installer import install_missing_hooks
    try:
        await asyncio.to_thread(install_missing_hooks)
    except Exception:
        logger.exception("failed to install scheduling hooks at startup")


async def _recover_and_start_dispatch() -> None:
    """Resume interrupted sessions, adopt live runs, then arm the dispatch tick.

    Runs as a background task rather than inline in ``lifespan`` — see the
    ``_startup_task`` handoff there for why. The *relative* order of the four
    steps below is load-bearing and must not be rearranged:

      1. ``recover_interrupted_sessions`` — must precede step 4 so the reaper's
         first tick can't release (and orphan) the claims it is resuming, and
         must precede step 3 because ``reset_autodispatch_for_boot`` forces
         every project's autodispatch flag off; recovery reads that same
         enabled-set (``list_autodispatch_projects``) and would become a silent
         no-op if the reset ran first.
      2. headless + ACP adoption — same reaper-ordering constraint as step 1,
         applied to the second and third liveness sources.
      3. ``reset_autodispatch_for_boot`` — before the tick is scheduled.
      4. ``schedule_kanban_dispatch`` — the reaper/dispatcher goes live here.

    Every step is individually best-effort: one failure must not stop the
    later steps, or a transient recovery error would leave the dispatch tick
    permanently unarmed.
    """
    # 1. Resume agent sessions interrupted by a host/backend restart.
    from app.kanban.session_recovery import recover_interrupted_sessions
    from app.services.scheduling.scheduler import scheduler_service
    try:
        await recover_interrupted_sessions()
    except Exception:
        logger.exception("session recovery failed at startup")
    # 2. Adopt still-running headless transport runs from their durable pidfiles
    # (kaart a450df1a…). MUST run before the dispatch scheduler/reaper so the
    # reaper's first tick sees adopted runs as alive — otherwise every live
    # headless run would look dead, the reaper would release the claims, and
    # the dispatcher would re-spawn into the same worktree (the same ordering
    # session_recovery above uses, applied to the third liveness source).
    from app.kanban.dispatch import _registered_project_paths
    from app.kanban.headless_runner import (
        adopt_headless_runs,
        start_headless_tailer,
    )
    paths: list[str] = []
    try:
        paths = await _registered_project_paths()
        # BUG FIX (kaart a450df1a…, sigh): ``_registered_project_paths``
        # returns ``list[str]`` (it does ``return list(rows)`` over a
        # scalar column), so ``paths.values()`` raised ``AttributeError`` and
        # the bare ``except Exception`` swallowed it — every backend start
        # adopted zero runs, the reaper's first tick then released every
        # live headless claim, and the dispatcher re-spawned into the same
        # worktree (the three-bullet failure mode the impediment review
        # called out). The function already iterates ``project_paths``
        # directly, so we just pass the list through.
        adopted_records = adopt_headless_runs(paths)
        if adopted_records:
            logger.info(
                "adopted %d live headless run(s) after restart",
                len(adopted_records),
            )
            # Spawn a tailer task per adopted record. The tailer reads
            # the on-disk JSONL log from the persisted last_read_offset
            # so events that arrived between the previous parent's death
            # and this restart land in the dispatch state machine
            # (rate_limit → set_paused_until especially — see the
            # parent-card analysis for why losing those pauses was
            # a real failure mode). The task holds a strong reference
            # via ``_headless_start_tasks`` so it can't be GC'd.
            for rec in adopted_records:
                start_headless_tailer(rec)
    except Exception:
        logger.exception("headless adoption failed at startup")
    # ACP subprocess adoption (kaart f647a44e…): mirrors the headless
    # adoption path — durable ``.cockpit-acp.json`` pidfiles are
    # OS-verified and re-attached to the in-memory registry so the
    # reaper's first tick doesn't release-and-redispatch ACP claims.
    # The transport's reader-loop is launched lazily on the next ACP
    # dispatch (a fresh run re-establishes its own consumer task), so
    # we only need to (a) populate the cache and (b) re-reserve the
    # session_registry slot — no tailer is spawned for adopted runs.
    try:
        from app.kanban.acp_transport import adopt_acp_runs
        adopted_acp = adopt_acp_runs(paths)
        if adopted_acp:
            logger.info(
                "adopted %d live acp run(s) after restart",
                len(adopted_acp),
            )
    except Exception:
        logger.exception("acp adoption failed at startup")
    # 3. Force every project's autodispatch flag off before the tick is
    # scheduled below. The flag is persisted (KanbanMeta, device-local) and
    # survives restarts, so without this a project left toggled on would start
    # having cards auto-claimed/spawned on the very next tick after any backend
    # restart -- auto-dispatch must always start from an explicit opt-in.
    # Exception: a `uvicorn --reload` hot reload is the same running server, not
    # a restart, and force-disabling there made the factory disable itself every
    # time an agent merged a backend change (see reset_autodispatch_for_boot).
    try:
        from app.kanban import dispatch as kanban_dispatch
        from app.kanban.db import KanbanSessionLocal
        async with KanbanSessionLocal() as ks:
            await kanban_dispatch.reset_autodispatch_for_boot(ks)
            await ks.commit()
    except Exception:
        logger.exception("autodispatch boot reset failed at startup")
    # 3b. Rebuild one-shot scheduled work that died with the previous process.
    # The scheduler's jobstore is in-memory, so a pane resume scheduled at a
    # rate-limit reset time never fires after a restart and leaves the card
    # claimed with nobody left to nudge it. Best-effort: a failure here must
    # not block startup.
    try:
        from app.services.scheduling.reconciler import (
            hydrate_auto_resume,
            reinstall_pending_pane_resumes,
        )
        await hydrate_auto_resume()
        await reinstall_pending_pane_resumes()
    except Exception:
        logger.exception("pane-resume reconciliation failed at startup")
    # 4. Start kanban auto-dispatch polling.
    scheduler_service.schedule_kanban_dispatch(
        interval_seconds=settings.kanban_dispatch_interval_seconds
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Refuse to serve on a database that is behind the migrations. Skips a
    # store that is not under alembic at all -- that is the create_all shape
    # the test suite builds on purpose. See app/db_bootstrap.py.
    from app.config import settings as _settings
    from app.db_bootstrap import lifespan_schema_check, sqlite_path
    for _name, _url in (
        ("registry", _settings.database_url),
        ("kanban", _settings.kanban_database_url),
    ):
        _path = sqlite_path(_url)
        if _path is not None:
            lifespan_schema_check(_name, _path)

    # Startup: Initialize database
    await init_db()
    from app.database import AsyncSessionLocal
    from app.services.runs.attachments import run_attachment_service
    try:
        async with AsyncSessionLocal() as db:
            await run_attachment_service.cleanup_expired(db)
    except Exception:
        logger.exception("Failed to clean up expired Agent Bridge attachments")
    from app.kanban.db import init_kanban_db
    await init_kanban_db()
    # Seed the subscription-usage provider registry with honest no-signal
    # stubs (UnknownUsageProvider) for every supported (cli, provider) pair
    # — keeps the pool router's snapshot path alive even when no real
    # provider is wired (analyse §6.3 "no fabrication"). The actual real
    # providers (AnthropicUsageProvider / MinimaxUsageProvider) replace
    # these stubs by id when their configuration becomes available; the
    # default seed doesn't lock anything in. See kanban card ea7e038b…
    # (D2 — "registry is never populated"). The seed is best-effort: a
    # failed registration must not block backend startup.
    from app.services.subscriptions import registry as _subscription_registry
    try:
        _subscription_registry.register_default_providers()
    except Exception:
        logger.exception("failed to seed default subscription providers")
    # Upgrade the anthropic stub to a real AnthropicUsageProvider when a
    # plan-tier has already been configured (kaart d404a11f...) — otherwise
    # the pool router's drempel branch stays dead until the user next
    # touches the plan-tier UI, which is what re-syncs it live.
    from app.services.subscription_prefs_service import (
        sync_anthropic_provider_registration,
    )
    try:
        async with AsyncSessionLocal() as _db:
            await sync_anthropic_provider_registration(_db)
    except Exception:
        logger.exception("failed to sync Anthropic provider registration from prefs")
    # Clean up any orphaned relay processes from previous runs
    from app.services.runs.pty_relay import cleanup_orphaned_relays, close_all_relays
    cleanup_orphaned_relays()
    # Start the scheduler and reschedule persisted, enabled jobs
    from app.services.scheduling.scheduler import scheduler_service
    scheduler_service.start()
    # Session recovery + run adoption + the dispatch tick run in a BACKGROUND
    # task, never inline here. Recovery re-dispatches one real agent session per
    # interrupted card, and a single resume spawn was measured at ~37s — so with
    # two or more stale claims the lifespan never reached `yield`, uvicorn never
    # began accepting on :8000, and `cockpit.sh`'s health watchdog (30s grace +
    # 3x10s = 50s budget) SIGKILLed the process at 51s. The next boot found the
    # same claims and died identically: 12 consecutive restarts, a permanent 502
    # from the frontend proxy, and a fresh set of agent spawns burned each cycle.
    # Readiness must never be gated on work whose duration scales with board
    # state. `_recover_and_start_dispatch` documents the internal ordering it
    # still guarantees (recovery and adoption both precede the reaper's first
    # tick, exactly as when this ran inline).
    _startup_task = asyncio.create_task(_recover_and_start_dispatch())
    # Strong reference: a bare create_task is only weakly held by the event loop
    # and may be garbage-collected mid-flight.
    app.state.startup_task = _startup_task

    def _log_startup_task_result(task: asyncio.Task) -> None:
        # Without this the task's exception is only surfaced at GC time as an
        # unretrieved-exception warning — and a crash here means the dispatch
        # tick was never armed, which is exactly the failure a reader needs
        # to see in the log.
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("startup recovery task failed", exc_info=exc)

    _startup_task.add_done_callback(_log_startup_task_result)
    # Install the Notification/Stop/UserPromptSubmit/SessionStart hooks that feed
    # the usage-limit auto-resume pipeline. These used to require a manual click
    # on the Scheduled Messages page, which meant the whole pipeline stayed dead
    # code on any machine where nobody happened to visit that page first.
    await ensure_scheduling_hooks_installed()
    # NOTE: the autodispatch boot-reset and `schedule_kanban_dispatch` used to
    # sit here. Both moved into `_recover_and_start_dispatch` above so they keep
    # running *after* session recovery — the reset force-disables every
    # project's autodispatch flag, which is the same flag recovery reads, so
    # running it first would silently turn recovery into a no-op.
    # Signal (never block) product-projects whose Backlog has stalled — posts a
    # [portfolio-stale] comment, no Impediment move. See kanban/stale_detection.py.
    scheduler_service.schedule_stale_detection(
        interval_minutes=settings.stale_check_interval_minutes
    )
    # Register the daily automatic-backup job when enabled.
    from app.services.auto_backup_service import get_or_create_settings
    async with AsyncSessionLocal() as s:
        auto = await get_or_create_settings(s)
        if auto.enabled:
            scheduler_service.schedule_auto_backup(auto.time_of_day, auto.timezone)
    # Register cron jobs for every enabled recurring trigger, and cover any
    # occurrence that passed while the backend was down. The two halves are
    # independent — APScheduler only sees future ticks, the inhaal sweep
    # handles the past. See docs/cockpit/scheduled-trigger-consolidatie-decision.md §3.2.
    from sqlalchemy import select

    from app.models.recurring_trigger import RecurringTrigger
    from app.services.recurring_triggers import run_boot_inhaal
    async with AsyncSessionLocal() as s:
        triggers = (await s.execute(
            select(RecurringTrigger).where(RecurringTrigger.enabled == True)  # noqa: E712
        )).scalars().all()
        for t in triggers:
            scheduler_service.schedule_recurring_trigger(
                t.id, t.cron_expr, t.timezone,
            )
    try:
        await run_boot_inhaal()
    except Exception:
        logger.exception("recurring-trigger boot inhaal failed")
    yield
    # Shutdown: Cleanup. Cancel the recovery/adoption task first — on a fast
    # restart (or a `uvicorn --reload` hot reload) it can still be mid-spawn,
    # and leaving it running would race the scheduler teardown below.
    if not _startup_task.done():
        _startup_task.cancel()
        try:
            await _startup_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("startup recovery task failed during shutdown")
    scheduler_service.shutdown()
    await close_all_relays()


# Create FastAPI application
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

_UNPROTECTED_PATHS = {
    "/health",
    f"{settings.api_v1_prefix}/health",
    f"{settings.api_v1_prefix}/mcp-server",
}


class RequireApiTokenMiddleware:
    """Require a bearer token when remote-access protection is configured.

    Plain ASGI middleware rather than `BaseHTTPMiddleware` — see the
    docstring in app/middleware/correlation_id.py for why: stacking two
    `BaseHTTPMiddleware`s under concurrent load can corrupt one of the
    responses (AssertionError: Unexpected message: http.response.start).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        is_protected_api = (
            request.url.path.startswith(settings.api_v1_prefix)
            or request.url.path.startswith("/kanban-mcp")
        )
        if settings.api_token and is_protected_api and request.url.path not in _UNPROTECTED_PATHS:
            authorization = request.headers.get("authorization", "")
            token = authorization.removeprefix("Bearer ").strip()
            if not secrets.compare_digest(token, settings.api_token):
                response = JSONResponse(status_code=401, content={"detail": "Invalid API token"})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# Registration order matters: Starlette's add_middleware() does
# user_middleware.insert(0, ...), so the *last* one registered ends up
# outermost (runs first per request). Register require-api-token first so
# it stays innermost, then CORS, then correlation-id last/outermost — this
# reproduces the exact same effective request order as the original
# `@app.middleware("http")` + add_middleware(...) calls it replaces.
app.add_middleware(RequireApiTokenMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_credentials,
    allow_methods=settings.cors_methods,
    allow_headers=settings.cors_headers,
)
app.add_middleware(CorrelationIdMiddleware)

# Include API routers
app.include_router(api_v1_router, prefix=settings.api_v1_prefix)

# Mount the kanban MCP server (SSE) at /kanban-mcp. Agents point their
# .mcp.json at <base_url>/kanban-mcp/sse, where base_url is derived per-request
# (or PUBLIC_BASE_URL when set) — see app/api/v1/kanban/router.py::enable.
from app.kanban.mcp_server import mcp as kanban_mcp  # noqa: E402
from app.kanban.mcp_transport import build_session_aware_sse_app  # noqa: E402

# Do NOT pass mount_path here. The SSE transport already prepends the ASGI
# scope's root_path (the "/kanban-mcp" supplied by app.mount) to the advertised
# message endpoint. Passing mount_path="/kanban-mcp" bakes the prefix into the
# endpoint a second time, so the transport advertises /kanban-mcp/kanban-mcp/
# messages/ -- a 404 that strands every agent with zero kanban tools and no error.
# (Older mcp releases lacked the root_path prefixing, which is why mount_path was
# once needed; the dependency upgrade silently made it a doubling bug.)
# Regression-guarded by tests/test_kanban_mcp_mount.py.
#
# Use the session-aware wrapper (not the bare ``kanban_mcp.sse_app()``) so an
# MCP tool call with an unknown session_id — exactly what happens when an
# in-flight agent's session outlives a ``uvicorn --reload`` — returns a
# structured 410 Gone with ``error: session_not_found`` instead of the default
# 404 plain text. The plain-text 404 surfaces to the agent as ``MCP error
# -32602: Invalid request parameters`` (misleading — the params are fine, the
# *session* is gone); the 410 body tells the agent to reconnect the SSE stream.
# See kanban kaart ``ae19ced1d18646609739cfbb8ff694dd`` and
# ``docs/cockpit/kanban-mcp-session-410-decision.md``.
app.mount("/kanban-mcp", build_session_aware_sse_app(kanban_mcp))


@app.get("/health")
async def health():
    """Health check endpoint.

    Returns a minimal status only — app name/version are deliberately omitted
    so the unauthenticated endpoint does not help attackers fingerprint the
    deployment. Verbose info lives behind the auth-protected /api/v1/status.
    """
    return {"status": "ok"}

# Serve static files from the frontend build directory
frontend_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend", "dist")

if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

    @app.exception_handler(404)
    async def not_found_exception_handler(request, exc):
        """Standard 404 handler to serve index.html for SPA routing."""
        if not request.url.path.startswith(settings.api_v1_prefix):
            return FileResponse(os.path.join(frontend_path, "index.html"), status_code=200)
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
else:
    @app.get("/")
    async def root():
        """Root endpoint fallback when frontend is not built."""
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "message": "Frontend not found. Please build the frontend."
        }
