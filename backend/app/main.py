"""FastAPI application entry point."""
import os
import secrets
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.config import settings
from app.database import init_db
from app.api.v1.router import router as api_v1_router
from fastapi.staticfiles import StaticFiles
import app.models.scheduled_message  # noqa: F401  (register tables for create_all)

import app.models.mcp_token  # noqa: F401  (register tables for create_all)
import app.models.sandcastle  # noqa: F401  (register tables for create_all)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup: Initialize database
    await init_db()
    from app.kanban.db import init_kanban_db
    await init_kanban_db()
    from app.services.scheduling.schema_guard import (
        ensure_scheduled_message_columns,
        ensure_backup_columns,
    )
    from app.database import engine
    await ensure_scheduled_message_columns(engine)
    await ensure_backup_columns(engine)
    # Clean up any orphaned relay processes from previous runs
    from app.services.cc_bridge.pty_relay import close_all_relays, cleanup_orphaned_relays
    cleanup_orphaned_relays()
    # Start the scheduler and reschedule persisted, enabled jobs
    from app.services.scheduling.scheduler import scheduler_service
    from app.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models.scheduled_message import ScheduledMessage
    scheduler_service.start()
    # Start kanban auto-dispatch polling (every 10 seconds)
    scheduler_service.schedule_kanban_dispatch(interval_seconds=10)
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

# Include API routers
app.include_router(api_v1_router, prefix=settings.api_v1_prefix)

# Mount the kanban MCP server (SSE) at /kanban-mcp. Agents point their
# .mcp.json at http://localhost:8000/kanban-mcp/sse.
from app.kanban.mcp_server import mcp as kanban_mcp  # noqa: E402
# Pass mount_path so the SSE transport advertises its message endpoint as
# /kanban-mcp/messages/ (matching the mount). Without it FastMCP advertises a
# bare /messages/, which Starlette routes to the StaticFiles frontend, so every
# agent tool call silently misses the MCP server.
app.mount("/kanban-mcp", kanban_mcp.sse_app(mount_path="/kanban-mcp"))


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "status": "running"
    }

# Serve static files from the frontend build directory
frontend_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "frontend", "dist")

if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")

    @app.exception_handler(404)
    async def not_found_exception_handler(request, exc):
        """Standard 404 handler to serve index.html for SPA routing."""
        if not request.url.path.startswith(settings.api_v1_prefix):
            return FileResponse(os.path.join(frontend_path, "index.html"), status_code=200)
        return {"detail": "Not Found"}
else:
    @app.get("/")
    async def root():
        """Root endpoint fallback when frontend is not built."""
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "message": "Frontend not found. Please build the frontend."
        }
