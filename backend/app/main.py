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

import app.models.host  # noqa: F401  (register tables for create_all)
import app.models.mcp_token  # noqa: F401  (register tables for create_all)
import app.models.sandcastle  # noqa: F401  (register tables for create_all)
import app.models.scheduled_message  # noqa: F401  (register tables for create_all)
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup: Initialize database
    await init_db()
    from app.kanban.db import init_kanban_db
    await init_kanban_db()
    from app.database import engine
    from app.services.scheduling.schema_guard import (
        ensure_backup_columns,
        ensure_model_columns,
        ensure_scheduled_message_columns,
    )
    await ensure_scheduled_message_columns(engine)
    await ensure_backup_columns(engine)
    await ensure_model_columns(engine)
    # Clean up any orphaned relay processes from previous runs
    from app.services.cc_bridge.pty_relay import cleanup_orphaned_relays, close_all_relays
    cleanup_orphaned_relays()
    # Start the scheduler and reschedule persisted, enabled jobs
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.scheduled_message import ScheduledMessage
    from app.services.scheduling.scheduler import scheduler_service
    scheduler_service.start()
    # Resume agent sessions interrupted by a host/backend restart. Runs before the
    # dispatch scheduler so the reaper can't release (and orphan) their claims first.
    from app.kanban.session_recovery import recover_interrupted_sessions
    try:
        await recover_interrupted_sessions()
    except Exception:
        logger.exception("session recovery failed at startup")
    # Install the Notification/Stop/UserPromptSubmit/SessionStart hooks that feed
    # the usage-limit auto-resume pipeline. These used to require a manual click
    # on the Scheduled Messages page, which meant the whole pipeline stayed dead
    # code on any machine where nobody happened to visit that page first.
    await ensure_scheduling_hooks_installed()
    # Start kanban auto-dispatch polling
    scheduler_service.schedule_kanban_dispatch(
        interval_seconds=settings.kanban_dispatch_interval_seconds
    )
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(ScheduledMessage).where(ScheduledMessage.enabled == True)  # noqa: E712
        )).scalars().all()
        for m in rows:
            if m.trigger_type == "once" and m.fire_at:
                scheduler_service.schedule_once(m.id, m.fire_at)
            elif m.trigger_type == "cron" and m.cron_expr:
                scheduler_service.schedule_cron(m.id, m.cron_expr, m.timezone)
    # Register the daily automatic-backup job when enabled.
    from app.services.auto_backup_service import get_or_create_settings
    async with AsyncSessionLocal() as s:
        auto = await get_or_create_settings(s)
        if auto.enabled:
            scheduler_service.schedule_auto_backup(auto.time_of_day, auto.timezone)
    yield
    # Shutdown: Cleanup
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


@app.middleware("http")
async def require_api_token(request: Request, call_next):
    """Require a bearer token when remote-access protection is configured."""
    is_protected_api = (
        request.url.path.startswith(settings.api_v1_prefix)
        or request.url.path.startswith("/kanban-mcp")
    )
    if settings.api_token and is_protected_api and request.url.path not in _UNPROTECTED_PATHS:
        authorization = request.headers.get("authorization", "")
        token = authorization.removeprefix("Bearer ").strip()
        if not secrets.compare_digest(token, settings.api_token):
            return JSONResponse(status_code=401, content={"detail": "Invalid API token"})
    return await call_next(request)


# Configure CORS
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

# Do NOT pass mount_path here. The SSE transport already prepends the ASGI
# scope's root_path (the "/kanban-mcp" supplied by app.mount) to the advertised
# message endpoint. Passing mount_path="/kanban-mcp" bakes the prefix into the
# endpoint a second time, so the transport advertises /kanban-mcp/kanban-mcp/
# messages/ -- a 404 that strands every agent with zero kanban tools and no error.
# (Older mcp releases lacked the root_path prefixing, which is why mount_path was
# once needed; the dependency upgrade silently made it a doubling bug.)
# Regression-guarded by tests/test_kanban_mcp_mount.py.
app.mount("/kanban-mcp", kanban_mcp.sse_app())


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
