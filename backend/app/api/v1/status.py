"""System status endpoint for header indicators."""
import asyncio
import time
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.models.schemas import SystemStatusResponse
from app.services.agentic_cli import get_agentic_cli, get_agentic_clis
from app.services.instance_identity import get_instance_identity
from app.services.memory_monitor import get_dynamic_limits, get_memory_status_cached
from app.services.runs.discovery import discover_agent_sessions
from app.services.scheduling.hook_installer import get_hooks_status

router = APIRouter()

# Cache CC version for 5 minutes
_version_cache: tuple[str | None, float] = (None, 0.0)
_version_lock = asyncio.Lock()
_CACHE_TTL = 300  # seconds


async def _get_claude_code_version() -> str | None:
    global _version_cache
    cached_version, cached_at = _version_cache
    if time.time() - cached_at < _CACHE_TTL:
        return cached_version

    async with _version_lock:
        # Re-check after acquiring lock (another request may have refreshed)
        cached_version, cached_at = _version_cache
        if time.time() - cached_at < _CACHE_TTL:
            return cached_version

        version = await asyncio.to_thread(get_agentic_cli("claude-code").get_version)

        _version_cache = (version, time.time())
        return version


async def _get_active_count() -> int:
    """Count live agent sessions by discovering their tmux panes.

    This used to read ``presence_sessions``, which was fed by opt-in HTTP
    hooks. Those hooks were rarely installed, so the header counter showed
    stale rows or zero. tmux discovery is the source the Agent Bridge
    already trusts, so the header now agrees with what that page shows.
    """
    return await asyncio.to_thread(lambda: len(discover_agent_sessions()))


async def _get_provider_statuses() -> dict[str, Any]:
    statuses = await asyncio.gather(
        *(asyncio.to_thread(provider.get_status) for provider in get_agentic_clis())
    )
    return {status["id"]: status for status in statuses}


async def _get_scheduling_hooks_installed() -> bool:
    hooks = await asyncio.to_thread(get_hooks_status)
    return all(status == "installed" for status in hooks.values())


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status():
    """Return system status for header indicators."""
    version, active_count, provider_statuses, hooks_installed = await asyncio.gather(
        _get_claude_code_version(),
        _get_active_count(),
        _get_provider_statuses(),
        _get_scheduling_hooks_installed(),
    )

    return SystemStatusResponse(
        claude_code_version=version,
        active_sessions=active_count,
        providers=provider_statuses,
        scheduling_hooks_installed=hooks_installed,
        instance=get_instance_identity(),
    )


class SystemResourcesResponse(BaseModel):
    """System resource status with hardware-aware limits."""
    memory_total_gb: float
    memory_available_gb: float
    memory_usage_percent: float
    memory_status: str  # "comfortable", "warning", "critical"
    max_active_sessions: int
    max_cached_sessions: int
    event_retention_hours: int
    estimated_bytes_per_session: int


@router.get("/system-resources", response_model=SystemResourcesResponse)
async def get_system_resources():
    """Return system resource status and dynamic limits."""
    status = get_memory_status_cached()
    limits = get_dynamic_limits()

    if status.is_critical:
        memory_status = "critical"
    elif status.is_warning:
        memory_status = "warning"
    else:
        memory_status = "comfortable"

    return SystemResourcesResponse(
        memory_total_gb=round(status.total_bytes / (1024**3), 2),
        memory_available_gb=round(status.available_bytes / (1024**3), 2),
        memory_usage_percent=round(status.usage_percent * 100, 1),
        memory_status=memory_status,
        max_active_sessions=limits.max_active_sessions,
        max_cached_sessions=limits.max_cached_sessions,
        event_retention_hours=limits.event_retention_hours,
        estimated_bytes_per_session=100 * 1024 * 1024,  # 100MB
    )
