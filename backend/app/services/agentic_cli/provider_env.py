"""Map an Agent Bridge provider selection to process environment variables.

Single source of truth for provider -> env mapping and for the
explicit-env construction contract used by every spawn path. This module
never resolves or stores secrets: for Bedrock only non-secret
configuration (region, profile name, model id) is set and the AWS SDK
credential chain on the host resolves actual creds; for MiniMax the API
key must be resolved by the caller (e.g. a secrets store) and passed in
as ``minimax_api_key``; for the ``anthropic-compatible`` provider the
endpoint (base_url + model) is caller-resolved configuration and the
auth token is the caller-resolved credential (same convention).

``build_spawn_env`` is the single entry point for ``services/runs/spawn.py``
(agent-bridge) and ``services/runs/cc_spawn.py`` (legacy CC-bridge) — both
call it with the cleaned extras + provider env + cockpit context, and
receive a merged dict + sorted name list. Every spec change to the env
contract lands here; see kanban cards ``b5c71e0c`` + the
``[self-improve] spawn.py + cc_spawn.py duplicate ...; extract
build_spawn_env()`` follow-up.
"""
from __future__ import annotations

import json
import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_BEDROCK = "bedrock"
PROVIDER_MINIMAX = "minimax"
PROVIDER_COMPATIBLE = "anthropic-compatible"

CLAUDE_CODE_CLI_ID = "claude-code"
CODEX_CLI_ID = "codex-cli"
OPEN_CODE_CLI_ID = "open-code"

# ``OPENCODE_CONFIG_CONTENT`` is OpenCode's own inline-config env var (see
# https://opencode.ai/docs/config — "allowing runtime overrides without
# modifying config files"). This is the *only* mechanism the endpoint
# translation below can rely on: unlike Claude-Code's ANTHROPIC_BASE_URL,
# OpenCode has no top-level "point at a different endpoint" env var — a
# custom endpoint is a full provider entry (npm package + baseURL + apiKey
# + model list), which only exists as JSON, either on disk or via this
# env var. The credential is referenced as ``{env:VAR}`` (OpenCode's own
# variable-substitution syntax) rather than embedded as a literal, so the
# secret never has to be duplicated inside the JSON string that lands in
# tmux's argv.
_OPENCODE_AUTH_TOKEN_ENV_VAR = "CCK_OPENCODE_AUTH_TOKEN"

MINIMAX_BASE_URL_INTERNATIONAL = "https://api.minimax.io/anthropic"
MINIMAX_BASE_URL_CHINA = "https://api.minimaxi.com/anthropic"
# MiniMax's Anthropic-compatible API only accepts bare model identifiers
# (MiniMax-M3, MiniMax-M2.7, …). The old "MiniMax-M3[1m]" form with a
# bracketed context-window suffix is rejected as an unknown model. The 1M
# context window is requested separately via CLAUDE_CODE_AUTO_COMPACT_WINDOW
# below, so the suffix is both redundant and breaking — keep this bare.
MINIMAX_DEFAULT_MODEL = "MiniMax-M3"
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


def _build_opencode_endpoint_env(base_url: str, model: str, auth_token: str | None) -> dict[str, str]:
    """Translate a resolved endpoint into OpenCode's ``OPENCODE_CONFIG_CONTENT``.

    Shape follows the documented custom-Anthropic-compatible-provider recipe
    (https://opencode.ai/docs/providers/ — ``npm: "@ai-sdk/anthropic"`` with
    ``options.baseURL`` + ``options.apiKey``): one inline provider entry
    keyed by the model id (stable and unique for a single per-spawn config),
    declaring exactly the one model the endpoint should serve. The apiKey is
    always the ``{env:VAR}`` substitution form, never a literal, so the
    secret never has to live twice (once as a JSON string value, once as an
    env var) — OpenCode resolves it from ``_OPENCODE_AUTH_TOKEN_ENV_VAR`` at
    startup. When no token is supplied the reference is still emitted (the
    "ambient credential" pattern MiniMax already uses); the var is just left
    unset here.
    """
    env: dict[str, str] = {}
    if auth_token:
        env[_OPENCODE_AUTH_TOKEN_ENV_VAR] = auth_token
    config = {
        "provider": {
            model: {
                "npm": "@ai-sdk/anthropic",
                "options": {
                    "baseURL": base_url,
                    "apiKey": f"{{env:{_OPENCODE_AUTH_TOKEN_ENV_VAR}}}",
                },
                "models": {model: {}},
            },
        },
    }
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    return env


def _build_endpoint_env(
    cli_id: str,
    base_url: str,
    model: str,
    auth_token: str | None,
    *,
    extra_claude_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Translate a resolved ``(base_url, model, auth_token)`` endpoint into
    the spawned CLI's own env/config contract.

    Each CLI has its own answer to "how do I point at a different
    inference endpoint" — this is the per-CLI translation table the card
    asks for, replacing the single Claude-shaped branch with an
    early-return. A CLI with no such mechanism raises explicitly rather
    than silently doing nothing (the exact bug this card fixes: silence
    here previously meant "looks configured, does nothing").
    """
    if cli_id == CLAUDE_CODE_CLI_ID:
        env = {"ANTHROPIC_BASE_URL": base_url, "ANTHROPIC_MODEL": model}
        if auth_token:
            env["ANTHROPIC_AUTH_TOKEN"] = auth_token
        if extra_claude_env:
            env.update(extra_claude_env)
        return env

    if cli_id == OPEN_CODE_CLI_ID:
        return _build_opencode_endpoint_env(base_url, model, auth_token)

    if cli_id == CODEX_CLI_ID:
        raise ValueError(
            f"{cli_id} does not support routing to a custom Anthropic-compatible "
            "endpoint via environment variables — Codex selects its provider "
            "through its own config.toml (model_provider=…), not "
            "ANTHROPIC_BASE_URL; see codex_cli.py",
        )

    raise ValueError(
        f"{cli_id} does not support routing to a custom endpoint "
        "(no ANTHROPIC_BASE_URL-equivalent mechanism is wired for this CLI)",
    )


def build_provider_env(
    provider: str | None,
    region: str | None = None,
    aws_profile: str | None = None,
    model: str | None = None,
    minimax_api_key: str | None = None,
    minimax_base_url: str | None = None,
    cli_id: str = CLAUDE_CODE_CLI_ID,
    base_url: str | None = None,
    auth_token: str | None = None,
) -> dict[str, str]:
    """Return the env vars for a provider selection (empty for Anthropic).

    ``minimax_api_key`` and ``auth_token`` are caller-resolved credentials
    (e.g. from a secrets store); this function never hardcodes or looks up
    secrets itself.

    CLAUDE_CODE_USE_BEDROCK and ANTHROPIC_MODEL are Claude-Code-specific and
    would be meaningless (or actively wrong) for any other CLI, so they
    are only set for ``cli_id="claude-code"``. Every other CLI
    (Codex CLI, which selects Bedrock via its own ``--config model_provider=``
    flag — see ``codex_cli.py`` — plus OpenCode/Copilot/MiniMax) only gets the
    shared, non-secret AWS_REGION/AWS_PROFILE env.

    For endpoint-setting providers (MiniMax, ``anthropic-compatible``), the
    resolved ``(base_url, model, auth_token)`` triple is handed to
    ``_build_endpoint_env``, which is the per-CLI translation table: each
    CLI decides for itself how (or whether) it can be pointed at a custom
    endpoint, instead of one Claude-shaped branch with an early-return for
    everyone else.
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
        # Always resolve base URL/model explicitly (never conditionally) so
        # a stale ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN inherited from the
        # session's ambient environment can't leak through from a previous
        # provider choice, per MiniMax's own docs warning about conflicts.
        resolved_base_url = _clean(minimax_base_url) or MINIMAX_BASE_URL_INTERNATIONAL
        resolved_model = _clean(model) or MINIMAX_DEFAULT_MODEL
        resolved_token = _clean(minimax_api_key)
        return _build_endpoint_env(
            cli_id, resolved_base_url, resolved_model, resolved_token,
            extra_claude_env={"CLAUDE_CODE_AUTO_COMPACT_WINDOW": MINIMAX_AUTO_COMPACT_WINDOW},
        )

    if provider == PROVIDER_COMPATIBLE:
        # Data-driven branch: ``base_url`` and ``model`` come from caller-
        # resolved endpoint configuration (see
        # ``app.services.agentic_cli.endpoints``). This module never looks
        # up secrets — ``auth_token`` is the caller-resolved credential,
        # mirroring the MiniMax convention. Both fields are required so the
        # spawned CLI always points at an explicit endpoint (the same
        # "no stale ambient env leak" discipline MiniMax uses); a missing
        # base_url or model is a configuration error and raises here rather
        # than silently falling back to the Anthropic API.
        cleaned_base_url = _clean(base_url)
        if not cleaned_base_url:
            raise ValueError(
                "anthropic-compatible provider requires a non-empty base_url",
            )
        cleaned_model = _clean(model)
        if not cleaned_model:
            raise ValueError(
                "anthropic-compatible provider requires a non-empty model",
            )
        cleaned_token = _clean(auth_token)
        return _build_endpoint_env(cli_id, cleaned_base_url, cleaned_model, cleaned_token)

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