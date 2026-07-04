"""Application configuration using pydantic-settings."""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
