"""Map an Agent Bridge provider selection to process environment variables.

Single source of truth for provider -> env mapping and for the
explicit-env construction contract used by every spawn path. This module
never resolves or stores secrets: for Bedrock only non-secret
configuration (region, profile name, model id) is set and the AWS SDK
credential chain on the host resolves actual creds; for MiniMax the API
key must be resolved by the caller (e.g. a secrets store) and passed in
as ``minimax_api_key``.

``build_spawn_env`` is the single entry point for ``services/runs/spawn.py``
(agent-bridge) and ``services/runs/cc_spawn.py`` (legacy CC-bridge) — both
call it with the cleaned extras + provider env + cockpit context, and
receive a merged dict + sorted name list. Every spec change to the env
contract lands here; see kanban cards ``b5c71e0c`` + the
``[self-improve] spawn.py + cc_spawn.py duplicate ...; extract
build_spawn_env()`` follow-up.
"""
from __future__ import annotations

import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_BEDROCK = "bedrock"
PROVIDER_MINIMAX = "minimax"

CLAUDE_CODE_CLI_ID = "claude-code"

MINIMAX_BASE_URL_INTERNATIONAL = "https://api.minimax.io/anthropic"
MINIMAX_BASE_URL_CHINA = "https://api.minimaxi.com/anthropic"
MINIMAX_DEFAULT_MODEL = "MiniMax-M3[1m]"
MINIMAX_AUTO_COMPACT_WINDOW = "1000000"


def _clean(value: str | None) -> str | None:
    """Trim a value and reject control characters that break env injection."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if "\n" in stripped or "\r" in stripped or "\x00" in stripped:
        raise ValueError("Environment value must not contain newlines or null bytes")
    return stripped


def build_provider_env(
    provider: str | None,
    region: str | None = None,
    aws_profile: str | None = None,
    model: str | None = None,
    minimax_api_key: str | None = None,
    minimax_base_url: str | None = None,
    cli_id: str = CLAUDE_CODE_CLI_ID,
) -> dict[str, str]:
    """Return the env vars for a provider selection (empty for Anthropic).

    ``minimax_api_key`` is the caller-resolved credential (e.g. from a secrets
    store); this function never hardcodes or looks up secrets itself.

    CLAUDE_CODE_USE_BEDROCK and ANTHROPIC_MODEL are Claude-Code-specific and
    would be meaningless (or actively wrong) for any other CLI, so they
    are only set for ``cli_id="claude-code"``. Every other CLI
    (Codex CLI, which selects Bedrock via its own ``--config model_provider=``
    flag — see ``codex_cli.py`` — plus OpenCode/Copilot/MiniMax) only gets the
    shared, non-secret AWS_REGION/AWS_PROFILE env.
    """
    if provider == PROVIDER_BEDROCK:
        env: dict[str, str] = {}
        cleaned_region = _clean(region)
        if cleaned_region:
            env["AWS_REGION"] = cleaned_region
        cleaned_profile = _clean(aws_profile)
        if cleaned_profile:
            env["AWS_PROFILE"] = cleaned_profile

        if cli_id != CLAUDE_CODE_CLI_ID:
            return env

        env = {"CLAUDE_CODE_USE_BEDROCK": "1", **env}
        cleaned_model = _clean(model)
        if cleaned_model:
            env["ANTHROPIC_MODEL"] = cleaned_model
        return env

    if provider == PROVIDER_MINIMAX:
        # Always set base URL/model explicitly (never conditionally) so a
        # stale ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN inherited from the
        # session's ambient environment can't leak through from a previous
        # provider choice, per MiniMax's own docs warning about conflicts.
        env = {
            "ANTHROPIC_BASE_URL": _clean(minimax_base_url) or MINIMAX_BASE_URL_INTERNATIONAL,
            "ANTHROPIC_MODEL": _clean(model) or MINIMAX_DEFAULT_MODEL,
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": MINIMAX_AUTO_COMPACT_WINDOW,
        }
        cleaned_key = _clean(minimax_api_key)
        if cleaned_key:
            env["ANTHROPIC_AUTH_TOKEN"] = cleaned_key
        return env

    return {}


class SpawnEnv(NamedTuple):
    """Result of ``build_spawn_env``: merged env dict + sorted name list.

    Returning both lets every caller (spawn.py, cc_spawn.py) consume a single
    source of truth for the merged env and the audit-only sorted name list —
    the audit hook + tmux argv can no longer drift apart from each other.
    """

    env: dict[str, str]
    names: list[str]


def _clean_extra_env(extra_env: dict[str, str] | None) -> dict[str, str]:
    """Validate caller-supplied env values; reject control characters.

    Mirrors the helpers previously duplicated in ``services/runs/spawn.py``
    and ``services/runs/cc_spawn.py``; any value containing ``\\n``, ``\\r``,
    or ``\\x00`` raises ``ValueError`` before it reaches tmux's argv.
    An empty/None input returns an empty dict.
    """
    if not extra_env:
        return {}
    cleaned: dict[str, str] = {}
    for key, value in extra_env.items():
        if not isinstance(key, str) or not key:
            raise ValueError("Environment key must be a non-empty string")
        if not isinstance(value, str):
            raise ValueError(f"Environment value for {key!r} must be a string")
        _clean(value)
        cleaned[key] = value
    return cleaned


def build_spawn_env(
    provider_env: dict[str, str],
    extra_env: dict[str, str] | None,
    project_key: str | None,
    runtime: str | None,
) -> SpawnEnv:
    """Merge every explicit-env input into one canonical dict.

    Precedence on collision: ``extras < provider_env < cockpit_*``. The
    caller-supplied extras (today a dict; once follow-up #4 lands this is
    where ``SecretStore.get(project_key, ...)`` results land) lose to the
    provider's own env — a stale project secret must never downgrade the
    CLI's own configuration (Bedrock region, MiniMax base URL, etc.).
    The ``COCKPIT_*`` vars win over everything because the runtime's
    project context is the single source of truth for an agent's
    identity.

    The backend's ``os.environ`` is **never** merged in — every var must
    come from an explicit, auditable input. The whole point of the
    explicit-env contract is that nothing reaches tmux unless the caller
    (or the provider-env builder) chose to put it there.

    Returns:
        A ``SpawnEnv(env, names)`` named tuple: the merged dict and the
        sorted key list (sorted so audit + tmux argv can both consume one
        canonical ordering).
    """
    cleaned_extras = _clean_extra_env(extra_env)
    merged: dict[str, str] = {}
    # extras first; provider env overwrites on collision (collision
    # semantics: a stale secret must never downgrade CLI config).
    merged.update(cleaned_extras)
    merged.update(provider_env)
    # cockpit_* wins last — the runtime's project context is authoritative.
    if project_key is not None:
        merged["COCKPIT_PROJECT_KEY"] = project_key
    if runtime is not None:
        merged["COCKPIT_RUNTIME"] = runtime
    return SpawnEnv(env=merged, names=sorted(merged.keys()))


# Audit-log sink for security-relevant spawn events. Persists one row per
# invocation to the ``security_audit`` table; best-effort by design —
# callers must not depend on this hook returning anything (always
# callable, never raises) and var *names* are the only stable identifier
# (no secret values).
#
# The function is **sync** because it lives in a sync call-site
# (every spawn runs tmux via subprocess, not in an await chain). The
# actual DB write is dispatched to the running event loop via
# ``asyncio.ensure_future``; if no loop is running (sync tooling, test
# fixtures without an async context), the write is dropped — the
# ``logger.info`` line is the contract for that case.
#
# The function also still emits the structured ``logger.info`` line that
# ``test_runs_spawn_env_isolation`` and the grep-style tooling rely on,
# so an upgrade to a real table doesn't drop the log signal.
def _record_audit(
    project_key: str | None,
    runtime: str | None,
    session_name: str,
    env_var_names: list[str],
    kind: str = "env_inject",
) -> None:
    """Insert one ``security_audit`` row for an env-inject / run-start / run-stop.

    Replaces the two near-identical audit helpers that used to live in
    ``services/runs/spawn.py`` and ``services/runs/cc_spawn.py``. Spawn
    paths re-export this from their own module for backward compat with
    the ``run_service.py`` import surface.
    """
    if not project_key:
        # Without a project_key we have no scope to audit against; skip
        # rather than emit a row that's orphaned in the table.
        return
    logger.info(
        "%s project_key=%s runtime=%s session=%s vars=%s",
        kind,
        project_key,
        runtime or "-",
        session_name,
        sorted(env_var_names),
    )
    try:
        import asyncio

        from app.database import AsyncSessionLocal
        from app.models.security_audit import SecurityAuditKind
        from app.services.security_audit_service import record

        async def _do_record() -> None:
            async with AsyncSessionLocal() as db:
                await record(
                    db,
                    kind=SecurityAuditKind(kind),
                    project_key=project_key,
                    actor="run-service",
                    payload_ref={
                        "session_or_instance": session_name,
                        "runtime": runtime,
                        "env_var_names": sorted(env_var_names),
                    },
                )
                await db.commit()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            # Inside an event loop — schedule without blocking the
            # originating sync call. Tests can monkeypatch this helper
            # directly (see test_runs_spawn_env_isolation) when they
            # need to capture the audit payload without a DB.
            loop.create_task(_do_record())
    except Exception:
        logger.exception(
            "security_audit insert failed (kind=%s project_key=%s)",
            kind,
            project_key,
        )