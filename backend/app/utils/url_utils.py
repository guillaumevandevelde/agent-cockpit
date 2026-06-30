"""Helpers for building public-facing URLs advertised to clients."""
from fastapi import Request

from app.config import settings


def resolve_base_url(request: Request) -> str:
    """Base URL (no trailing slash) for URLs we hand to external consumers.

    Prefers the configured ``public_base_url`` when set; otherwise derives it
    from the incoming request so the value tracks the host the client used,
    including reverse-proxy setups, instead of a hardcoded localhost:8000.
    """
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    return str(request.base_url).rstrip("/")
