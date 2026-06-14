"""FastAPI application entry point."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.api.v1.router import router as api_v1_router
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os
import app.models.scheduled_message  # noqa: F401  (register tables for create_all)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup: Initialize database
    await init_db()
    from app.kanban.db import init_kanban_db
    await init_kanban_db()
    # Clean up any orphaned relay processes from previous runs
    from app.services.cc_bridge.pty_relay import close_all_relays, cleanup_orphaned_relays
    cleanup_orphaned_relays()
    # Start the scheduler and reschedule persisted, enabled jobs
    from app.services.scheduling.scheduler import scheduler_service
    from app.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models.scheduled_message import ScheduledMessage
    scheduler_service.start()
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(ScheduledMessage).where(ScheduledMessage.enabled == True)  # noqa: E712
        )).scalars().all()
        for m in rows:
            if m.trigger_type == "once" and m.fire_at:
                scheduler_service.schedule_once(m.id, m.fire_at)
            elif m.trigger_type == "cron" and m.cron_expr:
                scheduler_service.schedule_cron(m.id, m.cron_expr, m.timezone)
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

# Configure CORS
# Accept any origin reaching the dev (5173) or prod (8000) ports — this lets
# the UI load over localhost, LAN, or tailnet without requiring env config.
# allow_credentials must be False when using a wildcard regex (browsers reject
# "*"-style wildcards with credentials); our API does not rely on cookies.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://[^/]+(:\d+)?$",
    allow_credentials=False,
    allow_methods=settings.cors_methods,
    allow_headers=settings.cors_headers,
)

# Include API routers
app.include_router(api_v1_router, prefix=settings.api_v1_prefix)

# Mount the kanban MCP server (SSE) at /kanban-mcp. Agents point their
# .mcp.json at http://localhost:8000/kanban-mcp/sse.
from app.kanban.mcp_server import mcp as kanban_mcp  # noqa: E402
app.mount("/kanban-mcp", kanban_mcp.sse_app())


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
