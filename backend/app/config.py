"""Application configuration using pydantic-settings."""
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _default_portfolio_cap_value() -> int:
    """Portfolio-cap default: the hardware-aware session budget, capped at 4.

    Imported lazily so config import doesn't pull in the memory monitor /
    session registry at module load (config.py is imported very early).
    """
    from app.services.scheduling.session_registry import session_registry

    return min(session_registry.effective_max_sessions, 4)


def _default_kanban_database_url() -> str:
    """Absolute, CWD-independent location for the kanban board DB.

    The board is meant to be one-per-machine, but a relative URL
    (sqlite+aiosqlite:///./kanban.db) resolves against the launch CWD, so every
    worktree / launch directory got its own board and tests could clobber the
    live one. Anchor it beside the existing ~/.claude-registry backups instead.
    """
    db_path = Path.home() / ".claude-registry" / "kanban.db"
    return f"sqlite+aiosqlite:///{db_path}"


def _read_version() -> str:
    try:
        return (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0.1.0"


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application settings
    app_name: str = "Claude Cockpit"
    app_version: str = _read_version()
    debug: bool = False

    # API settings
    api_v1_prefix: str = "/api/v1"

    # CORS settings
    cors_origins: list[str] = ["http://localhost:5173"]
    cors_credentials: bool = True
    cors_methods: list[str] = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"]
    cors_headers: list[str] = ["Content-Type", "Authorization", "X-API-Token"]

    # Database settings
    database_url: str = "sqlite+aiosqlite:///./claude_registry.db"
    # Separate store for the kanban board domain (portable, sync-able).
    # Kept apart from database_url, which holds device-local data.
    # Absolute + CWD-independent so there is one board per machine; override
    # with KANBAN_DATABASE_URL (e.g. a mounted volume in Docker).
    kanban_database_url: str = _default_kanban_database_url()

    # Agent Bridge attachment settings
    bridge_attachment_dir: str = "~/.claude-registry/bridge-attachments"
    bridge_attachment_agent_root: str | None = None
    bridge_attachment_max_bytes: int = 10 * 1024 * 1024
    bridge_attachment_retention_days: int = 7
    bridge_attachment_max_per_session_per_day: int = 100

    # Server settings
    host: str = "127.0.0.1"
    port: int = 8000
    api_token: str | None = None
    # Public-facing base URL advertised to clients (Claude Code hooks, the kanban
    # MCP entry in .mcp.json). Leave None to derive it per-request from the
    # incoming Request (handles reverse proxies that set forwarded headers); set
    # it explicitly for deployments where the request host isn't reachable by the
    # consumer, e.g. PUBLIC_BASE_URL=https://cockpit.example.com.
    public_base_url: str | None = None

    # Portfolio-cap: gate the total number of concurrent agent-claims across all
    # autodispatch-enabled projects in run_dispatch_tick. Off by default so rollout
    # is gradual; when on, the whole tick is skipped once the sum hits the cap.
    portfolio_cap_enabled: bool = False
    portfolio_cap_value: int = Field(default_factory=_default_portfolio_cap_value)

    # Stale-project detection: a scheduler task (see main.py lifespan) that signals
    # — never blocks — when an autodispatch-enabled project's Backlog sits with no
    # Done-move for too long. `stale_threshold_hours` is the age past which the
    # last Done-move counts as stale; `stale_check_interval_minutes` is how often
    # the detector runs. `stale_comment_template` is `.format(hours=..., backlog=...)`.
    stale_threshold_hours: int = 24
    stale_check_interval_minutes: int = 30
    stale_comment_template: str = (
        "[portfolio-stale] Dit project heeft al ~{hours}u geen Done-move gehad "
        "terwijl er nog {backlog} kaart(en) in Backlog staan. Dit is een signaal, "
        "geen blokkade — overweeg dit project dispatch-aandacht of prioritering te geven."
    )

    # Tunable operational constants (defaults match the historical hardcoded values)
    kanban_dispatch_interval_seconds: int = 10
    provider_doctor_timeout_seconds: int = 30
    sqlite_busy_timeout_ms: int = 5000
    default_backup_retention_days: int = 7

    # MiniMax credentials (process-env only, e.g. via .env). Cockpit never
    # stores this in the database or returns it in an API response; spawn_session
    # reads it server-side and injects it into the tmux session's env.
    minimax_api_key: str | None = None
    minimax_base_url: str | None = None


# Global settings instance
settings = Settings()
