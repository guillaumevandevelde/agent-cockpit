"""Detect the vendor/subscription that started a running Agent Bridge session.

Every Agent Bridge session runs through the same CLI binary regardless of
which vendor it talks to — a ``claude`` process pointed at Anthropic and a
``claude`` process pointed at MiniMax are indistinguishable from tmux
metadata alone (only ``ANTHROPIC_BASE_URL`` differs). The one reliable
signal for a *running* process is its environment, so this module reads
``/proc/<pid>/environ`` and classifies the vendor using the same env
contract that ``agentic_cli/provider_env.py`` writes at spawn time.

This is the read-side mirror of ``build_provider_env``; the two must stay
in sync (a spec change to the spawn env contract there is a classification
change here). Detection is **best-effort** — a missing/unreadable
``/proc`` entry never raises, it just yields the CLI's own vendor. No
secret ever leaves this module: only the vendor id + display name are
returned, never the auth token that lives alongside the base URL.
"""
from __future__ import annotations

import logging

from app.services.agentic_cli.provider_env import (
    CLAUDE_CODE_CLI_ID,
    PROVIDER_ANTHROPIC,
    PROVIDER_BEDROCK,
    PROVIDER_COMPATIBLE,
    PROVIDER_MINIMAX,
)

logger = logging.getLogger(__name__)

_PROVIDER_DISPLAY: dict[str, str] = {
    PROVIDER_ANTHROPIC: "Anthropic",
    PROVIDER_MINIMAX: "MiniMax",
    PROVIDER_BEDROCK: "Bedrock",
    PROVIDER_COMPATIBLE: "Anthropic-compatible",
}


def read_process_env(pid: str | int) -> dict[str, str]:
    """Return the environment of ``pid`` from ``/proc/<pid>/environ``.

    Best-effort: any error (missing pid, permission denied, non-Linux host
    without ``/proc``) yields an empty dict rather than raising, so a
    session whose env we can't read simply falls back to the CLI's own
    vendor instead of breaking discovery.
    """
    try:
        with open(f"/proc/{pid}/environ", "rb") as handle:
            raw = handle.read()
    except (OSError, ValueError):
        return {}
    env: dict[str, str] = {}
    for chunk in raw.split(b"\x00"):
        if not chunk:
            continue
        key, sep, value = chunk.decode("utf-8", "replace").partition("=")
        if sep and key:
            env[key] = value
    return env


def classify_provider_env(
    env: dict[str, str],
    *,
    cli_id: str,
    cli_display_name: str | None = None,
) -> tuple[str, str]:
    """Classify the vendor from a session's environment.

    Returns ``(provider_id, provider_display_name)``. Precedence mirrors
    ``build_provider_env``: an explicit Bedrock flag wins over a base-URL
    override, which wins over the Anthropic default. When no Anthropic-style
    override is present the vendor is the CLI itself (so a Codex session
    reports Codex, not a fabricated "Anthropic").
    """
    if _truthy(env.get("CLAUDE_CODE_USE_BEDROCK")):
        return PROVIDER_BEDROCK, _PROVIDER_DISPLAY[PROVIDER_BEDROCK]

    base_url = (env.get("ANTHROPIC_BASE_URL") or "").strip()
    if base_url:
        provider = (
            PROVIDER_MINIMAX if "minimax" in base_url.lower() else PROVIDER_COMPATIBLE
        )
        return provider, _PROVIDER_DISPLAY[provider]

    if cli_id == CLAUDE_CODE_CLI_ID:
        return PROVIDER_ANTHROPIC, _PROVIDER_DISPLAY[PROVIDER_ANTHROPIC]

    return cli_id, cli_display_name or cli_id


def detect_session_provider(
    pid: str | int,
    *,
    cli_id: str,
    cli_display_name: str | None = None,
) -> tuple[str, str]:
    """Detect the vendor/subscription a running session was started with."""
    env = read_process_env(pid)
    return classify_provider_env(
        env, cli_id=cli_id, cli_display_name=cli_display_name
    )


def _truthy(value: str | None) -> bool:
    """Match ``build_provider_env``'s truthiness: any non-empty, non-``0`` value."""
    if value is None:
        return False
    stripped = value.strip().lower()
    return stripped not in ("", "0", "false", "no")
