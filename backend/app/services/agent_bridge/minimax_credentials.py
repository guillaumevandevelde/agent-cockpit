"""Persist the MiniMax API key from the UI to the backend .env file.

Mirrors the "never store in the database, never return to the browser"
invariant from platform_env.py: this writes a plain KEY=value line to the same
.env file pydantic-settings reads at startup, and mutates the in-process
Settings object so the change takes effect immediately, without a restart.
"""
from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.services.subscriptions import invalidate_snapshot_cache

_ENV_KEY = "MINIMAX_API_KEY"


def _env_path() -> Path:
    return Path.cwd() / ".env"


def _validate_value(raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        raise ValueError("MiniMax API key cannot be empty")
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("MiniMax API key must not contain newlines or null bytes")
    return value


def _upsert_env_line(env_path: Path, key: str, value: str | None) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    prefix = f"{key}="
    kept = [line for line in lines if not line.startswith(prefix)]
    if value is not None:
        kept.append(f"{key}={value}")
    env_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")


def set_minimax_api_key(raw_value: str) -> None:
    value = _validate_value(raw_value)
    _upsert_env_line(_env_path(), _ENV_KEY, value)
    settings.minimax_api_key = value
    invalidate_snapshot_cache("minimax")


def clear_minimax_api_key() -> None:
    _upsert_env_line(_env_path(), _ENV_KEY, None)
    settings.minimax_api_key = None
    invalidate_snapshot_cache("minimax")
