"""Hardware-aware memory monitoring for dynamic session limits.

Reads /proc/meminfo on Linux to determine available memory and adjusts
session limits accordingly. Falls back to conservative defaults on other
platforms or when memory info is unavailable.
"""
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Memory thresholds (percentage of total RAM)
MEMORY_CRITICAL_THRESHOLD = 0.90  # 90% used - enforce hard limits
MEMORY_WARNING_THRESHOLD = 0.75   # 75% used - start cleanup
MEMORY_COMFORTABLE_THRESHOLD = 0.50  # 50% used - normal operation

# Per-session memory estimate (bytes)
# Conservative estimate: each active CC session uses ~50MB-200MB
# Including tmux pane, Python process, and context window
ESTIMATED_BYTES_PER_SESSION = 100 * 1024 * 1024  # 100MB

# Minimum guaranteed sessions even under memory pressure
MIN_SESSIONS_GUARANTEED = 2


@dataclass
class MemoryStatus:
    """Current system memory status."""
    total_bytes: int
    available_bytes: int
    used_bytes: int
    usage_percent: float
    is_critical: bool
    is_warning: bool
    estimated_max_sessions: int


@dataclass
class SessionLimits:
    """Dynamic session limits based on hardware."""
    max_active_sessions: int
    max_cached_sessions: int
    event_retention_hours: int
    cleanup_threshold_percent: float


def _read_proc_meminfo() -> dict[str, int] | None:
    """Read /proc/meminfo on Linux. Returns None on other platforms."""
    try:
        with open("/proc/meminfo") as f:
            meminfo = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    # Values are in kB
                    meminfo[key] = int(parts[1]) * 1024  # Convert to bytes
            return meminfo
    except (FileNotFoundError, PermissionError, ValueError):
        return None


def get_memory_status() -> MemoryStatus:
    """Get current system memory status."""
    meminfo = _read_proc_meminfo()

    if meminfo is None:
        # Fallback: assume 8GB system with conservative limits
        logger.debug("Cannot read /proc/meminfo, using conservative defaults")
        total = 8 * 1024 * 1024 * 1024  # 8GB
        available = total // 2  # Assume 50% available
        used = total - available
    else:
        total = meminfo.get("MemTotal", 8 * 1024 * 1024 * 1024)
        # MemAvailable is more accurate than MemFree for actual usable memory
        available = meminfo.get("MemAvailable", meminfo.get("MemFree", total // 2))
        used = total - available

    usage_percent = used / total if total > 0 else 0.0
    is_critical = usage_percent >= MEMORY_CRITICAL_THRESHOLD
    is_warning = usage_percent >= MEMORY_WARNING_THRESHOLD

    # Estimate max sessions based on available memory
    # Leave 20% headroom for system + other processes
    usable_bytes = available * 0.8
    estimated_max = max(MIN_SESSIONS_GUARANTEED, int(usable_bytes / ESTIMATED_BYTES_PER_SESSION))

    return MemoryStatus(
        total_bytes=total,
        available_bytes=available,
        used_bytes=used,
        usage_percent=usage_percent,
        is_critical=is_critical,
        is_warning=is_warning,
        estimated_max_sessions=estimated_max,
    )


def get_dynamic_limits(
    base_max_sessions: int = 20,
    base_max_cached: int = 500,
    base_retention_hours: int = 168,  # 7 days
) -> SessionLimits:
    """Calculate dynamic session limits based on current memory status.
    
    Args:
        base_max_sessions: Base limit for active sessions (overridden if memory is tight)
        base_max_cached: Base limit for cached session metadata
        base_retention_hours: Base event retention in hours
    
    Returns:
        SessionLimits with hardware-adjusted values
    """
    status = get_memory_status()

    if status.is_critical:
        # Under memory pressure: aggressive limits
        max_sessions = max(MIN_SESSIONS_GUARANTEED, min(base_max_sessions // 4, status.estimated_max_sessions))
        max_cached = max(50, base_max_cached // 4)
        retention_hours = max(24, base_retention_hours // 4)  # 1 day minimum
        cleanup_threshold = MEMORY_CRITICAL_THRESHOLD
        logger.warning(
            f"Memory critical ({status.usage_percent:.0%} used): "
            f"reducing limits to {max_sessions} sessions, {max_cached} cached"
        )
    elif status.is_warning:
        # Moderate pressure: reduced limits
        max_sessions = max(MIN_SESSIONS_GUARANTEED, min(base_max_sessions // 2, status.estimated_max_sessions))
        max_cached = max(100, base_max_cached // 2)
        retention_hours = max(48, base_retention_hours // 2)  # 2 days minimum
        cleanup_threshold = MEMORY_WARNING_THRESHOLD
        logger.info(
            f"Memory warning ({status.usage_percent:.0%} used): "
            f"reducing limits to {max_sessions} sessions, {max_cached} cached"
        )
    else:
        # Comfortable: use base limits (capped by estimated max)
        max_sessions = min(base_max_sessions, status.estimated_max_sessions)
        max_cached = base_max_cached
        retention_hours = base_retention_hours
        cleanup_threshold = MEMORY_WARNING_THRESHOLD

    return SessionLimits(
        max_active_sessions=max_sessions,
        max_cached_sessions=max_cached,
        event_retention_hours=retention_hours,
        cleanup_threshold_percent=cleanup_threshold,
    )


# Module-level cache to avoid per-request system calls
_cached_status: MemoryStatus | None = None
_cache_timestamp: float = 0
_CACHE_TTL_SECONDS = 30  # Refresh every 30 seconds


def get_memory_status_cached() -> MemoryStatus:
    """Get memory status with caching to avoid repeated /proc reads."""
    import time
    global _cached_status, _cache_timestamp

    now = time.monotonic()
    if _cached_status is None or (now - _cache_timestamp) > _CACHE_TTL_SECONDS:
        _cached_status = get_memory_status()
        _cache_timestamp = now

    return _cached_status
