"""Auto-dispatch: spawn a Claude session for unclaimed Analysis/Todo cards.

The dispatcher claims a card *as the session that will work it* (claim-before-spawn,
so racing ticks/devices produce exactly one winner), moves it to Doing, then spawns
via a pluggable transport (tmux today, podman later). See docs/cockpit/kanban-dispatch-spec.md.

When hardware-aware memory limits are reached, cards are queued in PendingQueue
and retried automatically when resources become available.
"""
from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import httpx
import yaml

from app.config import settings
from app.kanban import subscription_pool

# Local import so the dep-filter check inside the dispatch tick stays a pure
# helper (no DB / session state — see app.kanban.dep_resolver).
from app.kanban.dep_resolver import (
    HOLD_AWAITING_PLAN_REF,
    HOLD_GATED,
    HOLD_MISSING_PARENT,
    HOLD_SCHEDULED,
    classify_hold,
    dangling_dep_ids,
    has_plan_ref,
    meets_dep_prerequisites,
)
from app.kanban.dep_resolver import is_due as dep_is_due

# PERMISSION_PROMPT_TOOL_NAME is the MCP tool name dispatched sessions get via
# ``--permission-prompt-tool`` on the product lane (skip_permissions=False).
# Centralised in ``mcp_server`` so the producer (the ``permission_prompt``
# tool) and the wire-up (this module) cannot drift apart — kaart 5278a5bd…
# AC2.
from app.kanban.mcp_server import PERMISSION_PROMPT_TOOL_NAME
from app.kanban.models import KanbanCard, KanbanMeta
from app.kanban.operations import ClaimRejected, apply_operation
from app.kanban.project_key import (
    resolve_project_key,
    resolve_project_path,
    safe_resolve_project_key,
)
from app.kanban.service import (
    all_card_ids,
    get_card,
    get_column_default_endpoint_name,
    get_column_default_model,
    get_column_default_provider,
    list_cards,
)
from app.kanban.subscription_pool import (
    PoolEntry,
    get_subscription_pool,
)
from app.services.agentic_cli.provider_env import (
    CLAUDE_CODE_CLI_ID,
    OPEN_CODE_CLI_ID,
    PROVIDER_ANTHROPIC,
    PROVIDER_BEDROCK,
    PROVIDER_COMPATIBLE,
    PROVIDER_MINIMAX,
    PROVIDER_OPENCODE_GO,
    PROVIDER_OPENCODE_ZEN,
)
from app.services.memory_monitor import get_memory_status_cached
from app.services.scheduling.session_registry import session_registry
from app.services.subscriptions.base import SubscriptionUsage
from app.utils.path_utils import convert_path_to_folder_name
from app.utils.timeutils import ensure_aware

logger = logging.getLogger(__name__)


# ---- security-audit invulpunten -------------------------------------------
#
# Dispatch mutates ``KanbanMeta`` (in the kanban DB) while the security-audit
# table lives in the app DB (per docs/cockpit/veilig-bouwen-en-uitleveren.md
# §4.8 "Apart"). The two stores have no shared transaction, so the audit
# insert runs in its own short-lived session and is **best-effort**: a failed
# audit row is logged and dropped, never propagated back to the caller. The
# meta-flip is the security-relevant action; the audit is observability.
#
# ``actor`` is the request-level actor (user/operator). For dispatch we use
# the same constant the REST router uses — there's no per-user identity
# layer yet, so anything that mutates the board via the REST API is
# attributed to the same default actor. Per-user attribution is follow-up.


async def _record_audit(
    kanban_session,
    *,
    kind: str,
    project_key: str,
    payload_ref: dict,
) -> None:
    """Insert one security-audit row, swallowing all errors.

    The leading ``kanban_session`` is the kanban-DB session the caller is
    already using for the meta write above; it's accepted (and ignored)
    purely so the call-site reads naturally as a sibling of the meta
    write. The audit insert runs against the **app DB** (``security_audit``
    lives there — see veilig-bouwen-en-uitleveren.md §4.8) via its own
    short-lived session.

    Best-effort: a failed audit row is logged and dropped, never
    propagated back to the caller. The meta-flip is the security-relevant
    action; the audit is observability.
    """
    try:
        from app.database import AsyncSessionLocal
        from app.models.security_audit import SecurityAuditKind
        from app.services.security_audit_service import record

        async with AsyncSessionLocal() as db:
            await record(
                db,
                kind=SecurityAuditKind(kind),
                project_key=project_key,
                actor="dispatch-api",
                payload_ref=payload_ref,
            )
            await db.commit()
    except Exception:
        logger.exception(
            "security_audit insert failed (kind=%s project_key=%s); "
            "the dispatch write itself was NOT rolled back",
            kind,
            project_key,
        )

def _cli_id_for_opencode_provider(
    cli_id: str,
    provider: str | None,
    explicit_cli_chosen: bool,
) -> str:
    """Switch the spawn transport to OpenCode when the precedence chain
    picked an OpenCode-host subscription and no explicit per-card CLI
    pin overrules it.

    claude-code's ``build_provider_env`` returns ``{}`` for opencode-go
    and opencode/Zen — a claude-code spawn for these providers silently
    runs without ``--model opencode-go/<id>`` (only OpenCode's
    ``build_spawn_command`` emits that flag form). Without this switch
    an opencode-go column would spawn via claude-code and do nothing.

    Respect an explicit per-card CLI pin: a user who set
    ``executor_agent_id="claude-code"`` on an opencode-go column meant
    that CLI, not "let the column decide".
    """
    if cli_id != CLAUDE_CODE_CLI_ID:
        return cli_id
    if explicit_cli_chosen:
        return cli_id
    if provider in (PROVIDER_OPENCODE_GO, PROVIDER_OPENCODE_ZEN):
        return OPEN_CODE_CLI_ID
    return cli_id


# Known CLI IDs are registered in app.services.agentic_cli; re-derived here
# so the phase router doesn't depend on that module being imported at typing time.
def _phase_cli_id(card, *, phase: str, known_clis: set | None = None) -> str:
    """Resolve which spawn transport/CLI id the card picks for `phase`.

    For analyst: the card's analyst_agent_id, or "claude-code" when unset.
    For executor: the card's executor_agent_id first; if that's unset, fall
    back to card.agent — but only when it is itself a registered CLI id.
    A legacy `card.agent` like "engineer" (a persona/column name, not a
    CLI id) must NOT leak into the spawn transport, so when `known_clis`
    is supplied, only matching values are accepted as a fallback; when it is
    None (tests / pre-resolution callers), `card.agent` is taken at face value.
    Without that filter the executor branch would pass `"engineer"` to a
    transport that expects a registered CLI id."""
    if phase == "analyst":
        return getattr(card, "analyst_agent_id", None) or "claude-code"
    # phase == "executor"
    executor_id = getattr(card, "executor_agent_id", None)
    if executor_id:
        return executor_id
    fallback = getattr(card, "agent", None)
    if fallback and (known_clis is None or fallback in known_clis):
        return fallback
    return "claude-code"


def _phase_target_agent(card, *, project_path: str, phase: str, source_column: str,
                        agent_override: str | None = None,
                        known_clis: set | None = None,
                        fallback_persona: str | None = None) -> str:
    """Persona/column for the spawned session. Analyst phase is fixed to
    'analyst'; executor phase reuses the legacy overload-resolution rules from
    `_run_card` (the pre-`_phase_*` lines 767-768 of this module), so an
    explicit non-CLI `agent_override` — e.g. "engineer" — still wins over
    card.agent and the column-derived fallback.

    `known_clis` is passed in so the agent_override short-circuit can tell
    a CLI id apart from a persona/column name (mirrors the CLI-id
    resolution on the spawn side). When it's None, `agent_override` is taken
    at face value, which preserves the pre-refactor semantics for tests that
    don't populate the agentic_cli registry.

    `fallback_persona` is the work_type-resolved persona (see
    `_resolve_work_type_fallback` and `get_work_type_persona`). It kicks in
    only when `card.agent` is missing or doesn't match a known persona file —
    which is exactly the regression behind kanban card 9cf106e7 ("Card with
    analysis work type got picked up by an engineer"), where a legacy card
    created before the create-time auto-fill (commit 80e139e) had
    `agent='claude-code'` (a CLI id, not a persona) and was routed to the
    hardcoded 'engineer' fallback. Resolving at dispatch time closes that gap
    — including cards whose user later PATCHed work_type without re-picking
    agent — without touching the existing card row in DB.
    """
    if phase == "analyst":
        return "analyst"
    if agent_override and (known_clis is None or agent_override not in known_clis):
        return agent_override
    agents_dir = Path(project_path) / ".claude" / "agents"
    known_agents = {p.stem for p in agents_dir.glob("*.md")} if agents_dir.is_dir() else set()
    card_agent = getattr(card, "agent", None)
    if card_agent and card_agent in known_agents:
        return card_agent
    # Work-type fallback: when card.agent is missing or doesn't match a known
    # persona (e.g. it's a CLI id like 'claude-code' from a legacy row),
    # the work_type mapping decides the persona instead of the hardcoded
    # 'engineer' fallback. Required to also match a known persona file —
    # otherwise the work_type mapping could route to a column whose persona
    # the project doesn't have, and the spawn below would land in an empty
    # column.
    if fallback_persona and fallback_persona in known_agents:
        return fallback_persona
    persona = _persona_for_card(project_path, card, source_column)
    return _resolve_agent_from_persona(persona) or "engineer"


def resolve_phase(card) -> str:
    """Decide which phase to dispatch a card in.

    Returns ``"analyst"`` when the card has an ``analyst_agent_id`` configured
    but has not yet been analysed (no ``analyst_run_id``). Returns
    ``"executor"`` otherwise — covering both the multi-agent path (the
    analyst already finished, executor is next) and the legacy single-agent
    path (no analyst configured at all).

    Both the dispatch tick (dispatch_project) and redispatch_card must call
    this so a user hitting "redispatch" on a stuck analyst card re-spawns the
    analyst — not the executor. See spec §8 "analyst sessie crasht halverwege
    → gebruiker kan redispatch_card aanroepen".

    Lives in this module (not dep_resolver) because it has no DB / session
    dependencies and is a pure function over the card row.
    """
    if getattr(card, "analyst_agent_id", None) and not getattr(card, "analyst_run_id", None):
        return "analyst"
    return "executor"


META_PREFIX = "autodispatch:"
CLAIMANT_PREFIX = "agent:"
SHIPMODE_PREFIX = "shipmode:"
SKIP_PERMISSIONS_PREFIX = "skip_permissions:"
SHIP_MODES = ("pull-request", "direct")
DEFAULT_SHIP_MODE = "pull-request"
TRANSPORT_PREFIX = "transport:"
TRANSPORTS = ("worktree", "sandcastle", "headless")
DEFAULT_TRANSPORT = "worktree"
# A card whose session dies within seconds of being dispatched, this many times in a
# row with no successful run in between, is flagged to Impediment instead of being
# retried again — see _release_dead_claim. Without this, a card with a persistently
# broken spawn target (stale --resume worktree, missing sandcastle config, ...) loops
# forever: claimed, dead in seconds, reaped, re-claimed by the very next tick.
MAX_DISPATCH_FAILURES = 3
# A claim reaped younger than this counts as "dead on arrival" toward
# MAX_DISPATCH_FAILURES. Observed real spawn failures die in ~4-6s; a session that
# survived past this age did real work before dying (crash, OOM, manual kill), which
# says nothing about whether the dispatch *target* itself is broken — see
# _release_dead_claim.
DEAD_ON_ARRIVAL_SECONDS = 30
# Label applied to a card auto-moved to Impediment by a *technical* dispatch
# failure (repeated spawn failures), so the board can render it red and
# distinguish it from a card a human parked in Impediment for a decision. The
# frontend special-cases this exact string (see CardItem.tsx).
ERROR_LABEL = "error"

# Prefix for the comment posted when the dispatch tick refuses to keep silently
# holding a card whose `depends_on` names a card id that no longer exists on the
# board (a *dangling* dep). Deliberately distinct from every consumer-read
# prefix in docs/cockpit/kanban-conventions.md §2 (`**Impediment:** `,
# `**Resolution:** `, `[dispatch-failure]`, …) so no classifier/resume walker
# mistakes it for a question, an answer, or a spawn-failure — it is documentary,
# mirroring service._DEP_REMOVED_PREFIX. The fix is an operator board-action
# (recreate the parent, or strip the id from depends_on), not a resume/redispatch,
# so it must NOT trigger the needs_answer/dispatch_failed flows.
# See docs/cockpit/dangling-depends-on-analyse.md §1.1/§4.
DANGLING_DEP_COMMENT_PREFIX = "**Dangling dependency:** "


# ---- enablement: device-local, stored in KanbanMeta (not part of the op-log) ----

async def is_autodispatch_enabled(session, project_key: str) -> bool:
    row = await session.get(KanbanMeta, META_PREFIX + project_key)
    return bool(row and row.value == "1")


async def set_autodispatch(session, project_key: str, enabled: bool) -> None:
    key = META_PREFIX + project_key
    row = await session.get(KanbanMeta, key)
    if row is None:
        row = KanbanMeta(key=key, value="1" if enabled else "0")
        session.add(row)
    else:
        row.value = "1" if enabled else "0"
    await session.flush()
    await _record_audit(
        session,
        kind="autodispatch_change",
        project_key=project_key,
        payload_ref={"enabled": enabled},
    )


async def list_autodispatch_projects(session) -> list[str]:
    from sqlalchemy import select
    rows = (await session.execute(select(KanbanMeta))).scalars().all()
    return [
        r.key[len(META_PREFIX):]
        for r in rows
        if r.key.startswith(META_PREFIX) and r.value == "1"
    ]


async def disable_all_autodispatch(session) -> None:
    """Force every project's autodispatch flag off.

    Called once at backend startup (``app.main.lifespan``), before the dispatch
    tick is scheduled: the enablement flag is persisted per-project in
    ``KanbanMeta`` (see module docstring in ``dispatch_pause.py`` for why —
    device-local, survives restarts), so without this an operator who left a
    project's toggle on would have the dispatcher immediately start
    claiming/spawning Todo cards on the very next tick after any backend
    restart or crash-recovery, with nobody necessarily watching. Auto-dispatch
    must always start from an explicit opt-in each time the backend comes up.
    """
    from sqlalchemy import select
    rows = (await session.execute(select(KanbanMeta))).scalars().all()
    for row in rows:
        if row.key.startswith(META_PREFIX) and row.value == "1":
            row.value = "0"
    await session.flush()


# ---- risk_class-driven dispatch defaults ----------------------------------
#
# A project's ``ProjectSecurityProfile.risk_class`` (docs/cockpit/risk-class-
# taxonomie.md §0) drives the safe-by-default dispatch stance when no explicit
# per-project KanbanMeta override is set. Only the path-anchored ``meta`` class
# (Cockpit's own repo) keeps the historical permissive defaults — every
# product/untrusted class enforces permissions and runs in a sandbox. Signals
# may lower trust autonomously, never raise it.


async def _project_risk_class(project_key: str) -> str | None:
    """Resolve ``project_key`` to its ``ProjectSecurityProfile.risk_class``.

    Returns ``None`` when the key can't be resolved to a registered project
    path, the project has no security profile yet, or on any DB error —
    callers treat ``None`` as "no profile, keep the permissive meta default".
    """
    try:
        project_path = await resolve_project_path(project_key)
        if project_path is None:
            return None
        from app.database import AsyncSessionLocal
        from app.services.security_profile_service import SecurityProfileService
        async with AsyncSessionLocal() as db:
            profile = await SecurityProfileService(db).get(project_path)
        return profile.risk_class if profile is not None else None
    except Exception:
        logger.debug("risk_class lookup failed for %s", project_key, exc_info=True)
        return None


def _skip_permissions_for_risk_class(risk_class: str | None) -> bool:
    """Safe ``skip_permissions`` default for a ``risk_class``.

    Only ``meta`` (and the no-profile fallback) keep the permissive bypass;
    every product/untrusted class defaults to enforcing permissions.
    """
    return risk_class is None or risk_class == "meta"


def _transport_for_risk_class(risk_class: str | None) -> str:
    """Safe ``default_transport`` for a ``risk_class``.

    ``meta`` (and the no-profile fallback) stay on the host worktree; every
    product/untrusted class defaults to the isolating ``sandcastle`` transport.
    """
    if risk_class is None or risk_class == "meta":
        return DEFAULT_TRANSPORT
    return "sandcastle"


async def get_skip_permissions(session, project_key: str) -> bool:
    row = await session.get(KanbanMeta, SKIP_PERMISSIONS_PREFIX + project_key)
    if row is not None:
        return row.value == "1"  # explicit per-project override wins
    # No explicit override: consult the project's security profile. A
    # product/untrusted risk_class enforces permissions (skip=False); a meta
    # project (or no profile at all) keeps the historical bypass.
    return _skip_permissions_for_risk_class(await _project_risk_class(project_key))


async def set_skip_permissions(session, project_key: str, enabled: bool) -> None:
    key = SKIP_PERMISSIONS_PREFIX + project_key
    row = await session.get(KanbanMeta, key)
    if row is None:
        row = KanbanMeta(key=key, value="1" if enabled else "0")
        session.add(row)
    else:
        row.value = "1" if enabled else "0"
    await session.flush()
    await _record_audit(
        session,
        kind="skip_permissions_flip",
        project_key=project_key,
        payload_ref={"enabled": enabled},
    )


async def get_default_transport(session, project_key: str) -> str:
    row = await session.get(KanbanMeta, TRANSPORT_PREFIX + project_key)
    if row and row.value in TRANSPORTS:
        return row.value  # explicit per-project override wins
    # No explicit override: let the project's risk_class pick the transport
    # (product/untrusted -> sandcastle, meta/none -> worktree).
    return _transport_for_risk_class(await _project_risk_class(project_key))


async def set_default_transport(session, project_key: str, value: str) -> None:
    if value not in TRANSPORTS:
        raise ValueError(f"unknown transport: {value}")
    key = TRANSPORT_PREFIX + project_key
    row = await session.get(KanbanMeta, key)
    before = row.value if row is not None else None
    if row is None:
        session.add(KanbanMeta(key=key, value=value))
    else:
        row.value = value
    await session.flush()
    await _sync_sandcastle_enabled(project_key, value == "sandcastle")
    await _record_audit(
        session,
        kind="transport_change",
        project_key=project_key,
        payload_ref={"before": before, "after": value},
    )


# ---- model options: device-local cache of `claude -p "/model"`'s alias list ----

MODEL_OPTIONS_KEY = "model_options:claude-code"
MODEL_OPTIONS_SEED = ("sonnet", "opus", "haiku")


def _parse_model_options(output: str) -> list[str]:
    """Parse `claude -p "/model"` stdout into the list of available aliases.

    Real output (Claude Code 2.1.206, verified 2026-07-10):
        Current model: Sonnet 5 (default)
        Usage: /model <name>. Available: sonnet, opus, haiku, fable, best,
        sonnet[1m], opus[1m], fable[1m], opusplan, default, or a full model ID.

    The trailing "or a full model ID" clause is dropped -- it isn't an alias,
    it's a note that any string is accepted. Returns [] if the "Available: "
    marker isn't found (unexpected CLI output shape) rather than raising --
    callers fall back to the cached/seed list.
    """
    marker = "Available: "
    idx = output.find(marker)
    if idx == -1:
        return []
    tail = " ".join(output[idx + len(marker):].split())
    items = [s.strip() for s in tail.split(",")]
    return [s for s in items if s and "full model ID" not in s]


def refresh_claude_model_options_sync() -> list[str]:
    """Run `claude -p "/model"` and parse the available model aliases.

    Synchronous subprocess.run: a short-lived, one-shot CLI query, not a
    spawned session -- no worktree, no tmux. Raises subprocess.SubprocessError
    or OSError (e.g. `claude` not on PATH) on failure; callers decide whether
    to surface that or fall back to the cache.
    """
    result = subprocess.run(
        ["claude", "-p", "/model"],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return _parse_model_options(result.stdout)


async def refresh_claude_model_options(session) -> list[str]:
    """Refresh and cache the model-options list. An empty parse result is
    returned as-is but does NOT overwrite a previously cached non-empty list
    -- a transient CLI output-shape hiccup shouldn't wipe out a known-good
    cache."""
    import asyncio
    options = await asyncio.to_thread(refresh_claude_model_options_sync)
    if options:
        await _set_model_options_cache(session, options)
    return options


async def get_cached_model_options(session) -> list[str]:
    row = await session.get(KanbanMeta, MODEL_OPTIONS_KEY)
    if row is None:
        return list(MODEL_OPTIONS_SEED)
    try:
        options = json.loads(row.value)
    except (TypeError, ValueError):
        return list(MODEL_OPTIONS_SEED)
    return options if options else list(MODEL_OPTIONS_SEED)


async def _set_model_options_cache(session, options: list[str]) -> None:
    value = json.dumps(options)
    row = await session.get(KanbanMeta, MODEL_OPTIONS_KEY)
    if row is None:
        session.add(KanbanMeta(key=MODEL_OPTIONS_KEY, value=value))
    else:
        row.value = value
    await session.flush()


# ---- MiniMax model discovery ----------------------------------------------
#
# MiniMax exposes its models through the same Anthropic-compatible CLI as
# Anthropic itself (only ANTHROPIC_BASE_URL differs — see provider_env.py).
# There is no `claude -p "/model"`-equivalent for MiniMax: the model picker
# must be populated from somewhere else. The signal we DO have is the JSONL
# usage logs: every dispatched session writes a `message.model` line, and
# the prefix `minimax-` (see subscriptions/attribution.py) tags a row as
# MiniMax. The set of unique values we've actually seen IS the de-facto
# catalog of MiniMax models on this machine — same honesty posture as
# subscriptions/base.py ("no fabrication", no guessing from a hardcoded
# list). The seed (``MINIMAX_DEFAULT_MODEL``) covers the "first deploy,
# never seen anything yet" case so the datalist is never empty.
MINIMAX_MODEL_OPTIONS_KEY = "model_options:minimax"
MINIMAX_MODEL_OPTIONS_SEED = ("MiniMax-M3",)


def _discover_minimax_models_sync_glob() -> list[str]:
    """Glob ``~/.claude/projects/**/*.jsonl`` into a list of paths.

    Module-level so tests can monkeypatch the path list without touching
    the real filesystem (the production scanner walks whatever this
    returns, in mtime-descending order — see
    ``_discover_minimax_models_sync``).
    """
    import glob
    import os as _os

    pattern = _os.path.join(
        _os.path.expanduser("~"), ".claude", "projects", "*", "*.jsonl",
    )
    return sorted(glob.glob(pattern), key=_os.path.getmtime, reverse=True)


def _discover_minimax_models_sync() -> list[str]:
    """Scan ``~/.claude/projects/**/*.jsonl`` for unique MiniMax model ids.

    Mirrors the claude-code discovery path: a fresh subscription that has
    never spawned leaves no JSONL rows, so we fall back to the seed (and
    return the seed alone, not a stale empty list). Order is
    most-recently-seen first; duplicates are dropped.

    The path list comes from ``_discover_minimax_models_sync_glob`` so
    tests can patch the glob without touching the real filesystem.
    """
    seen_order: list[str] = []
    seen_set: set[str] = set()
    for path in _discover_minimax_models_sync_glob():
        try:
            entries = _sync_parse_usage_from_jsonl(path)
        except (OSError, ValueError):
            continue
        for entry in entries:
            model = (entry.get("model") or "").strip()
            if not model or model in seen_set:
                continue
            normalized = model.lower()
            if not normalized.startswith("minimax-"):
                continue
            seen_set.add(model)
            seen_order.append(model)
    return seen_order if seen_order else list(MINIMAX_MODEL_OPTIONS_SEED)


def _sync_parse_usage_from_jsonl(path: str) -> list[dict]:
    """Synchronous JSONL scan for the minimax-model-discovery path.

    Mirrors the assistant-message filter from
    ``UsageService.parse_usage_from_jsonl`` but only returns rows that
    actually carry a model string — we don't care about token counts
    here, just the unique model names. Imported lazily so the existing
    async usage path isn't disturbed by an extra import.
    """
    import json as _json

    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = _json.loads(line)
                except ValueError:
                    continue
                if obj.get("type") != "assistant":
                    continue
                model = obj.get("message", {}).get("model")
                if model:
                    rows.append({"model": model})
    except OSError:
        return rows
    return rows


async def refresh_minimax_model_options(session) -> list[str]:
    """Refresh and cache the MiniMax model list from JSONL logs."""
    import asyncio
    options = await asyncio.to_thread(_discover_minimax_models_sync)
    if options:
        await _set_minimax_model_options_cache(session, options)
    return options


async def get_cached_minimax_model_options(session) -> list[str]:
    """Return the cached MiniMax model list (seed if never refreshed)."""
    row = await session.get(KanbanMeta, MINIMAX_MODEL_OPTIONS_KEY)
    if row is None:
        return list(MINIMAX_MODEL_OPTIONS_SEED)
    try:
        options = json.loads(row.value)
    except (TypeError, ValueError):
        return list(MINIMAX_MODEL_OPTIONS_SEED)
    return options if options else list(MINIMAX_MODEL_OPTIONS_SEED)


async def _set_minimax_model_options_cache(session, options: list[str]) -> None:
    value = json.dumps(options)
    row = await session.get(KanbanMeta, MINIMAX_MODEL_OPTIONS_KEY)
    if row is None:
        session.add(KanbanMeta(key=MINIMAX_MODEL_OPTIONS_KEY, value=value))
    else:
        row.value = value
    await session.flush()


async def _sync_sandcastle_enabled(project_key: str, enabled: bool) -> None:
    """Keep SandcastleConfig.enabled aligned with the project's default transport so
    the two never drift. Resolves the project path from the registry; no-op if the
    project isn't locally registered."""
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.database import Project
    from app.models.sandcastle import SandcastleConfig
    from app.services.sandcastle_service import _pick_default_sandbox_provider, sandcastle_service

    try:
        async with AsyncSessionLocal() as db:
            paths = (await db.execute(select(Project.path))).scalars().all()
            target = next(
                (p for p in paths if safe_resolve_project_key(p) == project_key), None
            )
            if target is None:
                return
            cfg = (await db.execute(
                select(SandcastleConfig).where(SandcastleConfig.project_path == target)
            )).scalar_one_or_none()
            if cfg is None:
                if enabled:
                    # Model default is "no-sandbox", but choosing the sandcastle
                    # transport means the user wants container isolation — pick a
                    # real runtime when the host actually has one.
                    provider = _pick_default_sandbox_provider(await sandcastle_service.check_health())
                    db.add(SandcastleConfig(project_path=target, enabled=True, sandbox_provider=provider))
                    await db.commit()
                return
            if cfg.enabled != enabled:
                cfg.enabled = enabled
                await db.commit()
    except Exception:
        logger.exception("failed to sync sandcastle enabled for %s", project_key)


async def get_ship_mode(session, project_key: str) -> str:
    row = await session.get(KanbanMeta, SHIPMODE_PREFIX + project_key)
    if row and row.value in SHIP_MODES:
        return row.value
    return DEFAULT_SHIP_MODE


async def set_ship_mode(session, project_key: str, mode: str) -> None:
    if mode not in SHIP_MODES:
        raise ValueError(f"unknown ship mode: {mode}")
    key = SHIPMODE_PREFIX + project_key
    row = await session.get(KanbanMeta, key)
    if row is None:
        session.add(KanbanMeta(key=key, value=mode))
    else:
        row.value = mode
    await session.flush()


# ---- active subscription override (fase 0 / quick win) -------------------
#
# A single board-wide pin that routes ALL auto-dispatch onto one subscription,
# regardless of per-column or per-card defaults. Stored in KanbanMeta so the
# existing key-value table carries it without a schema migration. Precedence
# (in _run_card): override > card.column_overrides[col] > column.default_*.
# `None` = exactly today's behaviour — see test_active_subscription_override.

SUBSCRIPTION_OVERRIDE_PREFIX = "subscription_override:"
# ``anthropic-compatible`` is accepted here, but only when the override
# carries a resolvable ``endpoint_name`` — see ``set_active_subscription_override``
# for the fail-fast check. The allow-list mirrors the storage shape of
# ``subscription_pool._ALLOWED_POOL_PROVIDERS`` so both pin-shape knobs share
# the same source of truth (kaart 293d1faa…).
_ALLOWED_OVERRIDE_PROVIDERS = (
    PROVIDER_ANTHROPIC, PROVIDER_BEDROCK, PROVIDER_MINIMAX,
    PROVIDER_COMPATIBLE,
)


async def get_active_subscription_override(
    session, project_key: str,
) -> dict | None:
    """Return the board-wide subscription override for `project_key`, or None.

    Shape when set::

        {"provider": "anthropic|bedrock|minimax|anthropic-compatible",
         "model": str|None,
         "endpoint_name": str|None}

    ``model`` is optional — an override that pins only the provider leaves the
    model to fall through to the column default / card model / persona
    frontmatter chain (same shape as a partial column-override, just one level
    higher in the precedence). ``endpoint_name`` is required when ``provider``
    is ``"anthropic-compatible"``; see ``set_active_subscription_override``
    for the storage-side fail-fast check and ``_resolve_compatible_endpoint``
    for the dispatch-side helper that turns the name into ``base_url`` +
    credential.

    Earlier rows (pre-kaart-293d1faa…) without ``endpoint_name`` round-trip
    with that key as ``None`` so legacy overrides keep working unchanged."""
    row = await session.get(
        KanbanMeta, SUBSCRIPTION_OVERRIDE_PREFIX + project_key,
    )
    if row is None:
        return None
    try:
        parsed = json.loads(row.value)
    except (TypeError, ValueError):
        # Corrupt row — treat as no override rather than wedging dispatch.
        logger.warning(
            "corrupt subscription_override row for %s; ignoring", project_key,
        )
        return None
    if not isinstance(parsed, dict):
        return None
    provider = parsed.get("provider")
    if provider not in _ALLOWED_OVERRIDE_PROVIDERS:
        return None
    model = parsed.get("model")
    endpoint_name = parsed.get("endpoint_name")
    return {
        "provider": provider,
        "model": model if isinstance(model, str) else None,
        "endpoint_name": endpoint_name if isinstance(endpoint_name, str) else None,
    }


async def set_active_subscription_override(
    session, project_key: str, override: dict | None,
) -> None:
    """Persist (or clear, when `None`) the board-wide subscription override.

    ``override`` is validated against the allow-list before storage; an
    unknown provider raises ValueError so the caller surfaces a 422 instead
    of writing a row that the dispatcher would then refuse to honour.

    When the provider is ``"anthropic-compatible"`` a non-empty ``endpoint_name``
    is required, and that name must resolve to a row in the project's endpoint
    registry (``agents.endpoints.list_endpoints``). The check happens here so
    a board-wide pin that the dispatcher can't honour never lands on disk;
    the same fail-fast is mirrored on the pool side
    (``subscription_pool._validate_entries``) and on the per-card
    ``column_overrides`` write path.

    Storing `None` deletes the row entirely so a follow-up read sees no
    override and falls through to the column-default precedence — keeping
    the "unset = exact pre-feature behaviour" contract testable."""
    key = SUBSCRIPTION_OVERRIDE_PREFIX + project_key
    if override is None:
        row = await session.get(KanbanMeta, key)
        if row is not None:
            await session.delete(row)
            await session.flush()
        return
    provider = override.get("provider")
    if provider not in _ALLOWED_OVERRIDE_PROVIDERS:
        raise ValueError(
            f"unknown provider: {provider!r}; "
            f"expected one of {_ALLOWED_OVERRIDE_PROVIDERS}",
        )
    model = override.get("model")
    if model is not None and not isinstance(model, str):
        raise ValueError("override.model must be a string or null")
    endpoint_name = override.get("endpoint_name")
    if endpoint_name is not None and not isinstance(endpoint_name, str):
        raise ValueError("override.endpoint_name must be a string or null")
    if provider == PROVIDER_COMPATIBLE:
        if not endpoint_name:
            raise ValueError(
                "anthropic-compatible provider requires a non-empty endpoint_name; "
                "configure one via /api/v1/agent-bridge/platforms/endpoints",
            )
        # Fail-fast at write time: an endpoint_name the backend can't
        # resolve to a base_url/auth_token is precisely the
        # ``ValueError in build_provider_env`` failure mode the dispatcher
        # used to hit three times before the card landed in Impediment
        # (kaart 293d1faa…). The endpoint lookup uses the same helper
        # dispatch does, so the validation here cannot drift from the
        # resolution path at run time.
        from app.services.agentic_cli.endpoints import (
            get_endpoint as _get_endpoint,
        )
        from app.services.agentic_cli.endpoints import (
            resolve_compatible_endpoint as _resolve_compatible_endpoint,
        )
        endpoint = await _get_endpoint(session, project_key, endpoint_name)
        if endpoint is None:
            raise ValueError(
                f"endpoint {endpoint_name!r} is not registered for project "
                f"{project_key!r}; configure it via "
                f"/api/v1/agent-bridge/platforms/endpoints",
            )
        # kaart 27317b4871… (FCR gap 5): a registered endpoint can
        # still fail to resolve at dispatch time when its
        # ``credential_name`` points at a missing key
        # (``settings.minimax_api_key`` empty). The earlier endpoint-
        # existence check above only confirms the row is there; the
        # resolver also runs the credential-resolution path so this
        # override save surfaces the missing-key message immediately
        # instead of letting it leak into the dispatch loop's
        # ``MAX_DISPATCH_FAILURES`` cycle.
        await _resolve_compatible_endpoint(
            session, project_key, endpoint_name,
        )
    value = json.dumps({
        "provider": provider,
        "model": model if model else None,
        "endpoint_name": endpoint_name if endpoint_name else None,
    })
    row = await session.get(KanbanMeta, key)
    if row is None:
        session.add(KanbanMeta(key=key, value=value))
    else:
        row.value = value
    await session.flush()


# ---- subscription pool (fase 1b) -------------------------------------------
#
# A geordende pool van subscriptions met per-subscription drempels, gepickt
# bij dispatch op basis van de per-subscription usage-snapshot (analyse
# §4 / §5). De pure logica leeft in `app.kanban.subscription_pool`; dit
# stuk verzamelt de snapshot-inputs (paused providers + per-subscription
# usage) en geeft het gekozen ``PoolEntry`` terug aan ``_run_card``.
#
# The wiring is intentionally defensive:
# - Providers that aren't wired (no concrete SubscriptionUsageProvider for
#   the entry's {cli, provider}) contribute NO snapshot. The router
#   treats missing snapshots as "no signal — always available" (analyse
#   §6.3) — the per-provider pause is what gates the spawn downstream.
# - A provider that raises during ``get_usage()`` is silently dropped
#   from the snapshot map (the same defensive shape as the per-provider
#   pause's read-only listing helper). The dispatcher must NEVER wedge
#   on a flaky usage source.


async def _gather_pool_usage_snapshots(
    entries: list[PoolEntry],
) -> dict[str, SubscriptionUsage]:
    """Return ``{subscription_id: SubscriptionUsage}`` for the providers
    that have a concrete ``SubscriptionUsageProvider`` registered.

    Subscription identity follows analyse §3 (``{cli, provider}``); the
    lookup mirrors ``SubscriptionUsageProvider.DEFAULT_ID`` so the
    router's snapshot map aligns with the snapshot's own ``id`` field.
    Missing snapshots (or snapshots that raise) are simply absent from
    the returned dict — ``pick_subscription_for_cli`` interprets
    absent as "no signal → available", which is exactly analyse §6.3.

    Kaart 8f40d443…: each ``PoolEntry`` carries its own ``cli`` (the
    per-CLI quota axis). The registry lookup uses that entry-level
    ``cli`` so an OpenCode entry looks up ``open-code:{provider}`` and
    a Codex entry looks up ``codex-cli:{provider}`` — quotas are
    orthogonal across CLIs.
    """
    from app.services.subscriptions import registry as _registry

    snapshots: dict[str, SubscriptionUsage] = {}
    for entry in entries:
        # ``get_provider_for`` is a synchronous dict lookup — awaiting it
        # raises ``TypeError: object … can't be used in 'await' expression``,
        # which the surrounding ``except Exception`` in ``_pick_pool_choice``
        # silently swallows → empty snapshot map → drempel branch is dead
        # code. Drop the ``await``. See kanban card ea7e038b… (D1).
        provider = _registry.get_provider_for(
            cli=entry.resolved_cli, provider=entry.provider,
        )
        if provider is None:
            continue
        try:
            snap = await provider.get_usage()
        except Exception:
            logger.exception(
                "subscription pool: %s raised in get_usage(); "
                "treating as 'no signal'",
                getattr(provider, "id", "<unknown>"),
            )
            continue
        snapshots[snap.subscription_id] = snap
    return snapshots


# ---- endpoint reachability probe (kaart 424c23d4…) -------------------------
#
# When an ``anthropic-compatible`` pool entry's resolved endpoint URL is
# unreachable, the pool router must treat the provider as paused so the
# pool picks the next entry instead of looping through
# ``MAX_DISPATCH_FAILURES`` on a dead proxy. Explicit pins on the same
# provider (column_overrides / per-card provider pin) stay fail-closed —
# they walk the existing spawn-failure path because
# ``resolve_effective_provider_and_model`` does NOT consult
# ``_paused_providers_for_pool`` on its higher layers (see
# ``_pick_pool_choice`` as the only entry point that does).
#
# The probe result is cached per ``(project_key, endpoint_name)`` for
# ``_ENDPOINT_PROBE_TTL_S`` so the dispatch hot path issues at most one
# HTTP call per dispatch tick per endpoint, not one per card. ``False``
# (unreachable) keeps the provider in the paused set; ``True`` (or
# probe failure — see _probe_endpoint_reachable) keeps it available.

_ENDPOINT_PROBE_TTL_S = 30.0
_ENDPOINT_PROBE_HTTP_TIMEOUT_S = 2.0

# Per-endpoint probe verdict cache. ``(project_key, endpoint_name)`` →
# ``(reachable, expires_at_monotonic)``. Process-local — the window is
# too short for cross-process coordination to be worth a global lock,
# and a falsified verdict on another process is far cheaper than
# gating dispatch on it. Tests clear this in their fixture.
_endpoint_reach_cache: dict[tuple[str, str], tuple[bool, float]] = {}


async def _probe_endpoint_reachable(base_url: str) -> bool:
    """Return ``True`` iff ``base_url`` answers *some* HTTP response.

    Public seam with the wrapper contract: any exception inside the
    raw probe is swallowed and translated to ``True`` ("available"),
    per the acceptance criteria ("bij twijfel geldt de provider als
    beschikbaar, zodat een kapotte probe geen werkende lane platlegt").
    Tests patch the inner ``_endpoint_probe_uncached`` so they exercise
    the fail-soft contract without a real network.

    Empty/``None`` URLs return ``True`` — "no signal → available",
    matching the analyse §6.3 contract every other probe in dispatch
    honours.
    """
    if not base_url:
        return True
    try:
        return await _endpoint_probe_uncached(base_url)
    except Exception:
        return True


async def _endpoint_probe_uncached(base_url: str) -> bool:
    """Inner probe: raw ``GET {base_url}`` with no auth header. Tests
    patch this (NOT the wrapper) so the wrapper's exception-swallow
    contract stays exercised end-to-end.

    Accepts any status < 600 as "reachable" — a 401 means the proxy is
    up but credentials are wrong, which is a *different* failure mode
    that the spawn/credential layer already captures. Network-level
    failures (timeout, DNS, connection refused) propagate as
    ``httpx.RequestError`` or ``OSError`` to the wrapper.
    """
    async with httpx.AsyncClient(
        timeout=_ENDPOINT_PROBE_HTTP_TIMEOUT_S,
    ) as client:
        response = await client.get(base_url)
        return response.status_code < 600


async def _unreachable_compatible_providers(
    session, *, project_key: str,
) -> set[str]:
    """Return the set of pool-provider names whose
    ``anthropic-compatible`` endpoint fails the reachability probe.

    Walks the project's pool, resolves each compatible entry's
    ``endpoint_name`` to its ``base_url`` via the project-scoped
    endpoint registry, and probes the URL. ``False`` adds the provider
    to the returned set; the pool router's hard-skip semantics then
    keep that provider off the dispatch path until a subsequent
    probe finds it reachable again (cache TTL ``_ENDPOINT_PROBE_TTL_S``).

    Probe or registry failures (timeout, DNS, missing endpoint row)
    resolve to "available" via ``_probe_endpoint_reachable``'s
    fail-soft contract — they do NOT add the provider to the paused
    set.
    """
    from app.services.agentic_cli.endpoints import (
        get_endpoint as _get_endpoint,
    )
    entries = await subscription_pool.get_subscription_pool(
        session, project_key,
    )
    if not entries:
        return set()
    paused: set[str] = set()
    seen: set[str] = set()
    now = time.monotonic()
    for entry in entries:
        if entry.provider != PROVIDER_COMPATIBLE:
            continue
        if not entry.endpoint_name:
            # No endpoint to probe — skip (per validator this should not
            # happen for a stored entry, but be defensive for legacy /
            # hand-edited rows).
            continue
        if entry.endpoint_name in seen:
            # Same endpoint under multiple entries — one probe covers all.
            continue
        seen.add(entry.endpoint_name)
        cache_key = (project_key, entry.endpoint_name)
        cached = _endpoint_reach_cache.get(cache_key)
        if cached is not None:
            reachable, expires_at = cached
            if expires_at > now:
                if not reachable:
                    paused.add(entry.provider)
                continue
        try:
            endpoint = await _get_endpoint(
                session, project_key, entry.endpoint_name,
            )
        except Exception:
            # Endpoint lookup itself failed (DB / serialiser drift) —
            # fall through and treat as "no signal → available". The
            # next tick can retry; surfacing a louder error here would
            # only wedge the dispatch tick on a registry hiccup.
            logger.exception(
                "subscription pool: endpoint lookup raised for %s/%s; "
                "treating as available",
                project_key, entry.endpoint_name,
            )
            continue
        if endpoint is None:
            # Endpoint row missing — skip. The provider is "registered
            # nowhere → no signal", per the same §6.3 contract.
            continue
        reachable = await _probe_endpoint_reachable(endpoint.base_url)
        _endpoint_reach_cache[cache_key] = (reachable, now + _ENDPOINT_PROBE_TTL_S)
        if not reachable:
            paused.add(entry.provider)
            logger.info(
                "subscription pool: anthropic-compatible endpoint "
                "%s/%s unreachable — pausing provider %r until next "
                "probe (TTL %.1fs)",
                project_key, entry.endpoint_name, entry.provider,
                _ENDPOINT_PROBE_TTL_S,
            )
    return paused


async def _paused_providers_for_pool(
    session, *, project_key: str,
) -> set[str]:
    """Return the set of providers whose per-provider pause is currently
    active. Three sources are merged:

    1. The time-based per-provider pause (``dispatch_pause.list_paused_providers``)
    2. The operator's manual pause list (``list_manually_paused_providers``,
       merged in by ``_pick_pool_choice`` itself)
    3. The endpoint-reachability probe for ``anthropic-compatible`` pool
       entries (kaart 424c23d4…): a dead proxy pauses the provider so
       the pool picks the next entry instead of looping on a dead host.

    Callers (pool picker, spillover gate) pass the project_key they are
    routing for. The probe layer is process-local + TTL-cached so the
    hot path issues at most one HTTP call per dispatch tick per
    endpoint, never one per card.
    """
    from app.kanban import dispatch_pause
    paused = await dispatch_pause.list_paused_providers(session)
    base_paused = {p for p in paused if p}
    reachable_paused = await _unreachable_compatible_providers(
        session, project_key=project_key,
    )
    return base_paused | reachable_paused


async def _pick_pool_choice(
    session, entries: list[PoolEntry], *, project_key: str, cli_id: str,
) -> PoolEntry | None:
    """Run the pure ``pick_subscription_for_cli`` against the live usage
    snapshot map and the currently-paused providers, scoped to the
    dispatched CLI.

    Kaart 8f40d443…: the pool router is called with the dispatched
    ``cli_id`` (resolved earlier in this dispatcher by ``_phase_cli_id``
    + ``_run_card``) so an OpenCode-spawned session picks from
    OpenCode-only pool entries and never silently degrades to a
    claude-code quota. Returns the chosen ``PoolEntry`` or ``None``
    when the pool has no entry for ``cli_id`` — the dispatch path then
    falls through to the column-default chain (matching the acceptance
    criterion 'geen entry voor deze CLI').

    A failure inside the live-snapshot gathering falls back to "no
    signal → first entry of cli_id wins" so a flaky usage provider
    cannot block dispatch.
    """
    paused = await _paused_providers_for_pool(session, project_key=project_key)
    # Kaart f056b2888a…: merge in the operator's manual pause list. The pool
    # router treats ``paused_providers`` as a hard skip set; if we don't add
    # the manual slots here, a pool that contains only manually-paused
    # entries can still pick one and route the spawn through it — bypassing
    # the operator toggle. FCR-blokkade: paused = time-based only.
    from app.kanban.dispatch_pause import list_manually_paused_providers
    manually_paused = await list_manually_paused_providers(session)
    if manually_paused:
        paused = sorted(set(paused) | set(manually_paused))
    try:
        snapshots = await _gather_pool_usage_snapshots(entries)
    except Exception:
        # ``_gather_pool_usage_snapshots`` already swallows per-provider
        # failures; this is a belt-and-braces guard against an unexpected
        # error in the registry wiring itself. Treat as no-signal so the
        # dispatch path falls through to the first pool entry rather than
        # crashing the spawn loop.
        logger.exception(
            "subscription pool: usage-snapshot gather failed for %s; "
            "falling back to 'no signal' (first pool entry wins)",
            project_key,
        )
        snapshots = {}
    return subscription_pool.pick_subscription_for_cli(
        entries, snapshots,
        paused_providers=paused, cli_id=cli_id,
    )


async def _pool_spillover_available(
    session, *, project_key: str, limited_provider: str, cli_id: str,
    column: str | None = None,
) -> bool:
    """Fase 2 (analyse §4 Optie B / §5): can the just-limited card spill
    over onto another subscription in the pool instead of waiting for
    ``limited_provider`` to reset?

    Threshold-/failover branch of the fase-1b pool router: mark
    ``limited_provider`` (plus every already-paused provider) as
    unavailable, then ask ``has_available_spillover`` whether the pool
    still offers a genuinely-available subscription to route to. Returns
    False when the project has no pool, when the pool has no entry for
    ``cli_id``, or when every subscription for ``cli_id`` is now
    paused/exhausted — the reactive limit path then keeps its existing
    behaviour (per-provider pause → "To Resume" + reset-time scheduled_at).

    Kaart 8f40d443…: the spillover check is now CLI-aware — the reactive
    limit path supplies the dispatched ``cli_id`` so the drempel-/failover
    branch only considers entries of the same CLI as the spawned session
    (an OpenCode-limit only spills to other OpenCode entries).

    ``limited_provider`` is added explicitly (not only read from the
    per-provider pause slots) so the decision is correct even when the
    caller sets the pause *after* this check — the just-hit provider is
    unavailable regardless of write ordering.
    """
    entries = await get_subscription_pool(session, project_key, column=column)
    if not entries:
        return False
    paused = await _paused_providers_for_pool(session, project_key=project_key)
    paused.add(limited_provider)
    try:
        snapshots = await _gather_pool_usage_snapshots(entries)
    except Exception:
        # Same defensive posture as ``_pick_pool_choice``: a flaky usage
        # provider must never wedge the limit-handling path. No signal =
        # analyse §6.3 (available until the pause catches it).
        logger.exception(
            "subscription pool: spillover snapshot gather failed for %s; "
            "treating as 'no signal'",
            project_key,
        )
        snapshots = {}
    return subscription_pool.has_available_spillover(
        entries, snapshots,
        paused_providers=paused, cli_id=cli_id,
    )


# ---- persona helpers -------------------------------------------------------

_PERSONA_BY_COLUMN = {}  # Dynamic - loaded from project agents


def _persona_filename(column: str) -> str | None:
    """Get the persona filename for a column. For agent columns, the column name IS the agent name."""
    # Fixed columns (incl. `intake`, added for the inceptie-pipeline — kanban
    # card c33b2f14) don't have personas. Reading from `COLUMNS` instead of a
    # hard-coded tuple means adding a new fixed column is one edit (schemas.py)
    # not two.
    from app.kanban.schemas import COLUMNS
    if column in COLUMNS:
        return None
    # Agent columns: column name matches agent filename
    return f"{column}.md"


def _resolve_agent_from_persona(persona: str | None) -> str | None:
    """Extract agent name from persona filename (e.g., 'developer.md' -> 'developer')."""
    if persona and persona.endswith(".md"):
        return persona[:-3]
    return None


def _resolve_analyst_persona(project_path: str) -> str:
    """Persona body for the analyst phase.

    Prefers `.claude/agents/analyst.md` in the project (so a user can tune the
    analyst role locally); falls back to the hardcoded `ANALYST_PROMPT` in
    `analyst_prompt.py` when no project-local file exists, or when it exists
    but is empty (only frontmatter). Without this fallback, an analyst
    session gets an empty preamble.

    The analyst persona runs in one of two modi — see the kanban card
    c2b478ca396a473287aa0c04a79890e2 for the rationale:

    - **Modus 1 — multi-agent decompositie** (default, `analyst_agent_id`
      set on the card): the persona is a planner; it splits the card into
      child cards with `depends_on`, writes a plan-attachment, and moves
      the parent to Done. Implementing is the executor's job.
    - **Modus 2 — leaf design-deliverable** (`work_type='analysis'` or
      `card.agent='analyst'` without `analyst_agent_id`): the persona is
      the implementer; it writes a single design-doc / prototype,
      commits, ships to master, and moves THIS card to Done. No child
      cards.

    The persona body itself (both this fallback and the project-local
    `.claude/agents/analyst.md`) self-scopes both modi — the "Verboden"
    prohibitions are explicitly marked as modus-1-only and the "Leaf
    design-deliverable" section states the full modus-2 contract. Dispatch
    does not inject any additional override text on top of it (removed in
    kanban card fbe7937e99484941b196bf2ebc0866f6 — the previous per-dispatch
    override had grown mostly redundant with the persona's own framing).
    """
    from app.kanban.analyst_prompt import ANALYST_PROMPT
    project_body = _read_persona_file(project_path, "analyst.md")
    if project_body:
        return project_body
    return ANALYST_PROMPT.strip()


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + len("\n---\n"):]
    return text


def _read_persona(project_path: str, column: str) -> str | None:
    filename = _persona_filename(column)
    if not filename:
        return None
    return _read_persona_file(project_path, filename)


def _read_persona_file(project_path: str, filename: str) -> str | None:
    path = Path(project_path) / ".claude" / "agents" / filename
    try:
        return _strip_frontmatter(path.read_text()).strip()
    except OSError:
        return None


def _read_persona_model(project_path: str, filename: str) -> str | None:
    """Read the `model:` field from a persona file's YAML frontmatter, if any.

    Complements `_read_persona_file`, which strips this exact frontmatter
    block before the persona body reaches the prompt (see `_strip_frontmatter`)
    -- today that `model:` field (already present in engineer.md/analyst.md)
    is silently discarded. This is the read that makes it a real fallback in
    the model-resolution precedence. Never raises: a missing file, absent
    frontmatter, missing `model` key, or malformed YAML all resolve to None,
    which falls through to the next precedence level.
    """
    path = Path(project_path) / ".claude" / "agents" / filename
    try:
        text = path.read_text()
    except OSError:
        return None
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    try:
        frontmatter = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return None
    if not isinstance(frontmatter, dict):
        return None
    model = frontmatter.get("model")
    return model if isinstance(model, str) and model else None


def _effective_model(override_model: str | None, card_model: str | None,
                     column_default_model: str | None,
                     persona_model: str | None,
                     provider: str | None = None,
                     column_default_provider: str | None = None,
                     provider_pinned_by_higher_layer: bool = False) -> str | None:
    """Precedence: per-column override (`card.column_overrides[<target>].model`)
    > card.model > column.default_model > persona frontmatter `model:` > None
    (no --model flag, provider default applies). The per-column override is the
    most explicit intent the card author can express for a specific agent
    column, so it wins over the card-global `model`. Empty strings are treated
    as unset, same as None.

    Two of these layers are *provider-gated* — a model alias only carries
    through when it is native to the provider the spawn will actually run on:

    - **persona frontmatter** (`opus`, …) is an Anthropic-subscription alias
      written before per-column provider selection existed. It only applies for
      Anthropic (or when `provider` is unknown/None).

    - **column.default_model** is native to the column's *own* provider
      (`column_default_provider`, or the Anthropic chain-default when the column
      names none). When a HIGHER layer — `global_override` or a
      pool/spillover choice, flagged via `provider_pinned_by_higher_layer` —
      pins the spawn to a provider that *differs* from `column_default_provider`,
      the column model alias is meaningless there (it would be passed as
      `--model opus` / `ANTHROPIC_MODEL=opus` and wrongly override the
      provider-native default), so it falls through. When provider and model
      came from the same column layer (no higher-layer pin, or the higher layer
      pinned the same provider) nothing changes. A per-card `column_override`
      *provider* does NOT trigger this drop — that path deliberately leaves the
      column model as a fallthrough.

    The explicit override/card models always win: those are deliberate authoring
    choices that may legitimately name a provider-native model."""
    column_fallback = column_default_model
    if (provider_pinned_by_higher_layer and column_default_model
            and provider != column_default_provider):
        column_fallback = None
    persona_fallback = persona_model if provider in (None, PROVIDER_ANTHROPIC) else None
    return override_model or card_model or column_fallback or persona_fallback or None


# Precedence sources, in dispatch order. Used to label where a resolved model
# (or provider) came from so the column-settings UI can tell the user why
# their selection isn't applied. Higher indices win over lower ones.
PRECEDENCE_GLOBAL_OVERRIDE = "global_override"
PRECEDENCE_POOL = "pool"
PRECEDENCE_COLUMN_OVERRIDE = "column_override"
PRECEDENCE_COLUMN_DEFAULT = "column_default"
PRECEDENCE_PERSONA = "persona"
PRECEDENCE_NONE = "none"


def _resolve_model_source(
    override_model: str | None,
    card_model: str | None,
    column_default_model: str | None,
    persona_model: str | None,
    provider: str | None = None,
    column_default_provider: str | None = None,
    provider_pinned_by_higher_layer: bool = False,
) -> str | None:
    """Return the precedence-level a resolved model came from.

    Mirrors `_effective_model` so the caller can render "Will use X
    (source: <level>)" beneath the model input — useful when the user
    picks one in the column-settings UI but a board-wide override or
    pool choice silently wins (kaart 1782fa43…).

    The two provider-gated fallbacks are honored identically to
    `_effective_model`: a persona `model:` alias on a non-Anthropic
    provider is not a real source, and a `column.default_model` alias is
    not a real source when a higher layer (`global_override`/pool) pinned
    a provider that differs from `column_default_provider` — in both cases
    the chain falls through past it. When the chain produces nothing this
    returns None (same shape as `_effective_model`).
    """
    if override_model:
        return PRECEDENCE_COLUMN_OVERRIDE
    if card_model:
        return "card_model"
    column_dropped = (
        provider_pinned_by_higher_layer and provider != column_default_provider
    )
    if column_default_model and not column_dropped:
        return PRECEDENCE_COLUMN_DEFAULT
    if persona_model and provider in (None, PROVIDER_ANTHROPIC):
        return PRECEDENCE_PERSONA
    return PRECEDENCE_NONE


async def resolve_effective_provider_and_model(
    session,
    *,
    project_key: str,
    target_agent: str,
    project_path: str,
    pick_pool: Callable[[list[PoolEntry]], Awaitable[PoolEntry | None]] | None,
    card_overrides: dict | None = None,
    card_model: str | None = None,
    cli_id: str = CLAUDE_CODE_CLI_ID,
) -> dict:
    """**CANONICAL**: "what provider/model would this card spawn on?"

    **Always call this function** — never re-walk the 5-layer chain ad-hoc
    — from any new dispatch-side gate, pool helper, per-card spawn
    decision, quota-accounting step, or cost-attribution hook. Re-walking
    even a *subset* of the chain is the bug class the manual-pause gate
    shipped with (kaart f056b2888a…, fix commit ``77f5c8c``): the early
    ``_provider_for_card`` helper only walked the last two layers and
    silently gated against the wrong provider. A new gate writer landing
    here should be able to spot this and the self-check in
    ``scripts/check-dispatch-resolver-usage.sh`` in ≤1 grep.

    The self-check greps ``backend/app/kanban/dispatch.py`` for direct
    reads of ``column_override[.get("provider"/.get("model")]``,
    direct calls of the column-default helpers (``get_column_default_provider``,
    ``get_column_default_model``), direct attribute access of the form
    ``column.default_provider`` / ``column.default_model``, and direct
    reads of the per-card ``model`` field (via ``getattr(card, "model"``)
    outside this function and outside any line annotated with a
    ``# resolver-bypass: <reason>`` justification — the bare sentinel
    with no reason text does NOT exempt a line. New ad-hoc lookups get
    flagged at PR time; the exemption is reserved for the handful of
    helpers that intentionally narrow the chain (e.g.
    ``_provider_for_card`` walking only the last two layers for the
    rate-limit/spillover path).

    Walk the kanban model-precedence chain once and return the resolved
    provider/model plus the precedence level each field came from.

    Single source of truth for the chain previously duplicated between
    ``dispatch_card`` and ``resolve_column_effective_model``. A future
    tweak to the precedence (new override layer, reorder, etc.) only
    has to be made here, and the pin-tests in
    ``test_kanban_column_effective_model.py`` +
    ``test_dispatch_card_chain_matches_resolver_chain`` catch any
    drift at both call sites (kaart 8da646d8…).

    Precedence (highest wins; first non-empty value is used):
      1. ``global_override`` (board-wide subscription pin)
      2. ``pool_choice``     (subscription-pool spillover-chain entry
                                 — see ``_build_spillover_candidates``)
      3. ``column_override`` (per-card override for ``target_agent``)
      4. ``column.default_provider`` / ``column.default_model``
      5. ``persona``         (engineer.md / analyst.md frontmatter
                                 ``model:`` only; gated on Anthropic
                                 unless the explicit overrides name a
                                 provider-native model)
      → ``PROVIDER_ANTHROPIC`` / ``None`` as the chain-end default.

    Spillover-keten (kaart 0172e94d…): sinds deze kaart is de pool geen
    routing-pin meer die élke kolom-default overruled, maar een
    spillover-*keten* met ``column.default_provider`` als impliciete
    eerste entry. De effectieve-kandidatenlijst die naar de router gaat
    is ``[kolom-default-head] ++ [pool-entries minus de matchende head]``;
    de head erft ``drempel`` en ``model`` van een eventuele matchende
    pool-entry (zie ``_build_spillover_candidates``). ``provider_source``
    is eerlijk: ``column_default`` wanneer de head wint, ``pool`` alleen
    bij een echte uitwijk naar de staart.

    Spec: this precedence chain is the canonical reference for the
    model-routing decision. The board-wide pin + pool layer are
    documented in ``docs/cockpit/subscription-flexibiliteit-analyse.md``
    and the per-card-overrides layer in
    ``docs/cockpit/kanban-model-override.md``; the column/persona
    fallback layers are codified in this function (and the
    ``_effective_model`` helper it calls). Any new dispatch-side gate
    that needs to know "what subscription would this card land on"
    must read the result of this function — not a partial re-derivation.

    Args:
      - ``pick_pool``: caller-supplied async pool picker. ``dispatch``
        passes a closure over ``_pick_pool_choice`` (live usage-aware,
        CLI-scoped); the column-settings UI passes a no-snapshot
        closure that returns the first pool entry for
        ``CLAUDE_CODE_CLI_ID`` (no spawn here, so the live router is
        irrelevant). Pass ``None`` to skip the pool layer entirely
        (used by tests that pin only the static precedence).
      - ``card_overrides``: per-card ``column_overrides[target_agent]``.
        The column-settings UI has no card context, so it passes
        ``None`` for ``card_overrides`` — the function-level
        ``column_override`` argument is preserved for backwards
        compatibility with the existing column-settings caller.
      - ``card_model``: per-card ``card.model`` — only the dispatch
        path owns this layer; the column-settings UI leaves it None.
      - ``cli_id``: the CLI this card will spawn under
        (``CLAUDE_CODE_CLI_ID`` / ``open-code`` / …). Used by
        ``_build_spillover_candidates`` to keep the synthetic head
        entry on the same CLI as the spawned session, so the router's
        cli-filter doesn't accidentally drop it. Must match the CLI
        the caller's ``pick_pool`` closure consults (the dispatcher
        threads both from ``_phase_cli_id``); defaults to
        ``CLAUDE_CODE_CLI_ID`` so direct callers without spawn context
        (notably column-settings) keep working unchanged.
    """
    column_override = card_overrides or {}
    override_provider = column_override.get("provider") or None
    override_model = column_override.get("model") or None
    global_override = await get_active_subscription_override(session, project_key)
    # Kaart b36ca702…: the per-column tail takes precedence over the
    # board-wide pool when one is configured for ``target_agent``.
    # ``get_subscription_pool(..., column=...)`` returns the column-
    # specific tail if present (including an explicit empty ``[]``),
    # or falls back to the board-wide pool otherwise. ``column=None``
    # preserves the legacy board-wide-only read (used by tests that
    # don't exercise column context).
    pool_entries = await get_subscription_pool(
        session, project_key, column=target_agent,
    )
    column_default_provider = await get_column_default_provider(session, project_key, target_agent)
    # Spillover-keten (kaart 0172e94d…): ``[kolom-default-head] ++
    # [pool-entries minus de matchende head]``. Wanneer er geen pool
    # is, bestaat de keten uit alleen de head; wanneer er geen
    # kolom-default is bestaat de head uit de eerste pool-entry (en
    # ``provider_source`` wordt hieronder eerlijk ``pool``).
    head_entry, chain_candidates = _build_spillover_candidates(
        column_default_provider=column_default_provider,
        pool=pool_entries or [],
        cli_id=cli_id,
    )
    pool_choice: PoolEntry | None = None
    if pool_entries is not None and not global_override and pick_pool is not None:
        # Geef de bestaande ``pick_pool``-closure dezelfde lijst die de
        # keten zou zien — de router kiest de head (kolom-default) of de
        # staart (pool-entry) volgens z'n bestaande drempel/pause-regels.
        pool_choice = await pick_pool(chain_candidates)
    provider = (
        (global_override or {}).get("provider")
        or (pool_choice.provider if pool_choice else None)
        or override_provider
        or column_default_provider
        or PROVIDER_ANTHROPIC
    )
    persona_model = _read_persona_model(project_path, f"{target_agent}.md")
    column_default_model = await get_column_default_model(session, project_key, target_agent)
    # Did a layer ABOVE the per-card column_override (i.e. global_override or the
    # subscription pool/spillover) pin the provider? That is the only trigger for
    # dropping a column.default_model alias that is native to a *different*
    # provider — a per-card column_override provider deliberately leaves the
    # column model as a fallthrough (see `_effective_model`).
    provider_pinned_by_higher_layer = bool(
        (global_override or {}).get("provider")
        or (pool_choice.provider if pool_choice else None)
    )
    effective_model_override = (
        (global_override or {}).get("model")
        or (pool_choice.model if pool_choice else None)
        or override_model
    )
    model = _effective_model(
        effective_model_override, card_model, column_default_model, persona_model,
        provider=provider,
        column_default_provider=column_default_provider,
        provider_pinned_by_higher_layer=provider_pinned_by_higher_layer,
    )
    if effective_model_override:
        if global_override and (global_override.get("model") == effective_model_override):
            model_source = PRECEDENCE_GLOBAL_OVERRIDE
        else:
            model_source = PRECEDENCE_COLUMN_OVERRIDE
    else:
        model_source = _resolve_model_source(
            None, card_model, column_default_model, persona_model, provider=provider,
            column_default_provider=column_default_provider,
            provider_pinned_by_higher_layer=provider_pinned_by_higher_layer,
        )
    if global_override:
        provider_source = PRECEDENCE_GLOBAL_OVERRIDE
    elif pool_choice:
        # Eerlijk: de head won (de router koos de door ons gebouwde
        # impliciete kop) → ``column_default``; de staart won (head viel
        # af door pause/drempel) → ``pool``. We vergelijken op
        # head-identity, niet op provider: na dedup staat
        # ``column_default_provider`` op de kop, maar een tweede
        # pool-entry met dezelfde provider (bestaand edge-case) zou
        # anders ten onrechte als kolom-default-routing worden
        # gerapporteerd.
        if column_default_provider and pool_choice is head_entry:
            provider_source = PRECEDENCE_COLUMN_DEFAULT
        else:
            provider_source = PRECEDENCE_POOL
    elif override_provider:
        provider_source = PRECEDENCE_COLUMN_OVERRIDE
    elif column_default_provider:
        provider_source = PRECEDENCE_COLUMN_DEFAULT
    else:
        provider_source = PRECEDENCE_NONE
    # Endpoint-name resolution rides the *same* precedence chain as the
    # provider (global_override > pool_choice > column_override >
    # column.default_endpoint_name) — this is the single source of truth
    # the dispatch path consumes as ``resolved["endpoint_name"]``. Keeping
    # it here (rather than re-deriving global_override/pool_choice/
    # column_override in ``dispatch_card``, where those intermediates don't
    # exist) is what closes the F821 crash from commit 0ed8796. Gated on
    # ``PROVIDER_COMPATIBLE`` so the common Anthropic path pays no extra DB
    # call for the column-default lookup.
    endpoint_name: str | None = None
    if provider == PROVIDER_COMPATIBLE:
        endpoint_name = (
            (global_override or {}).get("endpoint_name")
            or (pool_choice.endpoint_name if pool_choice else None)
            or column_override.get("endpoint_name")
            or await get_column_default_endpoint_name(
                session, project_key=project_key, column_name=target_agent,
            )
        )
    return {
        "provider": provider,
        "model": model,
        "provider_source": provider_source,
        "model_source": model_source,
        "global_override": global_override,
        "pool_choice": (
            {"provider": pool_choice.provider, "model": pool_choice.model}
            if pool_choice else None
        ),
        "column_default_provider": column_default_provider,
        "column_default_model": column_default_model,
        "persona_model": persona_model,
        "endpoint_name": endpoint_name,
        # Kaart 7411d25e…: ordered list of providers in the resolved
        # spillover chain for this column (``[head] ++ [tail]``). The
        # ``SubscriptionPoolDialog`` renders this verbatim so an operator
        # can see the full chain without re-deriving it client-side.
        # Always non-empty: ``_build_spillover_candidates`` emits a
        # synthetic ``anthropic`` head when there is no column default
        # AND no pool, so the chain stays list-shaped — the UI treats
        # a length-1 chain as 'no spillover configured, waiting on the
        # reset' rather than re-implementing the runtime chain-end
        # default.
        "spillover_chain": [e.provider for e in chain_candidates],
    }


# No-snapshot pool picker used by the column-settings UI: the UI has no
# spawn to dispatch, so it doesn't run the live usage router — it just
# shows "would the pool pin me?" via the first entry that matches the
# claude-code CLI. ``pick_subscription_for_cli`` with empty snapshots
# + no paused providers falls through to that "first entry" branch.
def _build_spillover_candidates(
    column_default_provider: str | None,
    pool: list[PoolEntry],
    *,
    cli_id: str,
) -> tuple[PoolEntry, list[PoolEntry]]:
    """Bouw de spillover-keten voor een kolom.

    Vorm B uit ``docs/cockpit/spillover-per-kolom-decision.md``: de
    effectieve-kandidatenlijst voor kolom K is
    ``[K.default_provider] ++ [pool-entries minus K.default_provider]``.
    De kop erft ``drempel`` en ``model`` van een matchende pool-entry
    (en álle entries met dezelfde provider+cli verdwijnen uit de staart
    — dedup op provider, niet op object-identity, want de pool mag
    duplicaat-providers bevatten en anders zou de tweede anthropic
    in de staart terechtkomen); geen matchende entry → kop met
    ``drempel=1.0`` (gebruik tot de per-provider pause hem raakt) en
    ``model=None``. De kop draagt ``cli=cli_id`` zodat de router
    (``pick_subscription_for_cli``) hem niet wegfiltert.

    Args:
        column_default_provider: ``column.default_provider`` of None
            wanneer de kolom geen default heeft.
        pool: de pool-entries zoals
            ``get_subscription_pool()`` ze teruggeeft (mogelijk leeg).
        cli_id: het spawn-CLI waar deze kolom op draait
            (``claude-code`` / ``open-code`` / …). De kop krijgt deze
            CLI mee; de router filtert de staart op dezelfde CLI.

    Returns:
        ``(head, chain)``: ``head`` is de impliciete eerste entry;
        ``chain`` is de (mogelijk lege) staart die de router achtereenvolgens
        afloopt. De staart bevat géén entries met dezelfde
        ``(provider, cli)`` als de kop (dedup-contract, ook als de
        pool meerdere entries met die provider draagt). Wanneer de
        pool leeg is bevat ``chain`` de kop niet.
    """
    if column_default_provider:
        # Zoek een pool-entry op provider én cli — een open-code-pool
        # mag nooit de claude-code-kop voeden. Dedup de staart op
        # provider+cli (niet op object-identity): de pool staat
        # duplicaat-providers toe, en anders zou de tweede
        # ``column_default_provider``-entry in de staart overleven
        # en ten onrechte als kolom-default-routing worden gerapporteerd.
        matched = next(
            (e for e in pool
             if e.provider == column_default_provider and e.resolved_cli == cli_id),
            None,
        )
        head = PoolEntry(
            provider=column_default_provider,
            model=matched.model if matched else None,
            drempel=matched.drempel if matched else 1.0,
            cli=cli_id,
            endpoint_name=matched.endpoint_name if matched else None,
        )
        tail = [
            e for e in pool
            if not (e.provider == column_default_provider and e.resolved_cli == cli_id)
        ]
    elif pool:
        head, tail = pool[0], list(pool[1:])
    else:
        # Geen kolom-default én geen pool: synthetische kop zodat de
        # keten altijd non-empty is en de caller nog steeds iets heeft
        # om op te lossen. Provider blijft None → resolve_effective
        # valt door naar de chain-end default (PROVIDER_ANTHROPIC).
        head = PoolEntry(provider="anthropic", model=None, drempel=1.0, cli=cli_id)
        tail = []
    chain = [head, *tail]
    return head, chain


async def _column_settings_pool_picker(
    pool_entries: list[PoolEntry],
) -> PoolEntry | None:
    return subscription_pool.pick_subscription_for_cli(
        pool_entries, {},
        paused_providers=set(), cli_id=CLAUDE_CODE_CLI_ID,
    )


async def resolve_column_effective_model(
    session,
    project_key: str,
    column_name: str,
    project_path: str,
    card_model: str | None = None,
    column_override: dict | None = None,
) -> dict:
    """Return the resolved provider/model/source for a column.

    Thin backward-compatible wrapper around
    ``resolve_effective_provider_and_model``. Used by the column-settings
    UI to render an "effective model" line beneath the model input so a
    user editing a column sees why their selection isn't applied (kaart
    1782fa43…). The dispatcher applies the per-card override at dispatch
    time, so this UI-side call passes ``None`` for ``card_overrides`` —
    a different ``column_override`` path in the column-settings handler
    surfaces column-level overrides if/when that's added.
    """
    return await resolve_effective_provider_and_model(
        session,
        project_key=project_key,
        target_agent=column_name,
        project_path=project_path,
        pick_pool=_column_settings_pool_picker,
        card_overrides=column_override,
        card_model=card_model,
        cli_id=CLAUDE_CODE_CLI_ID,
    )


def _persona_for_card(project_path: str, card, column: str) -> str | None:
    """Resolve persona for a card. `column` is passed explicitly because the card
    may already have been moved to a non-persona column."""
    agent = getattr(card, "agent", None)
    if agent:
        # card.agent may be a persona name (e.g. "analyst") — try it first
        persona = _read_persona_file(project_path, f"{agent}.md")
        if persona:
            return persona
    return _read_persona(project_path, column)


async def _resolve_work_type_fallback(session, project_key: str, card) -> str | None:
    """Resolve a work_type-driven fallback persona for dispatch.

    Returns the persona string the work_type mapping suggests for this card, or
    None when work_type is unset / not a recognised value. Only the persona
    name is returned here; the final accept/reject against the project's
    known agent files happens in `_phase_target_agent`, so the fallback can
    never route to a column whose persona the project lacks.

    Returns None — instead of the default 'engineer' fallback — because
    `_phase_target_agent` already handles the missing-fallback case (falls
    through to the legacy persona/column lookup + hardcoded 'engineer'). We
    want to set the fallback only when work_type actively says something.

    Two cases return None:
      * `card.work_type` is unset or empty (no routing hint at all).
      * `card.work_type` is a value not in `WORK_TYPES` (e.g. an enum value
        left over from before the schema tightened, or a manually PATCHed
        legacy row). In that case, `get_work_type_persona` would silently
        return 'engineer' via the `WORK_TYPE_PERSONA_DEFAULTS` default —
        and we'd silently override a custom source-column persona with the
        engineer column. Returning None here keeps the column-derived
        persona in charge. The actual enum is locked by the API+schema
        layer; this is a defensive check for legacy/PATCHed data.

    Mirrors the create-time `resolve_create_agent` (commit 80e139e) but is
    called at dispatch time so it covers legacy rows from before that commit
    and cards whose user later PATCHed `work_type` without re-picking agent —
    see kanban card 9cf106e7 ("Card with analysis work type got picked up by
    an engineer") for the regression this fixes.
    """
    work_type = getattr(card, "work_type", None)
    if not work_type:
        return None
    from app.kanban.schemas import WORK_TYPES
    if work_type not in WORK_TYPES:
        # Unrecognised work_type (legacy data, schema drift, manual PATCH).
        # Don't apply any fallback — let the column-derived persona win so a
        # custom source-column persona isn't silently swapped for 'engineer'.
        return None
    from app.kanban.service import get_work_type_persona
    return await get_work_type_persona(session, project_key, work_type)


# ---- prompt ----------------------------------------------------------------

# Prefix matched against a card's activity-feed comments when extracting the
# latest revisit note. Kept in sync with service._REVISIT_PREFIX — the
# service stamps this prefix on reopen_comment writes, dispatch reads it back
# via extract_revisit_question. Same prefix discipline as
# service._DONE_SUMMARY_PREFIX / _REVIEW_REQUESTED_PREFIX (distinct prefixes
# so no two scopes collide).
_REVISIT_PREFIX = "**Revisit:** "

# Prefix matched against a card's activity-feed comments when extracting the
# human's answer to an impediment. Mirrors _REVISIT_PREFIX: the resolve path
# (router.resolve_impediment) stamps this prefix on a human-supplied answer,
# dispatch reads it back via extract_impediment_answer and injects it into the
# `## IMPEDIMENT` section. Distinct from the `**Impediment:**` question prefix
# so the two never collide when both live in the same feed.
_IMPEDIMENT_ANSWER_PREFIX = "**Resolution:** "


def extract_impediment_answer(activity) -> str | None:
    """Return the text of the latest `**Resolution:** <answer>` comment on a
    card's activity feed, or None when no such comment exists.

    Mirrors `extract_revisit_question`: walk the feed in reverse (newest
    first) so, when a human refines their answer across multiple resolve
    rounds, the *latest* resolution wins. Anything that's not a `comment`
    op is skipped; the prefix match is on `payload["text"]`.
    """
    for op in reversed(list(activity)):
        if op.op_type != "comment":
            continue
        text = (op.payload.get("text") or "")
        if text.startswith(_IMPEDIMENT_ANSWER_PREFIX):
            return text[len(_IMPEDIMENT_ANSWER_PREFIX):]
    return None


def compose_impediment_answer(gate_answer: str | None,
                              free_text: str | None) -> str | None:
    """Merge the two substrates a human answer can arrive on into the single
    ``impediment_answer`` string the prompt renders.

    Two independent channels exist and an operator can use both in one
    resolve round:

    * a **structured gate choice** (``report_impediment(options=[...])`` →
      ``service.answer_gate``), which lives in the ``kanban_gates`` table and
      posts *no* activity comment, and
    * **free text**, stamped as a durable ``**Resolution:** <text>`` comment by
      ``router.resolve_impediment``.

    Precedence: the gate pick is the decision (it's the structured artefact
    from the dedicated UI), the free text is supporting context — so when both
    are present they are rendered as a labelled pair rather than one
    overwriting the other (kanban card c3419f63: the choice never reached the
    resumed session at all, because the downstream reader only knew about the
    comment). Identical values collapse to one line: an operator who typed
    exactly what they clicked shouldn't see it echoed twice.

    Returns None when neither channel carries anything, which keeps
    ``build_card_prompt`` on its "please address this question" framing.
    """
    gate = (gate_answer or "").strip()
    text = (free_text or "").strip()
    if gate and text and gate != text:
        return f"Chosen option: {gate}\n\nAdditional context: {text}"
    return gate or text or None


def extract_revisit_question(activity) -> str | None:
    """Return the text of the latest `**Revisit:** <note>` comment on a
    card's activity feed, or None when no such comment exists.

    Mirrors the `**Impediment:**` extraction in router.resolve_impediment:
    walk the feed in reverse (newest first) so multiple reopen rounds
    return the *latest* rebuttal instead of the oldest one.

    `activity` is the op-log KanbanOp list returned by
    `service.card_activity`. Anything that's not an op of type `comment`
    is skipped; the prefix match is on `payload["text"]`.
    """
    for op in reversed(list(activity)):
        if op.op_type != "comment":
            continue
        text = (op.payload.get("text") or "")
        if text.startswith(_REVISIT_PREFIX):
            return text[len(_REVISIT_PREFIX):]
    return None


def _build_attachments_section(card) -> str:
    """Render the ``## Screenshots`` section listing each attachment's absolute
    on-disk path, so the spawned session can open them with its ``Read`` tool
    (Claude Code's Read renders images). Empty string when the card carries no
    attachments — every legacy card round-trips unchanged.

    Reads ``card.attachments`` defensively (``getattr``) so unit tests can pass
    a lightweight card stub without the ORM relationship.
    """
    attachments = getattr(card, "attachments", None) or []
    if not attachments:
        return ""
    lines = ["\n## Screenshots\n",
             "The human attached the following image(s) to this card. Use your "
             "`Read` tool on each absolute path to view them — they carry "
             "context for the task:\n"]
    for att in attachments:
        path = getattr(att, "storage_path", "") or ""
        if not path:
            continue
        filename = getattr(att, "filename", "") or "attachment"
        lines.append(f"- `{path}` ({filename})")
    lines.append("")
    return "\n".join(lines)


def _build_spec_doc_line(card) -> str:
    """Render a single ``**Brondoc (spec_doc):** <pad>`` line when
    ``card.meta['spec_doc']`` (SPEC_DOC_META_KEY) is a non-empty string. Empty
    string otherwise.

    The analyst-persona sets this forward *implements*-link on every child
    card that updates a ``docs/cockpit/*.md`` doc; without this rendering the
    executor's ship-stap 3 ("voeg een `✅ Geïmplementeerd (kaart <id>)`-regel
    toe aan het brondoc") is blind to which doc to update and the
    bijwerk-stap gets skipped or lands on the wrong file (kanban card
    87ced87b…). Tolerates ``card.meta is None`` / missing key / non-string
    value (defensive — analysts *should* write a string but dispatch must not
    500 on a malformed legacy row)."""
    from app.kanban.schemas import SPEC_DOC_META_KEY
    meta = getattr(card, "meta", None) or {}
    raw = meta.get(SPEC_DOC_META_KEY)
    if not isinstance(raw, str):
        return ""
    spec_doc = raw.strip()
    if not spec_doc:
        return ""
    return f"**Brondoc (spec_doc):** `{spec_doc}`\n"


def _build_prior_branch_warning(project_path: str, prior_session_name: str | None) -> str:
    """Render a warning block when a prior dispatch left unmerged commits behind.

    Closes the "re-dispatch starts cold" gap (kanban card ff2d03fce…): when
    a session is interrupted after `git commit` but before the merge, the
    reaper eventually releases the claim and the dispatcher spawns a fresh
    worktree for the same card. Without this hint, that fresh session has
    no signal that its predecessor already shipped commits that just need
    to land on master — so it redoes the work and the two diverge.

    Pure synchronous helper (uses ``subprocess.run`` against the project
    repo, NOT the worktree path — the worktree may already be GC'd by the
    time we run this check). Returns ``""`` in three cases, so callers can
    treat the empty string as the explicit "no warning" sentinel and
    prepend only when non-empty:

      - ``prior_session_name`` is falsy (no prior claim found in the op-log,
        or the card was never picked up before).
      - the prior branch doesn't exist on the remote (was force-pushed
        away, never pushed, or its session was killed before pushing).
      - the prior branch has zero commits ahead of ``origin/master``
        (already merged in a concurrent merge, or the commit was empty).
      - any subprocess / repo error — fail open, never wedge dispatch on a
        transient git hiccup.

    Acceptance criteria (from the card): the rendered block must name both
    the branch and the commit count so a re-dispatched agent can act on it
    with one `git log origin/master..<branch>` inspection rather than
    rediscovering the gap from scratch.
    """
    if not prior_session_name or not project_path:
        return ""
    try:
        result = subprocess.run(
            ["git", "-C", project_path, "log", "--oneline",
             f"origin/master..{prior_session_name}"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if result.returncode != 0:
        return ""
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    if not lines:
        return ""
    count = len(lines)
    return (
        f"## PRID-BRANCH-WAARSCHUWING\n"
        f"**Let op:** een eerdere sessie (`{prior_session_name}`) liet "
        f"{count} commit{'s' if count != 1 else ''} achter die nog niet "
        f"gemerged zijn. Inspecteer die branch eerst vóór je opnieuw begint "
        f"— mogelijk is het werk al af en hoef je alleen te shippen/verifiëren:\n\n"
        f"```\n"
        f"git log origin/master..{prior_session_name} --oneline\n"
        f"git diff origin/master..{prior_session_name} --stat\n"
        f"```\n"
        f"\nAls de branch al precies doet wat de kaart vraagt, ga dan direct "
        f"door naar de ship-stappen hieronder (in plaats van het werk te "
        f"herbouwen). Is de branch achterhaald of conflicterend, dan mag je "
        f"opnieuw beginnen — maar bevestig dat expliciet in een "
        f"`**Self-improve:**` comment op deze kaart.\n"
    )


async def _resolve_prior_branch_warning(
    session, *, card, project_path: str,
) -> str:
    """Build a prior-branch warning for ``card`` if a previous dispatch
    left commits behind, otherwise return ``""``.

    Glue between ``_build_prior_branch_warning`` (the pure git-aware
    renderer) and the kanban op-log (which knows whether the card was
    ever picked up before). Walks the op-log backwards, finds the latest
    ``claim`` op whose ``claimed_by`` starts with ``agent:`` and whose
    session name is NOT the current session (the new claim that the caller
    is about to commit is excluded — we want the *previous* session's
    branch, not the one the freshly-spawned session is creating right
    now), and feeds that name to the helper.

    Async + DB-bound because it queries the op-log via ``card_activity``,
    same shape as the revisit/resume resolvers above.

    Returns ``""`` (the explicit no-warning sentinel) when:
      - the card has no prior claim (first dispatch, or manual restart
        after a manual release — both common),
      - the prior branch has nothing ahead of ``origin/master`` (already
        merged, never pushed, or GC'd),
      - the op-log query fails (fail open — a transient DB hiccup must
        never wedge dispatch).

    Wired into ``_run_card`` so the warning reaches every dispatch path
    (auto-tick, manual ``dispatch_card``, ``redispatch_card``, ``dispatch_impediment_card``).
    """
    from app.kanban.service import card_activity

    try:
        activity = await card_activity(session, card.id)
    except Exception:
        logger.debug(
            "could not read op-log for prior-branch warning (card %s); skipping",
            card.id, exc_info=True,
        )
        return ""
    current_session = _claimant_session(card)
    for op in reversed(list(activity)):
        if op.op_type != "claim":
            continue
        claimed_by = (op.payload or {}).get("claimed_by") or ""
        if not claimed_by.startswith(CLAIMANT_PREFIX):
            continue
        session_name = claimed_by[len(CLAIMANT_PREFIX):]
        # Skip the brand-new claim the caller is about to commit — we want
        # the *previous* session's branch, not the new worktree that's
        # still empty.
        if session_name == current_session:
            continue
        return _build_prior_branch_warning(project_path, session_name)
    return ""


def build_card_prompt(card, *, persona: str | None, ship_mode: str,
                      phase: str = "executor",
                      impediment_question: str | None = None,
                      impediment_answer: str | None = None,
                      revisit_question: str | None = None,
                      revisit_prior_decision: dict | None = None,
                      prior_branch_warning: str | None = None,
                      project_path: str | None = None,
                      worktree_path: str | None = None) -> str:
    # A card dispatched in the executor phase (no `analyst_agent_id`) can
    # still resolve to the analyst persona via `work_type='analysis'` or
    # `card.agent='analyst'` (the "leaf analyst spike" case — see
    # `is_analyst_leaf_spike`). analyst.md (and its ANALYST_PROMPT
    # fallback) self-scopes for this: the "Verboden" prohibitions are
    # explicitly marked as modus-1-only and the persona's own "Leaf
    # design-deliverable" section states the modus-2 contract, so no
    # dispatch-level override is needed to reconcile it with the executor
    # ship workflow injected below. See kanban card c2b478ca396a473287aa0c04a79890e2
    # for the two-modi framing and fbe7937e99484941b196bf2ebc0866f6 for the
    # removal of the (now redundant) per-dispatch override preamble.
    preamble = (persona.strip() + "\n\n") if persona else ""
    impediment_section = ""
    if impediment_question:
        impediment_section = (
            "\n\n## IMPEDIMENT\n"
            "A previous agent was blocked on this card. Their question:\n"
            f"> {impediment_question}\n\n"
        )
        if impediment_answer:
            # A human answered the blocker via /resolve-impediment: a structured
            # gate choice, a `**Resolution:**` comment, or both merged by
            # `compose_impediment_answer`. Surface it as authoritative so the
            # resumed session acts on the decision instead of re-asking.
            # Blockquote every line — a merged answer is multi-line, and a bare
            # `> ` on the first line only would leave the rest reading as loose
            # prose outside the quote.
            quoted = "\n".join(
                f"> {line}" if line.strip() else ">"
                for line in str(impediment_answer).splitlines()
            )
            impediment_section += (
                "A human has since answered this — treat the answer as an "
                "authoritative decision and proceed accordingly:\n"
                f"{quoted}\n\n"
            )
        else:
            impediment_section += (
                "Please address this question or clarify what's needed "
                "before proceeding.\n"
            )

    revisit_section = ""
    if revisit_question:
        # Mirror of `impediment_section`. The prior-decision dict carries the
        # Done summary + deliverable refs so the re-picked-up session has
        # enough context to revise without re-reading every comment. When
        # None or empty, only the rebuttal is rendered — that's the safe
        # fallback for cards without the (optional) decision enrichment.
        parts = [
            "\n\n## REVISIT",
            "A previous agent completed this card and a human has reopened it "
            "with the following rebuttal. Treat this as a request to revise "
            "the prior decision, not a brand-new task.\n",
            f"> {revisit_question}\n",
        ]
        prior = revisit_prior_decision or {}
        prior_lines = []
        if prior.get("summary"):
            prior_lines.append(f"- **Previous summary:** {prior['summary'].strip()}")
        prior_deliverables = prior.get("deliverables") or []
        if prior_deliverables:
            refs = "\n".join(
                f"  - `{d.get('kind', '?')}: {d.get('ref', '?')}`"
                for d in prior_deliverables
            )
            prior_lines.append(f"- **Previous deliverables:**\n{refs}")
        if prior_lines:
            parts.append("\nFor context, the prior decision referenced:\n\n"
                         + "\n".join(prior_lines) + "\n")
        parts.append(
            "\nPlease re-read the prior decision (deliverable docs in git) "
            "and revise or uphold it with reasoning, then ship the update.\n"
        )
        revisit_section = "".join(parts)

    # Standardised session-end workflow — provider-agnostic, works with any
    # coding agent (Claude Code, OpenCode, Codex CLI, …). Executor/engineer
    # sessions run tests → ship (merge/PR) → attach the deliverable → retro →
    # move the card to Done. Analyst sessions never ship code (planning-only,
    # exits via move_parent → Done) so they get a lighter retro-then-move
    # workflow instead of the full engineer ship instructions.
    if phase == "analyst":
        ship_instructions = _build_analyst_session_end_instructions()
    elif getattr(card, "agent", None) == "reviewer":
        ship_instructions = _build_reviewer_session_end_instructions()
    else:
        ship_instructions = _build_ship_instructions(
            ship_mode, project_path=project_path,
        )
    problem_flag_instructions = _build_problem_flag_instructions()
    mcp_fallback_instructions = _build_mcp_fallback_instructions()
    worktree_safety_callout = _build_worktree_safety_callout(
        project_path=project_path, worktree_path=worktree_path,
    )
    attachments_section = _build_attachments_section(card)
    spec_doc_line = _build_spec_doc_line(card)

    return (
        f"{preamble}"
        "You are picking up a Kanban card from the Agent Cockpit board. "
        'It is already claimed by you and moved to "Doing".\n\n'
        f"Host card id: {getattr(card, 'id', '') or ''}\n"
        f"# {card.title}\n"
        f"{getattr(card, 'description', '') or ''}\n"
        f"{spec_doc_line}"
        f"{attachments_section}"
        f"{prior_branch_warning or ''}\n"
        f"{impediment_section}\n"
        f"{revisit_section}\n"
        f"Ship mode: {ship_mode}\n\n"
        "Work autonomously to completion, following your role instructions above. "
        "Use the `cockpit-kanban` MCP tools (`move_card`, `attach_deliverable`, "
        "`comment`) to update the card exactly as those instructions direct. If you are "
        "blocked, use `report_impediment` with a clear question explaining what you need "
        "plus a required `options` list of exactly 4 candidate answers — the Impediment "
        "card always shows the human a row of choice buttons, so a call without options "
        "is rejected (`options_required`). If you have fewer than 4 real alternatives, "
        "propose plausible ones anyway; the human can ignore all 4 and type a free-text "
        "answer in the field the UI always shows alongside the buttons."
        f"\n\n{mcp_fallback_instructions}"
        f"\n\n{problem_flag_instructions}"
        f"\n\n{worktree_safety_callout}"
        f"\n## Session-end workflow\n"
        "When your work on this card is complete, follow these steps in order:\n\n"
        f"{ship_instructions}"
    )


def _build_mcp_fallback_instructions() -> str:
    """REST fallback for when the `cockpit-kanban` MCP tools fail with JSON-RPC
    `-32602` (Invalid request parameters).

    Root cause (confirmed; see app/kanban/mcp_health.py failure-mode B): the
    agent completes its MCP `initialize` handshake, then the backend restarts
    or reconnects (dev `--reload`, a supervisor restart, a crash-restart). The
    SSE stream reconnects but the *server-side* session state was never
    re-initialized, so the fresh session answers **every** subsequent request —
    including `ping` — with a generic ``-32602``. The payload the agent sent is
    fine; retrying often clears it once the client re-initializes. A session
    that doesn't know the REST equivalents can burn several turns rediscovering
    endpoint paths, or worse strand a finished card in its dispatch column. See
    kanban card 7b1d0a91 for the full postmortem.

    Two follow-up gaps from kanban card 939a9770 (a session where -32602 was
    100% of calls, not an intermittent race, and the REST fallback carried the
    whole session) are closed here: the list endpoint's `{"items": [...]}`
    envelope is spelled out (three parse attempts failed on the assumption it
    returned a bare list), `POST /cards` is listed so filing a follow-up card
    doesn't need endpoint archaeology, and the retry advice now caps at one
    failed retry per session instead of one per call.

    Analyst-fase coverage (kanban card a254b3111a2340478da726eb8fd015b9): an
    analyst session with MCP down must still be able to finish decomposition.
    `POST /cards/{id}/plan-attachment` is the REST mirror of
    `add_plan_attachment` — wiring a `plan_ref` deliverable on every child is
    **not optional**, even when `depends_on_graph={}` (an independent child
    without a `plan_ref` is silently held by the `awaiting_plan_ref` dispatch
    gate and never runs). `POST /cards` must also expose `parent_card_id` and
    `metadata`, since the `decomposed`-gate on the parent's Done-move refuses
    the move with `no_children` when a child is missing `parent_card_id`."""
    return (
        "## If a `cockpit-kanban` MCP call fails with `-32602`\n"
        "`-32602` (Invalid request parameters) from a `mcp__cockpit-kanban__*` "
        "tool is usually an intermittent MCP handshake race, **not** a bad "
        "payload — retry the same call once. If that retry also fails, treat "
        "MCP as **down for the rest of this session**: stop retrying per call "
        "and go straight to REST for every subsequent board update (a broken "
        "session handshake fails 100% of calls, not intermittently, and the "
        "per-call retry never clears it). The REST API is at "
        "`http://localhost:8000/api/v1/kanban` (same board, same effect):\n"
        "- `POST /cards/{id}/comment` — body `{\"text\": \"…\"}`\n"
        "- `POST /cards/{id}/move` — body `{\"column\": \"…\"}` (for Done/Impediment, "
        "follow with a `comment` carrying your summary — the REST move has no "
        "`summary` field)\n"
        "- `POST /cards/{id}/deliverables` — body `{\"kind\": \"branch|pr|commit|link|note\", \"ref\": \"…\"}`\n"
        "- `GET /cards?project_key=<key>&column=<col>` — list cards. Returns "
        "`{\"items\": [...]}` — an object, **not** a bare list; index `.items` "
        "before iterating (`jq '.items[]'`)\n"
        "- `POST /cards` — create a card; body `{\"project_key\": \"…\", "
        "\"title\": \"…\", \"description\": \"…\", \"column\": \"Backlog\", "
        "\"work_type\": \"…\", \"parent_card_id\": \"…\", \"metadata\": {…}}` "
        "(`column` defaults to `Backlog`; `parent_card_id` is required when "
        "creating a child of an analyst decomposition (a child without it makes "
        "the parent's `decomposed`-Done-move fail with `no_children`); "
        "`metadata` carries analyst/parent-tag context; an unknown "
        "`project_key` is rejected with 404 `unknown_project_key` — resolve it "
        "first, don't guess)\n"
        "- `POST /cards/{id}/plan-attachment` — body "
        "`{\"plan_markdown\": \"…\", \"child_card_ids\": [\"…\"], "
        "\"depends_on_graph\": {\"<child_id>\": [\"<sibling_id>\", …]}}` "
        "(REST mirror of `add_plan_attachment`; **not optional** — every "
        "decomposition child MUST get a `plan_ref` deliverable, even when "
        "`depends_on_graph={}`. Without it the `awaiting_plan_ref` dispatch "
        "hold keeps the child silent: it looks unclaimed and unstarted but "
        "never dispatches. Returns `{\"parent_card_id\": \"…\", "
        "\"plan_deliverable_id\": \"…\", "
        "\"child_card_ids\": [\"…\"], "
        "\"plan_refs\": {\"<child_id>\": \"<plan_ref_deliverable_id>\", …}}` — "
        "the `plan_refs` map echoes the freshly wired `plan_ref` deliverable "
        "id per child, so the write is verifiable from the response itself. "
        "Treat a non-empty `plan_refs[<child_id>]` as proof the deliverable "
        "landed; no re-fetch is needed. The MCP `add_plan_attachment` tool "
        "returns the same shape)\n"
        "- `GET /project-key?project_path=<abs path>` — resolve the project "
        "key; returns `{\"project_key\": \"…\"}`\n"
    )


def _build_problem_flag_instructions() -> str:
    """Standing reminder to file (not just mention) problems noticed outside the
    assigned card's scope. A skill at ``.claude/skills/flag-problem/SKILL.md``
    has the full dedupe/project-key procedure when the agent has filesystem
    access; this inlines the essential steps for parity with the ship
    instructions above, which work the same way for the same reason."""
    return (
        "## Noticed a problem outside this card's scope?\n"
        "If you hit a bug, a stale doc, or a workflow gap that isn't the task "
        "above, don't just mention it in chat — it vanishes when this session "
        "ends. File it: resolve this repo's real project key first — call the "
        "`resolve_project_key` MCP tool with this repo's working directory "
        "(or, with shell access, `curl -s \"http://localhost:8000/api/v1/"
        'kanban/project-key?project_path=$(git rev-parse --show-toplevel)"`) '
        "— guessing the key silently creates an invisible parallel board. "
        "Then check `list_cards` on `Backlog`/`Impediment` for an existing "
        "card describing the same root cause, and either `comment` on it "
        "with what's new or `create_card` (column `Backlog`, title "
        "`[problem] <summary>`) if none exists. See the `flag-problem` skill "
        "for the full procedure. Keep this quick — don't let it derail the "
        "card you were actually dispatched for.\n"
    )


def _build_worktree_safety_callout(
    project_path: str | None = None,
    worktree_path: str | None = None,
) -> str:
    """Top-of-prompt callout forbidding writes to the canonical checkout path.

    Background — kanban card 513e37a1a86e41db8b6af8423292f6b6: a dispatched
    analyst session edited two docs via the absolute path
    ``<project_path>/docs/cockpit/...`` instead of its worktree path.
    ``Edit`` succeeded because the committed content matched in both
    checkouts, so ``old_string`` resolved; the change landed on top of a
    concurrent session's uncommitted work in the main checkout. The persona
    doc already warns against ``cd <project_path>/...`` for shell commands
    but says nothing about Write/Edit — an agent reading the card description
    (which references ``/home/vdvgu/claude-cockpit/...`` for canonical
    filenames) easily constructs an absolute *write* path that bypasses the
    worktree.

    This callout is the in-prompt mirror of the persona-doc guidance: it
    names the safe pattern, names the forbidden one, and names the tools that
    can clobber (``Write`` / ``Edit`` / ``MultiEdit``). It is rendered above
    the ``## Session-end workflow`` heading so it lands in the agent's
    early context, not buried under later steps — same parity principle as
    the ship-instructions inline.

    ``project_path`` and ``worktree_path`` are interpolated when the
    dispatcher knows them (kanban card a962b209aea4489680c15de3562eb8bb).
    Before this card the callout hardcoded the meta project's
    ``/home/vdvgu/claude-cockpit`` and the ``<branch>`` placeholder — those
    values are *wrong* for any non-meta dispatched project and silently
    coaxed an agent on a throwaway product project into writing its
    deliverable into the meta project's tree. Pass ``None`` (the legacy
    fallback) only when the dispatcher hasn't resolved a project yet —
    kept as a default so pre-existing callers and tests keep working.

    The forbidden canonical path here is the project's *own* main checkout,
    not the meta project tree: a card dispatched against project X must
    not be allowed to write into project X's shared checkout either, for
    exactly the same concurrent-session reason.

    When ``worktree_path`` is ``None`` the callout deliberately drops the
    "spawned in a git worktree at …" framing — that framing is only true
    for the worktree transport. Resume sessions, sandcastle sessions, and
    headless sessions do NOT run in a freshly-minted host-side worktree;
    naming a fabricated path (the legacy ``<branch>`` placeholder was a
    fallback for unresolved cases but reads as a real claim to the agent)
    tells the agent a lie about its actual cwd. The forbidden canonical
    path guidance still applies — concurrent dispatched sessions on this
    project can still have uncommitted work in the main checkout.
    """
    canonical_main = project_path or "/home/vdvgu/claude-cockpit"
    # Branch into two templates: when the dispatcher knows the worktree
    # path, render the full claim ("spawned in a git worktree at …");
    # otherwise render a neutral "your shell cwd is …" framing so resume
    # / sandcastle / headless sessions don't get a false claim.
    if worktree_path:
        scope_intro = (
            f"You were spawned in a git worktree at ``{worktree_path}`` "
            "(see your shell's cwd). Your **only** writable surface is "
            "that worktree root."
        )
        right_example = (
            f"absolute ``{worktree_path}/docs/cockpit/foo.md``"
        )
    else:
        scope_intro = (
            "Your shell's cwd is your writable surface for this card — "
            "it is **not** a freshly-minted git worktree (resume / "
            "sandcastle / headless transports skip the worktree step), "
            "so write into that cwd and nowhere else."
        )
        right_example = (
            "relative ``docs/cockpit/foo.md`` from your shell's cwd"
        )

    return (
        "## Worktree scope — write only inside your worktree\n"
        f"{scope_intro} **Never** call ``Write``, ``Edit``, ``MultiEdit``, "
        f"or ``NotebookEdit`` with an absolute path that resolves to "
        f"``{canonical_main}/...`` outside your writable surface — that "
        "is the shared canonical checkout where ``master`` is checked "
        "out, and concurrent dispatched sessions may have uncommitted "
        "work there. A write to that path silently lands on top of "
        "someone else's changes (kanban card "
        "513e37a1a86e41db8b6af8423292f6b6 was a near-clobber from "
        "exactly this).\n\n"
        "Concretely:\n"
        "- **Right:** ``docs/cockpit/foo.md``, ``backend/app/x.py``, or "
        f"{right_example}.\n"
        f"- **Wrong:** ``{canonical_main}/docs/cockpit/foo.md`` — "
        "this resolves to the *main* checkout, not your writable "
        "surface, even though the file content is identical.\n\n"
        f"Same rule for shell: don't ``cd {canonical_main}/...`` "
        "and run a write from there — see the persona's *Werkomgeving in "
        "worktree* section for the broader cwd-safety rules. Read paths to "
        "the canonical checkout are fine; only writes are forbidden.\n\n"
        "- **Bash-cwd persists between tool-calls.** A compound "
        "``cd backend && pytest …`` keeps the new cwd into the next "
        "Bash-call — that call then sees a cwd it doesn't expect and "
        "fails with ``no such file or directory`` (or runs against the "
        "wrong paths). Wrap each compound ``cd`` in a subshell "
        "``( cd backend && … )`` (or use ``git -C <abs-path>``), so the "
        "cwd change stays scoped to that one command. See the persona's "
        "*Werkomgeving in worktree* section for the broader cwd-safety "
        "rules (kaart 1181b6fa…).\n"
    )


async def _resolve_revisit(session, card) -> tuple[str | None, dict | None]:
    """Look up the latest `**Revisit:**` rebuttal for a card (None when no
    reopen has happened) plus a small "prior decision" envelope that the
    `## REVISIT` prompt section consumes.

    Returns `(question, prior_decision_dict)` where `prior_decision_dict`
    carries the Done summary + the deliverable refs. Both are None when the
    card has no Revisit comment (the common case for non-reopened cards).

    The prior-decision envelope is intentionally a small dict (not a
    structured object) so callers don't need to import service-layer types;
    `build_card_prompt` consumes it directly.
    """
    from app.kanban import service as svc

    activity = await svc.card_activity(session, card.id)
    revisit = extract_revisit_question(activity)
    if revisit is None:
        return None, None

    done_summary, _ = await svc.enrich_done_info(session, card.id)
    # Refresh the card to pick up deliverables (the session may have stale
    # relationship state after _make_card creates the row without the
    # deliverable eager-load).
    fresh = await svc.get_card(session, card.id)
    deliverables = []
    if fresh is not None:
        for d in (fresh.deliverables or []):
            deliverables.append({"kind": d.kind, "ref": d.ref})
    return revisit, {
        "summary": done_summary or "",
        "deliverables": deliverables,
    }


async def _resolve_impediment(session, card) -> tuple[str | None, str | None]:
    """Look up the latest ``**Impediment:**`` question and the human's answer
    to it, reading **both** answer substrates: the structured gate choice
    (``kanban_gates``) and the free-text ``**Resolution:**`` comment.

    Returns ``(question, answer)``; both are None when the card has no
    impediment question (the common case — only cards that went through the
    resolve-impediment flow carry one). Mirrors ``_resolve_revisit`` in
    shape and intent: cheap (one short scan of an already-materialised op-log)
    and only contributes to the prompt when the question actually exists, so
    ordinary cards see no IMPEDIMENT section.

    Plumbed through ``dispatch_project`` (auto-tick) so a card that just
    landed in Backlog via ``dispatch_impediment_card`` (kaart af951ad70...
    — Resolve impediment moet eerst naar Backlog, niet meteen naar engineer
    kolom) still gets the human's question + authoritative decision in the
    next spawned session's prompt. Without this, dropping the card in
    Backlog would silently lose the impediment context.

    **Why the gate lookup lives here** (kaart c3419f63): the resolve endpoint
    computes the answer correctly but ``dispatch_impediment_card`` only parks
    the card on Backlog — the spawn happens a tick later and re-derives the
    context from here. Reading only the ``**Resolution:**`` comment therefore
    dropped every gate-only resolve (``service.answer_gate`` posts no comment),
    so an operator who clicked "Postgres" and typed nothing saw the resumed
    agent re-ask the settled question. The gate query is skipped entirely when
    the card has no impediment question, so an ordinary card carrying an
    ``open_gate`` answer can't grow a phantom IMPEDIMENT answer.
    """
    from app.kanban import service as svc

    activity = await svc.card_activity(session, card.id)
    # Walk newest-first; the latest Resolution wins on re-resolve (matches
    # router.resolve_impediment's priority rules). The Impediment question
    # itself doesn't change across resolve rounds, but walking in the same
    # direction keeps the two extractors symmetric.
    free_text = extract_impediment_answer(activity)
    question = None
    for op in reversed(list(activity)):
        if op.op_type != "comment":
            continue
        text = (op.payload.get("text") or "")
        if text.startswith(svc._IMPEDIMENT_QUESTION_PREFIX):
            question = text[len(svc._IMPEDIMENT_QUESTION_PREFIX):]
            break
    if question is None:
        return None, None
    gate_answer = await svc.latest_gate_answer(session, card.id)
    return question, compose_impediment_answer(gate_answer, free_text)


async def _stamp_resume_target(session, *, card, project_key: str,
                               project_path: str) -> None:
    """Best-effort resume: if the previous agent claim points at a session
    whose worktree + Claude transcript still exist, persist
    `resume_session_id`/`resume_project_folder` on the card so the spawn
    below picks the resume transport.

    Used by `dispatch_project` (auto-tick) right before picking up a
    reopened card, so the agent session that revisits the decision can
    literally continue where the prior one left off. Failure is silent
    by design — analyst cards routinely GC their worktree after merging,
    so a None fallback is the expected path. The dispatcher then runs a
    fresh session; the agent rebuilds context from the `## REVISIT`
    prompt-injected material instead.

    No-op when the card has no `agent:` claim (e.g. it was never picked
    up, only commented on by hand) — there's no prior session to resume.
    """
    from app.kanban.operations import apply_operation
    from app.kanban.session_recovery import _resolve_resume_target

    claimant = card.claimed_by or ""
    if not claimant.startswith(CLAIMANT_PREFIX):
        return
    session_name = claimant[len(CLAIMANT_PREFIX):]
    target = _resolve_resume_target(project_path, session_name)
    if target is None:
        return
    resume_session_id, resume_project_folder = target
    await apply_operation(
        session, op_type="update", entity_type="card",
        project_key=project_key, entity_id=card.id,
        payload={"resume_session_id": resume_session_id,
                 "resume_project_folder": resume_project_folder},
    )
    logger.info(
        "reopen: stamped resume target on card %s (session %s -> %s)",
        card.id, session_name, resume_session_id,
    )


# Statuses returned by `_resolve_plan_for_child`. Distinct values let
# `_plan_context_section` render an accurate diagnosis (was the parent
# deleted, or was the plan simply never written?) instead of one generic
# "kon niet worden geladen" message. See kanban card 4a03565d ("Dispatch
# PLAN CONTEXT reports 'plan-attachment kon niet worden geladen' while a
# valid plan_ref deliverable exists") for the originating complaint.
PLAN_OK = "ok"
PLAN_NO_REF = "no_plan_ref"                  # child carries no plan_ref deliverable
PLAN_DANGLING_PARENT = "dangling_parent"     # plan_ref present, parent card gone
PLAN_MISSING_ON_PARENT = "plan_missing_on_parent"  # parent alive, plan deliverable absent
PLAN_MALFORMED = "malformed_ref"             # plan_ref JSON doesn't parse or lacks required keys


def _plan_context_section(*, status: str, plan_markdown: str | None,
                          plan_deliverable_id: str | None,
                          parent_card_id: str | None,
                          card_description: str | None = None) -> str:
    """Build the PLAN CONTEXT preamble that the executor sees in its prompt.

    On success, embeds the plan markdown verbatim so the executor can follow
    the analyst's steps. On failure, renders a status-specific diagnosis and
    picks the right nudge:

    - When the card carries its own self-sufficient description (the analyst
      wrote enough context in the title/description that the work can be
      reconstructed from the source material), the placeholder tells the
      executor to **proceed using the card description**, post a
      `**Self-improve:**` note on the card, and only fall back to
      `report_impediment` if the card is genuinely un-actionable without the
      plan.
    - When the card has no description (or an empty one), the placeholder
      steers to `report_impediment` directly — without the analyst's plan
      the executor has no source of truth and would otherwise burn context
      guessing.

    Previously this helper unconditionally pushed the executor to
    `report_impediment` even for cards that were self-sufficient from their
    own source doc, which forced every decomposed-family card with a
    dangling parent into a needless blocker session.
    """
    if status == PLAN_OK:
        return (
            f"PLAN CONTEXT — read this first\n"
            f"Plan deliverable: {plan_deliverable_id}\n"
            f"Parent card: {parent_card_id}\n\n"
            f"{plan_markdown}\n\n"
            f"---\n"
            f"Bovenstaande is het plan van de analyst. Volg deze stappen, "
            f"tenzij je tijdens het werk ontdekt dat het plan niet klopt — "
            f"gebruik dan report_impediment.\n"
        )

    # Failure modes — status-specific diagnosis, so the executor (and the
    # operator reading the transcript) can tell whether the parent was
    # deleted, the plan was never written, or the ref is corrupt.
    if status == PLAN_DANGLING_PARENT:
        diag = (
            "PLAN CONTEXT — Plan niet beschikbaar: de parent-kaart "
            f"`{parent_card_id}` van deze kaart bestaat niet meer "
            "(verwijderd of nooit aangemaakt), waardoor het plan-attachment "
            f"(`plan_deliverable_id={plan_deliverable_id}`) niet meer "
            "bereikbaar is. Dit is meestal een gevolg van het verwijderen "
            "van de analyst-parent nadat de kind-kaarten al waren aangemaakt."
        )
    elif status == PLAN_MISSING_ON_PARENT:
        diag = (
            "PLAN CONTEXT — Plan niet beschikbaar: de parent-kaart "
            f"`{parent_card_id}` bestaat, maar het plan-attachment "
            f"`{plan_deliverable_id}` is daar niet (meer) op te vinden. "
            "De analyst heeft het plan dus niet (of niet meer) gekoppeld."
        )
    elif status == PLAN_MALFORMED:
        diag = (
            "PLAN CONTEXT — Plan niet beschikbaar: het `plan_ref`-deliverable "
            "op deze kaart is misvormd (geen parseerbare JSON, of mist "
            "`parent_card_id`/`plan_deliverable_id`). De kind-kaart verwijst "
            "dus naar een onbruikbare referentie."
        )
    elif status == PLAN_NO_REF:
        diag = (
            "PLAN CONTEXT — Plan niet beschikbaar: deze kind-kaart heeft "
            "geen `plan_ref`-deliverable (de analyst heeft het plan niet "
            "gekoppeld via `add_plan_attachment`)."
        )
    else:
        diag = (
            "PLAN CONTEXT — Plan niet beschikbaar: onbekende fout tijdens "
            f"het laden van het plan-attachment (status={status})."
        )

    # Soften the guidance: only steer to report_impediment when the card is
    # genuinely un-actionable. A non-empty description means the analyst or
    # the card author wrote enough context in the title/description to
    # reconstruct the work from the source material — that path keeps the
    # executor productive and surfaces a `**Self-improve:**` note so the
    # dispatcher can clean up the dangling ref.
    description = (card_description or "").strip()
    if description:
        guidance = (
            "\n\nDe kaartbeschrijving hierboven bevat genoeg context om deze "
            "kaart alsnog op te pakken. Ga door met de implementatie op "
            "basis van die beschrijving en post onderaan een "
            "`**Self-improve:**` comment op deze kaart zodat de dispatch-"
            "loop de dangle opruimt. ALLEEN als de kaart zonder plan echt "
            "niet uitvoerbaar is: gebruik dan "
            "`mcp__cockpit-kanban__report_impediment`."
        )
    else:
        guidance = (
            "\n\nDe kaart heeft geen beschrijving die het werk draagt, dus "
            "is het plan-attachment de enige bron van waarheid. Gebruik "
            "`mcp__cockpit-kanban__report_impediment` om dit te signaleren."
        )
    return diag + guidance + "\n"


async def _resolve_plan_for_child(session, card) -> tuple[str, str | None, str | None, str | None]:
    """Return ``(status, plan_markdown, plan_deliverable_id, parent_card_id)``
    for a child card that holds a ``plan_ref`` deliverable.

    Looks up the ``plan_ref`` deliverable on the child, parses it for the
    parent_card_id and plan_deliverable_id, fetches the parent, and pulls
    the actual plan markdown from the parent's ``plan`` deliverable. The
    status distinguishes why resolution failed so ``_plan_context_section``
    can render an accurate diagnosis instead of one generic "could not be
    loaded" message:

    - ``PLAN_OK``                       — plan found and resolved
    - ``PLAN_NO_REF``                   — child carries no ``plan_ref`` deliverable
    - ``PLAN_DANGLING_PARENT``          — parent card no longer exists (deleted, never written)
    - ``PLAN_MISSING_ON_PARENT``        — parent exists, but the referenced ``plan`` deliverable isn't on it
    - ``PLAN_MALFORMED``                — ``plan_ref`` JSON doesn't parse or lacks required keys

    Async because resolving the plan needs a DB roundtrip (parent card
    lookup) — the brief's draft had this as a sync helper, which would
    have crashed the first time an executor session was dispatched.
    """
    plan_refs = [d for d in getattr(card, "deliverables", []) or []
                 if d.kind == "plan_ref"]
    if not plan_refs:
        return (PLAN_NO_REF, None, None, None)
    # A child should have at most one plan_ref, but be defensive: pick the
    # first and treat the rest as a soft sign of corruption (we still
    # surface the resolution attempt under PLAN_OK or the appropriate
    # failure status — never silently swallow an extra plan_ref).
    d = plan_refs[0]
    try:
        ref = json.loads(d.ref)
    except (TypeError, ValueError):
        return (PLAN_MALFORMED, None, None, None)
    parent_id = ref.get("parent_card_id")
    plan_id = ref.get("plan_deliverable_id")
    if not parent_id or not plan_id:
        return (PLAN_MALFORMED, None, plan_id, parent_id)
    parent = await get_card(session, parent_id)
    if parent is None:
        return (PLAN_DANGLING_PARENT, None, plan_id, parent_id)
    for pd in parent.deliverables:
        if pd.id == plan_id and pd.kind == "plan":
            return (PLAN_OK, pd.ref, plan_id, parent_id)
    return (PLAN_MISSING_ON_PARENT, None, plan_id, parent_id)


def _build_ship_instructions(ship_mode: str, project_path: str | None = None) -> str:
    """Build the standardised session-end workflow instructions.

    These instructions are provider-agnostic: they work the same for Claude Code,
    OpenCode, Codex CLI, or any other coding agent that spawns in a git worktree.
    A skill at ``.claude/skills/git-ship/SKILL.md`` mirrors this logic when the
    agent has filesystem access.

    The first block in the returned string is a pre-ship
    ``Feature-Compliance-Review (FCR)`` step — a subagent-call with cleared
    context that validates the implementation against the card spec BEFORE the
    numbered ship workflow runs. This mirrors engineer.md §6 (and the kanban
    decision doc ``reviewer-agent-decision.md``); the drift guard
    ``backend/tests/test_fcr_prompt_drift.py`` enforces that the prompt
    text stays identical across both mirrors (drift-val: kaart ``d9447e49``).
    """
    # Subshell-cwd reminder (kaart 1181b6fa…). A compound ``cd backend &&
    # pytest …`` persists the new cwd into the next Bash-call — that call
    # then sees a cwd it doesn't expect and fails with ``no such file or
    # directory`` (or runs against the wrong paths). The worktree-scope
    # callout above the Session-end workflow also covers this, but the
    # ship recipe runs many cd-into-backend/frontend-style commands itself
    # (npm ci, pytest, ruff …), so a one-line reminder right at the top of
    # this block re-anchors the rule before the agent reaches step 2.
    subshell_callout = (
        "**Subshell-cwd reminder (kaart 1181b6fa…):** compound "
        "``cd backend && pytest …`` keeps the new cwd into the next "
        "Bash-call. Wrap each compound ``cd`` in a subshell "
        "``( cd backend && … )`` (or use ``git -C <abs-path>``), so the "
        "cwd change stays scoped to that one command. See the persona's "
        "*Werkomgeving in worktree* section for the broader cwd-safety "
        "rules.\n\n"
    )
    # Pre-ship: Feature-Compliance-Review (FCR) — reviewed by a fresh-context
    # subagent before the numbered ship workflow begins. The prompt text must
    # stay byte-identical to the engineer.md mirror; update both in lockstep.
    #
    # Note on wording: avoid the literal ``move_card`` token here — the ship
    # workflow's last step is the canonical "move card to Done" call, and
    # ``_build_ship_instructions`` ordering tests use ``index('move_card')``
    # to find that step. A mention of ``move_card`` in this pre-ship block
    # would mask the real step behind an earlier match.
    feature_compliance_review = (
        "**Pre-ship: Feature-Compliance-Review (FCR) als pre-Done subagent-call** "
        "— `/code-review` / `iteration-loop verify` lezen de oorspronkelijke "
        "kaart-spec niet; deze stap vult dat gat. **Vóór je de kaart naar Done "
        "verplaatst**, draai je een subagent-call met **cleared context** die de "
        "implementatie toetst aan de oorspronkelijke kaart-spec: kaart-titel, "
        "kaart-beschrijving, en — expliciet — de huidige commit-hash die de "
        "implementatie bevat (typisch `git rev-parse HEAD`, door jou letterlijk "
        "meegegeven in de subagent-prompt; default: voor een sessie die net een "
        "FCR-triggerende commit heeft gemaakt).\n\n"
        "   **Voorkeur-volgorde van subagent-type** — kies het type op basis van "
        "wat de FCR moet doen. De `Agent`-tool default (`general-purpose`) trekt "
        "de hele toolset mee en kan bij kaarten met een lange beschrijving "
        "(>~2k tekens) of een grote diff-context **falen op \"Prompt is too "
        "long\"**; in de praktijk kost dat 1–3 retries of de agent breekt de "
        "FCR-stap af. Gebruik daarom standaard het smallere type:\n\n"
        "   1. **`Explore`** (default) — read-only, smalle toolset, past binnen "
        "élke prompt-lengte. Voor de standaard compliance-check (diff vs. "
        "kaart-beschrijving) is dit genoeg en het is wat je in ~95% van de "
        "feature-kaarten gebruikt. Bewust gekozen na een observatie dat twee "
        "opeenvolgende `general-purpose`-FCR-calls faalden en een derde poging "
        "met `Explore` meteen slaagde.\n"
        "   2. **`Plan`** — als de FCR een ontwerp-element of refactor-impact "
        "moet beoordelen en de bredere Plan-toolset nodig is.\n"
        "   3. **`general-purpose`** — alleen wanneer de FCR-shell-uitvoering "
        "nodig heeft die `Explore`/`Plan` niet bieden (bv. een commando draaien "
        "om een deliverable te valideren). Wees je bewust van de context-cap: "
        "combineer kaart-context en diff-context liever in twee kleinere calls "
        "dan in één grote, en val terug op een smaller type zodra je merkt dat "
        "de prompt tegen de limiet aan loopt.\n\n"
        "   Voer letterlijk deze prompt uit (eerste regel: vul `<COMMIT_HASH>` "
        "in met de letterlijke SHA van de implementatie-commit — typisch `git "
        "rev-parse HEAD` direct vóór deze subagent-call; geef het commando door "
        "in plaats van de SHA als je de hash niet beschikbaar hebt):\n\n"
        "   > Je reviewt een feature-implementatie tegen zijn oorspronkelijke\n"
        "   > specificatie. Inputs: de oorspronkelijke kaart-titel, -beschrijving, en\n"
        "   > — expliciet — de huidige commit-hash die de implementatie bevat.\n"
        "   > Vraag: doet de implementatie wat er gevraagd werd?\n"
        "   >\n"
        "   > **Bron-van-waarheid: de commit-hash, niet je eigen HEAD of de\n"
        "   > werkboom-state.** Jouw sessie draait in een geïsoleerde werkboom\n"
        "   > gebaseerd op `origin/master`, waar jouw HEAD identiek is aan\n"
        "   > `origin/master`. Reconstrueer de implementatie in twee stappen —\n"
        "   > de eerste is je **authoritative scope**, de tweede alleen context:\n"
        "   >\n"
        "   > - **Step 1 — scope (authoritative):** `git show <COMMIT_HASH> --stat`.\n"
        "   >   List de files/paden die deze commit zelf heeft veranderd. **Elke\n"
        "   >   file die hier NIET in staat is geen onderdeel van jouw review** —\n"
        "   >   hoort niet in je blocker-set, niet in je OK-redenering. Dit is\n"
        "   >   je scope; alles wat hierbuiten valt is scope-creep, niet jouw zaak.\n"
        "   > - **Step 2 — context:** `git diff origin/master..<COMMIT_HASH>`.\n"
        "   >   Cumulatieve delta tegen de origin/master-baseline. **Waarschuwing:**\n"
        "   >   `origin/master` kan tijdens de sessie bewogen hebben (parallel\n"
        "   >   chore-PR die merged is tussen commit en ship). Files die hierin\n"
        "   >   verschijnen maar NIET in `git show <HASH> --stat` staan zijn op\n"
        "   >   `origin/master` geland, niet door deze commit — negeer ze als\n"
        "   >   scope, niet als review-target.\n"
        "   >\n"
        "   > Dat is de *enige* manier waarop je de implementatie in deze set-up te\n"
        "   > zien krijgt; een lege diff met non-empty requirements is per definitie\n"
        "   > een reviewer-blokkade, geen OK.\n"
        "   >\n"
        "   > **Actionable refusal — twee verschillende fouten, twee verschillende\n"
        "   > retoures.**\n"
        "   > 1. **Unresolvable commit-hash.** Als `<COMMIT_HASH>` ontbreekt in deze\n"
        "   >    prompt, niet-resolveert via `git show <COMMIT_HASH>`, of beide\n"
        "   >    diff-commando's leeg zijn waar implementatie te verwachten is: stop\n"
        "   >    dan met een **actionable foutmelding**\n"
        "   >    (`unresolvable commit-hash: <wat er ontbreekt of niet matcht>`) en\n"
        "   >    **geen content-oordeel**. Een false-OK op een onresolveerbare hash\n"
        "   >    is precies de falsified-verdict die we hiermee voorkomen.\n"
        "   > 2. **Out-of-scope review.** Als je blocker-set files noemt die NIET in\n"
        "   >    `git show <COMMIT_HASH> --stat` voorkomen, dan zit je scope\n"
        "   >    verkeerd — die files horen niet bij deze implementatie. Retourneer\n"
        "   >    **`out-of-scope review: <files> not in <COMMIT_HASH>`** in plaats\n"
        "   >    van een content-oordeel. Een false-positive scope-creep-blokkade\n"
        "   >    (files van een parallelle merge op origin/master) is precies het\n"
        "   >    false-blokkade dat we hiermee voorkomen.\n"
        "   >\n"
        "   > Specifiek:\n"
        "   > - Elke requirement/bullet uit de beschrijving is geïmplementeerd.\n"
        "   > - De API/UI matcht de specificatie (naamgeving, gedrag, edge cases).\n"
        "   > - De implementatie integreert zonder siblings te breken.\n"
        "   > - Het deliverable dat in de samenvatting geclaimd wordt, is\n"
        "   >   daadwerkelijk aanwezig.\n"
        "   > - Wanneer de kaart een auto-recovery in een error-handler\n"
        "   >   beschrijft: verify dat de recovery in het uitvoeringspad zit\n"
        "   >   (dezelfde `if`-blok als de fout-detectie), niet als prose of\n"
        "   >   commentaar ná een `exit 1` (kanban-kaart `efb8187b…` /\n"
        "   >   `c06a3a2a…`; conventie: `docs/cockpit/recipe-writing-conventions.md`).\n"
        "   >\n"
        "   > Output: OK om te shippen, OF een lijst met blokkerende issues met\n"
        "   > `file:line`-refs. Dit is een **feature-compliance-check**, geen\n"
        "   > code-quality-check — die is al apart gelopen via `/code-review`.\n\n"
        "   **Carve-out — docs-only / analyst leaf-spike:** De FCR is een "
        "*feature-compliance*-check op een **code-diff**. Heeft je kaart geen "
        "feature-diff om te reviewen — een analyst leaf-spike "
        "(`work_type='analysis'`, geen `analyst_agent_id`) of een docs-only "
        "deliverable waarvan het resultaat een `docs/cockpit/*.md`-analyse is, "
        "zonder API/UI en zonder siblings om te breken — dan sla je de "
        "subagent-FCR **over** (spawn dus géén review-subagent; dat respecteert "
        "ook de top-level \"spawn geen agents tenzij gevraagd\"-richtlijn) en doe "
        "je in plaats daarvan een **inline** compliance-check tegen de "
        "kaart-eisen: is de gevraagde analyse-breedte gedekt, zijn de gevraagde "
        "artefacten opgeleverd, en zijn de follow-up-kaarten aangemaakt die de "
        "kaart vroeg. Alleen een kaart met een echte code-diff draait de "
        "subagent-FCR hierboven.\n\n"
        "   **Resultaat interpreteren:** OK → ga door naar stap 1 hieronder. "
        "Blokkerende issues → fix die eerst in dezelfde sessie (geen nieuwe "
        "kaart — FCR-blokkades zijn van jou, niet van het bord), herhaal de "
        "FCR tot `OK`, en ga dan pas naar de ship-stappen.\n\n"
    )

    sync = (
        "1. **Sync** — `git fetch origin` so you are up to date with the remote.\n"
    )
    # ``project_path`` interpolation (kanban card a962b209…): the dispatched
    # project is not always the meta project — pin the ``node_modules``
    # symlink to the *dispatched* project's main checkout, not the hardcoded
    # ``/home/vdvgu/claude-cockpit`` that only held for the meta project.
    # The legacy fallback (``project_path=None``) keeps the hardcoded string
    # so pre-existing tests/observers still match.
    #
    # Bash-quoting (kaart a962b209… blocker 2): ``project_path`` can contain
    # spaces or shell metacharacters (the dispatch target may live under
    # ``/scratch/scratchpad/My Project/...``). Both the `test -d` probe and
    # the `ln -s` source must wrap the path in double quotes; an unquoted
    # path with a space silently turns `[ -d /foo bar/... ]` into a syntax
    # error and `ln -s /foo bar/...` into a symlink to ``/foo``.
    frontend_root = (
        project_path.rstrip('/') if project_path
        else "/home/vdvgu/claude-cockpit"
    )
    nm_path = f"{frontend_root}/frontend/node_modules"
    bin_path = f"{nm_path}/.bin"
    # Pre-quoted forms for the bash snippets below. ``shlex.quote`` (kaart
    # a962b209… blocker C) wraps the path in single quotes and escapes any
    # embedded single quotes — a path like ``/tmp/prod$1/claude-cockpit``
    # survives variable expansion, command substitution, and embedded
    # double quotes, where a bare double-quote wrapper would still let
    # ``$``/``\```/``"`` through to the shell. Single-quoted shell strings
    # also tolerate spaces without the awkward escape sequences a manual
    # wrapper would have to grow. The legacy fallback path contains no
    # metacharacters, so ``shlex.quote`` produces an equivalent result.
    nm_q = shlex.quote(nm_path)
    bin_q = shlex.quote(bin_path)
    # Main-checkout path for the post-push local-master sync (kanban card
    # 5e83b6e0…, third iteration). The dispatcher inlines `project_path` =
    # the canonical checkout where `master` is checked out, pre-quoted via
    # ``shlex.quote`` (kaart a962b209… blocker C: single-quote-wrapped
    # paths survive spaces, ``$``, embedded quotes, and backslashes; a
    # bare double-quote wrapper still lets ``$``/``\```/``"`` through).
    # The legacy ``project_path=None`` fallback uses the hardcoded meta
    # project root, matching the frontend_root behaviour above.
    main_checkout_q = shlex.quote(frontend_root)
    tests = (
        "2. **Run frontend checks yourself before shipping (only when the branch "
        "touches ``frontend/``)** — there is no pre-push gate; nothing blocks a "
        "red push.  First check whether this branch changed any frontend code, "
        "and run ``npm run lint && npm run build`` only if it did — a docs-/"
        "backend-only branch would otherwise pay a multi-minute ``npm ci`` + "
        "build for zero frontend coverage:\n"
        "   ```bash\n"
        "   git fetch origin -q\n"
        "   FRONTEND_TOUCHED=$( { BASE=$(git merge-base HEAD origin/master); "
        "git diff --name-only \"$BASE\" -- frontend/; "
        "git ls-files --others --exclude-standard -- frontend/; } | head -1 )\n"
        "   if [ -n \"$FRONTEND_TOUCHED\" ]; then\n"
        "     # Fresh worktrees have no node_modules (gitignored). Fast path: "
        "when ``frontend/package-lock.json`` is unchanged vs origin/master, "
        f"symlink the main checkout's already-installed ``{nm_path}`` "
        "instead of paying a multi-minute ``npm ci``. Fall back to ``npm ci`` "
        "when the lockfile diverges (frontend deps changed) or main's "
        "``node_modules`` is absent / itself missing ``.bin/`` (partial).\n"
        "     # Card 15cc257d… also handled the partial-install trap: an "
        "interrupted ``npm ci`` leaves some scoped dirs but no ``.bin/``, which "
        "makes ``npm run lint`` die with ``eslint: not found`` and blocks a "
        "plain symlink. Move the partial aside (``mv``, not ``rm`` — ``rm`` is "
        "deny-listed) before bootstrapping.\n"
        "     # Note: ``<project-root>`` in the bash below is the absolute path "
        "of the dispatched project's main checkout — never the worktree path "
        "(that tree has no node_modules yet). The dispatcher inlines the exact "
        "string (see ``_build_ship_instructions`` in backend/app/kanban/"
        "dispatch.py, kaart a962b209…). Path is double-quoted so a project "
        "named ``My Project`` or ``prod$1`` doesn't break the test/ln.\n"
        "     ( cd frontend && \\\n"
        "       if [ -d node_modules ] && [ ! -d node_modules/.bin ]; then \\\n"
        "         mv node_modules \"../node_modules.partial-$(date +%s)\" && \\\n"
        "         echo \"moved partial node_modules aside (missing .bin/)\"; \\\n"
        "       fi && \\\n"
        "       if [ ! -d node_modules ]; then \\\n"
        "         BASE=$(git merge-base HEAD origin/master) && \\\n"
        "         if git diff --quiet \"$BASE\" origin/master -- frontend/package-lock.json \\\n"
        f"            && [ -d {bin_q} ]; then \\\n"
        f"           ln -s {nm_q} node_modules && \\\n"
        f"           echo \"bootstrapped frontend/node_modules via symlink (lockfile matches master)\"; \\\n"
        "         else \\\n"
        "           npm ci; \\\n"
        "         fi; \\\n"
        "       fi && \\\n"
        "       npm run lint && npm run build \\\n"
        "     )   # only proceed once green\n"
        "   else\n"
        "     echo 'geen frontend-diff — gate overgeslagen'\n"
        "   fi\n"
        "   ```\n"
        "   A branch that *does* touch ``frontend/`` (including a mixed "
        "frontend+docs diff) runs the gate unconditionally; only a branch with "
        "no ``frontend/`` change skips it.  "
        "Do **not** run backend pytest locally in this repo — that step was removed "
        "deliberately (shared box; concurrent dispatched sessions running full pytest "
        "caused multi-minute stalls / SSH idle-disconnects).  GitHub Actions "
        "(``quality.yml``) runs ruff + pytest against your push and is the backend "
        "gate; it also re-runs the frontend checks as a backstop, but by then the "
        "work may already be merged — it is not a substitute for checking the "
        "frontend yourself first.  If a frontend check fails, fix it, re-run, and "
        "only ship once green.  Never ship a known-red frontend check.\n"
        "\n"
        "   **Layout-chain guard (kaart 41a75826…):** raakt je diff een "
        "layout-afhankelijke prop (``fillArea``, ``flexibleHeight``, of iets "
        "anders dat erop rekent dat ``flex-1 min-h-0`` van de container tot aan "
        "de widget doorloopt), mock dan **niet de component wiens layout-keten "
        "in scope is** — een ``vi.mock(\"./Child\", () => ({ Child: () => null "
        "}))`` op precies die child maakt elke keten-assertie vacuüm: de test "
        "blijft groen terwijl de productie-keten halverwege breekt.  Stub in "
        "plaats daarvan de *leaves* die jsdom niet aankan (xterm's "
        "``TerminalView``, pollende hooks) en assert de className-keten hop "
        "voor hop op de echte component; zet de bug eenmalig terug om te zien "
        "dat de test écht faalt.\n"
    )
    commit = (
        "3. **Commit your work** — "
        "**Schema/column-rename sweept:** als je diff een `ALTER TABLE "
        "... RENAME COLUMN` (of een andere model/Pydantic-schema-rename) "
        "introduceert, draai dan `bash scripts/check-schema-rename-coverage.sh "
        "--strict` en werk elke hit bij vóór de commit. Een gemiste "
        "referentie levert een silent-red test op CI — net zoals "
        "kanban-kaart `ad15e08271c242238db239a90dc559d4` documenteerde voor "
        "commit 558ca55 (de `provider` → `cli` rename shipte met 2 latent-red "
        "tests). Het script grept `backend/app/` én `backend/tests/` op "
        "resterende verwijzingen. "
        "**Bron-analysedoc bijwerken (na een gefilede follow-up):** rondt je "
        "kaart een follow-up af die in zijn beschrijving of "
        "`metadata.facet`/`metadata.parent_card` naar een "
        "`docs/cockpit/*.md`-analyse-/designdoc verwijst, voeg dan **vóór de "
        "commit** een korte `✅ Geïmplementeerd (kaart <id>)`-regel toe aan de "
        "paragraaf van dat doc die de gap beschreef. Zo blijft het doc niet als "
        "\"niets geïmplementeerd, alleen analyse + gefilede gaten\" staan "
        "terwijl zijn eigen follow-ups al gemerged zijn (geobserveerd op de "
        "vier facet-docs van synthese-kaart `c980a926…`: 33 van 35 follow-ups "
        "waren al gemerged terwijl 2 van de 4 docs zich nog als pure analyse "
        "presenteerden). **De bron is `metadata[\"spec_doc\"]`** — als de "
        "kaart-context boven aan deze prompt een regel `**Brondoc (spec_doc):** "
        "…` toont, is dat het docpad dat je moet bijwerken. Geen `spec_doc`-regel "
        "in de prompt én geen analysedoc-verwijzing in beschrijving/facet/"
        "parent_card? Sla deze stap over. **Geen retroactieve verplichting** — "
        "alleen het doc dat jouw kaart raakt; raakt je kaart geen analysedoc, "
        "sla je deze stap over. "
        "make sure every change is committed to the current branch.\n"
    )

    retro_direct = _build_session_retro_step(step_number=6)
    retro_pr = _build_session_retro_step(step_number=7)

    if ship_mode == "direct":
        shipping = (
            "4. **Ship (direct mode)** — merge your branch into master and push. "
            "You are in a linked worktree while ``master`` is checked out in the "
            "main working copy, so checking out ``master`` here fails with "
            "``'master' is already used by worktree at ...``. Merge through a "
            "throwaway detached worktree instead — it never touches your current "
            "checkout:\n"
            "   ```bash\n"
            "   BRANCH=$(git rev-parse --abbrev-ref HEAD)\n"
            "   # Pre-flight: the detached worktree below only sees COMMITTED "
            "state. Uncommitted changes to TRACKED files would merge as a silent "
            "no-op (\"Everything up-to-date\") — abort so you commit them first.\n"
            "   # Tracked-only on purpose: `git ls-files --others "
            "--exclude-standard` used to be part of this condition and blocked "
            "ships on untracked files belonging to OTHER concurrent sessions "
            "sharing this worktree mount (544 files in a foreign "
            "`.tmp-measure-token-saver/` harness dir, kanban card c28e576d…). "
            "Those files can't cause the silent no-op this guard exists for — "
            "the merge never reads them — and since `rm` is deny-listed the only "
            "recovery was `mv`-ing another session's work aside. "
            "`git status --porcelain | grep -v '^??'` keeps every tracked state "
            "(` M`, `M `, `MM`, `A `, `D `) so a `git add` without a "
            "`git commit` is still refused, and drops only the `??` lines.\n"
            "   # The trailing `--` is load-bearing: it separates revisions "
            "from paths. Without it, a file named `HEAD` anywhere in the repo "
            "root makes the argument ambiguous and git exits 128 with `fatal: "
            "ambiguous argument 'HEAD': both revision and filename` — which, "
            "under `if ! ...`, reads as \"tree is dirty\" and aborts EVERY "
            "ship with a bogus uncommitted-changes error (kanban card "
            "7dd8a3dd…). `--` costs nothing and makes the guard immune to "
            "that class.\n"
            "   if ! git diff --quiet HEAD -- || [ -n \"$(git status --porcelain "
            "| grep -v '^??')\" ]; then\n"
            "     echo 'ERROR: uncommitted changes to tracked files in this "
            "worktree — run git add + git commit (step 3), then re-run.' >&2; "
            "exit 1\n"
            "   fi\n"
            "   # Untracked files are advisory, never fatal: a brand-new file you "
            "forgot to `git add` would ship as a silent omission, so list them — "
            "but do NOT exit, because most untracked noise here belongs to a "
            "concurrent session.\n"
            "   UNTRACKED=$(git ls-files --others --exclude-standard)\n"
            "   if [ -n \"$UNTRACKED\" ]; then\n"
            "     echo 'NOTE: untracked files present (not blocking the ship). "
            "If any of these are YOURS and belong in this card, git add + git "
            "commit them now:' >&2\n"
            "     printf '%s\\n' \"$UNTRACKED\" | head -20 >&2\n"
            "   fi\n"
            "   # Throwaway worktree location. Two constraints, pulling in "
            "opposite directions:\n"
            "   #   1. NOT under `mktemp -d` / `/tmp`. The Bash tool's harness "
            "can reap `/tmp` between calls, so a /tmp-resident worktree may "
            "vanish mid-ship: the merge commit lands in a now-missing "
            "checkout, the subsequent `git push` fails with a spurious "
            "non-fast-forward, and the local merge is lost. (kanban card "
            "01aa1ef5…)\n"
            "   #   2. NOT under `.git/worktrees/` either. That was the "
            "previous fix for (1), and it is actively harmful: "
            "`.git/worktrees/<name>/` is ALSO where git keeps its own admin "
            "files (HEAD, index, MERGE_*, commondir, gitdir) for that very "
            "worktree. Placing the CHECKOUT at the same path means the two "
            "overlap — so `git -C \"$WT\" add -A` in the conflict carve-out "
            "staged git's own admin files, and one ship through that branch "
            "committed ten of them to the repo root. From then on every "
            "`git worktree add` checked the tracked copies out over git's "
            "live admin files (\"fatal: .../index: index file smaller than "
            "expected\") and no card could ship at all. (kanban card "
            "7dd8a3dd…)\n"
            "   # `$HOME/.cache/` satisfies both: persistent across Bash "
            "calls (not reaped), and outside every git working tree and "
            "gitdir. Note git still registers the admin slot under "
            "`.git/worktrees/ship-merge-$$` — that is correct and harmless; "
            "only the CHECKOUT must live elsewhere.\n"
            "   SHIP_TMP=\"${HOME}/.cache/cockpit-ship\"\n"
            "   mkdir -p \"$SHIP_TMP\"\n"
            "   WT=\"$SHIP_TMP/ship-merge-$$\"\n"
            "   # Main-checkout path discovery (kanban card 5e83b6e0…, third "
            "iteration). The ship-worktree is a detached checkout that "
            "cannot update `master` itself — only the canonical checkout "
            "where `master` is checked out can do that. The dispatcher "
            "inlines `project_path` (= the main-checkout path) into the "
            "prompt (same source as the `<project-root>` used for the "
            "node_modules symlink), so we reuse it here. The skill mirror "
            "in `.claude/skills/git-ship/SKILL.md` is self-discovering via "
            "`dirname $(git rev-parse --git-common-dir)` because the skill "
            "must work without the dispatch prompt.\n"
            "   MAIN_CHECKOUT=\"<main-checkout>\"\n"
            "   # Local-master divergence guard (kanban card 5e83b6e0…). Step 1 "
            "already fetched origin, but to be defensive we fetch again here — "
            "this worktree may have been running between step 1 and step 4, and "
            "a concurrent session could have pushed to origin in that window. "
            "The throwaway worktree MUST base on LOCAL `master` (the "
            "integration point, not the last-pushed remote state) — otherwise "
            "a concurrent session's not-yet-pushed commits would be stranded "
            "when we push to origin. The negation of "
            "`--is-ancestor origin/master master` catches both \"origin ahead\" "
            "(origin has commits local doesn't) and the rarer \"diverged\" "
            "case (both sides have new commits); in either state, a push from "
            "local `master` would be rejected as non-fast-forward. Fail-fast "
            "with `report_impediment` and a clear remediation rather than "
            "producing a stale merge or a useless \"Everything up-to-date\" "
            "push.\n"
            "   git fetch origin -q\n"
            "   if ! git merge-base --is-ancestor origin/master master "
            "2>/dev/null; then\n"
            "     # Label semantics (kanban card 5e83b6e0…, second "
            "iteration): in `git rev-list --count A..B`, A..B enumerates "
            "commits reachable from B but NOT from A — i.e. commits B has "
            "that A doesn't. So `master..origin/master` = commits "
            "`origin/master` has that local `master` doesn't = how far "
            "local is BEHIND; and `origin/master..master` = the symmetric "
            "AHEAD count. The previous wiring had the two swapped, which "
            "printed `ahead=2 behind=0` while local master was actually 2 "
            "BEHIND origin. Don't swap them back.\n"
            "     BEHIND=$(git rev-list --count master..origin/master "
            "2>/dev/null || echo \"?\")\n"
            "     AHEAD=$(git rev-list --count origin/master..master "
            "2>/dev/null || echo \"?\")\n"
            "     echo \"ERROR: local master is STALE — origin/master has "
            "commits local doesn't have.\" >&2\n"
            "     echo \"  ahead=$AHEAD behind=$BEHIND (master vs "
            "origin/master)\" >&2\n"
            "     echo \"  Reconcile: git -C <main-checkout> pull --rebase "
            "origin master\" >&2\n"
            "     echo \"  Then re-run the ship from this worktree. "
            "report_impediment.\" >&2\n"
            "     exit 1\n"
            "   fi\n"
            "   git worktree add --detach \"$WT\" master\n"
            "   # 0-byte-index guard. A predecessor that aborted mid-ship in "
            "the shared gitdir can leave this slot's `index` truncated to 0 "
            "bytes, and `git worktree add` reports success anyway — the "
            "corruption only surfaces on the next command, as "
            "`fatal: …/index: index file smaller than expected`. Worse, "
            "`git worktree remove --force` then refuses with `is not a "
            "working tree`, so the slot is orphaned and the ship needs a "
            "manual rescue (kanban card 608e2a27…). The checkout already "
            "holds the right tree; only the index needs rebuilding, which "
            "`read-tree HEAD` does from the slot's own HEAD. Detect and "
            "repair here, BEFORE the merge, so the recovery is automatic "
            "instead of ~4 manual tool calls.\n"
            "   WT_GITDIR=$(git -C \"$WT\" rev-parse --absolute-git-dir)\n"
            "   if [ ! -s \"$WT_GITDIR/index\" ]; then\n"
            "     echo \"WARN: 0-byte index in $WT_GITDIR — rebuilding from "
            "HEAD (aborted predecessor in the shared gitdir).\" >&2\n"
            "     if ! git -C \"$WT\" read-tree HEAD; then\n"
            "       echo \"ERROR: read-tree HEAD failed — slot $WT is "
            "unusable; report_impediment.\" >&2\n"
            "       exit 1\n"
            "     fi\n"
            "   fi\n"
            "   if ! git -C \"$WT\" merge --no-ff \"$BRANCH\" -m \"Merge $BRANCH\"; then\n"
            "     # CONFLICT path: try the generated-doc-index carve-out, "
            "otherwise report_impediment. The condition MUST be "
            "machine-checkable — a handwritten conflict always falls "
            "through (kanban card efb8187b…). A *non-empty subset* of "
            "{docs/cockpit/README.md, docs/cockpit/llms.txt} also passes "
            "(kanban card 72db7429…): both files are regenerated from "
            "frontmatter anyway, so a conflict in only one of the two is "
            "the same class as both — `comm -23` over the expected set "
            "surfaces any path that ISN'T a generated file (the actual "
            "exclusion predicate).\n"
            "     CONFLICTED=$(git -C \"$WT\" diff --name-only --diff-filter=U "
            "| LC_ALL=C sort -u)\n"
            "     EXPECTED=$(printf 'docs/cockpit/README.md\\ndocs/cockpit/"
            "llms.txt\\n' | LC_ALL=C sort -u)\n"
            "     NON_GENERATED=$(comm -23 <(printf '%s\\n' \"$CONFLICTED\") "
            "<(printf '%s\\n' \"$EXPECTED\"))\n"
            "     if [ -n \"$CONFLICTED\" ] && [ -z \"$NON_GENERATED\" ]; then\n"
            "       # Subset predicate passed. README.md is *partially* "
            "generated — only the block between "
            "`<!-- BEGIN GENERATED DOC INDEX -->` and "
            "`<!-- END GENERATED DOC INDEX -->` is owned by the regenerate "
            "script; the surrounding hand-curated prose (feature→canonical-doc "
            "mapping, \"Regels\", etc.) must NOT be silently clobbered. So "
            "if README.md is in the conflict set, verify every conflict hunk "
            "sits between the markers — anything outside falls through "
            "(kanban card 72db7429…). The check runs BEFORE the "
            "`checkout --theirs` below clears the merge markers; once "
            "cleared, the hunks are gone and the check has nothing to look "
            "at. The structural invariant in "
            "`backend/tests/test_ship_recipe_drift.py::test_readme_marker_check_sits_between_enumeration_and_open` "
            "pins this order.\n"
            "       if printf '%s\\n' \"$CONFLICTED\" | grep -qx 'docs/cockpit/README.md'; then\n"
            "         README_FILE=\"$WT/docs/cockpit/README.md\"\n"
            "         BEGIN_LINE=$(grep -nF '<!-- BEGIN GENERATED DOC INDEX' "
            "\"$README_FILE\" 2>/dev/null | head -1 | cut -d: -f1)\n"
            "         END_LINE=$(grep -nF '<!-- END GENERATED DOC INDEX -->' "
            "\"$README_FILE\" 2>/dev/null | head -1 | cut -d: -f1)\n"
            "         if [ -z \"$BEGIN_LINE\" ] || [ -z \"$END_LINE\" ]; then\n"
            "           echo \"ERROR: docs/cockpit/README.md missing "
            "BEGIN/END GENERATED DOC INDEX markers — falling back to "
            "report_impediment.\" >&2\n"
            "           printf '  conflicted: %s\\n' $CONFLICTED >&2\n"
            "           echo \"Conflicted worktree left at $WT for inspection "
            "(not removed).\" >&2\n"
            "           exit 1\n"
            "         fi\n"
            "         CONFLICT_LINES=$(grep -nE '^(<<<<<<< |=======$|"
            ">>>>>>> )' \"$README_FILE\" 2>/dev/null || true)\n"
            "         if [ -n \"$CONFLICT_LINES\" ]; then\n"
            "           OUTSIDE=$(awk -F: -v b=\"$BEGIN_LINE\" -v e=\"$END_LINE\" "
            "'$1 < b || $1 > e { print }' <<< \"$CONFLICT_LINES\")\n"
            "           if [ -n \"$OUTSIDE\" ]; then\n"
            "             echo \"ERROR: docs/cockpit/README.md has conflict "
            "hunks outside the generated block — falling back to "
            "report_impediment.\" >&2\n"
            "             printf '  offending lines:\\n%s\\n' \"$OUTSIDE\" >&2\n"
            "             echo \"Conflicted worktree left at $WT for inspection "
            "(not removed).\" >&2\n"
            "             exit 1\n"
            "           fi\n"
            "         fi\n"
            "       fi\n"
            "     else\n"
            "       echo \"ERROR: merge conflict in non-generated files (or "
            "empty conflict set) — falling back to report_impediment.\" >&2\n"
            "       printf '  conflicted: %s\\n' $CONFLICTED >&2\n"
            "       echo \"Conflicted worktree left at $WT for inspection "
            "(not removed).\" >&2\n"
            "       exit 1\n"
            "     fi\n"
            "     # Carve-out: at least one of the two generated doc-index "
            "files is conflicted. `--theirs` clears the merge markers; the "
            "next regenerate step overwrites both files anyway, so `--theirs` "
            "vs `--ours` is moot in practice. The script MUST be invoked "
            "through the worktree path — "
            "`scripts/generate-doc-index.py:78` derives its repo-root from "
            "`Path(__file__).resolve().parent.parent`, so "
            "`./scripts/generate-doc-index.py` would regenerate the calling "
            "shell's tree, not $WT.\n"
            "     git -C \"$WT\" checkout --theirs -- docs/cockpit/README.md "
            "docs/cockpit/llms.txt\n"
            "     \"$WT\"/scripts/generate-doc-index.py\n"
            "     if ! \"$WT\"/scripts/generate-doc-index.py --check --strict; then\n"
            "       echo \"ERROR: generate-doc-index.py --check --strict "
            "failed after regenerate.\" >&2\n"
            "       exit 1\n"
            "     fi\n"
            "     # Stage the two generated files BY NAME, never `add -A`. A "
            "blind `add -A` stages everything under the worktree root, which "
            "is how ten of git's own admin files (HEAD, index, MERGE_*, …) "
            "got committed to the repo root and broke every subsequent ship "
            "(kanban card 7dd8a3dd…). Moving the worktree out of `.git/` "
            "already removes that specific exposure; an explicit path list "
            "closes the class — the carve-out is only ever entitled to commit "
            "the files it just regenerated, so it should only ever be able to "
            "stage those.\n"
            "     git -C \"$WT\" add -- docs/cockpit/README.md "
            "docs/cockpit/llms.txt\n"
            "     git -C \"$WT\" commit --no-edit\n"
            "   fi\n"
            "   if git -C \"$WT\" push origin HEAD:master; then\n"
            "     # Post-push local-master sync (kanban card 5e83b6e0…, "
            "third iteration). The divergence guard above bases on local "
            "`master`, so a successful push that doesn't also move local "
            "`master` leaves the guard tripped on every subsequent ship on "
            "this multi-session box — even though the divergence is fully "
            "explained by *our own* push. The cleanest way to sync the main "
            "checkout is `git -C \"$MAIN_CHECKOUT\" pull --ff-only origin "
            "master`, which in one step (a) fast-forwards the local master "
            "ref and (b) updates the index AND working tree in the main "
            "checkout — so the dev-stack (`cockpit.sh`) keeps running "
            "against the latest tree. The throwaway `$WT` is detached HEAD "
            "and cannot update master itself, which is why the sync runs "
            "against `$MAIN_CHECKOUT` where master is actually checked out.\n"
            "     # `git pull --ff-only` REFUSES if the main checkout's "
            "working tree has changes that would be overwritten by the "
            "merge (e.g. a concurrent agent editing a file the merge "
            "also touches) — that is the right default, we do not want "
            "to clobber in-flight edits. In that case we fall back to "
            "`git update-ref refs/heads/master origin/master`, which "
            "updates only the ref and at least keeps the divergence "
            "guard from tripping on the next ship. The main checkout's "
            "working tree stays on the user's conflicting edits (they "
            "are preserved; the merged files are not on disk yet) until "
            "someone resolves the conflict and runs `git pull --ff-only` "
            "by hand — but the push still landed, that's what matters. "
            "Fail-open in both cases: a successful push must NEVER be "
            "reverted by a local-sync error.\n"
            "     # Why `update-ref` here and not `git fetch origin "
            "master:master`? The fetch refspec `master:master` is "
            "exactly what we need, but git REFUSES to update a ref "
            "that's currently checked out in another worktree "
            "(\"refusing to fetch into branch 'refs/heads/master' "
            "checked out at …\"). `update-ref` writes the ref directly "
            "and bypasses that check — at the cost of leaving the "
            "working tree stale. That's the trade-off we accept in the "
            "fallback: the TYPICAL case is a clean main checkout, where "
            "`pull --ff-only` keeps it fully current; the EDGE case (a "
            "concurrent agent editing a file the merge also touches) "
            "gets a ref-only update, the working tree stays on the "
            "user's edits, and the next person to resolve the conflict "
            "runs `git pull --ff-only` themselves. `update-ref` was "
            "rejected as the SECOND-iteration PRIMARY path because it "
            "left a clean main checkout stale (the 74-staged-deletions "
            "bug, observed in the impediment on this card); here it's a "
            "deliberate fallback that ONLY fires when the working tree "
            "is already in a state we can't safely overwrite.\n"
            "     if ! git -C \"$MAIN_CHECKOUT\" pull --ff-only origin "
            "master 2>/dev/null; then\n"
            "       if ! git -C \"$MAIN_CHECKOUT\" update-ref refs/heads/"
            "master origin/master 2>/dev/null; then\n"
            "         echo \"WARN: kon lokale master in hoofd-checkout "
            "niet bijwerken naar origin/master — volgende ship kan op "
            "de divergentie-guard lopen, herstel handmatig met 'git "
            "-C \\\"$MAIN_CHECKOUT\\\" pull --ff-only origin master' (en "
            "los eventuele conflicten op die de working tree vuil "
            "houden).\" >&2\n"
            "       fi\n"
            "     fi\n"
            "     # Merge landed on master — delete the now-dead remote "
            "branch. GitHub's `delete_branch_on_merge` (enabled 2026-07-07) "
            "only fires when a *PR* merges; this route closes no PR, so "
            "without this line every shipped card leaves a branch on "
            "`origin` forever (kanban card 3027671c…: 7 fully-merged "
            "branches piled up over 6 weeks). Fail-open — an already-deleted "
            "branch must not kill the ship. Only the REMOTE branch goes; the "
            "local branch stays, so redispatch/resume off it still works.\n"
            "     git push origin --delete \"$BRANCH\" || echo \"WARN: kon "
            "origin/$BRANCH niet verwijderen (al weg?)\"\n"
            "   else\n"
            "     # Push rejected (master moved / protected). Keep "
            "`origin/$BRANCH` alive — the pull-request fallback needs it. "
            "Deleting here would strand the work on exactly the path where "
            "the branch is still required.\n"
            "     echo \"WARN: push naar master afgewezen — origin/$BRANCH "
            "bewaard voor de pull-request-fallback.\" >&2\n"
            "   fi\n"
            "   git worktree remove --force \"$WT\"\n"
            "   ```\n"
            "   **Carve-out semantics.** If the merge block hits a conflict, "
            "the script enumerates the conflict set with ``git diff --name-only "
            "--diff-filter=U``. When that set is a **non-empty subset** of "
            "``{docs/cockpit/README.md, docs/cockpit/llms.txt}``, the script "
            "runs the carve-out automatically and the merge completes inline "
            "— no human intervention. ``docs/cockpit/llms.txt`` is fully "
            "regenerated; ``docs/cockpit/README.md`` is regenerated only "
            "between the ``<!-- BEGIN GENERATED DOC INDEX -->` and "
            "``<!-- END GENERATED DOC INDEX -->`` markers, and the carve-out "
            "verifies (via line numbers) that every conflict hunk sits "
            "inside that block — a conflict outside the markers falls through "
            "to ``report_impediment`` (kanban card 72db7429…). Both files are "
            "regenerated by ``scripts/generate-doc-index.py`` from the "
            "frontmatter of ``docs/cockpit/*.md``; concurrent docs-sessions "
            "each regenerate from their own frontmatter snapshot, the merged "
            "frontmatter is the union, and the regenerate inside ``$WT`` "
            "reconciles from that union. **Why the conflict must remain "
            "visible** "
            "(``.gitattributes``-alternative rejected): a ``merge=ours`` rule "
            "for both paths would suppress the conflict entirely and silently "
            "keep master's pre-regeneration index, losing any new frontmatter "
            "added on the branch until someone manually re-runs the script. "
            "The \"conflict → regenerate\" loop is the right pattern — the "
            "conflict acts as a freshness alarm.\n\n"
            "   If the carve-out rejects (a handwritten file is in the conflict "
            "set), the worktree at ``$WT`` is left in its conflicted state for "
            "inspection and the script exits 1. Follow the existing rule, "
            "``report_impediment`` naming all conflicting files so a human "
            "can resolve it; never force-push or discard either side of the "
            "conflict.\n"
            "5. **Attach the deliverable** — ``attach_deliverable`` with "
            "``kind=\"branch\"`` and ``ref=<your-branch-name>``.\n"
            + retro_direct +
            "7. **Move the card to Done** — ``move_card`` with ``column=\"Done\"`` "
            "and ``summary=<what you did>``, a few sentences on the work you "
            "completed.  ``summary`` is required for this move; the call is "
            "rejected without it.  **Product-taal** (conventie §5 van "
            "`docs/cockpit/kanban-conventions.md`, kaart `4358fe0a…`): leid "
            "met één zin *productbetekenis* (wat kan de product owner nu "
            "doen / zien / beslissen dat voorheen niet kon), zet de "
            "engineering-detail (bestanden, endpoints, tests) erna. Voorbeeld: "
            "niet \"POST /usage/subscription + SubscriptionUsageCard.tsx\", wél "
            "\"Product owner kan nu het abonnementsverbruik zien op de "
            "Usage-pagina (POST /usage/subscription + SubscriptionUsageCard.tsx)"
            "\". Een kale engineering-summary voldoet aan de gate maar niet "
            "aan de product-taal-conventie. Voor een "
            "``report_impediment`` met ``options``: druk de opties uit als "
            "**producttrade-offs**, niet als implementatie-forks.  "
            "The backend will kill this session and remove the worktree.\n"
        )
    else:
        shipping = (
            "4. **Ship (pull-request mode)** — push your branch, open a PR, and "
            "queue it to merge automatically once checks pass:\n"
            "   ```bash\n"
            "   gh auth status || { echo 'gh unavailable — manual PR needed'; exit 1; }\n"
            "   git push -u origin HEAD\n"
            "   gh pr create --draft --base master --fill\n"
            "   gh pr ready\n"
            "   gh pr merge --auto --squash\n"
            "   ```\n"
            "   Capture the PR URL from ``gh pr create`` output.\n"
            "   If ``gh`` is unavailable: push the branch, ``comment`` with the "
            "branch name and note that a manual PR is needed, then stop here — "
            "do not move the card to Done.\n"
            "5. **Wait for the merge gate** — poll until the PR merges or a "
            "check fails; do not skip this, the card's next step depends on it:\n"
            "   ```bash\n"
            "   ITER=0\n"
            "   while true; do\n"
            "     DATA=$(gh pr view --json state,mergeStateStatus,statusCheckRollup)\n"
            "     STATE=$(echo \"$DATA\" | jq -r '.state')\n"
            "     MERGE_STATUS=$(echo \"$DATA\" | jq -r '.mergeStateStatus')\n"
            "     echo \"PR state: $STATE mergeStateStatus=$MERGE_STATUS\"\n"
            "     if [ \"$STATE\" = \"MERGED\" ]; then\n"
            "       break\n"
            "     fi\n"
            "     if [ \"$STATE\" = \"CLOSED\" ]; then\n"
            "       echo 'PR was closed without merging'; exit 1\n"
            "     fi\n"
            "     # mergeStateStatus=BLOCKED also just means \"checks still running\" "
            "— only a genuinely failed/cancelled/timed-out check is a real failure.\n"
            "     FAILED=$(echo \"$DATA\" | jq '[.statusCheckRollup[]? | "
            "select((.conclusion // .status // .state // \"\") | "
            "test(\"FAILURE|ERROR|CANCELLED|TIMED_OUT|ACTION_REQUIRED\"; \"i\"))] "
            "| length')\n"
            "     if [ \"$FAILED\" -gt 0 ]; then\n"
            "       echo 'A required check failed'; exit 1\n"
            "     fi\n"
            "     if [ \"$MERGE_STATUS\" = \"DIRTY\" ]; then\n"
            "       echo 'PR has merge conflicts with the base branch'; exit 1\n"
            "     fi\n"
            "     ITER=$((ITER + 1))\n"
            "     if [ \"$ITER\" -ge 40 ]; then\n"
            "       echo 'Timed out after ~20 minutes waiting for PR to merge'; "
            "exit 1\n"
            "     fi\n"
            "     sleep 30\n"
            "   done\n"
            "   ```\n"
            "6. **Attach the deliverable** — ``attach_deliverable`` with "
            "``kind=\"pr\"`` and ``ref=<PR-URL>`` (or ``kind=\"branch\"`` if no PR).\n"
            + retro_pr +
            "8. **Move the card** — if the PR merged, ``move_card`` with "
            "``column=\"Done\"`` and ``summary=<what you did>``, a few sentences "
            "on the work you completed (``summary`` is required for this move; "
            "the call is rejected without it).  **Product-taal** (de "
            "product-taal-conventie §5 van "
            "`docs/cockpit/kanban-conventions.md`, kaart `4358fe0a…`): leid "
            "met één zin *productbetekenis*, zet de engineering-detail erna. "
            "Een kale engineering-summary voldoet aan de gate maar niet aan "
            "de product-taal-conventie.  If the poll loop exited because a "
            "check failed, the PR was closed, or the wait timed out, call "
            "``report_impediment`` instead so a human can look at it — do not "
            "move to Done. Voor een ``report_impediment``: druk ``options`` als "
            "*producttrade-offs* uit, niet als implementatie-forks.\n"
        )

    # ``<main-checkout>`` is a placeholder for the canonical checkout where
    # ``master`` is checked out — interpolated from ``project_path`` above
    # via the pre-quoted ``main_checkout_q``. The skill mirror in
    # ``.claude/skills/git-ship/SKILL.md`` is self-discovering via
    # ``dirname $(git rev-parse --git-common-dir)`` because the skill
    # must work without the dispatch prompt. Both forms end up identical
    # on the meta project (``/home/vdvgu/claude-cockpit``).
    return (
        subshell_callout
        + feature_compliance_review
        + sync
        + tests
        + commit
        + shipping
    ).replace("<main-checkout>", main_checkout_q)


def _build_session_retro_step(step_number: int = 6) -> str:
    """Step injected before ``move_card → Done`` (after ``attach_deliverable``
    for executor/engineer cards; directly before the parent move for analyst
    cards).

    Inlines the headless-trim version of the ``session-retro`` skill so the
    step works for any spawned agent (whether or not it can read the skill
    files). Mirrors the source of truth at
    ``.claude/skills/session-retro/SKILL.md`` — keep them in sync.

    Wired for both phases: executor/engineer cards run it after shipping,
    analyst cards run it right before the ``move_parent → Done`` exit (see
    ``_build_analyst_session_end_instructions``).

    The ``step_number`` argument lets the caller pick the right place in the
    numbered sequence — 6 in direct mode (attach=5, move=7), 7 in
    pull-request mode (attach=6, move=8), 1 in the analyst flow (move=2).
    """
    return (
        f"{step_number}. **Run the session-end retro** — invoke the "
        "``session-retro`` skill "
        "(read ``.claude/skills/session-retro/SKILL.md`` for the full procedure). "
        "It walks this session backwards, applies a four-pass filter "
        "(systemic, materieel, actionable, novel), dedupes against existing "
        "Backlog/Impediment cards, and files 0–N ``[self-improve]`` cards. Even "
        "a clean session gets a no-op ``comment`` on this card so a follow-up "
        "sweeper can see the retro ran. Keep it light — under a minute, "
        "~3–5 tool calls; don't burn the ship budget writing lengthy "
        "descriptions.\n"
    )


def _build_analyst_session_end_instructions() -> str:
    """Session-end workflow for analyst-phase cards.

    Analyst sessions are planning-only — no code is shipped, no worktree
    merge happens — so they get a lighter close than
    ``_build_ship_instructions``: run the retro, then the existing
    ``move_card(parent → Done)`` exit. No sync/test/commit/merge steps.
    """
    retro = _build_session_retro_step(step_number=1)
    move = (
        "2. **Move the parent card to Done** — ``move_card`` on the parent "
        "with ``column=\"Done\"`` and a summary of the plan (``summary`` is "
        "required for this move; the call is rejected without it). This is "
        "your exit signal — the backend then kills this session and removes "
        "the worktree. **Product-taal** (conventie §5 van "
        "`docs/cockpit/kanban-conventions.md`, kaart `4358fe0a…`): leid met "
        "één zin *productbetekenis* (wat kan de product owner nu doen / "
        "zien / beslissen dat voorheen niet kon), zet de engineering-"
        "detail (kind-kaart-titels of deliverable-refs) als opsomming "
        "erna. Een kale \"Plan opgesplitst in N taken\" voldoet aan de gate "
        "maar niet aan de product-taal-conventie. Voor een "
        "``report_impediment``: druk de ``options`` als "
        "**producttrade-offs** uit, niet als implementatie-forks.\n"
    )
    return retro + move


def _build_reviewer_session_end_instructions() -> str:
    """Session-end workflow for reviewer-phase cards (independent pre-Done gate).

    The reviewer is an *independent* gate: it reads the original card spec plus
    the work the engineer produced and decides whether the card may reach Done.
    It never writes code, merges, or ships — those already happened in the
    engineer session; re-doing them here would defeat the point of an
    independent reviewer. So this is deliberately NOT
    ``_build_ship_instructions``: no sync/test/commit/merge steps, just
    review → approve (Done) or reject (Impediment).

    Kept in sync with ``.claude/agents/reviewer.md`` — the persona body carries
    the same contract; update both together. See
    ``docs/cockpit/reviewer-agent-decision.md`` (REVISED 2026-07-18).
    """
    return (
        "You are the **independent reviewer**. This card was completed by "
        "another agent and routed to you *before* it may reach Done. Your job "
        "is a feature-compliance + consistency gate — **not** to write, fix, "
        "merge, or ship code. Follow these steps:\n\n"
        "1. **Read the original request** — the card title + description above "
        "are the wish (`de gestelde wens`). Note every requirement/bullet.\n"
        "2. **Find what was built** — call ``get_card`` (MCP) to read the "
        "deliverables and the engineer's ``**Summary:**`` comment. The branch "
        "deliverable names the work; in direct-ship mode the work is already on "
        "``master`` as a ``Merge <branch>`` commit. ``git fetch origin`` first, "
        "then inspect the diff — e.g. find the merge commit "
        "(``git log origin/master --merges --grep=<branch> -1 --format=%H``) "
        "and read ``git show <merge>`` / ``git diff <merge>^1 <merge>``, or "
        "review the open PR when one is attached.\n"
        "3. **Judge two things.** (a) *Compliance*: does the implementation do "
        "what the card asked — every requirement met, naming/behaviour/edge "
        "cases matching the spec, the claimed deliverable actually present? "
        "(b) *Consistency*: does it fit the rest of the application — existing "
        "patterns, conventions, no sibling features broken? Read the "
        "surrounding code to confirm, don't assume.\n"
        "4. **Decide.**\n"
        "   - **In order** → ``move_card`` with ``column=\"Done\"`` and a "
        "``summary`` recording what you verified (``summary`` is required; the "
        "call is rejected without it). This is the only approval path — the "
        "card reaches Done because you, the reviewer, cleared it.\n"
        "   - **Not in order** → ``report_impediment`` with a ``question`` that "
        "states clearly **why it is not in order** (concrete, with "
        "``file:line`` refs where possible) and what must change. Prefer a "
        "short ``options`` list when there's a decision for the human. The card "
        "moves to Impediment and, when the human resolves it, resumes with the "
        "original engineer to fix — then it comes back to you.\n"
        "   Never move a non-compliant card to Done, and never edit the code "
        "yourself to make it pass.\n"
    )



# ---- transport -------------------------------------------------------------

class SpawnTransport(Protocol):
    def __call__(self, *, directory: str, prompt: str, session_name: str,
                 cli_id: str = "claude-code", provider: str = "anthropic",
                 model: str | None = None,
                 endpoint_name: str | None = None,
                 endpoint_base_url: str | None = None,
                 endpoint_auth_token: str | None = None,
                 card_id: str | None = None,
                 column_name: str | None = None) -> dict: ...


def _known_cli_ids() -> set[str]:
    """Ids of the agentic CLIs the registry knows about (claude-code, codex-cli, …).

    Used to tell a CLI selection apart from a persona/column name, since
    `card.agent` overloads both."""
    from app.services.agentic_cli import get_agentic_clis
    return {p.id for p in get_agentic_clis()}


def _secret_store():
    """Factory for the project-scoped SecretStore. Overridable in tests.

    Mirrors ``app.api.v1.secrets._store`` — cheap to construct, resolves its
    passphrase lazily on the first CRUD call.
    """
    from app.services.secrets_store import AGESecretStore
    return AGESecretStore()


def _resolve_project_secrets(project_key: str | None) -> dict[str, str]:
    """Best-effort read of every stored secret for ``project_key`` as env vars.

    Feeds ``spawn_session``'s ``extra_env`` so a project's agents see only
    their own project-scoped secrets. Any failure (no store file, no passphrase
    configured, decryption error) yields an empty dict — a missing or
    misconfigured secret store must never break dispatch.
    """
    if not project_key:
        return {}
    try:
        store = _secret_store()
        env: dict[str, str] = {}
        for name in store.list(project_key):
            value = store.get(project_key, name)
            if value is not None:
                env[name] = value
        return env
    except Exception:
        logger.debug("secret-store read failed for %s", project_key, exc_info=True)
        return {}


def _install_rtk_for_dispatch(
    *, card_id: str, project_key: str, column_name: str,
    worktree_path: str, repo_path: str,
) -> str:
    """Sync bridge to :func:`app.kanban.token_saver.maybe_install`.

    The worktree transport is synchronous, while the normal dispatch caller
    runs it on the asyncio event-loop thread. Execute the async installer on a
    private worker thread when a loop is already running; otherwise use the
    current thread. The coroutine is created inside the execution context so a
    bridge failure cannot leak an un-awaited coroutine. Never raises — any
    exception is logged and downgraded to ``"failed"`` so the spawn continues
    unchanged.
    """
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _run_install() -> tuple[str, str]:
        return asyncio.run(_install_rtk_for_dispatch_async(
            card_id=card_id, project_key=project_key, column_name=column_name,
            worktree_path=worktree_path, repo_path=repo_path,
        ))

    def _execute(call: Callable[[], tuple[str, str]]) -> tuple[str, str]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return call()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(call).result()

    try:
        return _execute(_run_install)[0]
    except Exception as exc:
        failure_reason = f"bridge {type(exc).__name__}: {exc}"
        logger.warning(
            "token-saver install failed for card %s (column=%s): %s",
            card_id, column_name, exc,
        )

        def _post_failure() -> tuple[str, str]:
            return asyncio.run(_post_rtk_bridge_failure_async(
                card_id=card_id, reason=failure_reason,
            ))

        try:
            _execute(_post_failure)
        except Exception:
            logger.warning(
                "token-saver fail-open note failed for card %s",
                card_id, exc_info=True,
            )
        return "failed"


async def _post_rtk_bridge_failure_async(
    *, card_id: str, reason: str,
) -> tuple[str, str]:
    """Best-effort audit note when the installer bridge itself raises."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.kanban import token_saver

    engine = create_async_engine(settings.kanban_database_url, future=True)
    try:
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as session:
            await token_saver.post_note(
                session, card_id,
                f"**Note:** Token saver fail-open: {reason}",
            )
            await session.commit()
        return ("failed", reason)
    finally:
        await engine.dispose()


async def _install_rtk_for_dispatch_async(
    *, card_id: str, project_key: str, column_name: str,
    worktree_path: str, repo_path: str,
) -> tuple[str, str]:
    """Async core of :func:`_install_rtk_for_dispatch`.

    Opens a private kanban-DB engine on the current loop, calls
    :func:`token_saver.maybe_install`, then posts the activity-feed
    note that satisfies the card acceptance criterion ("Zichtbaar in
    de activity-feed"). Active + failed (fail-open) both post —
    inactive is silent because the default-off path runs on every
    Backlog dispatch and would flood the feed.

    Returns ``(status, reason)`` so the activity-feed note and the
    ``RTK_TELEMETRY=off`` decision in the caller stay in sync. The
    caller never has to re-derive the reason from the status string.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.kanban import token_saver

    engine = create_async_engine(
        settings.kanban_database_url, future=True,
    )
    try:
        Session = async_sessionmaker(
            engine, expire_on_commit=False,
        )
        async with Session() as session:
            status, reason = await token_saver.maybe_install(
                session,
                card_id=card_id,
                project_key=project_key,
                column_name=column_name,
                worktree_path=worktree_path,
                repo_path=repo_path,
            )
            # Activity-feed observability. Only active + failed post;
            # inactive is the default-off path and would flood the feed.
            # Any exception escapes to the sync bridge, which keeps the spawn
            # fail-open and makes a best-effort fallback note.
            if status == "active":
                await token_saver.post_note(
                    session, card_id,
                    f"**Note:** Token saver activated: {reason}",
                )
            elif status == "failed":
                await token_saver.post_note(
                    session, card_id,
                    f"**Note:** Token saver fail-open: {reason}",
                )
            await session.commit()
            return (status, reason)
    finally:
        await engine.dispose()



def make_worktree_transport(skip_permissions: bool = True) -> SpawnTransport:
    """Factory that returns a worktree transport with configurable permission bypass."""
    def _transport(*, directory: str, prompt: str, session_name: str,
                   cli_id: str = "claude-code", provider: str = "anthropic",
                   model: str | None = None,
                   endpoint_name: str | None = None,
                   endpoint_base_url: str | None = None,
                   endpoint_auth_token: str | None = None,
                   card_id: str | None = None,
                   column_name: str | None = None) -> dict:
        """Create a worktree off origin/master, then spawn a `cli_id` session in it,
        against the given `provider` subscription
        (anthropic | bedrock | minimax | anthropic-compatible).

        ``endpoint_*`` are forwarded into ``SpawnCommandOptions`` only when
        the resolved provider is ``"anthropic-compatible"`` — see
        ``SpawnCommandOptions.endpoint_*`` for the field semantics
        (kaart 293d1faa…). They are accepted for every other provider so
        the dispatch helper never has to branch on provider; non-compatible
        providers simply ignore them.

        ``card_id`` and ``column_name`` are optional. When both are
        provided, the dispatcher invoked the per-lane RTK (token-saver)
        installer so the spawned session reduces ``Bash`` tool output
        mechanically (kaart ``c31333bf…``). The helper is fail-open —
        install errors never break the spawn.

        Raises MemoryLimitExceeded if hardware memory limits are reached.
        """
        from app.services.agentic_cli.base import SpawnCommandOptions
        from app.services.runs.spawn import spawn_session
        from app.services.scheduling.session_registry import session_registry

        if not session_registry.can_add_session():
            # ``build_limit_message`` distinguishes the two distinct causes of
            # ``can_add_session() == False`` (counter ceiling vs. memory
            # ceiling) and surfaces a counter leak (slots held without a live
            # tmux pane) from the message alone — see bevinding 5 in
            # docs/cockpit/spawn-test-bridge-sessions-analyse.md.
            raise MemoryLimitExceeded(session_registry.build_limit_message())

        repo = directory
        worktree_path = str(Path(repo) / ".claude" / "worktrees" / session_name)

        subprocess.run(["git", "-C", repo, "fetch", "origin"],
                       capture_output=True, text=True, timeout=60, check=True)
        subprocess.run(
            ["git", "-C", repo, "worktree", "add", "-b", session_name,
             worktree_path, "origin/master"],
            capture_output=True, text=True, timeout=60, check=True)

        # NOTE: we deliberately do NOT copy the repo-root ``.mcp.json`` into
        # the worktree. A fresh ``git worktree add origin/master`` only checks
        # out *tracked* files, so for an external product-project the
        # ``.mcp.json`` written by ``POST /enable`` (untracked/gitignored) is
        # absent in the worktree. The dispatched agent still needs it to reach
        # ``cockpit-kanban`` via MCP — but that is handled by pointing
        # ``--mcp-config`` at the repo-root copy via
        # ``SpawnCommandOptions.repo_path`` (see ``_project_mcp_config_args``),
        # NOT by materialising an untracked file inside the worktree. Copying
        # it in left the worktree permanently dirty: the ship gate refused to
        # run ("uncommitted/untracked changes") and the prescribed
        # ``git add -A && git commit`` recovery would have committed Cockpit's
        # ``Authorization: Bearer`` token into the customer's git history and
        # pushed it to their remote (kaart ``3672c073…``).

        # Resolve project_key for env-injection (card `[security][D]
        # Per-project env-injectie in spawn_session`). Uses the safe
        # helper — a project without a git remote still spawns, just
        # without a `COCKPIT_PROJECT_KEY` to audit against.
        project_key = safe_resolve_project_key(repo)

        # Per-lane RTK (token-saver) install — opt-in, fail-open, kill-switchable.
        # Only runs when both card_id and column_name are set (the dispatcher
        # knows the target column at this point). The helper never raises; a
        # "failed" or "inactive" status means the spawn continues unchanged.
        # When "active", we add RTK_TELEMETRY=off to the spawn env so the
        # spawned session does not phone home.
        #
        # The transport is a sync callable (the SpawnTransport protocol) so
        # we have to bridge to the async helper here. The helper does not
        # touch the surrounding asyncio loop — it opens its own session
        # inside the new loop and the loop exits before the spawn below.
        token_saver_status = "inactive"
        if card_id and column_name:
            token_saver_status = _install_rtk_for_dispatch(
                card_id=card_id,
                project_key=project_key or "",
                column_name=column_name,
                worktree_path=worktree_path,
                repo_path=repo,
            )

        # Project-scoped secrets become the spawn's explicit env (never the
        # backend's os.environ). Best-effort: no store / no passphrase -> {}.
        extra_env = _resolve_project_secrets(project_key)
        if token_saver_status == "active":
            extra_env = {**extra_env, "RTK_TELEMETRY": "off"}

        options = SpawnCommandOptions(
            directory=worktree_path, mode="plain", prompt=prompt,
            skip_permissions=skip_permissions,
            # Wire Claude Code's --permission-prompt-tool only on the product
            # lane (skip_permissions=False). Meta keeps the historical bypass
            # and emits no flag here — see analysis doc
            # ``docs/cockpit/approval-privilege-separation-analyse.md`` §4 and
            # kanban card 5278a5bd… AC2. The MCP tool name lives in
            # ``mcp_server.PERMISSION_PROMPT_TOOL_NAME`` so the producer and
            # the dispatcher stay in lockstep.
            permission_prompt_tool=(
                PERMISSION_PROMPT_TOOL_NAME if not skip_permissions else None
            ),
            worktree_path=worktree_path, repo_path=repo,
            provider=provider, model=model,
            endpoint_name=endpoint_name,
            endpoint_base_url=endpoint_base_url,
            endpoint_auth_token=endpoint_auth_token,
        )
        try:
            result = spawn_session(
                cli_id,
                options,
                session_name=session_name,
                project_key=project_key,
                runtime="worktree",
                extra_env=extra_env,
            )
        except Exception:
            subprocess.run(["git", "-C", repo, "worktree", "remove", worktree_path, "--force"],
                           capture_output=True, text=True, timeout=30)
            raise
        # Track the spawn so the dispatch reaper can detect sessions that die
        # before their first hook event (e.g. a 429 Token Plan limit on the
        # first `claude` invocation -- the tmux pane stays open but no hook
        # script ever runs, so `record()` would never be called for this name).
        session_registry.mark_spawned(session_name)
        return result

    # Tag the closure with an explicit transport kind so callers can detect a
    # worktree transport by *label*, not by object identity. Every call to
    # this factory (the module singleton below AND the fresh
    # ``make_worktree_transport(skip_permissions=skip)`` closure the normal
    # route builds in ``get_transport_for_project``) carries the same kind —
    # an ``is`` check against the singleton would miss the fresh closure and
    # mislabel every ordinary dispatch as "not a worktree" (kaart a962b209…).
    _transport.transport_kind = "worktree"
    return _transport


def _transport_is_worktree(transport: SpawnTransport) -> bool:
    """True when ``transport`` creates a fresh host-side git worktree.

    Detects the worktree transport by the ``transport_kind`` label set in
    ``make_worktree_transport`` rather than object identity against the
    module-level ``worktree_transport`` singleton. The normal dispatch route
    (``card.transport is None``) resolves its transport through
    ``get_transport_for_project`` → ``make_worktree_transport(skip_permissions=skip)``,
    which returns a *distinct* closure; an ``is worktree_transport`` check
    returned False for it and left every ordinary dispatched agent — which
    really is in a fresh worktree — without its real write-address in the
    worktree-safety callout (kaart a962b209…). Resume / sandcastle / headless
    transports are unlabeled (or labeled otherwise) and correctly return
    False, matching their "no fresh host worktree" semantics.
    """
    return getattr(transport, "transport_kind", None) == "worktree"


# Default transport keeps existing behaviour (permissions bypassed)
worktree_transport = make_worktree_transport(skip_permissions=True)


# Strong references to in-flight sandcastle start tasks. asyncio only keeps weak
# references to tasks, so without this set a fire-and-forget task can be garbage
# collected mid-flight and the run silently never starts.
_sandcastle_start_tasks: set = set()


def sandcastle_transport(*, directory: str, prompt: str, session_name: str,
                         cli_id: str = "claude-code", provider: str = "anthropic",
                         model: str | None = None,
                         endpoint_name: str | None = None,
                         endpoint_base_url: str | None = None,
                         endpoint_auth_token: str | None = None,
                         # Accepted for SpawnTransport parity, deliberately
                         # unused — the RTK token-saver (kaart c31333bf…) is
                         # installed only by the worktree transport; the
                         # sandbox run is driven by the per-project sandcastle
                         # config. See
                         # tests/test_spawn_transport_signature_parity.py.
                         card_id: str | None = None,
                         column_name: str | None = None) -> dict:
    """Sandcastle transport: run the agent in an isolated sandbox via sandcastle.

    `cli_id`, `provider`, `model` are accepted for transport-signature parity
    but the actual sandbox run uses the per-project sandcastle config's
    `agent_provider`, not the card's, column's, or persona's.

    kaart 27317b4871… (FCR gap 1): when the dispatcher forwards the
    ``endpoint_*`` carrier at this transport the sandbox would silently
    use its own provider/model instead of the named endpoint — the
    worst kind of failure because the spawned session would bill the
    wrong upstream with no visible signal. Reject the call upfront with
    a clear ``ValueError`` so the operator can either switch the card
    to ``transport='worktree'`` (where the endpoint kwargs reach
    ``build_provider_env`` as designed) or drop the ``endpoint_name``
    on the card / pool / override.

    Runs the agent in a Docker/Podman container. The actual run is kicked off
    asynchronously; this function returns immediately after scheduling it.

    The dispatcher always calls transports from inside a running event loop, so we
    schedule the start as a tracked task (kept alive via `_sandcastle_start_tasks`)
    rather than blocking. `session_name` is stored as the run's branch so the reaper
    can recognise the live sandcastle session and not release its claim.

    Raises MemoryLimitExceeded if hardware memory limits are reached, or
    ValueError when a non-compatible transport is asked to honour an
    anthropic-compatible configuration.
    """
    import asyncio

    from app.services.scheduling.session_registry import session_registry

    # kaart 27317b4871… (FCR gap 1): reject the
    # anthropic-compatible + endpoint-* combo so it can't silently
    # bill the wrong upstream inside the sandbox. The dispatch path
    # used to forward these kwargs through, and the sandbox ignored
    # them — the spawned session would still run with the per-project
    # sandcastle config's ``agent_provider`` (Anthropic by default),
    # NOT the named endpoint. That's a worse failure than "dispatch
    # refused" because the operator sees a green spawn + a successful
    # upstream call, but to a different vendor than they configured.
    # Raise early so the caller can either fall back to the worktree
    # transport (set ``card.transport='worktree'``) or pick a
    # non-compatible provider at the column / card level.
    if provider == PROVIDER_COMPATIBLE and (
        endpoint_name or endpoint_base_url or endpoint_auth_token
    ):
        raise ValueError(
            "sandcastle transport does not support anthropic-compatible "
            "provider: endpoint_name/endpoint_base_url/endpoint_auth_token "
            "are not forwarded into the sandbox. Set "
            "card.transport='worktree' for this card, or use a "
            "non-compatible provider at the column/card level.",
        )

    # Check memory limits before spawning
    if not session_registry.can_add_session():
        # Mirrors the worktree transport above — same cause-aware message,
        # so a counter leak doesn't get mis-diagnosed as a memory problem
        # (bevinding 5 in docs/cockpit/spawn-test-bridge-sessions-analyse.md).
        raise MemoryLimitExceeded(session_registry.build_limit_message())

    from app.services.sandcastle_service import sandcastle_service

    # Reserve a slot synchronously so this run counts against the shared session
    # budget for the rest of the dispatch tick (the run record is created later, in a
    # background task). The reservation is released by the run lifecycle: on success
    # when the run finishes (_execute_run), or immediately if start_run fails.
    session_registry.reserve_external(session_name)

    # Project-scoped secrets reach the sandbox container as env vars (never the
    # backend's os.environ). risk_class-driven defaults route product/untrusted
    # projects here, so this is the transport where per-project secret isolation
    # actually matters. Best-effort: no store / no passphrase -> {}.
    project_key = safe_resolve_project_key(directory)
    extra_env = _resolve_project_secrets(project_key)

    async def _start():
        try:
            return await sandcastle_service.start_run(
                project_path=directory,
                prompt=prompt,
                branch_name=session_name,
                extra_env=extra_env,
            )
        except Exception:
            session_registry.release_external(session_name)
            raise

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        # Async context (the normal dispatch path): schedule without blocking and
        # keep a strong reference so the task can't be GC'd before it runs.
        task = loop.create_task(_start())
        _sandcastle_start_tasks.add(task)
        task.add_done_callback(_sandcastle_start_tasks.discard)
        return {
            "session_name": session_name,
            "transport": "sandcastle",
            "status": "started",
        }

    # No running loop (e.g. a sync caller): run to completion so we can return run_id.
    run = asyncio.run(_start())
    return {
        "session_name": session_name,
        "transport": "sandcastle",
        "run_id": run.id,
        "status": "started",
    }


async def _live_sandcastle_sessions() -> set[str]:
    """Session names of pending/running sandcastle runs on this device.

    Returned as the `sandcastle_live` liveness source for the reaper. Defensive: any
    failure yields an empty set, which only makes the reaper *more* eager — never
    less — so a transient DB hiccup can't keep a truly-dead claim alive forever."""
    try:
        from sqlalchemy import select

        from app.database import AsyncSessionLocal
        from app.models.sandcastle import SandcastleRun

        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(SandcastleRun.branch).where(
                        SandcastleRun.status.in_(("pending", "running"))
                    )
                )
            ).scalars().all()
        return {b for b in rows if b}
    except Exception:
        logger.exception("could not query live sandcastle sessions")
        return set()


async def _live_headless_sessions() -> set[str]:
    """Session names of headless ``stream-json`` subprocesses still running.

    Third liveness source for ``reap_stale_claims`` — sits alongside
    ``_live_sessions`` (tmux) and ``_live_sandcastle_sessions``. A headless
    run has neither a tmux session nor a SandcastleRun row, so without this
    third source the reaper would release + re-dispatch the card every
    tick — exactly the dispatch-loop sandcastle had before its own second
    source was added. See
    ``docs/cockpit/headless-stream-json-transport-spike.md`` §5 for the
    precedent; the liveness-orakel is the only one of the four identity
    facets that changes (spike §5.1).

    Defensive: any failure yields an empty set, which only makes the
    reaper *more* eager — never less — so a transient registry hiccup
    can't keep a truly-dead claim alive forever. Same contract as
    ``_live_sandcastle_sessions``.
    """
    try:
        from app.kanban.headless_runner import live_headless_sessions
        return live_headless_sessions()
    except Exception:
        logger.exception("could not query live headless sessions")
        return set()


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return s or "project"


def _mint_session_name(
    project_path: str, card_title: str = "", live_sessions: set[str] | None = None,
) -> str:
    # Keep the whole name <= 20 chars so the tmux-bridge sanitizer never truncates
    # it: a truncated session name would diverge from the claimant label and the
    # worktree branch, breaking cleanup. "k-" + slug(<=13) + "-" + 4 hex = <=20.
    # Prefer card title over project path for clarity.
    #
    # Must also avoid colliding with a currently-live tmux session: the claim,
    # git worktree and git branch are all committed under this exact name before
    # spawn_session runs. If it happened to already be a running session, spawn's
    # own collision fallback (runs.spawn._session_name_for) would silently
    # rename just the tmux session -- leaving cleanup_session_for_card looking up
    # a name that never existed, assuming the agent "already exited", and
    # orphaning the real session forever (see kanban card "session termination").
    # `live_sessions` mirrors the None-means-skip convention used for reaping
    # (see dispatch_project): callers that already queried tmux this tick pass
    # their snapshot; callers/tests with no snapshot skip the check rather than
    # each minting triggering its own tmux subprocess call.
    source = card_title if card_title else Path(project_path).name
    slug = (_slug(source)[:13].rstrip("-")) or "card"
    if live_sessions is None:
        return f"k-{slug}-{uuid.uuid4().hex[:4]}"
    for _ in range(20):
        name = f"k-{slug}-{uuid.uuid4().hex[:4]}"
        if name not in live_sessions:
            return name
    return name


async def _resolve_target_column(session, card, *, project_path: str,
                                 project_key: str,
                                 agent_override: str | None = None) -> str:
    """Resolve the agent column this card will be dispatched to (executor phase).

    Delegates to `_phase_target_agent` — the *same* resolver the real spawn
    path (`_run_card`) uses — so the per-column cap gate can never route a card
    to a different column than the one it is actually dispatched to.

    Crucially this includes the `work_type` → persona fallback: a card whose
    `agent` is a CLI id (e.g. "claude-code") rather than a persona file resolves
    to its work_type persona ("analysis" → "analyst"), exactly as the spawn
    does. The previous version mirrored only the *first half* of
    `_phase_target_agent` and dropped this fallback, so such a card was gated
    against the hardcoded "engineer" column instead of its real target
    ("analyst"). A full engineer column then starved analysis cards that the
    (empty) analyst column had room for — the analyst never picked them up. See
    the "analyst neemt geen analyse-kaarten op" postmortem.

    The work_type lookup mirrors `_run_card`'s try/except: a transient DB error
    degrades to the legacy engineer routing rather than wedging the tick.
    """
    try:
        fallback_persona = await _resolve_work_type_fallback(
            session, project_key, card,
        )
    except Exception:
        logger.exception(
            "work_type fallback lookup failed for card %s in %s; cap gate "
            "falling back to legacy engineer routing",
            card.id, project_key,
        )
        fallback_persona = None
    return _phase_target_agent(
        card, project_path=project_path, phase="executor",
        source_column=card.column, agent_override=agent_override,
        known_clis=_known_cli_ids(), fallback_persona=fallback_persona,
    )


def _active_session_count(cards: Iterable[KanbanCard]) -> int:
    """Number of occupied dispatch slots: cards in agent columns (not Backlog,
    Impediment, or Done) held by an `agent:` claim."""
    from app.kanban.schemas import COLUMNS
    return sum(
        1 for c in cards
        if c.column not in COLUMNS and (c.claimed_by or "").startswith(CLAIMANT_PREFIX)
    )


def _active_session_count_by_column(cards: Iterable[KanbanCard]) -> dict[str, int]:
    """Per-column count of occupied dispatch slots (agent-claimed cards in agent
    columns). Returns a dict {column_name: count}. Non-agent columns are omitted."""
    from app.kanban.schemas import COLUMNS
    counts: dict[str, int] = {}
    for c in cards:
        if c.column not in COLUMNS and (c.claimed_by or "").startswith(CLAIMANT_PREFIX):
            counts[c.column] = counts.get(c.column, 0) + 1
    return counts


async def _column_max_sessions(session, project_key: str) -> dict[str, int]:
    """Per-column max_sessions caps for a project.

    Returns a dict {column_name: cap}. Only columns with an explicit
    max_sessions setting are included — columns not in the dict fall
    back to the project-level cap.
    """
    from sqlalchemy import select

    from app.kanban.models import KanbanColumn
    rows = (await session.execute(
        select(KanbanColumn)
        .where(KanbanColumn.project_key == project_key)
        .where(KanbanColumn.max_sessions.isnot(None))
    )).scalars().all()
    return {r.name: r.max_sessions for r in rows if r.max_sessions is not None and r.max_sessions >= 0}


def _claimant_session(card: KanbanCard) -> str | None:
    """The tmux session name behind an `agent:` claim, or None for unclaimed cards
    and human (`me@ui`) claims — those are never reaped."""
    claimant = card.claimed_by or ""
    if claimant.startswith(CLAIMANT_PREFIX):
        return claimant[len(CLAIMANT_PREFIX):]
    return None


# ---- stuck-session reaper helpers -----------------------------------------
#
# When a dispatched session hits a 429 / Token Plan limit on first invocation,
# the `claude` CLI prints the error and never initialises hooks — so the tmux
# pane stays open while SessionRegistry.record() is never called. The standard
# `reap_stale_claims` path treats the session as alive and skips it, leaving
# the card claimed forever. These helpers extend the reaper with a pane-content
# scan that detects the rate limit and triggers a full cleanup.

# Stuck tmux sessions that don't initialise hooks inside this window are
# inspected by the reaper. The default of 120s is a balance: it must be long
# enough not to race a normal `claude` startup (~10-30s) but short enough that
# the next tick after a 429 actually cleans up the orphaned claim. Mirrors the
# default in SessionRegistry.get_stuck_sessions().
STUCK_SESSION_TIMEOUT_S = 120

# ---- progress-liveness (kaart f0953a11…) -----------------------------------
#
# ``reap_stale_claims`` checks whether the tmux pane still exists, which a
# session that hit its subscription limit (or crashed in a loop, or is parked
# on a permission prompt, or is hung on a network timeout) keeps — so the
# claim stays claimed forever. Progress-liveness adds a *second* criterion:
# the transcript must keep growing. If the transcript of an ``agent:``-claimed
# session stops advancing for ``PROGRESS_LIVENESS_SIGNAL_SECONDS``, the card
# gets a "stilstaand" activity comment so an operator can see the stall on the
# board; after ``PROGRESS_LIVENESS_ACTION_SECONDS`` the claim is released via
# the existing ``_move_to_resume`` path. See
# docs/cockpit/sessie-limiet-auto-dispatch-analyse.md §5 R3.
#
# The defaults are deliberately roomy: a productive Claude session writes to
# its transcript every few seconds, but a long file-edit + verify + commit
# stretch can run several minutes between writes. 30 min silence is already a
# strong signal; 60 min crosses from "agent is doing something" into
# "operator should know about this". Both are overridable via the
# ``signal_seconds`` / ``action_seconds`` kwargs of ``check_progress_liveness``
# (mainly for tests).
PROGRESS_LIVENESS_SIGNAL_SECONDS = 30 * 60
PROGRESS_LIVENESS_ACTION_SECONDS = 60 * 60

# Process-local state for progress-liveness. Keyed by ``card.id`` (not by
# session name) so a reaped+re-dispatched session that happens to mint the
# same name doesn't inherit the prior session's stall baseline. Holds a
# small snapshot per card: the wall-clock time at which we last *saw* the
# session (so stall is measured against our observation cadence, not
# against the file's mtime directly), the mtime we observed it at (so the
# next tick can detect growth), and a one-shot flag so the "stilstaand"
# comment is posted at most once per stall window. Entries are pruned when
# the card is no longer claimed by an agent session; a backend restart
# wipes the dict — same in-memory-by-design pattern as
# ``SessionRegistry._spawn_times``.
@dataclass
class _ProgressSnapshot:
    observation_time: float
    observed_mtime: float
    signal_posted: bool = False


_progress_liveness_state: dict[str, _ProgressSnapshot] = {}

# Activity-comment copy. Format kwargs: ``minutes`` (rounded stall duration),
# ``name`` (session name), ``action_minutes`` (configured action threshold).
# Mirrors the Dutch-with-emoji convention used by the rate-limit / spillover
# / pane-detector comments elsewhere in this file — see ``_post_rate_limit_activity_comment``,
# ``_cleanup_stuck_session``, and the spillover branch of ``move_limited_session_to_resume``.
_STILLESTAAND_COMMENT_TEMPLATE = (
    "⏸️ Geen transcript-activiteit voor ~{minutes} min in sessie {name} — "
    "mogelijk stilstaand (abonnementslimiet, crash-loop, wachtende prompt "
    "of netwerk-timeout). Wordt automatisch vrijgegeven rond {action_minutes} "
    "min stilstand; reageer in de pane als de sessie nog productief is."
)


def _capture_pane_content(session_name: str, *, lines: int = 20) -> str | None:
    """Capture the last `lines` of tmux pane content for `session_name`.

    Returns the captured text (ANSI escapes preserved — strip if you need
    to), or None on any failure: tmux missing on PATH, non-zero exit
    (session gone), timeout, etc. Returning None is the whole point: a
    missing capture must NEVER translate into a false positive that
    triggers a clean-up of a healthy but slow session. The reaper treats
    None as "we don't know", which means fail-open."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", session_name, "-p", "-S", f"-{lines}"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


# ---- pane-scan rate-limit needles ------------------------------------------
#
# The pane substring-scan is a fallback for the 429-on-first-spawn case
# where `claude` printed the error and died before initialising hooks (so
# the transcript detector never sees it). It runs on raw terminal output,
# which means an agent doing curl/HTTP tests or reading third-party API
# error logs can leave benign '429' / 'api error' substrings in pane
# history. The 2026-07-22 incident (card 3a8f27a4…,
# logs/backend/run-20260722-095619-2861-0.log:947) killed a healthy session
# over a '429' from a curl probe — the cost of one false positive was
# higher than the cost of a missed detection (dispatch paused 5h, card
# dispatched to Impediment), so these needles are tighter than the
# Notification classifier's. The classifier still consumes its existing
# loose needles via `is_limit_notification`; this is the pane-specific
# set.
#
# Single-phrase matches: vendor-specific phrasings that are very unlikely
# to appear outside a real limit notification.
_PANE_RATE_LIMIT_PHRASES: tuple[str, ...] = (
    "hit your session limit",
    "hit your weekly limit",
    "token plan",
)

# Combination matches: the generic HTTP-error phrases ("api error", "429",
# "request rejected") are too loose on their own — agents that probe
# third-party APIs routinely print one or the other. A match requires the
# co-occurrence of an error/status phrase AND a rate-limit context phrase.
# The two-token combos below are still safe because a curl probe prints
# "HTTP/2 429" (no "api error") and a bare rejection prints "Request
# rejected: invalid API key" (no "429") — neither trips.
_PANE_RATE_LIMIT_COMBOS: tuple[frozenset[str], ...] = (
    frozenset({"api error", "429"}),
    frozenset({"api error", "429", "rate"}),
    frozenset({"api error", "429", "too many"}),
    frozenset({"api error", "429", "limit"}),
    frozenset({"request rejected", "429"}),
)


def _find_rate_limit_match(pane_content: str) -> str | None:
    """Return the specific needle/phrase that classifies `pane_content` as
    a Claude/MiniMax rate-limit hit, or None when no match.

    The string returned is the literal matched needle (single phrase) or
    the combo name (e.g. ``"api error+429+too many"``); the caller uses it
    to log *why* the reaper acted, which the old boolean-only
    `_is_rate_limited_session` couldn't surface. Tightened per card
    3a8f27a4… — bare ``"429"`` or ``"api error"`` alone is no longer
    sufficient."""
    if not pane_content:
        return None
    text = pane_content.lower()
    for needle in _PANE_RATE_LIMIT_PHRASES:
        if needle in text:
            return needle
    # Combo matches: require every phrase in the combo to appear in the
    # text (substring, so 'API Error:' still satisfies 'api error'). This
    # catches the canonical MiniMax reject format ('API Error: Request
    # rejected (429) · Token Plan usage limit reached') and the standard
    # HTTP 429 wording ('API Error: 429 Too Many Requests') without
    # false-positiving on bare 'HTTP/2 429' from a curl probe.
    for combo in _PANE_RATE_LIMIT_COMBOS:
        if all(piece in text for piece in combo):
            return "+".join(sorted(combo))
    return None


def _session_has_transcript(project_path: str | None, session_name: str) -> bool:
    """True if the worktree for `session_name` has a Claude transcript file
    on disk with content.

    Used by the reaper's pane-scan branch (card 3a8f27a4…): the pane
    substring-scan should only fire when no transcript exists yet, since a
    session with a productive transcript is the transcript-tail
    detector's territory. Any unreadable / missing transcript counts as
    'no transcript' so the scan still fires for the classic 429-on-first-
    spawn case (`claude` died before writing anything)."""
    if not project_path:
        return False
    try:
        from app.kanban.session_recovery import _resolve_transcript_file
        path = _resolve_transcript_file(project_path, session_name)
    except Exception:
        # Fail open: a transient resolver hiccup must never block the
        # pane scan. Better to risk one false positive than to silently
        # strand a true 429-on-first-spawn.
        return False
    if path is None:
        return False
    try:
        return path.stat().st_size > 0
    except OSError:
        return False


def _matching_pane_line(pane_content: str, needle: str) -> str:
    """Return the first line of `pane_content` that contains any of the
    tokens in `needle` (split on '+'), stripped, truncated to 300 chars.

    Used by the reaper's structured log so an operator chasing a (real or
    false) cleanup can see the matching line instead of the truncated
    200-char pane prefix the old `_cleanup_stuck_session` log emitted —
    that prefix often hid the actual match because the offending substring
    sat further down in the 20-line capture."""
    if not pane_content or not needle:
        return ""
    tokens = [t for t in needle.split("+") if t]
    if not tokens:
        return ""
    for line in pane_content.splitlines():
        low = line.lower()
        if any(tok in low for tok in tokens):
            return line.strip()[:300]
    return ""


def _is_rate_limited_session(pane_content: str) -> bool:
    """True if `pane_content` (the last 20 lines of a tmux pane capture)
    shows a Claude/MiniMax rate-limit indicator.

    Backward-compatible boolean wrapper around `_find_rate_limit_match` —
    callers that only need the yes/no answer (existing tests, the reaper's
    fallback branch) keep working without change; callers that need to
    log *which* needle matched (the reaper itself) call
    `_find_rate_limit_match` directly.

    Tighter than `auto_resume_service.is_limit_notification`: that
    classifier consumes Notification-hook payloads which are structured
    messages from Claude Code itself, while the pane scan runs on raw
    terminal output where any '429' substring is suspect. See card
    3a8f27a4… for the incident that drove the split."""
    return _find_rate_limit_match(pane_content) is not None


def _structured_rate_limit_signal(session_name: str) -> bool:
    """True if the structured Notification pipeline has flagged `session_name`
    as rate-limited.

    This is the typed fast-path for the reaper's stuck-session sweep:
    ``session_signals.record_limit`` is called from the hook endpoint
    whenever a Notification event classifies as "limit" (canonical
    "hit your session limit …", 429, Token Plan, "request rejected", or any
    of the provider-specific variants the auto-resume detector recognises),
    so a recorded signal is the same fact the hook path would have used to
    drive ``move_limited_session_to_resume``. When the registry has no
    entry for `session_name` we fall back to the pane scrape — the session
    is either still booting or, the classic case the reaper exists for,
    rate-limited on first invocation before its hook scripts ever ran.

    The return type is intentionally bool, not ``bool | None``: the
    reaper's decision tree is "structured-said-yes → cleanup, structured-said-no
    → fall through to pane scrape, then fall through to dead-skip"; both
    "no" and "no signal yet" collapse into the same branch, so a tri-state
    answer would force the caller to special-case a value it has no use
    for. The fail-open ``None`` semantics from the pane scraper still
    apply — ``_capture_pane_content`` returning ``None`` is the existing
    "we don't know" signal that keeps the reaper from acting on guesses.
    """
    from app.services.scheduling.session_signals import session_signals
    return session_signals.is_rate_limited(session_name)


# ---- transcript-tail rate-limit detection ----------------------------------
#
# The Notification hook never carries a usage-limit message (§2.1 of
# docs/cockpit/sessie-limiet-auto-dispatch-analyse.md): a rate limit shows up
# in the transcript as a plain assistant message with `isApiErrorMessage:
# true`, not as a Notification event. The reaper's stuck-session pane scan
# above only inspects sessions that never sent a single hook event within
# STUCK_SESSION_TIMEOUT_S (§2.2) -- once a session has been productive for a
# while, a mid-session limit is invisible to both existing detectors, and the
# session stays open on the limit screen forever (the CLI never exits). These
# helpers close that gap by reading the tail of the transcript itself, which
# always carries the limit for both vendors, whether it happens at spawn or
# hours in.

# Bytes read from the tail of a transcript when scanning for an unresolved
# rate limit. Transcripts grow to tens of MB over a long session; we only
# ever need the last few conversational turns to decide whether the session
# is currently stuck, so a full-file parse on every dispatch tick would be
# wasteful for no benefit.
_TRANSCRIPT_TAIL_BYTES = 65536


def _transcript_entry_text(entry: dict) -> str:
    """Flatten an assistant/user transcript entry's message content to plain
    text for substring classification -- mirrors the reproduction script in
    docs/cockpit/sessie-limiet-auto-dispatch-analyse.md §1.1."""
    message = entry.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    return ""


def _is_conversational_transcript_entry(entry: dict) -> bool:
    """True for a real assistant/user turn, as opposed to session bookkeeping
    entries (system/summary/last-prompt/file-history-snapshot/attachment/...)
    that Claude Code interleaves into the same JSONL file but that carry no
    "the agent did something" signal. Only conversational entries count
    towards "is there activity after the limit" in `_tail_rate_limit_message`
    -- a bookkeeping entry recorded right after the api-error must not be
    mistaken for the session having recovered on its own."""
    return entry.get("type") in ("assistant", "user") and isinstance(entry.get("message"), dict)


def _read_transcript_tail_entries(
    path: Path, *, max_bytes: int = _TRANSCRIPT_TAIL_BYTES,
) -> list[dict]:
    """Parse the last `max_bytes` of a transcript JSONL file into entry dicts.

    Reads only the tail via a seek — transcripts run to tens of MB over a long
    session. The first line after a non-zero seek is dropped since it's
    likely truncated mid-line; malformed lines are skipped rather than
    raising, since a truncated JSON line at the chunk boundary is expected,
    not an error. Returns `[]` on any read failure (missing file, permission
    error, ...) -- fail open, same contract as `_capture_pane_content`."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    start = max(0, size - max_bytes)
    try:
        with path.open("rb") as f:
            f.seek(start)
            raw = f.read()
    except OSError:
        return []
    text = raw.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if start > 0:
        lines = lines[1:]
    entries = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except (TypeError, ValueError):
            continue
    return entries


def _tail_rate_limit_message(path: Path) -> str | None:
    """Return the api-error text if the transcript's tail shows an
    *unresolved* rate-limit hit, else None.

    "Unresolved" means: scanning from the end, the most recent conversational
    (assistant/user) entry is an `isApiErrorMessage: true` entry whose text
    classifies as a limit via `auto_resume_service.is_limit_notification`. If
    ordinary assistant/user activity follows the error instead, the session
    already recovered on its own and nothing should happen — this is the R1
    contract from docs/cockpit/sessie-limiet-auto-dispatch-analyse.md §5:
    "alleen reageren wanneer de api-error het laatste betekenisvolle event
    is"."""
    from app.services.scheduling.auto_resume import auto_resume_service

    for entry in reversed(_read_transcript_tail_entries(path)):
        if not _is_conversational_transcript_entry(entry):
            continue
        if entry.get("isApiErrorMessage"):
            text = _transcript_entry_text(entry)
            if auto_resume_service.is_limit_notification(text):
                return text
            return None
        return None  # most recent conversational turn is ordinary activity
    return None


# Pane-resume: when a rate-limited session's tmux pane is still alive, defer
# the kill+To Resume reaction and try to nudge the same session back to life
# at the parsed reset time via the existing tmux_inject helpers. See
# docs/cockpit/sessie-limiet-auto-dispatch-analyse.md §5 (R2) for the
# motivation; this constant block sets the timing knobs.
#
# Margin: how long after the parsed reset time the *first* nudge fires, in
# seconds. Tiny on purpose — we want to resume the moment the limit resets,
# not minutes later. 60 s absorbs clock skew + the time the Claude CLI takes
# to render the post-reset prompt frame.
PANE_RESUME_MARGIN_S = 60
# Backoff: how much *additional* delay each subsequent nudge adds when a
# previous nudge re-hit the limit. Linear in `attempts` — `reset + margin*attempts`
# — so attempts 1/2/3 fire at reset+60s/+120s/+180s. Small enough that even
# the third try lands inside the same wall-clock window, large enough that
# we don't burn scheduler slots on a stuck limit.
PANE_RESUME_BACKOFF_S = 60
# Max attempts before falling back to the existing kill+To Resume reaction.
# 3 follows the standard retry-with-fallback shape (initial + 2 retries) and
# keeps total wall-clock under ~3 minutes so the operator doesn't sit on a
# stalled session for long.
PANE_RESUME_MAX_ATTEMPTS = 3


def _pane_resume_job_id(cwd: str) -> str:
    """Stable apscheduler job id for the pane-resume nudge of a given cwd.

    Exposed so the fallback path can remove the job deterministically —
    without that, the previously-scheduled nudge still fires at the parsed
    reset time even after the card has been moved to "To Resume", and ends
    up injecting keystrokes into a worktree that's been reused for a
    different session (kaart e2116332, gemeten op 2026-07-24).
    """
    return f"pane-resume-{hash(cwd) % 100000}"


async def _read_pane_resume_state(cwd: str) -> dict | None:
    """Read the pane-resume metadata for the card claimed by `cwd`'s session.

    Returns ``{"attempts": int, "reset_at": iso, "fired": bool}`` when a
    previous nudge is pending, ``None`` otherwise. Reads via
    `_resume_target_from_cwd` + `list_cards` so it follows the same "kanban
    card claimed by this session on a non-fixed column" predicate as
    `move_limited_session_to_resume`.

    ``fired`` distinguishes "nudge scheduled, waiting for the apscheduler
    job to fire it" (False) from "nudge already fired, monitoring for
    recovery/re-limit on subsequent ticks" (True). Without this flag the
    dispatch tick would treat every re-detection of the same in-transcript
    limit as a fresh re-hit and burn the attempt budget in ~30 s, well
    before the scheduled nudge ever got a chance to fire — see kanban card
    e2116332 for the production measurement that surfaced this race.
    """
    target = _resume_target_from_cwd(cwd)
    if target is None:
        return None
    project_path, session_name = target
    project_key = safe_resolve_project_key(project_path)
    if project_key is None:
        return None

    claimant = CLAIMANT_PREFIX + session_name
    from app.kanban.db import KanbanSessionLocal
    async with KanbanSessionLocal() as ks:
        cards = await list_cards(ks, project_key)
    card = next(
        (c for c in cards
         if c.column not in ("Done", "To Resume")
         and c.claimed_by == claimant),
        None,
    )
    if card is None:
        return None
    meta = card.meta or {}
    if not meta.get("pane_resume_pending"):
        return None
    return {
        "attempts": int(meta.get("pane_resume_attempts", 0)),
        "reset_at": meta.get("pane_resume_reset_at"),
        "fired": bool(meta.get("pane_resume_fired", False)),
    }


async def _clear_pane_resume_state(cwd: str) -> None:
    """Strip the pane-resume metadata keys from the card claimed by `cwd`'s
    session. Idempotent — a no-op when there's no card or no pending state.
    Used at fallback time so a later sweep doesn't try to nudge a card that's
    already been moved to "To Resume"."""
    target = _resume_target_from_cwd(cwd)
    if target is None:
        return
    project_path, session_name = target
    project_key = safe_resolve_project_key(project_path)
    if project_key is None:
        return
    claimant = CLAIMANT_PREFIX + session_name
    from app.kanban.db import KanbanSessionLocal
    async with KanbanSessionLocal() as ks:
        cards = await list_cards(ks, project_key)
        card = next(
            (c for c in cards
             if c.column not in ("Done", "To Resume")
             and c.claimed_by == claimant),
            None,
        )
        if card is None:
            return
        meta = dict(card.meta or {})
        if not meta:
            return
        meta.pop("pane_resume_pending", None)
        meta.pop("pane_resume_attempts", None)
        meta.pop("pane_resume_reset_at", None)
        meta.pop("pane_resume_fired", None)
        await apply_operation(
            ks, op_type="update", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={"metadata": meta},
        )
        await ks.commit()


async def _clear_pane_resume_metadata_for_card(card, project_path: str) -> None:
    """Strip pane-resume metadata from a card we already have in hand.

    Used by the detection sweep when the transcript no longer shows an active
    limit (i.e. the previous nudge did land and the session recovered) — keeps
    the attempt counter fresh for the next genuine limit cycle. No-op when
    the card has no pending state. Card stays in place; only the metadata
    keys change.
    """
    meta = dict(card.meta or {})
    if not meta.get("pane_resume_pending"):
        return
    meta.pop("pane_resume_pending", None)
    meta.pop("pane_resume_attempts", None)
    meta.pop("pane_resume_reset_at", None)
    meta.pop("pane_resume_fired", None)
    project_key = safe_resolve_project_key(project_path)
    if project_key is None:
        return
    from app.kanban.db import KanbanSessionLocal
    async with KanbanSessionLocal() as ks:
        await apply_operation(
            ks, op_type="update", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={"metadata": meta},
        )
        await ks.commit()
    logger.info(
        "pane-resume pending cleared on card %s (transcript shows recovery)",
        card.id,
    )


async def _do_move_to_resume(cwd: str, pause_until) -> bool:
    """The existing kill+To Resume reaction (sibling helper so `handle_rate_limit_signal`
    stays a single composition). Mirrors the original exception-swallow so the
    caller doesn't have to."""
    try:
        return await move_limited_session_to_resume(
            cwd, scheduled_at=pause_until.isoformat(),
        )
    except Exception:
        logger.exception("failed to move kanban card to To Resume for %s", cwd)
        return False


async def try_pane_resume(
    cwd: str, reset_time, message: str, *, attempts: int = 1,
) -> bool:
    """Try to keep a rate-limited session alive by injecting a continuation
    nudge into its still-alive tmux pane at reset_time + margin*attempts.

    The Claude Code CLI doesn't exit on a rate limit — it prints the notice
    and returns to its prompt — so the tmux pane is usually still alive at
    detection time. Killing it loses the entire session context; nudging the
    same pane at the reset time recovers without losing anything. Reuses the
    existing `tmux_inject` + `auto_resume_service.schedule_resume` machinery
    so the keystroke delivery path is identical to the manual scheduled-
    message flow that's been in production.

    Returns True when a nudge is scheduled and the card metadata reflects the
    pending state; False when the pane is gone (the caller should fall back to
    `move_limited_session_to_resume`). A no-card-found case is also False —
    for non-kanban sessions (human-started, sandcastle) there's nothing to
    update on the board anyway.
    """
    from apscheduler.triggers.date import DateTrigger

    from app.kanban.db import KanbanSessionLocal
    from app.services.scheduling.scheduler import scheduler_service
    from app.services.scheduling.session_resolver import resolve_target

    target = resolve_target(cwd)
    if target is None:
        return False  # pane gone — caller falls back

    fire_at = reset_time + timedelta(
        seconds=PANE_RESUME_MARGIN_S + PANE_RESUME_BACKOFF_S * (attempts - 1),
    )

    # Schedule the nudge via the scheduler directly so we can call our own
    # `_execute_pane_resume` (the standard auto_resume_service._execute_resume
    # doesn't include the wait_for_pane_ready guard the acceptance criteria
    # ask for).
    job_id = _pane_resume_job_id(cwd)
    try:
        scheduler_service._sched.remove_job(job_id)
    except Exception:
        pass
    scheduler_service._sched.add_job(
        _execute_pane_resume,
        trigger=DateTrigger(run_date=fire_at),
        args=[cwd, message],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=300,
        coalesce=True,
    )

    # Persist the pending state on the card so the next dispatch tick knows
    # this nudge is in flight and can back off / fall back when (if) another
    # limit is detected. `pane_resume_fired` starts False and flips to True
    # once `_execute_pane_resume` actually delivers the keystroke — that's
    # the signal that a *new* limit detection is a real re-hit rather than
    # the same in-transcript message being re-scanned every tick.
    resume_target = _resume_target_from_cwd(cwd)
    if resume_target is None:
        return False
    project_path, session_name = resume_target
    project_key = safe_resolve_project_key(project_path)
    if project_key is None:
        return False
    claimant = CLAIMANT_PREFIX + session_name

    async with KanbanSessionLocal() as ks:
        cards = await list_cards(ks, project_key)
        card = next(
            (c for c in cards
             if c.column not in ("Done", "To Resume")
             and c.claimed_by == claimant),
            None,
        )
        if card is None:
            return False
        meta = dict(card.meta or {})
        meta["pane_resume_pending"] = True
        meta["pane_resume_attempts"] = attempts
        meta["pane_resume_reset_at"] = reset_time.isoformat()
        meta["pane_resume_fired"] = False
        await apply_operation(
            ks, op_type="update", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={"metadata": meta},
        )
        await _post_rate_limit_activity_comment(
            ks, card=card, project_key=project_key,
            text=(
                f"⏰ Rate-limit gedetecteerd op live pane — nudge #{attempts} "
                f"gepland om {fire_at.isoformat()} (reset {reset_time.isoformat()}). "
                f"Kaart blijft geclaimd; pane blijft leven."
            ),
        )
        await ks.commit()

    logger.info(
        "pane-resume scheduled (cwd=%s attempts=%s fire_at=%s)",
        cwd, attempts, fire_at.isoformat(),
    )
    return True


async def _execute_pane_resume(cwd: str, message: str) -> bool:
    """Scheduler-fired executor for a pending pane-resume.

    Resolves the live tmux pane, waits for it to be ready (so a stuck or
    unrendered pane doesn't silently swallow the keystroke), and sends the
    continuation message via the existing `tmux_inject.send_text` helper.
    Any failure (pane gone, never ready, send_text returned False) falls
    back to the existing kill+To Resume reaction so the card doesn't sit
    claimed indefinitely while the session is actually dead.

    On a successful delivery the card's `pane_resume_fired` metadata flips
    to True — that's the signal that flips the bookkeeping from "nudge
    scheduled, don't reschedule" to "nudge landed, watch the transcript for
    recovery or a real re-hit" (kaart e2116332).
    """
    from app.services.scheduling.session_resolver import resolve_target
    from app.services.scheduling.tmux_inject import send_text, wait_for_pane_ready

    target = resolve_target(cwd)
    if target is None:
        logger.warning("pane-resume: pane gone for %s; falling back", cwd)
        await _pane_resume_fallback_to_kill(cwd)
        return False

    ready = await wait_for_pane_ready(target, timeout_s=30.0)
    if not ready:
        logger.warning(
            "pane-resume: pane %s never became ready; falling back", target,
        )
        await _pane_resume_fallback_to_kill(cwd)
        return False

    ok = send_text(target, message)
    if not ok:
        logger.warning(
            "pane-resume: send_text failed for %s; falling back", target,
        )
        await _pane_resume_fallback_to_kill(cwd)
        return False

    await _mark_pane_resume_fired(cwd)
    logger.info("pane-resume: nudge sent to %s", target)
    return True


async def _mark_pane_resume_fired(cwd: str) -> None:
    """Flip the card's `pane_resume_fired` flag to True after the nudge is
    actually delivered. Idempotent — a no-op when there's no card in the
    expected claimed/pending state. Called from `_execute_pane_resume` after
    a successful send so the next dispatch tick knows that subsequent
    re-detection of an in-transcript limit is a real re-hit rather than the
    same scheduled nudge still waiting in the scheduler queue."""
    target = _resume_target_from_cwd(cwd)
    if target is None:
        return
    project_path, session_name = target
    project_key = safe_resolve_project_key(project_path)
    if project_key is None:
        return
    claimant = CLAIMANT_PREFIX + session_name
    from app.kanban.db import KanbanSessionLocal
    async with KanbanSessionLocal() as ks:
        cards = await list_cards(ks, project_key)
        card = next(
            (c for c in cards
             if c.column not in ("Done", "To Resume")
             and c.claimed_by == claimant),
            None,
        )
        if card is None:
            return
        meta = dict(card.meta or {})
        if not meta.get("pane_resume_pending"):
            return
        if meta.get("pane_resume_fired"):
            return  # already marked — no need to rewrite
        meta["pane_resume_fired"] = True
        await apply_operation(
            ks, op_type="update", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={"metadata": meta},
        )
        await ks.commit()


async def _pane_resume_fallback_to_kill(cwd: str) -> None:
    """When the scheduled nudge can't be delivered (pane gone / not ready /
    send_keys failed) or the max-attempts cap has been hit, strip the
    pending metadata, cancel the still-scheduled apscheduler job, and run
    the standard kill+To Resume reaction so the card moves into the
    existing auto-resume-rebuild path. Reads the parsed reset time back
    from the card metadata so the reaction schedules a resume at the same
    wall-clock moment the (failed) nudge was aiming at.

    The apscheduler-job cancellation is load-bearing: without it, the
    already-scheduled `_execute_pane_resume` still fires at its original
    reset-time + margin and ends up injecting a "Continue where you left
    off." keystroke into whatever tmux pane happens to be hosting the
    worktree by then — which, on a long reset window, is very likely a
    *different* session that claimed the same worktree in the meantime
    (kaart e2116332: 2 lost-injection events gemeten op 2026-07-24).
    """
    from app.services.scheduling.scheduler import scheduler_service

    resume_target = _resume_target_from_cwd(cwd)
    if resume_target is None:
        return
    project_path, session_name = resume_target
    project_key = safe_resolve_project_key(project_path)
    if project_key is None:
        return
    claimant = CLAIMANT_PREFIX + session_name

    # Cancel the still-scheduled apscheduler job up front — idempotent, a
    # missing job (the scheduler was restarted, the nudge already ran, …)
    # is not an error condition here.
    job_id = _pane_resume_job_id(cwd)
    try:
        scheduler_service._sched.remove_job(job_id)
    except Exception:
        pass

    from app.kanban.db import KanbanSessionLocal
    async with KanbanSessionLocal() as ks:
        cards = await list_cards(ks, project_key)
        card = next(
            (c for c in cards
             if c.column not in ("Done", "To Resume")
             and c.claimed_by == claimant),
            None,
        )
        if card is not None:
            meta = dict(card.meta or {})
            reset_at = meta.get("pane_resume_reset_at")
            meta.pop("pane_resume_pending", None)
            meta.pop("pane_resume_attempts", None)
            meta.pop("pane_resume_reset_at", None)
            meta.pop("pane_resume_fired", None)
            await apply_operation(
                ks, op_type="update", entity_type="card", project_key=project_key,
                entity_id=card.id, payload={"metadata": meta},
            )
            await ks.commit()
        else:
            reset_at = None

    scheduled_at = None
    if reset_at:
        try:
            scheduled_at = datetime.fromisoformat(reset_at)
        except ValueError:
            scheduled_at = None

    try:
        await move_limited_session_to_resume(cwd, scheduled_at=scheduled_at)
    except Exception:
        logger.exception(
            "pane-resume fallback: move_limited_session_to_resume failed for %s", cwd,
        )


async def handle_rate_limit_signal(cwd: str, message: str, *, source: str) -> bool:
    """Apply the standard rate-limit reaction for a kanban-dispatched session:
    record the structured signal, resolve/parse the reset time, pause the
    affected provider, and either nudge the still-alive tmux pane (pane-resume)
    or fall back to moving the card to "To Resume" via
    `move_limited_session_to_resume`.

    Shared by the Notification-hook handler
    (`scheduled_messages.router.hook_event`) and the transcript-tail sweep
    (`detect_transcript_rate_limits`) so a rate limit reaches exactly the same
    reaction regardless of which channel noticed it first -- no second,
    parallel reaction path. `source` is only used for logging/observability
    (e.g. "hook" vs "transcript"), so a later "why did this pause happen" read
    of the logs can tell the two channels apart.

    Returns True iff a kanban card was found and moved to "To Resume".
    """
    from datetime import timedelta

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dispatch_pause import set_paused_until
    from app.services.scheduling.auto_resume import (
        DEFAULT_RESUME_MESSAGE,
        FALLBACK_PAUSE_HOURS,
        auto_resume_service,
    )
    from app.services.scheduling.session_signals import session_signals

    session_signals.record_limit(cwd, message=message or "")
    parsed = auto_resume_service.parse_reset_time(message)
    if parsed:
        pause_until, _tz_name = parsed
    else:
        # Recognized as a limit hit but the reset time didn't match the known
        # clock-time format (e.g. a weekly/model cap with different
        # wording). Fall back to a conservative fixed pause instead of
        # skipping it -- skipping just re-triggers the same spin-and-burn
        # loop the pause exists to prevent.
        pause_until = datetime.now(UTC) + timedelta(hours=FALLBACK_PAUSE_HOURS)
        logger.warning(
            "unrecognized usage-limit message format for %s (source=%s), falling "
            "back to a %sh dispatch pause: %r",
            cwd, source, FALLBACK_PAUSE_HOURS, message,
        )

    try:
        provider = await _provider_for_cwd(cwd)
    except Exception:
        logger.exception("failed to resolve provider for %s", cwd)
        provider = None

    try:
        async with KanbanSessionLocal() as ks:
            await set_paused_until(ks, pause_until, provider=provider)
            await ks.commit()
    except Exception:
        logger.exception("failed to set dispatch pause for %s", cwd)

    # Pane-resume branch: prefer nudging the still-alive tmux pane at reset+margin
    # over the existing kill+To Resume reaction. The bookkeeping distinguishes
    # two flavours of "pending" — `fired=False` means a nudge is scheduled for
    # a future reset time and the apscheduler job will deliver it, while
    # `fired=True` means the nudge already landed and a fresh detection is a
    # real re-hit. Without this split, every dispatch tick (≈10 s) would
    # reschedule because the same in-transcript limit stays at the tail until
    # the nudge fires — burning the 3-attempt budget in ~30 s and falling back
    # before any nudge ever got a chance to run (kaart e2116332, gemeten op
    # 2026-07-24: 36 echte events × ~30 s tot fallback, 0 echte nudges).
    pending = await _read_pane_resume_state(cwd)
    if pending is not None and pending["fired"]:
        # Previous nudge already fired — this detection is a genuine re-hit.
        # Bump attempts (or fall back at the cap) the way the spec describes.
        next_attempts = pending["attempts"] + 1
        if next_attempts >= PANE_RESUME_MAX_ATTEMPTS:
            await _clear_pane_resume_state(cwd)
            moved = await _do_move_to_resume(cwd, pause_until)
        else:
            scheduled = await try_pane_resume(
                cwd, pause_until, DEFAULT_RESUME_MESSAGE,
                attempts=next_attempts,
            )
            if scheduled:
                moved = False
            else:
                moved = await _do_move_to_resume(cwd, pause_until)
    elif pending is not None:
        # Previous nudge still in flight (scheduled but not yet fired). Don't
        # reschedule — the apscheduler job handles delivery at the parsed reset
        # time, and a recovery will clear the pending state from the
        # transcript-tail detection sweep.
        moved = False
    else:
        scheduled = await try_pane_resume(
            cwd, pause_until, DEFAULT_RESUME_MESSAGE, attempts=1,
        )
        if scheduled:
            moved = False
        else:
            moved = await _do_move_to_resume(cwd, pause_until)

    logger.info(
        "rate-limit signal handled (source=%s cwd=%s moved=%s pause_until=%s)",
        source, cwd, moved, pause_until.isoformat(),
    )
    return moved


async def detect_transcript_rate_limits(
    *, cards: Iterable[KanbanCard], project_path: str,
) -> int:
    """Sweep every agent-claimed card for an unresolved rate-limit hit visible
    only in its Claude Code transcript tail.

    Closes the mid-session detection gap: a session that's been productive
    for a while before hitting its limit stays alive in tmux (the CLI never
    exits on a 429/session-limit — it prints the error and returns to its
    prompt) and has long since sent its first hook event, so it is invisible
    to both the Notification-hook path (§2.1: the hook never carries the
    limit message) and the reaper's stuck-session pane scan (§2.2: it only
    inspects sessions that never sent a hook). The transcript is the one
    channel that always carries the limit, for both vendors, whether it
    happens at spawn or hours in — see
    docs/cockpit/sessie-limiet-auto-dispatch-analyse.md §5 R1.

    Runs independently of tmux liveness — deliberately not folded into
    `reap_stale_claims`'s live/stuck/dead branching, since the transcript
    signal applies regardless of which of those buckets a session is
    currently in. Skips cards on fixed columns (Backlog/Impediment/Done/To
    Resume) and cards with no `agent:` claim, same as `reap_stale_claims`.

    Reuses `handle_rate_limit_signal` for the reaction (per-provider pause +
    `move_limited_session_to_resume`) so a limit reaches exactly the same
    outcome regardless of which channel noticed it first — no second,
    parallel reaction path. Returns the number of cards for which a limit was
    detected and handled.
    """
    from app.kanban.schemas import COLUMNS
    from app.kanban.session_recovery import _resolve_transcript_file

    handled = 0
    for card in cards:
        if card.column in COLUMNS:
            continue
        name = _claimant_session(card)
        if name is None:
            continue
        transcript = _resolve_transcript_file(project_path, name)
        if transcript is None:
            continue
        message = _tail_rate_limit_message(transcript)
        if message is None:
            # No active limit. If the card still carries pane-resume pending
            # metadata from a previous nudge that DID land (the session
            # recovered on its own), strip it so the next genuine limit cycle
            # starts with a fresh attempt counter instead of inheriting a
            # stale one from a limit that's already in the past.
            if (card.meta or {}).get("pane_resume_pending"):
                await _clear_pane_resume_metadata_for_card(card, project_path)
            continue
        cwd = str(Path(project_path) / ".claude" / "worktrees" / name)
        moved = await handle_rate_limit_signal(cwd, message, source="transcript")
        logger.info(
            "transcript-tail rate limit detected: session=%s card=%s "
            "transcript=%s classification=limit moved=%s",
            name, card.id, transcript, moved,
        )
        handled += 1
    return handled


async def _cleanup_stuck_session(
    session, *, card, project_key: str, session_name: str, pane_content: str,
    matched_needle: str | None = None,
    project_path: str | None = None,
) -> None:
    """Clean up a stuck tmux session that hit a rate limit.

    Compensating-ops shape mirrors `_release_dead_claim`'s dead-on-arrival
    branch: kill the tmux session, bump dispatch_failures, clear any stale
    resume pointer, release the agent claim — and additionally set the
    global dispatch pause so the next tick doesn't immediately re-spawn
    the same card into the same rate-limited wall.

    The session is NOT dead-on-arrival (it has been alive for at least
    STUCK_SESSION_TIMEOUT_S) but counting it as a failure is still right:
    a 429 is an infrastructure wall that will hit the same dispatch
    target on the next attempt, so we want MAX_DISPATCH_FAILURES to
    eventually move the card to Impediment instead of looping forever.

    `matched_needle` is the specific needle/phrase from the pane-scan
    matcher (when this branch was reached via the pane fallback). The
    structured-signal path leaves it None — that branch already knows
    the limit message verbatim, and the needle is implicit. Logging the
    matched needle alongside the matched pane line (instead of the old
    truncated 200-char pane prefix) was the observability gap card
    3a8f27a4… called out — see the 2026-07-22 incident where a healthy
    session was killed and the truncated log hid which needle had
    actually fired."""
    from datetime import UTC, datetime, timedelta

    from app.kanban.dispatch_pause import set_paused_until
    from app.services.scheduling.auto_resume import FALLBACK_PAUSE_HOURS

    if matched_needle is not None:
        matched_line = _matching_pane_line(pane_content, matched_needle)
        logger.warning(
            "stuck session %s hit a rate limit (needle=%r line=%r); "
            "pausing dispatch for %sh, killing tmux, releasing claim",
            session_name, matched_needle, matched_line,
            FALLBACK_PAUSE_HOURS,
        )
    else:
        logger.warning(
            "stuck session %s hit a rate limit (pane: %r); pausing dispatch for %sh, "
            "killing tmux, releasing claim",
            session_name, (pane_content or "")[:200].replace("\n", " "),
            FALLBACK_PAUSE_HOURS,
        )

    # Conservative fallback duration; the hook-event path has the parsed
    # reset time, but the reaper only sees tmux pane content.
    pause_until = datetime.now(UTC) + timedelta(hours=FALLBACK_PAUSE_HOURS)
    # Per-provider pause: only the subscription this session was hitting
    # gets gated, so a bedrock outage doesn't freeze anthropic/minimax
    # traffic. Kaart 9ff86416…: the reactive limit path now resolves the
    # provider via ``_limited_provider_for_card`` (which prefers
    # ``card.dispatch_provider`` and falls back to the full precedence
    # chain via ``_effective_provider_for_pause_gate``), so a pool-/
    # override-routed card pauses the right subscription. project_path is
    # best-effort: when it's None (some reaper invocations lack it), the
    # helper degrades to the narrow column-only resolver. provider=None
    # only happens when the card/column context is missing -- then the
    # legacy global pause keeps today's behaviour intact.
    provider = await _limited_provider_for_card(
        session, project_key=project_key, project_path=project_path,
        card=card, target_column=card.column,
    )
    await set_paused_until(session, pause_until, provider=provider)

    # _kill_agent_session also calls clear_spawn on the registry, so
    # get_stuck_sessions won't keep flagging this name next tick.
    _kill_agent_session(session_name)

    await _clear_stale_resume_fields(session, card=card, project_key=project_key)
    failures = await _bump_dispatch_failures(session, card=card, project_key=project_key)
    if failures >= MAX_DISPATCH_FAILURES:
        # We already captured the pane (the rate-limit pattern that
        # triggered this branch), so thread it into the impediment comment.
        # `_move_to_impediment_after_repeated_failures` truncates to a
        # single 300-char line, mirroring `_cleanup_stuck_session`'s own
        # pane-snippet pattern below. See kanban card
        # 5ec5a68013da4422b0a49fb2731cb8a7.
        await _move_to_impediment_after_repeated_failures(
            session, card=card, project_key=project_key,
            session_name=session_name, failures=failures,
            last_error=pane_content,
        )
    else:
        logger.info(
            "reaped stuck rate-limited session %s on card %s (%d/%d consecutive failures)",
            session_name, card.id, failures, MAX_DISPATCH_FAILURES,
        )

    await apply_operation(
        session, op_type="release", entity_type="card",
        project_key=project_key, entity_id=card.id, payload={},
    )

    # Surface the cleanup on the card's activity feed. The pane content can
    # be multiline + ANSI-encoded — collapse to the first 60 chars on one line
    # so the comment stays readable in the activity feed.
    pane_snippet = (pane_content or "").replace("\n", " ").replace("\r", " ")[:60]
    text = (
        f"🚦 Stuck session {session_name} detected with rate-limit message "
        f"({pane_snippet!r}). Pausing auto-dispatch for ~{FALLBACK_PAUSE_HOURS}h; "
        f"tmux killed, claim released."
    )
    await _post_rate_limit_activity_comment(
        session, card=card, project_key=project_key, text=text,
    )


def _live_sessions() -> set[str] | None:
    """Names of tmux sessions alive on this device, or None when tmux cannot be
    queried. Returning None (not an empty set) on an *ambiguous* failure is the
    whole point: the reaper must never mistake a transient `tmux` hiccup for "every
    session is dead" and release live claims. A clean "no server running" maps to
    an empty set, since that genuinely means zero live sessions.

    tmux's wording for "no server has ever been started" varies by version: older
    releases say "no server running on <socket>"; tmux 3.6 says "error connecting
    to <socket> (No such file or directory)" instead. Both mean the same thing --
    no socket file exists yet -- so both must map to an empty set, or a host that
    has never opened a tmux server (e.g. right after a restart) permanently blocks
    the reaper and session-recovery from ever touching a claim."""
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        stderr = (result.stderr or "").lower()
        no_server = (
            "no server running" in stderr
            or ("error connecting to" in stderr and "no such file or directory" in stderr)
        )
        if no_server:
            return set()
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


# Auto-dispatch scans ONLY this allow-list. Cards on any other column
# (agent columns, Done, Impediment, …) are NEVER auto-dispatched; they
# reach the agent only through explicit `report_impediment` → resume,
# analyst-fase promotion, or `redispatch_card`. Extending this tuple is
# not the way to "let a new column dispatch" — both the kanban-DB
# (`COLUMNS` in schemas.py) and the frontend's `FIXED_COLUMNS` set stay
# the source of truth for "what is a fixed column"; a column outside
# this tuple is by definition not auto-dispatched. See
# docs/cockpit/kanban-conventions.md §1 for the full convention map.
# Order is load-bearing, not cosmetic: `_next_card` walks this tuple and returns
# the first column that yields a selectable card, and `_dispatch_order_key` ranks
# the bulk paths by the same index. "To Resume" comes first so interrupted work is
# finished before new work is started ("stop starting, start finishing") — a card
# there already owns a worktree and a resumable transcript, both of which decay as
# master drifts away from them. Backlog-first was never a decision; it was the
# literal order of this tuple since the initial commit.
#
# Backlog does not starve behind it: a "To Resume" card parked by the limit-recovery
# path carries a future `scheduled_at`, so `_is_due` keeps it out of `selectable()`
# until its reset time, and the per-provider pause (dispatch_pause.py) plus
# MAX_DISPATCH_FAILURES bound the "resume → dies → back to To Resume" loop.
_DISPATCH_COLUMNS = ("To Resume", "Backlog")  # resumed cards first, then new cards from Backlog
_PRIORITY_RANK = {"high": 3, "medium": 2, "low": 1, "none": 0}


def _priority_key(card) -> int:
    """Sort key for priority-aware dispatch: higher value wins. Unknown / None
    priority collapses to 0 so the rank order still determines the tie-break
    (stable sort preserves within-priority rank order). Shared between the
    auto-tick's _next_card and the bulk paths (dispatch_all_pending,
    redispatch_all_orphans) so the three call sites can't drift."""
    return _PRIORITY_RANK.get(getattr(card, "priority", None), 0)


def _dispatch_order_key(card) -> tuple[int, int]:
    """Flat sort key reproducing `_next_card`'s ordering for the bulk paths.

    Lower tuple sorts first: `_DISPATCH_COLUMNS` index primary (so "To Resume"
    drains before "Backlog"), priority descending secondary. A column outside the
    tuple ranks last — the bulk callers pre-filter to dispatchable cards, so that
    branch only ever catches an orphan.

    Column beats priority on purpose: `_next_card` returns the first *column* that
    yields a selectable card and only sorts by priority *within* it, so a high
    Backlog card does not jump a low To Resume card there either. Keeping the bulk
    "Dispatch All" path on the same rule is the whole point of this helper — before
    it, the bulk path sorted by priority across both columns and silently
    contradicted the tick.
    """
    try:
        col_rank = _DISPATCH_COLUMNS.index(card.column)
    except ValueError:
        col_rank = len(_DISPATCH_COLUMNS)
    return (col_rank, -_priority_key(card))


def _is_due(card: KanbanCard) -> bool:
    """True unless `card.scheduled_at` names a not-yet-reached future time.

    Thin alias kept for the existing call sites and tests; the rule itself lives
    in ``dep_resolver.is_due`` so the dispatcher and ``classify_hold`` cannot
    drift on what "due" means.
    """
    return dep_is_due(card)


def _awaiting_plan_ref(card) -> bool:
    """True when a child card (has a ``parent_card_id``) has not yet received
    its ``plan_ref`` deliverable from the analyst's ``add_plan_attachment`` call.

    Closes the create_card→add_plan_attachment race: the analyst creates a child
    (step 3) directly into a dispatch-eligible column, but only links the plan
    (step 4) a few seconds later. A dispatch tick firing in that window would
    spawn an executor whose prompt renders the generic "Plan niet beschikbaar"
    placeholder (`_plan_context_section`) — indistinguishable from a genuinely
    missing plan — forcing a needless report_impediment. Holding such a child out
    of dispatch until its ``plan_ref`` exists makes it dispatch-eligible only once
    the plan it points at is actually attached. See the [self-improve] kanban card
    "Child card becomes dispatch-eligible before analyst's add_plan_attachment
    runs (race)".

    A card with no ``parent_card_id`` (an ordinary top-level card) is never
    gated — it never carries a ``plan_ref`` in the first place.

    **Phase-blind on purpose only as a raw predicate.** This answers "does the
    card lack its plan_ref", nothing more. Whether that *should* hold the card
    depends on where the card is: in an agent column it has already been
    dispatched, so the create→attach race cannot apply and the gate must not
    fire. That scoping lives in ``dep_resolver.classify_hold``
    (``plan_ref_columns``), which is what the dispatcher actually consults —
    applying this predicate unscoped is the bug that hid finished-but-unreviewed
    cards from the reviewer for five days.
    """
    if not getattr(card, "parent_card_id", None):
        return False
    return not has_plan_ref(card)


def _is_gated(card) -> bool:
    """True when the card carries a machine-readable business gate that the
    operator has not yet lifted.

    Reads ``card.meta["gated_on"]`` (the ORM attribute is ``meta`` because
    SQLAlchemy's Declarative API reserves ``metadata`` on the base class; the
    DB column is ``metadata`` — see models.py:94). The trigger string is
    opaque to the dispatcher: any non-empty value means "do not auto-dispatch
    this card; a human must clear it". An empty string is treated as no gate
    (fail open, same contract as ``_is_due``'s unparseable timestamp) so a
    user who sets ``gated_on: ""`` by mistake doesn't wedge their card
    forever.

    Why a metadata flag rather than reusing ``depends_on`` (card-DAG) or
    ``scheduled_at`` (time-based): the gate is *neither* a kanban-card
    dependency (it references external business state — e.g. 'second executor
    provider onboarded') nor a clock trigger ('dispatch after YYYY-MM-DD').
    The three filters are orthogonal: a card is dispatchable iff ``_is_due``
    AND ``not _awaiting_plan_ref`` AND ``not _is_gated`` AND
    ``meets_dep_prerequisites``. See kanban card "[problem] Gepoorte kaarten
    ('bewust niet nu, pas bij trigger X') worden auto-gedispatcht zodra hun
    depends_on klaar is" for the bug class that motivated this helper.
    """
    meta = getattr(card, "meta", None)
    if not meta:
        return False
    gate = meta.get("gated_on")
    return bool(gate)


# Hold reasons that disqualify a card from *selection*. `dependent` and
# `dangling_dep` are deliberately absent: the tick loop re-checks deps
# downstream, where it can surface a dangling dep to Impediment instead of
# skipping it in silence. Filtering them here would swallow that signal — the
# exact mistake this whole change exists to stop making.
_SELECTION_HOLDS = frozenset({
    HOLD_GATED, HOLD_AWAITING_PLAN_REF, HOLD_MISSING_PARENT, HOLD_SCHEDULED,
})


def card_hold(card, cards_by_id: dict, live_ids: set[str] | None = None):
    """The dispatcher's view of why ``card`` is not dispatchable (or None).

    One-line seam over ``dep_resolver.classify_hold`` that binds the two
    board-shaped arguments the pure function cannot know: which columns the
    plan-ref race applies to, and the existence oracle.
    """
    from app.kanban.schemas import COLUMNS
    return classify_hold(
        card, cards_by_id, live_ids=live_ids, plan_ref_columns=COLUMNS,
    )


async def _persist_holds(session, cards, live_ids: set[str] | None = None) -> None:
    """Write each card's current hold (``dep_resolver.classify_hold``) onto the
    card, so "not moving" becomes an inspectable state instead of an absence.

    ``held_since`` is only stamped when the *reason* changes, so it measures how
    long this particular hold has lasted — the clock an unclaimed card never had.
    Both existing watchdogs (``reap_stale_claims``,
    ``check_progress_liveness``) key on ``claimed_by`` and therefore supervise
    only work that was started; ageing a hold is what finally makes the other
    half observable.

    Scope: cards in a dispatch column or an agent column — the ones the tick
    actually considers. A card parked in ``Awaiting Subtasks`` or sitting in
    ``Done``/``Impediment`` has its status written on its column already; that
    was never the invisible case, so its hold is cleared rather than invented.

    Deliberately not routed through ``apply_operation``: hold state is derived
    and recomputed every tick, not an authored change, and it must not turn the
    card's activity feed into a poll log.

    Flushes but does not commit. Opening a transaction boundary of its own
    mid-tick broke test isolation (a committed-then-reused session left the
    process-global HLC lock bound to a dead event loop), and it buys nothing:
    the tick's caller commits, and a hold that misses one write is recomputed
    on the next tick anyway.
    """
    from app.kanban.schemas import COLUMNS

    cards_by_id = {c.id: c for c in cards}
    now = datetime.now(UTC).isoformat()
    changed = False

    for card in cards:
        tracked = card.column in _DISPATCH_COLUMNS or card.column not in COLUMNS
        hold = card_hold(card, cards_by_id, live_ids) if tracked and not card.claimed_by else None

        reason = hold.reason if hold else None
        blocker = list(hold.blocker_ids) if hold else None

        if card.held_reason == reason and (card.held_blocker or None) == blocker:
            continue
        if card.held_reason != reason or (reason and not card.held_since):
            # A different reason is a different hold, so it gets its own clock.
            # A same-reason blocker change (one of three deps went Done) must
            # *not* reset it, or a long hold would look perpetually fresh.
            card.held_since = now if reason else None
        card.held_reason = reason
        card.held_blocker = blocker
        changed = True

    if changed:
        await session.flush()


def _next_card(
    cards: Iterable[KanbanCard], live_ids: set[str] | None = None,
) -> KanbanCard | None:
    """Pick the next card to dispatch, or None.

    ``live_ids`` is the board-wide existence oracle; pass it when available so a
    card whose parent was deleted is recognised as permanently orphaned rather
    than treated as merely waiting for a plan. Omitting it only costs
    resolution, never correctness.
    """
    cards = list(cards)
    cards_by_id = {c.id: c for c in cards}

    def selectable(c) -> bool:
        if c.claimed_by:
            return False
        hold = card_hold(c, cards_by_id, live_ids)
        return hold is None or hold.reason not in _SELECTION_HOLDS

    for col in _DISPATCH_COLUMNS:
        col_cards = [c for c in cards if c.column == col and selectable(c)]
        if col_cards:
            # list_cards is ordered by rank; stable-sort by priority on top of that
            # so higher-priority cards jump the queue within the same column.
            col_cards.sort(key=_priority_key, reverse=True)
            return col_cards[0]

    # Fall back to orphans: cards left unclaimed in an agent column, most commonly
    # by reap_stale_claims releasing a dead session's claim without a resumable
    # transcript to fall back on. Without this, an orphan is invisible to every
    # later tick -- it sits in its agent column forever, cap slot unused, until a
    # human notices and hits "redispatch" by hand (see kanban card "auto dispatch
    # nakijken": auto-dispatch looked stuck even though it was enabled).
    from app.kanban.schemas import COLUMNS
    orphans = [c for c in cards if c.column not in COLUMNS and selectable(c)]
    if orphans:
        orphans.sort(key=_priority_key, reverse=True)
        return orphans[0]
    return None


# ---- core ------------------------------------------------------------------

async def _run_card(
    session, *, card, project_key: str, project_path: str, transport: SpawnTransport,
    phase: str = "executor",
    impediment_question: str | None = None,
    impediment_answer: str | None = None,
    revisit_question: str | None = None,
    revisit_prior_decision: dict | None = None,
    agent_override: str | None = None,
    live_sessions: set[str] | None = None,
    auto_dispatch: bool = False,
) -> dict | None:
    """Claim+move-to-agent-column+spawn one specific card. Returns a result dict, or None if
    the claim was lost. The persona honours an explicit per-card agent over the column.

    The transport parameter is the project default. If the card has an explicit transport
    setting, that takes precedence. `live_sessions`, when the caller already has a fresh
    tmux snapshot, is used to keep the minted session name from colliding with a running
    session (see _mint_session_name).

    `revisit_question` / `revisit_prior_decision` thread through to
    `build_card_prompt` (the `## REVISIT` section mirror of `## IMPEDIMENT`).
    Dispatch sets these when it picks up a card whose latest
    `**Revisit:**`-prefixed comment exists — see `dispatch_project` and
    `_run_card`'s caller; manual `dispatch_card` callers (UI button) leave
    them None so the prompt doesn't leak prior-decision context the human
    didn't ask for.

    `auto_dispatch=True` is set by the auto-tick (`dispatch_project`) so we
    can tell apart a scheduled-resume pickup (the card had a `scheduled_at`
    in the past, was held out of dispatch by `_is_due`) from a manual
    force-dispatch via the UI. When set AND the card had `scheduled_at`,
    post an `Auto-resuming (scheduled_at was <iso>)` activity comment so the
    activity feed shows the tick didn't force-dispatch early.

    **Provider routing:** the spawn-side provider/model pick below delegates
    to ``resolve_effective_provider_and_model`` (the canonical 5-layer
    resolver — see its docstring). Any new "what subscription would this
    card land on" decision in this path MUST go through the resolver rather
    than re-walking the chain ad-hoc — the self-check
    ``scripts/check-dispatch-resolver-usage.sh`` flags any new ad-hoc walk."""
    # Re-read the card from the DB before claiming. The auto-tick's cached
    # `cards` list reflects state from `list_cards` at the top of the tick;
    # a `set_resume` MCP call (or any other concurrent update) that commits
    # *between* that read and this function would otherwise be masked by
    # the stale card object. The most consequential masked update is
    # `set_resume`'s `resume_session_id` — without this re-read, the
    # operator's "continue from where you left off" intent gets silently
    # ignored and the card is dispatched with the worktree transport,
    # spawning a brand-new session. See kanban card
    # `[self-improve] set_resume races a fresh auto-dispatch`.
    fresh = await get_card(session, card.id)
    if fresh is None:
        return None
    card = fresh
    # Also honor a fresh `scheduled_at` that landed since the cached list
    # read. `_is_due` already gates `_next_card`, but the cached card may
    # predate the defer — the re-read closes that gap so a deferred card
    # isn't claimed + spawned in the same tick that just deferred it.
    if not _is_due(card):
        logger.info(
            "_run_card: card %s has a fresh scheduled_at (%s); deferring to next tick",
            card.id, card.scheduled_at,
        )
        return None

    source_column = card.column
    name = _mint_session_name(project_path, card.title, live_sessions=live_sessions)
    claimant = CLAIMANT_PREFIX + name

    # Capture the original scheduled_at BEFORE the claim, so the auto-resume
    # comment can echo it back. After the claim + move the card's column is no
    # longer "To Resume" but the scheduled_at field is preserved on the row.
    prior_scheduled_at = getattr(card, "scheduled_at", None)

    # Get the actual transport for this card (card transport > project default)
    card_transport = get_transport_for_card(card, transport)

    try:
        await apply_operation(
            session, op_type="claim", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={"claimed_by": claimant},
        )
    except ClaimRejected:
        return None  # lost the race; another tick/device took it

    # Auto-resume audit: when the auto-tick picked up a card that had a
    # `scheduled_at` in the past (was held out by `_is_due`), record that
    # fact on the activity feed so an operator reading the board later sees
    # this wasn't a force-dispatch — the scheduled time arrived. Gated on
    # `auto_dispatch=True` so manual UI dispatches don't get the same noise.
    if auto_dispatch and prior_scheduled_at:
        text = f"⏰ Auto-resuming (scheduled_at was {prior_scheduled_at})."
        await _post_rate_limit_activity_comment(
            session, card=card, project_key=project_key, text=text,
        )

    # `agent_override` / `card.agent` overload two unrelated concepts:
    #   - a CLI id (claude-code, mimo-code, …) → which CLI spawns the session
    #   - a persona name (engineer, analyst, …)  → which column + role prompt
    # Resolve them separately so a CLI id is never mistaken for a column.
    known_clis = _known_cli_ids()
    cli_id = next(
        (v for v in (agent_override, _phase_cli_id(card, phase=phase,
                                                       known_clis=known_clis))
         if v in known_clis),
        "claude-code",
    )
    # Resolve the work_type fallback *before* the persona check in
    # _phase_target_agent. Cheap when the project's mapping has no override
    # (single DB lookup with a small indexed table). Closes the regression
    # behind kanban card 9cf106e7: cards with work_type='analysis' but no
    # valid agent (legacy cards from before the create-time auto-fill, or
    # PATCHed work_type) used to silently route to 'engineer'. See
    # _phase_target_agent's fallback_persona docstring for the full contract.
    # Wrapped in try/except: a transient DB error here (between claim and
    # move) would otherwise leave the card committed as 'claimed' but never
    # moved, invisible to _next_card and stuck until a human clears the
    # claim. Falling back to None preserves the legacy 'engineer' routing —
    # strictly inferior to the work_type-aware routing, but the card still
    # gets dispatched and the card row isn't wedged.
    try:
        fallback_persona = await _resolve_work_type_fallback(
            session, project_key, card,
        )
    except Exception:
        logger.exception(
            "work_type fallback lookup failed for card %s in %s; "
            "falling back to legacy engineer routing",
            card.id, project_key,
        )
        fallback_persona = None
    target_agent = _phase_target_agent(
        card, project_path=project_path, phase=phase, source_column=source_column,
        agent_override=agent_override, known_clis=known_clis,
        fallback_persona=fallback_persona,
    )

    await apply_operation(
        session, op_type="move", entity_type="card", project_key=project_key,
        entity_id=card.id, payload={"column": target_agent},
    )

    # Load persona for the target agent. Analyst phase uses a dedicated
    # helper that falls back to the hardcoded ANALYST_PROMPT when no
    # `analyst.md` exists in the project — otherwise the analyst session
    # gets an empty preamble. The analyst persona itself documents both
    # modi (multi-agent decomposition + leaf design-deliverable), so the
    # helper doesn't need to branch here — see `_resolve_analyst_persona`.
    if phase == "analyst":
        persona = _resolve_analyst_persona(project_path)
    else:
        persona = _read_persona_file(project_path, f"{target_agent}.md")
    ship_mode = await get_ship_mode(session, project_key)
    # Resolve the spawn's provider/model through the shared precedence
    # chain. ``dispatch_card`` and ``resolve_column_effective_model`` both
    # delegate to ``resolve_effective_provider_and_model`` so a future
    # chain tweak (new override layer, reorder) only has to be made once
    # — see kaart 8da646d8…. The dispatch picker uses
    # ``_pick_pool_choice`` (live usage-aware, CLI-scoped via
    # ``_pick_pool_choice``); the column-settings UI uses the no-snapshot
    # ``_column_settings_pool_picker`` (kaart 8f40d443…).
    #
    # Precedence the helper walks (highest wins):
    #   1. global_override (board-wide subscription pin)
    #   2. pool_choice     (ordered, usage-aware router)
    #   3. per-card column_overrides[target_agent]
    #   4. column.default_provider / column.default_model / card.model
    #   5. persona frontmatter ``model:`` (Anthropic-only fallback)
    async def _dispatch_pool_picker(entries: list[PoolEntry]) -> PoolEntry | None:
        return await _pick_pool_choice(
            session, entries, project_key=project_key,
            cli_id=cli_id,
        )
    resolved = await resolve_effective_provider_and_model(
        session,
        project_key=project_key,
        target_agent=target_agent,
        project_path=project_path,
        pick_pool=_dispatch_pool_picker,
        card_overrides=(card.column_overrides or {}).get(target_agent),
        card_model=card.model,
        cli_id=cli_id,
    )
    provider = resolved["provider"]
    effective_model = resolved["model"]
    # Switch the spawn transport to OpenCode when the precedence chain
    # picked an opencode-go/opencode subscription, unless an explicit
    # per-card CLI pin overrules it. Pool picker used the original
    # cli_id, so OpenCode pool entries weren't considered this round —
    # closing that gap is a follow-up (thread column_default_provider
    # into ``_phase_cli_id`` so the pool picker sees open-code up front).
    explicit_cli_overrides = [
        agent_override,
        getattr(card, "analyst_agent_id" if phase == "analyst" else "executor_agent_id", None),
        getattr(card, "agent", None),
    ]
    explicit_cli_chosen = any(v for v in explicit_cli_overrides if v in known_clis)
    cli_id = _cli_id_for_opencode_provider(
        cli_id, provider, explicit_cli_chosen=explicit_cli_chosen,
    )
    # Re-dispatch safety net (kaart ff2d03fce…): when this card was previously
    # claimed by a session whose branch has unmerged commits, inject a
    # warning block so the new agent sees the existing branch and can
    # ship/verify instead of rebuilding from scratch. The op-log query is
    # cheap (one indexed scan of an already-materialised activity feed)
    # and only runs once per dispatch — the helper returns "" on first
    # dispatch or when the prior branch has no commits ahead of
    # origin/master, so the hot path is unaffected for ordinary cards.
    prior_branch_warning = await _resolve_prior_branch_warning(
        session, card=card, project_path=project_path,
    )
    # The worktree path the spawn will actually use (kanban card
    # a962b209aea4489680c15de3562eb8bb): compute it from the same
    # ``name``/``project_path`` pair the worktree transport uses, so the
    # callout and ship-recipe's ``cwd`` references point at the real
    # on-disk location of *this* dispatch and don't default to the meta
    # project. Only the worktree transport creates a fresh host-side
    # ``.claude/worktrees/<name>`` checkout:
    # - sandcastle: no host worktree exists; the constructed path would
    #   point at a non-existent directory and lie to the agent about its
    #   actual cwd (which lives inside a container).
    # - resume (`card.resume_session_id` set → ``make_resume_transport``):
    #   the spawned session's cwd is the *prior* session's project_folder,
    #   NOT a fresh worktree keyed on the brand-new ``name`` minted here.
    # - headless: no per-card worktree path by construction; the runner
    #   sets up cwd differently.
    # An earlier version of this predicate used ``!= sandcastle_transport``
    # which incorrectly classified the headless transport as a worktree
    # creator; a later version used ``is worktree_transport`` which broke the
    # *normal* route: ``card.transport is None`` resolves to a *fresh*
    # ``make_worktree_transport(skip_permissions=skip)`` closure (via
    # ``get_transport_for_project``) that is not identical to the module
    # singleton, so the identity check returned False and every ordinary
    # dispatched agent lost its real worktree write-address. Detect by the
    # transport *kind* label instead, which every worktree-factory instance
    # carries (kaart a962b209…).
    is_fresh_worktree = _transport_is_worktree(card_transport)
    worktree_path = (
        str(Path(project_path) / ".claude" / "worktrees" / name)
        if is_fresh_worktree else None
    )
    prompt = build_card_prompt(card, persona=persona, ship_mode=ship_mode,
        phase=phase,
        impediment_question=impediment_question,
        impediment_answer=impediment_answer,
        revisit_question=revisit_question,
        revisit_prior_decision=revisit_prior_decision,
        prior_branch_warning=prior_branch_warning,
        project_path=project_path,
        worktree_path=worktree_path)
    if phase == "executor" and card.parent_card_id is not None:
        # Only child cards (parent_card_id set) get the PLAN CONTEXT section.
        # Legacy single-agent cards never have a parent; prepending the
        # "Plan niet beschikbaar" placeholder to those would silently
        # downgrade every existing kanban executor prompt. When a child
        # card has a parent but its plan_ref is missing/unresolvable,
        # _resolve_plan_for_child returns a non-OK status and the helper
        # surfaces a status-specific placeholder — that's the desired signal
        # to the executor that something is off (parent deleted, plan never
        # written, ref malformed, …).
        plan_status, plan_md, plan_id, parent_id = await _resolve_plan_for_child(session, card)
        plan_section = _plan_context_section(
            status=plan_status,
            plan_markdown=plan_md,
            plan_deliverable_id=plan_id,
            parent_card_id=parent_id,
            card_description=getattr(card, "description", None),
        )
        prompt = plan_section + prompt
    # kaart 293d1faa…: when the resolved provider is
    # ``anthropic-compatible`` the auto-dispatch path used to skip the
    # endpoint resolution (only the REST interactive spawn did that),
    # which made every pool/override/column_override pin of this
    # provider silently fail at spawn time. The endpoint name comes
    # from the same precedence chain as the provider — global_override
    # > pool_choice > column_override > column.default_provider —
    # then ``resolve_compatible_endpoint`` (shared with the REST path)
    # turns the name into ``base_url`` + ``auth_token`` so
    # ``build_provider_env`` never raises on a missing base_url.
    endpoint_resolution: dict | None = None
    endpoint_name: str | None = None
    if provider == PROVIDER_COMPATIBLE:
        endpoint_name = resolved["endpoint_name"]
        from app.services.agentic_cli.endpoints import (
            resolve_compatible_endpoint as _resolve_compatible_endpoint,
        )
        endpoint_resolution = await _resolve_compatible_endpoint(
            session, project_key, endpoint_name,
            requested_model=effective_model,
        )
        # The endpoint row carries its own ``model`` as the per-endpoint
        # default — the REST path falls back to it when the request
        # didn't pin one. The dispatch path mirrors the same contract
        # so ``build_provider_env`` always receives a non-empty model
        # and the spawned CLI never inherits the column-default chain
        # (which is for Anthropic, not the named endpoint).
        if endpoint_resolution and endpoint_resolution.get("model"):
            effective_model = endpoint_resolution["model"]
    # Only forward the endpoint kwargs when the spawn actually targets
    # an anthropic-compatible provider — the unthreaded default is to
    # keep the existing transport signature (directory, prompt,
    # session_name, cli_id, provider, model) so all non-endpoint
    # test fakes + sandcastle forwarders keep working unchanged.
    endpoint_kwargs: dict = {}
    if provider == PROVIDER_COMPATIBLE and endpoint_resolution is not None:
        endpoint_kwargs = {
            "endpoint_name": endpoint_resolution["name"],
            "endpoint_base_url": endpoint_resolution["base_url"],
            "endpoint_auth_token": endpoint_resolution["auth_token"],
        }
    if is_fresh_worktree:
        # The synchronous worktree transport installs RTK through a private
        # worker-thread event loop and DB connection. Release this session's
        # SQLite write lock first; otherwise the worker blocks while posting
        # its activity note and the token saver silently degrades to failed.
        # Claim + move are durable before spawn, and the existing exception
        # path applies and commits compensating release/move operations.
        await session.commit()
    try:
        spawned = card_transport(directory=project_path, prompt=prompt, session_name=name,
                                 cli_id=cli_id, provider=provider, model=effective_model,
                                 # Token-saver (RTK) per-lane opt-in seam (kaart
                                 # c31333bf…). The worktree transport installs
                                 # the hook when the column flag is on and
                                 # the board kill-switch is enabled. Passing
                                 # these is a no-op for transports that don't
                                 # use them (resume, sandcastle, headless).
                                 card_id=card.id, column_name=target_agent,
                                 **endpoint_kwargs)
    except Exception as exc:
        await apply_operation(
            session, op_type="release", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={},
        )
        # A spawn that fails synchronously (before any session existed — e.g.
        # resolve_directory raising because a --resume worktree was merged and
        # GC'd) is unconditionally a dispatch-target failure, same as a tmux
        # session that dies within seconds (see _release_dead_claim). Counting it
        # and clearing any stale resume pointer stops the exact same card from
        # looping forever: move to source_column, immediately re-picked up next
        # tick, same exception again.
        await _clear_stale_resume_fields(session, card=card, project_key=project_key)
        failures = await _bump_dispatch_failures(session, card=card, project_key=project_key)
        if failures >= MAX_DISPATCH_FAILURES:
            # Thread `str(exc)` into the impediment comment so the operator
            # sees the actual error (`tmux new-session failed: command too
            # long`, etc.) without diving into the backend logs. See kanban
            # card 5ec5a68013da4422b0a49fb2731cb8a7.
            await _move_to_impediment_after_repeated_failures(
                session, card=card, project_key=project_key,
                session_name=name, failures=failures,
                last_error=str(exc),
            )
        else:
            await apply_operation(
                session, op_type="move", entity_type="card", project_key=project_key,
                entity_id=card.id, payload={"column": source_column},
            )
        logger.exception("spawn failed for card %s in %s", card.id, project_key)
        raise

    logger.info("dispatched card %s (%s) -> session %s (transport: %s, provider: %s)",
                card.id, source_column, name,
                "sandcastle" if card_transport == sandcastle_transport else "worktree",
                cli_id)

    # Per-dispatch telemetry breadcrumbs (kanban card 8a2ad986): write the
    # fields that the per-card usage endpoint reads. The worktree path is
    # what the spawned session's transcript folder is keyed on (Claude
    # Code encodes it via convert_path_to_folder_name). Sandcastle runs
    # have no local worktree, so dispatch_project_folder stays None and
    # the endpoint will refuse the lookup — by design, not a bug.
    try:
        telemetry: dict = {
            "dispatch_started_at": datetime.now(UTC).isoformat(),
            "dispatch_model": effective_model,
            "dispatch_provider": provider,
        }
        if card_transport != sandcastle_transport:
            worktree_path = str(Path(project_path) / ".claude" / "worktrees" / name)
            telemetry["dispatch_project_folder"] = convert_path_to_folder_name(worktree_path)
        await apply_operation(
            session, op_type="update", entity_type="card", project_key=project_key,
            entity_id=card.id, payload=telemetry,
        )
    except Exception:
        # Telemetry is best-effort. A failure here must not break the
        # dispatch: the session is already running in tmux. Log and move on;
        # the per-card usage endpoint will return None for this dispatch and
        # the card will still ship. See kanban card 8a2ad986 acceptance
        # criteria #4: no extra token cost — derived telemetry must not
        # poison the dispatch.
        logger.exception(
            "dispatch telemetry write failed for card %s (session %s); "
            "continuing — per-card usage will return no data for this dispatch",
            card.id, name,
        )

    return {"card_id": card.id, "session_name": name, "claimant": claimant,
            "source_column": source_column, "spawned": spawned}


async def _move_to_resume(
    session, *, card, project_key: str, project_path: str,
    scheduled_at: str | None = None,
) -> bool:
    """When a dead agent session has a resumable worktree, move its card to "To Resume".

    Resolves the resume target via ``_resolve_resume_target``, persists the resume
    session id/folder on the card, moves it to the "To Resume" fixed column, kills
    the dead tmux session, and releases the agent claim. Returns True when a resume
    target was found and the card was moved; False when the worktree has no resumable
    transcript — the caller should fall back to a plain claim release (reaper default).

    ``scheduled_at`` (ISO8601, optional) is written onto the card alongside the resume
    fields so ``_is_due`` holds it out of auto-dispatch until then, instead of relying
    solely on the global dispatch pause. Callers pass the parsed usage-limit reset time
    when known (Notification hook path) or a conservative fallback (reaper, which only
    sees tmux pane content and never a parsed reset time); a caller with no signal at
    all leaves it ``None``, making the card immediately dispatchable.

    Accepts cards on agent columns AND Backlog/Impediment (but not on fixed
    end-state columns "Done" / "To Resume" — those are terminal). Extending to
    Backlog/Impediment lets ``move_limited_session_to_resume`` rescue a card
    whose prior reap pushed it off its agent column before the hook event
    for its rate-limited session arrived — without that, the card sits on
    Backlog with a dead session and the reaper won't re-process it.
    """
    from app.kanban.schemas import COLUMNS
    from app.kanban.session_recovery import _resolve_resume_target

    # Block terminal end-state columns only. "To Resume" is a fixed column
    # too, but idempotently re-claiming it would still be a no-op move (same
    # column), so excluding it keeps the predicate tight. Any column the
    # kanban may carry new work on (agent columns + the project workflow
    # columns Backlog / Impediment) is fair game.
    if card.column == "Done" or card.column == "To Resume":
        return False
    if card.column in COLUMNS and card.column not in ("Backlog", "Impediment"):
        return False
    session_name = _claimant_session(card)
    if session_name is None:
        return False

    target = _resolve_resume_target(project_path, session_name)
    if target is None:
        return False

    session_id, project_folder = target
    await apply_operation(
        session, op_type="update", entity_type="card", project_key=project_key,
        entity_id=card.id,
        payload={"resume_session_id": session_id,
                 "resume_project_folder": project_folder,
                 "scheduled_at": scheduled_at},
    )
    await apply_operation(
        session, op_type="move", entity_type="card", project_key=project_key,
        entity_id=card.id, payload={"column": "To Resume"},
    )
    _kill_agent_session(session_name)
    await apply_operation(
        session, op_type="release", entity_type="card",
        project_key=project_key, entity_id=card.id, payload={},
    )
    logger.info(
        "moved card %s (column %s) to To Resume (session %s -> %s, project_folder=%s)",
        card.id, card.column, session_name, session_id, project_folder,
    )
    return True


def _resume_target_from_cwd(cwd: str) -> tuple[str, str] | None:
    """Derive (project_path, session_name) from a dispatched session's cwd.

    Kanban-dispatched sessions run in `<project_path>/.claude/worktrees/<session_name>`
    (see `session_recovery._resolve_resume_target`). Any other cwd shape (project
    root, sandcastle, a manually started `claude` session) isn't ours to touch.
    """
    worktree = Path(cwd)
    if worktree.parent.name != "worktrees" or worktree.parent.parent.name != ".claude":
        return None
    return str(worktree.parent.parent.parent), worktree.name


async def _provider_for_card(
    session, project_key: str, card, agent_column: str,
) -> str | None:
    """Resolve the provider that `card` was authenticated against while it sat
    in `agent_column`. Mirrors the precedence in `dispatch_card` (per-column
    override → column default → PROVIDER_ANTHROPIC) so a per-provider pause
    targets the same subscription a fresh respawn would.

    Returns None ONLY when the caller hands in insufficient info (no card or
    no agent column name) -- in that case callers must treat the limit as
    global (``provider=None`` in ``set_paused_until``) rather than silently
    targeting anthropic traffic via the hard-coded fallback. The card+column
    path always resolves to a concrete provider, so it never needs to fall
    back to None.

    .. warning::

       **This helper is deliberately pool-/override-blind** (kaart
       9ff86416…): it walks the last two layers of the dispatch precedence
       chain (column override → column default → ``PROVIDER_ANTHROPIC``) and
       **skips** the board-wide ``global_override`` and subscription-pool
       layers. The reactive limit path used to call this helper, which
       meant a card routed via global_override or a pool entry to a
       different vendor than the column default was pausing the wrong
       subscription. New limit-path code must call
       ``_limited_provider_for_card`` instead, which prefers
       ``card.dispatch_provider`` and falls back to the full chain via
       ``_effective_provider_for_pause_gate``. This narrow helper is
       retained as a deliberate last-resort fallback for the rare
       legacy-path caller that has no project_path / dispatch_provider
       context.
    """
    if card is None or not agent_column:
        return None
    column_override = (getattr(card, "column_overrides", None) or {}).get(agent_column) or {}
    override_provider = column_override.get("provider") or None  # resolver-bypass: intentional narrow helper (last two layers only) for the rate-limit / spillover path
    return (
        override_provider
        or await get_column_default_provider(session, project_key, agent_column)  # resolver-bypass: paired with the line above; see comment on `_provider_for_card`
        or PROVIDER_ANTHROPIC
    )


async def _limited_provider_for_card(
    session, *, project_key: str, project_path: str | None,
    card, target_column: str,
) -> str | None:
    """Resolve the provider a limit-hit card was authenticated against.

    Kaart 9ff86416… — the reactive limit path used to call the narrow
    ``_provider_for_card`` helper, which walks only the last two layers of
    the dispatch precedence chain (column override → column default →
    ``PROVIDER_ANTHROPIC``) and **silently skips** the board-wide
    ``global_override`` and subscription-pool layers. A card routed to a
    different vendor via either of those layers would, on a limit hit, have
    paused the **column-default** subscription rather than the subscription
    that actually hit the wall — and the pool router would then "spill over"
    onto the very provider that just hit its limit.

    This helper closes that gap by preferring ``card.dispatch_provider`` —
    the vendor the dispatcher actually picked at spawn time (kanban-card
    8a2ad986…, written alongside ``dispatch_model`` on line ~5070) — and
    falling back to the full precedence chain via
    ``_effective_provider_for_pause_gate`` for legacy / never-dispatched
    rows where ``dispatch_provider`` is None. When ``project_path`` is also
    unavailable (the reaper sometimes calls ``_cleanup_stuck_session``
    without it), the chain-walk can't run and we degrade to the narrow
    helper — preserving today's behaviour for the rare unresolvable case
    rather than raising.

    Returns None ONLY when the caller hands in insufficient info (no card
    or no target column) — callers must treat that as the legacy
    ``provider=None`` global-pause case (``set_paused_until(session, when,
    provider=None)``) rather than guessing a subscription.
    """
    if card is None or not target_column:
        return None
    pinned = getattr(card, "dispatch_provider", None)
    if pinned:
        return pinned
    if project_path:
        return await _effective_provider_for_pause_gate(
            session, project_key=project_key, project_path=project_path,
            card=card, target_column=target_column,
        )
    # Last-resort: no dispatch_provider and no project_path. Fall back to
    # the narrow column-only resolver so callers without project_path
    # context (e.g. the reaper path) still get a best-effort answer.
    return await _provider_for_card(session, project_key, card, target_column)


async def _effective_provider_for_pause_gate(
    session, *, project_key: str, project_path: str, card, target_column: str,
) -> str | None:
    """Resolve the provider the manual-pause gate should check against (kaart
    f056b2888a…).

    Walks the same precedence chain ``_run_card`` actually uses to pick a
    spawn provider — global override → pool choice → per-card column
    override → column default — so pausing the subscription the operator
    *thinks* this card routes to is the subscription the dispatcher skips.
    The earlier ``_provider_for_card`` helper only checked the last two
    layers and missed any board-wide override or pool choice, which meant
    pausing "anthropic" silently held back cards the pool was actually
    routing to bedrock, and vice versa.
    """
    if card is None or not target_column:
        return None
    card_overrides = (getattr(card, "column_overrides", None) or {}).get(target_column) or None
    card_model = getattr(card, "model", None)  # resolver-bypass: passthrough only — these args are fed straight into `resolve_effective_provider_and_model` below
    # ``_phase_cli_id`` is the sync resolver the spawn path uses to map a
    # card's agent to its CLI id; the pool picker needs this so a manually
    # paused entry on the wrong CLI doesn't accidentally gate a card that
    # would have routed to a different CLI. Pass ``known_clis`` so a legacy
    # ``card.agent`` like ``"engineer"`` (a persona/column name, not a CLI
    # id) does NOT leak into the pool router — without that filter, the
    # pool would see no candidates and fall through to the column default,
    # silently bypassing the operator's pause. FCR-follow-up: this is the
    # same known_clis plumbing ``_run_card`` uses at line 3345.
    known_clis = _known_cli_ids()
    cli_id = _phase_cli_id(card, phase="executor", known_clis=known_clis)
    async def _gate_pool_picker(entries: list[PoolEntry]) -> PoolEntry | None:
        return await _pick_pool_choice(
            session, entries, project_key=project_key, cli_id=cli_id,
        )
    resolved = await resolve_effective_provider_and_model(
        session,
        project_key=project_key,
        target_agent=target_column,
        project_path=project_path,
        pick_pool=_gate_pool_picker,
        card_overrides=card_overrides,
        card_model=card_model,
        cli_id=cli_id,
    )
    return resolved["provider"]


async def _card_is_manually_paused(
    session, *, project_key: str, project_path: str, card, target_column: str,
) -> bool:
    """True iff the card's *effective* provider is on the operator's manual
    pause list (kaart f056b2888a…). All dispatch-path gates — auto-tick,
    dispatch-all, memory retry, bulk orphan redispatch — go through here so
    they all honour the same precedence (``global override → pool → column
    override → default``). Returns ``False`` when the chain can't resolve,
    so a missing-card edge case never wedges a tick."""
    from app.kanban.dispatch_pause import is_manually_paused, list_manually_paused_providers
    if not await list_manually_paused_providers(session):
        return False
    provider = await _effective_provider_for_pause_gate(
        session, project_key=project_key, project_path=project_path,
        card=card, target_column=target_column,
    )
    if provider is None:
        return False
    return await is_manually_paused(session, provider)


async def _provider_for_cwd(cwd: str) -> str | None:
    """Resolve the provider for a card running in `cwd`, for callers that have
    only a cwd (the Notification hook path). Looks up the card the same way as
    `move_limited_session_to_resume` -- claimed by `agent:<session_name>`, on a
    non-terminal column -- then delegates to `_limited_provider_for_card`
    (kaart 9ff86416…) so a pool-/override-routed card pauses the right
    subscription. Returns None when no card can be matched, which is also the
    condition under which `move_limited_session_to_resume` would no-op, so the
    caller can safely fall back to the global pause.
    """
    from app.kanban.db import KanbanSessionLocal

    target = _resume_target_from_cwd(cwd)
    if target is None:
        return None
    project_path, session_name = target
    project_key = safe_resolve_project_key(project_path)
    if project_key is None:
        return None

    claimant = CLAIMANT_PREFIX + session_name
    async with KanbanSessionLocal() as ks:
        cards = await list_cards(ks, project_key)
        card = next(
            (c for c in cards
             if c.column not in ("Done", "To Resume")
             and c.claimed_by == claimant),
            None,
        )
        if card is None:
            return None
        # Kaart 9ff86416…: prefer the dispatcher's actual pick
        # (``card.dispatch_provider``) over the narrow column-only resolver,
        # so a pool-/override-routed card pauses the right subscription.
        return await _limited_provider_for_card(
            ks, project_key=project_key, project_path=project_path,
            card=card, target_column=card.column,
        )


async def move_limited_session_to_resume(cwd: str, *, scheduled_at: str | None = None) -> bool:
    """When a live kanban-dispatched session hits its Claude usage/session limit,
    move its card to "To Resume" and kill the tmux session right away.

    The dead-session reaper (`reap_stale_claims`) only notices a session once its
    tmux pane is gone, but a session that has hit its limit stays open showing the
    limit notice forever (the CLI never exits) -- so without this, the card would
    sit claimed in its agent column indefinitely. Reuses `_move_to_resume`, which
    doesn't check liveness itself, so calling it for a still-alive session is safe.

    Looks for the card on any of: agent columns, Backlog, Impediment. The Backlog /
    Impediment extension matters when the rate-limited session's card was already
    pushed there by a prior reap (e.g. the new stuck-session reaper path killed it
    and bumped dispatch_failures; on the next tick the card moves to Backlog while
    waiting for dispatch_failures to cross MAX_DISPATCH_FAILURES, and a Notification
    hook event for its 429 then arrives). Cards on fixed columns (Done / To Resume)
    are left alone.

    ``scheduled_at`` (ISO8601, optional) is the caller's already-parsed usage-limit
    reset time (`auto_resume_service.parse_reset_time`); passed straight through to
    `_move_to_resume` so `_is_due` can hold the card out of auto-dispatch until the
    limit actually resets, rather than only until the global dispatch pause expires.

    Fase 2 — spillover-bij-limiet (analyse §4 Optie B / §5): when the card's
    project has a subscription pool with another genuinely-available
    subscription (`_pool_spillover_available`), the card is instead made
    *immediately* dispatch-eligible (``scheduled_at`` forced to None) so the
    next tick re-routes it onto the spillover subscription via
    ``pick_subscription`` — the just-limited provider is skipped because the
    caller sets its per-provider pause. Only when every subscription in the
    pool is exhausted does the reset-time pause below still apply. When no
    pool is configured this is a no-op and the original behaviour holds.
    """
    from app.kanban.db import KanbanSessionLocal

    target = _resume_target_from_cwd(cwd)
    if target is None:
        return False
    project_path, session_name = target
    project_key = safe_resolve_project_key(project_path)
    if project_key is None:
        return False

    claimant = CLAIMANT_PREFIX + session_name
    async with KanbanSessionLocal() as ks:
        cards = await list_cards(ks, project_key)
        # A kanban card is eligible if it's claimed by this session AND it's
        # not in a terminal fixed column. We allow any agent column plus
        # Backlog / Impediment so the function can rescue cards that landed
        # off-agent between the rate limit and the hook event firing.
        card = next(
            (c for c in cards
             if c.column not in ("Done", "To Resume")
             and c.claimed_by == claimant),
            None,
        )
        if card is None:
            return False

        # Spillover decision (fase 2): resolve the provider this card was
        # running against (its agent column, before the move) and ask the
        # pool router whether another subscription is still available. When
        # it is, drop scheduled_at so the card re-dispatches now instead of
        # waiting for the limited provider to reset.
        #
        # Kaart 8f40d443…: the spillover is now CLI-aware — the dispatcher
        # resolves the spawned CLI from the card's resolved phase
        # (``_phase_cli_id``) so the spillover only considers entries
        # whose CLI matches the original spawn. An OpenCode-rate-limited
        # card only spills to other OpenCode entries — a Codex entry
        # is not a valid spillover target.
        limited_provider = await _limited_provider_for_card(
            ks, project_key=project_key, project_path=project_path,
            card=card, target_column=card.column,
        )
        spillover = False
        if limited_provider is not None:
            # Kaart b36ca702…: the per-column spillover tail lives
            # under ``subscription_pool:<project_key>:<column>``. We
            # pass ``card.column`` so the spillover check consults
            # the same tail the dispatch resolver will, not the
            # board-wide pool — matching the acceptance criterion
            # "reviewer met lege staart blijft bij een limiet op
            # To Resume met de reset-tijd staan; engineer met staart
            # [anthropic] wordt direct herdispatchbaar".
            spillover = await _pool_spillover_available(
                ks, project_key=project_key,
                limited_provider=limited_provider,
                cli_id=_phase_cli_id(
                    card, phase=resolve_phase(card),
                    known_clis=_known_cli_ids(),
                ),
                column=card.column,
            )
        effective_scheduled_at = None if spillover else scheduled_at

        moved = await _move_to_resume(
            ks, card=card, project_key=project_key, project_path=project_path,
            scheduled_at=effective_scheduled_at,
        )
        if moved:
            # Surface the move on the card's activity feed so an operator
            # looking at the board later sees *why* it landed here without
            # having to dive into dispatch.py logs. The reset time is the
            # Notification hook's parsed value when known; the `~5h fallback`
            # branch covers reaper-style callers that couldn't parse one.
            if spillover:
                text = (
                    f"🔀 Rate-limit hit on '{limited_provider}' — spilling over "
                    f"to the next subscription in the pool. Session {session_name} "
                    f"moved to To Resume and is immediately re-dispatchable."
                )
            elif scheduled_at:
                text = (
                    f"🚦 Rate-limit hit — session {session_name} moved to To "
                    f"Resume. Auto-resume scheduled at {scheduled_at}."
                )
            else:
                text = (
                    f"🚦 Rate-limit hit — session {session_name} moved to To "
                    f"Resume. Auto-resume in ~5h (fallback — couldn't parse "
                    f"reset time)."
                )
            await _post_rate_limit_activity_comment(
                ks, card=card, project_key=project_key, text=text,
            )
            await ks.commit()
        return moved


async def clear_dispatch_pause(session) -> tuple[bool, bool]:
    """Manually clear the global dispatch pause (operator override).

    For when the 429 auto-detection got it wrong -- garbled limit-message
    parsing, a missed Notification hook, or a provider whose limit actually
    reset before the parsed deadline. Posts an audit comment on every "To
    Resume" card (across all projects -- the pause itself is account-wide,
    not project-scoped, per dispatch_pause.py) so a later "why did dispatch
    start again" question has an answer on the card, not just in the log.

    Returns (cleared, was_paused). Idempotent: calling this when nothing is
    paused returns (False, False) without raising. Caller is responsible for
    committing the session.
    """
    from sqlalchemy import select

    from app.kanban import dispatch_pause

    was_paused = await dispatch_pause.is_dispatch_paused(session)
    if not was_paused:
        return False, False

    await dispatch_pause.set_paused_until(session, None)

    to_resume = (
        await session.execute(select(KanbanCard).where(KanbanCard.column == "To Resume"))
    ).scalars().all()
    for card in to_resume:
        await apply_operation(
            session, op_type="comment", entity_type="comment",
            project_key=card.project_key, entity_id=card.id,
            payload={"text": "Auto-dispatch pause cleared manually by an operator "
                              "(overriding the auto-detected usage-limit reset time)."},
        )
    logger.info(
        "dispatch pause manually cleared (to_resume_cards=%d)", len(to_resume),
    )
    return True, True


async def post_agent_status_comment(cwd: str, text: str) -> bool:
    """Post a comment to the kanban card owned by the session running in `cwd`.

    Used by the Notification hook for the new ``agent_needs_input`` /
    ``agent_completed`` background-agent subtypes (Claude Code 2.1.198+, Jul
    2026). The card is NOT moved: the explicit human/engineer move to Done
    stays authoritative — matching the rate-limit design where
    ``move_limited_session_to_resume`` is the only auto-move and only on a
    real rate-limit hit. Surfacing the event as an activity comment lets the
    operator see "this card's agent is waiting" / "this card's agent
    finished" without changing the dispatch state machine.

    Resolves the card the same way as ``move_limited_session_to_resume``
    (project via ``_resume_target_from_cwd`` → project_key, then the card
    claimed by ``agent:<session_name>``). Returns False for non-kanban
    sessions (cwd outside ``.claude/worktrees/``, unknown project_key, or
    no matching claim) so the hook path stays a no-op for hand-started
    sessions.
    """
    from app.kanban.db import KanbanSessionLocal

    target = _resume_target_from_cwd(cwd)
    if target is None:
        return False
    project_path, session_name = target
    project_key = safe_resolve_project_key(project_path)
    if project_key is None:
        return False

    claimant = CLAIMANT_PREFIX + session_name
    async with KanbanSessionLocal() as ks:
        cards = await list_cards(ks, project_key)
        # Restrict to active columns (anything that isn't a fixed end-state).
        # Posting "session reported completion" to a card already on Done
        # would be noise; same for a card the operator has already moved to
        # To Resume.
        card = next(
            (c for c in cards
             if c.column not in ("Done", "To Resume")
             and c.claimed_by == claimant),
            None,
        )
        if card is None:
            return False
        await apply_operation(
            ks, op_type="comment", entity_type="comment",
            project_key=project_key, entity_id=card.id, payload={"text": text},
        )
        await ks.commit()
        logger.info(
            "posted agent-status comment to card %s (session %s): %s",
            card.id, session_name, text,
        )
        return True


async def _post_rate_limit_activity_comment(
    session, *, card, project_key: str, text: str,
) -> bool:
    """Post a short audit comment explaining why a card landed on "To Resume".

    Used by the three rate-limit lifecycle paths so a later "why is this card
    parked?" reader can answer it from the activity feed in one second, rather
    than diving into dispatch.py logs:

      - ``move_limited_session_to_resume`` (Notification hook path, has parsed
        reset time)
      - ``_cleanup_stuck_session`` (reaper path, sees only tmux pane content)
      - ``_run_card`` (auto-dispatch tick pickup of a due
        scheduled_at card)

    The caller owns the session/transaction (some callsites embed this in a
    larger apply chain); we commit so the comment lands alongside the move /
    release op even if the caller forgets — idempotent for sites that
    commit afterwards. Returns True on success; never raises. Truncates
    nothing: callers are responsible for keeping text under 200 chars.
    """
    try:
        await apply_operation(
            session, op_type="comment", entity_type="comment",
            project_key=project_key, entity_id=card.id, payload={"text": text},
        )
        await session.commit()
        logger.info(
            "posted rate-limit activity comment to card %s: %s",
            card.id, text,
        )
        return True
    except Exception:
        logger.exception(
            "failed to post rate-limit activity comment to card %s", card.id,
        )
        return False


async def check_progress_liveness(
    session, *, project_key: str, cards: Iterable[KanbanCard],
    project_path: str | None = None,
    live_sessions: set[str] | None = None,
    sandcastle_live: set[str] | None = None,
    headless_live: set[str] | None = None,
    now: float | None = None,
    signal_seconds: int | None = None,
    action_seconds: int | None = None,
) -> set[str]:
    """Signal/release cards whose agent session has stopped making transcript
    progress (kaart f0953a11…).

    A second liveness source sitting alongside ``reap_stale_claims``'s
    pane-exists check. The pane check alone misses a session that hit its
    subscription limit, crashed in a loop, parked on a permission prompt, or
    is hung on a network timeout — the pane (and the claim) stays alive
    forever. The transcript is a better signal: a productive Claude session
    writes to its transcript every few seconds, so sustained silence is
    always wrong.

    For each ``agent:``-claimed card whose session is *not* in
    ``live_sessions`` / ``sandcastle_live`` / ``headless_live`` (those
    transports own their own liveness — never override them) and whose
    transcript is resolvable:

      - First observation: record the snapshot but do nothing — we don't
        know whether the agent is productive yet, so the first tick always
        has ``stalled_seconds == 0``.
      - mtime has advanced since the last observation: update the snapshot
        and clear the signal-posted flag. A productive session resets both
        counters every tick.
      - mtime unchanged and ``now - last_observation_time`` ≥ ``signal_seconds``
        and < ``action_seconds``: post a one-shot "stilstaand" activity
        comment so the stall is visible on the card (re-using
        ``_post_rate_limit_activity_comment``, the shared audit-comment
        helper). Subsequent ticks within the same stall window do NOT
        re-post — only the fresh stall after the next growth window re-arms.
      - mtime unchanged and ``now - last_observation_time`` ≥ ``action_seconds``:
        move the card to ``To Resume`` via the existing ``_move_to_resume``
        path (which kills the tmux session, releases the claim, and persists
        the resume pointer so the next dispatch tick resumes the conversation
        in place).

    Failure-open semantics: any IO/permission problem resolving the transcript
    (missing worktree, no jsonl, stat error) is treated as "we don't know"
    and the card is left alone. A false-positive signal here costs the user
    their session; a missing signal costs at most the next dispatch tick.

    ``now``, ``signal_seconds``, and ``action_seconds`` are injectable for
    deterministic tests; production callers pass none and read the module
    defaults ``PROGRESS_LIVENESS_SIGNAL_SECONDS`` / ``PROGRESS_LIVENESS_ACTION_SECONDS``.

    Returns the set of ``session_name``s that had any action this tick
    (signal comment posted, or claim released). Empty when nothing crossed
    a threshold — the caller uses it as a truthy "did anything happen" gate
    to decide whether to refetch the cards list.
    """
    from app.kanban.schemas import COLUMNS
    from app.kanban.session_recovery import _resolve_transcript_file

    if project_path is None:
        return set()
    if now is None:
        now = time.time()
    if signal_seconds is None:
        signal_seconds = PROGRESS_LIVENESS_SIGNAL_SECONDS
    if action_seconds is None:
        action_seconds = PROGRESS_LIVENESS_ACTION_SECONDS

    # Snapshot to a list — same defence-in-depth as `reap_stale_claims`:
    # `cards` is documented as Iterable, and a future refactor that turns it
    # into a streaming generator would otherwise be silently consumed.
    cards = list(cards)

    # Prune state for cards that no longer have an `agent:` claim (released,
    # moved to Done, operator redispatched under a fresh worktree, ...). Done
    # at function entry so the dict can't grow unbounded across long-running
    # backends. Scoped to cards iterated THIS tick — sessions on other
    # projects are handled by their own dispatch_project call.
    active_card_ids = {c.id for c in cards}
    for stale_id in [cid for cid in _progress_liveness_state if cid not in active_card_ids]:
        _progress_liveness_state.pop(stale_id, None)

    acted: set[str] = set()

    for card in cards:
        if card.column in COLUMNS:
            continue
        name = _claimant_session(card)
        if name is None:
            continue
        if name in live_sessions or name in sandcastle_live or name in headless_live:
            # Those transports own liveness — see reap_stale_claims for the
            # same carve-out. Skipping is correct even if the transcript
            # happens to exist for a sandcastle/headless session (it won't
            # normally — different cwd — but the carve-out is on the
            # transport, not on the transcript).
            continue

        transcript = _resolve_transcript_file(project_path, name)
        if transcript is None:
            # Fail-open: no resolvable transcript = no signal. Either the
            # worktree was GC'd, the session never wrote anything, or the
            # projects-dir layout is wrong — in all cases acting on missing
            # data is worse than not acting.
            continue

        try:
            current_mtime = transcript.stat().st_mtime
        except OSError:
            # File vanished between resolve and stat (worktree GC, race).
            # Same fail-open reasoning.
            continue

        snapshot = _progress_liveness_state.get(card.id)
        if snapshot is None:
            # First observation — record the baseline (our observation time
            # + the mtime we observed it at) but don't act yet. A signal at
            # the very first tick would be a "stalled since spawn" assertion
            # with no information about whether the agent was ever productive.
            _progress_liveness_state[card.id] = _ProgressSnapshot(
                observation_time=now, observed_mtime=current_mtime,
            )
            continue

        if current_mtime > snapshot.observed_mtime:
            # Transcript grew — reset the snapshot and clear the one-shot
            # signal flag. The new observation time becomes the stall
            # baseline for any future silence.
            _progress_liveness_state[card.id] = _ProgressSnapshot(
                observation_time=now, observed_mtime=current_mtime,
            )
            continue

        # current_mtime <= snapshot.observed_mtime: no growth since the last
        # observation. Stall is measured against our own observation cadence
        # so a session that wrote once at spawn and then went silent doesn't
        # get pre-charged with the time-since-spawn.
        stalled_seconds = now - snapshot.observation_time

        if stalled_seconds >= action_seconds:
            logger.warning(
                "progress-liveness: card %s session %s stalled ~%.0fs (action "
                "threshold %.0fs) — moving to To Resume via _move_to_resume",
                card.id, name, stalled_seconds, action_seconds,
            )
            moved = await _move_to_resume(
                session, card=card, project_key=project_key,
                project_path=project_path,
            )
            if not moved:
                # _move_to_resume refuses on Done / To Resume / fixed columns
                # (already enforced above by the COLUMNS check) or when the
                # worktree no longer has a resolvable transcript — shouldn't
                # happen since we just resolved it, but fall back to a plain
                # release rather than leaving the card claimed forever.
                await _release_dead_claim(
                    session, card=card, project_key=project_key,
                    session_name=name,
                )
            # Drop state for this card — claim is gone, no further observation
            # is possible until a new claim attaches.
            _progress_liveness_state.pop(card.id, None)
            acted.add(name)
            continue

        if stalled_seconds >= signal_seconds:
            if snapshot.signal_posted:
                # Already posted for this stall window — don't spam.
                continue
            _progress_liveness_state[card.id] = _ProgressSnapshot(
                observation_time=snapshot.observation_time,
                observed_mtime=snapshot.observed_mtime,
                signal_posted=True,
            )
            minutes = int(stalled_seconds // 60)
            text = _STILLESTAAND_COMMENT_TEMPLATE.format(
                minutes=minutes, name=name,
                action_minutes=action_seconds // 60,
            )
            posted = await _post_rate_limit_activity_comment(
                session, card=card, project_key=project_key, text=text,
            )
            if posted:
                logger.info(
                    "progress-liveness: card %s session %s stalled ~%.0fs — "
                    "posted stilstaand comment (signal threshold %.0fs)",
                    card.id, name, stalled_seconds, signal_seconds,
                )
                acted.add(name)

    return acted


async def reap_stale_claims(
    session, *, project_key: str, cards: Iterable[KanbanCard], live_sessions: set[str],
    sandcastle_live: set[str] | None = None,
    headless_live: set[str] | None = None,
    project_path: str | None = None,
) -> int:
    """Release `agent:` claims on cards in agent columns whose session is gone.

    A dispatched session that dies (crash, manual close, reboot) without moving its
    card out of an agent column would otherwise keep `_project_is_busy` True forever, silently
    wedging auto-dispatch for the whole project — the "auto-pick stopped working"
    symptom. Releasing the dead claim frees the per-project cap so the next card is
    picked up; the orphaned card is left in its agent column (now unclaimed), where
    `_next_card`'s orphan fallback will pick it back up itself once there's a free
    cap slot and nothing waiting in Backlog/To Resume — no human redispatch needed.
    Human (`me@ui`) claims are never touched. Returns the number reaped.

    Liveness has three sources: `live_sessions` (tmux session names, for
    worktree-transport cards), `sandcastle_live` (session names of
    pending/running sandcastle runs), and `headless_live` (session names of
    running headless stream-json subprocesses). Sandcastle cards have no tmux
    session, so without the second source every sandcastle card would be
    reaped on the very next tick and re-dispatched in a loop. Headless cards
    have neither tmux nor a SandcastleRun row — only the third source keeps
    them alive. Same dispatch-loop precedent as sandcastle; see
    ``docs/cockpit/headless-stream-json-transport-spike.md`` §5.

    When ``project_path`` is provided, dead sessions with a resumable transcript in
    their worktree are moved to the "To Resume" column (via ``_move_to_resume``)
    instead of being just released. Cards without a resumable worktree fall back to
    the plain release as before.

    Stuck sessions — alive in tmux but never sent any hook event past
    STUCK_SESSION_TIMEOUT_S — get a separate scan first. A pane that matches
    a 429 / Token Plan pattern is treated as a rate-limit hit and cleaned up
    via `_cleanup_stuck_session` (sets the global dispatch pause, kills tmux,
    bumps dispatch_failures, clears any stale resume pointer, releases the
    claim). A stuck session whose pane shows ordinary progress is left alone
    — it's just slow to initialise hooks, not actually broken."""
    from app.kanban.schemas import COLUMNS

    sandcastle_live = sandcastle_live or set()
    headless_live = headless_live or set()
    reaped = 0

    # Pre-compute the stuck set once per tick: get_stuck_sessions walks the
    # registry's spawn map and filters by the live set. Doing it per-card
    # would be wasteful, but more importantly it would race a kill the
    # cleanup path performs (registry.clear_spawn) — keeping a snapshot
    # here means the same name can't be re-classified mid-loop.
    stuck_names = session_registry.get_stuck_sessions(
        live_sessions, timeout_s=STUCK_SESSION_TIMEOUT_S,
    )

    for card in cards:
        if card.column in COLUMNS:  # Skip fixed columns (Backlog, Impediment, Done, To Resume)
            continue
        name = _claimant_session(card)
        if name is None:
            continue

        # New path: a session that's alive in tmux but never sent hooks is
        # the signature of a 429 on first invocation. Prefer the typed
        # Notification-classification signal when one is available (recorded
        # by the hook endpoint from a previous Notification event — survives
        # the pane being cleared, doesn't need to re-parse raw CC output);
        # fall back to a pane-content scan only when no structured signal
        # exists yet (the classic rate-limited-on-first-spawn case where
        # the `claude` process died before initialising hooks). If the pane
        # shows ordinary work or we couldn't capture it, do nothing — the
        # session is just slow, and falling through to the alive-skip
        # branch keeps the existing reaper semantics.
        #
        # The pane substring-scan only fires when no usable transcript
        # exists yet — once the session has produced one (i.e. it's been
        # productive for long enough that `_read_transcript_tail_entries`
        # would have seen the limit), the transcript-tail detector
        # (`detect_transcript_rate_limits`) is the authoritative source.
        # Without this guard an agent whose history contains a benign
        # '429' / 'api error' substring from a curl probe gets killed by a
        # false positive — that's the 2026-07-22 incident that drove card
        # 3a8f27a4… (logs/backend/run-20260722-095619-2861-0.log:947).
        if name in stuck_names:
            if _structured_rate_limit_signal(name):
                # Use the canonical CC notification message when the typed
                # signal carries one; fall back to the pane snippet only
                # when the signal was recorded without a message (older
                # callers may pass "") so the activity comment always has
                # *something* concrete to quote.
                from app.services.scheduling.session_signals import session_signals
                limit_msg = session_signals.limit_message(name) or ""
                pane = _capture_pane_content(name) or limit_msg
                await _cleanup_stuck_session(
                    session, card=card, project_key=project_key,
                    session_name=name, pane_content=pane,
                    project_path=project_path,
                )
                reaped += 1
                continue
            pane = _capture_pane_content(name)
            if pane is not None and _is_rate_limited_session(pane):
                # Pre-transcript-only contract: only run the pane scan if
                # there is no transcript to take precedence. If a
                # transcript exists (and has content) the transcript-tail
                # detector owns mid-session limit handling; running the
                # pane scan on top of it is the false-positive path.
                if _session_has_transcript(project_path, name):
                    # Drop through to the alive-skip branch — the session
                    # is just slow (or its limit, if any, will be caught
                    # by `detect_transcript_rate_limits` on the next tick).
                    pass
                else:
                    needle = _find_rate_limit_match(pane) or "?"
                    await _cleanup_stuck_session(
                        session, card=card, project_key=project_key,
                        session_name=name, pane_content=pane,
                        matched_needle=needle,
                        project_path=project_path,
                    )
                    reaped += 1
                    continue

        if name in live_sessions or name in sandcastle_live or name in headless_live:
            continue

        # If we know the project path, try resume recovery first
        if project_path is not None:
            # The reaper only sees tmux pane content, never a parsed usage-limit
            # reset time -- fall back to a conservative fixed pause (same
            # duration as _cleanup_stuck_session's global pause) so the card
            # doesn't get immediately re-picked up by the next dispatch tick
            # while the rate limit is still in effect.
            from datetime import timedelta

            from app.services.scheduling.auto_resume import FALLBACK_PAUSE_HOURS
            fallback_scheduled_at = (
                datetime.now(UTC) + timedelta(hours=FALLBACK_PAUSE_HOURS)
            ).isoformat()
            if await _move_to_resume(
                session, card=card, project_key=project_key,
                project_path=project_path, scheduled_at=fallback_scheduled_at,
            ):
                reaped += 1
                continue

        # Fallback: plain release for non-resumable dead sessions
        await _release_dead_claim(session, card=card, project_key=project_key, session_name=name)
        reaped += 1
    return reaped


def _claim_age_seconds(card) -> float | None:
    claimed_at = card.claimed_at
    if claimed_at is None:
        return None
    return (datetime.now(UTC) - ensure_aware(claimed_at)).total_seconds()


async def _clear_stale_resume_fields(session, *, card, project_key: str) -> None:
    """Clear a `resume_session_id`/`resume_project_folder` pointer that just failed.

    That pointer can only have survived from an *earlier* dispatch (this attempt's
    own resolution already failed, or it wouldn't be here) — usually because the
    worktree it pointed at was since merged and garbage-collected. Left in place,
    `get_transport_for_card` gives it priority forever, so every future tick retries
    the exact same broken `--resume <id>` and fails again — permanently wedging the
    card (this was the actual cause of the "cards starting with 'Create' never
    start" report: dead-on-arrival tmux sessions AND synchronous spawn failures both
    stem from this, via two different code paths — see _release_dead_claim and
    _run_card's except block).
    """
    if not (card.resume_session_id or card.resume_project_folder):
        return
    await apply_operation(
        session, op_type="update", entity_type="card",
        project_key=project_key, entity_id=card.id,
        payload={"resume_session_id": None, "resume_project_folder": None},
    )


async def _bump_dispatch_failures(session, *, card, project_key: str) -> int:
    """Increment card.dispatch_failures by one and return the new count.

    Shared by both dispatch-failure paths: a session that dies within seconds in
    tmux (see _release_dead_claim) and a synchronous spawn exception before any
    session existed (see _run_card). Both mean the dispatch *target* is broken, not
    the task itself.
    """
    failures = (card.dispatch_failures or 0) + 1
    await apply_operation(
        session, op_type="update", entity_type="card",
        project_key=project_key, entity_id=card.id,
        payload={"dispatch_failures": failures},
    )
    return failures


async def _reset_dispatch_failures(session, *, card, project_key: str) -> None:
    if card.dispatch_failures:
        await apply_operation(
            session, op_type="update", entity_type="card",
            project_key=project_key, entity_id=card.id,
            payload={"dispatch_failures": 0},
        )


async def _move_to_impediment_after_repeated_failures(
    session, *, card, project_key: str, session_name: str, failures: int,
    last_error: str | None = None,
) -> None:
    """Once a card hits MAX_DISPATCH_FAILURES with no successful run in between,
    move it to Impediment instead of retrying again — a card that can never
    actually start (bad transport config, missing sandcastle setup, a stale
    --resume worktree, ...) needs a human, not an infinite retry loop burning
    dispatch ticks.

    ``last_error`` (optional) is the most recent spawn error, when known —
    typically ``str(exc)`` from the synchronous spawn exception in ``_run_card``
    (the ``tmux new-session failed: command too long`` case that motivated
    kanban card 5ec5a68013da4422b0a49fb2731cb8a7), or captured tmux pane
    content for the rate-limit path in ``_cleanup_stuck_session``. Truncated
    to a single line and 300 chars so the activity-feed comment stays
    readable without scrolling — mirrors the pane-snippet pattern in
    ``_cleanup_stuck_session``. When ``last_error`` is None (e.g. the
    dead-on-arrival reaper path, where the session was already gone before
    we could capture anything), the comment falls back to the legacy
    "Check the backend logs" hint so operators still know where to look.

    Tags the card with the ``error`` label so the board can visually distinguish
    a *technical* dispatch failure (rendered red in the UI) from a card that a
    human deliberately parked in Impediment for a decision. The comment itself
    carries a structured ``[dispatch-failure]`` prefix so the per-card
    classification in `service.impediment_status_for_card` can detect this
    state deterministically (a substring on prose would be fragile)."""
    if last_error:
        # Collapse to a single line and cap at 300 chars — mirrors the
        # pane-snippet pattern in `_cleanup_stuck_session` (line ~1689).
        # 300 chars is large enough for a ValueError + message but small
        # enough to keep the comment from dominating the activity feed;
        # a runaway traceback still produces a single readable line.
        sanitized = last_error.replace("\r", " ").replace("\n", " ")
        if len(sanitized) > 300:
            sanitized = sanitized[:297] + "..."
        error_clause = f" Last spawn error: `{sanitized}`."
        log_hint = ""
    else:
        error_clause = ""
        log_hint = " Check the backend logs for the actual spawn error."

    await apply_operation(
        session, op_type="comment", entity_type="comment",
        project_key=project_key, entity_id=card.id,
        payload={"text": (
            f"[dispatch-failure] Session `{session_name}` failed to dispatch "
            f"{failures} times in a row — moved to Impediment instead of "
            f"retrying again.{error_clause} This usually means the dispatch "
            f"target is broken (a stale --resume worktree, a missing "
            f"sandcastle config, ...) rather than the task itself failing."
            f"{log_hint} Fix the underlying issue, then redispatch."
        )},
    )
    labels = list(card.labels or [])
    if ERROR_LABEL not in labels:
        labels.append(ERROR_LABEL)
        await apply_operation(
            session, op_type="update", entity_type="card",
            project_key=project_key, entity_id=card.id, payload={"labels": labels},
        )
    await apply_operation(
        session, op_type="move", entity_type="card",
        project_key=project_key, entity_id=card.id, payload={"column": "Impediment"},
    )
    await _reset_dispatch_failures(session, card=card, project_key=project_key)
    logger.warning(
        "card %s failed to dispatch %d times in a row (session %s) -> moved to Impediment",
        card.id, failures, session_name,
    )


async def _flag_dangling_dep_card(
    session, *, card, project_key: str, dangling: list[str],
) -> None:
    """Move a card whose `depends_on` names non-existent card ids to Impediment
    with an actionable comment, instead of letting the fail-closed dep-resolver
    hold it silently and permanently.

    The dep-resolver (`meets_dep_prerequisites`) fails *closed* on a dep that
    resolves to no live card: a deleted parent is indistinguishable from "not
    Done yet", so the card is never dispatchable and the board shows no reason.
    That is exactly the trap documented in
    docs/cockpit/dangling-depends-on-analyse.md §1.1. The delete-guard
    (`service.strip_dangling_deps_on_delete`) prevents *new* danglings on the
    delete path, but cross-project deletes and manual DB edits still leak them
    in — and the offline sweeper only flags them advisory. This is the runtime
    seam that makes the block loud: a red Impediment card a human can fix by
    recreating the parent or stripping the dep, rather than a silent hold.

    Tags ERROR_LABEL (red on the board) and writes a documentary
    `**Dangling dependency:** ` comment — the fix is an operator board-action,
    not a resume/redispatch, so the prefix is deliberately kept out of the
    question/answer/dispatch-failure classifier space."""
    ids = ", ".join(f"`{d}`" for d in dangling)
    await apply_operation(
        session, op_type="comment", entity_type="comment",
        project_key=project_key, entity_id=card.id,
        payload={"text": (
            f"{DANGLING_DEP_COMMENT_PREFIX}deze kaart hangt via `depends_on` af "
            f"van {ids}, maar die kaart(en) bestaan niet meer op het bord "
            f"(verwijderd of nooit aangemaakt). De dep-resolver faalt daardoor "
            f"*closed* — de kaart werd stil en permanent vastgehouden door "
            f"auto-dispatch. Verplaatst naar Impediment zodat de blokkade "
            f"zichtbaar is. Herstel: verwijder de dangling id('s) uit "
            f"`depends_on` als het bedoelde werk al af is, of maak de "
            f"ontbrekende parent-kaart opnieuw aan; daarna kan de kaart terug "
            f"naar Backlog. Achtergrond: "
            f"docs/cockpit/dangling-depends-on-analyse.md §1.1/§4."
        )},
    )
    labels = list(card.labels or [])
    if ERROR_LABEL not in labels:
        labels.append(ERROR_LABEL)
        await apply_operation(
            session, op_type="update", entity_type="card",
            project_key=project_key, entity_id=card.id, payload={"labels": labels},
        )
    await apply_operation(
        session, op_type="move", entity_type="card",
        project_key=project_key, entity_id=card.id, payload={"column": "Impediment"},
    )
    logger.warning(
        "card %s has dangling depends_on %s -> moved to Impediment "
        "(dep ids resolve to no live card)",
        card.id, dangling,
    )


async def _release_dead_claim(session, *, card, project_key: str, session_name: str) -> None:
    """Release a claim whose session is gone, with no resumable transcript.

    Gated on the claim having died within DEAD_ON_ARRIVAL_SECONDS of being claimed:
    a session that ran for a while first proved the dispatch target works, so a
    later crash is treated as a normal one-off — just release it and clear any
    prior failure streak. A session dead within that window counts toward
    MAX_DISPATCH_FAILURES (see _bump_dispatch_failures /
    _move_to_impediment_after_repeated_failures) and has its resume pointer
    cleared (see _clear_stale_resume_fields) so a stale one can't be retried
    forever.

    The not-dead-on-arrival branch preserves an operator-stamped
    `resume_session_id` (the one `mcp_server.set_resume` writes): a long-
    running session that died cleanly is exactly the case the operator is
    most likely trying to resume, and stripping the pointer here means the
    next dispatch uses the worktree transport — the very bug this method
    used to enable. See kanban card `[self-improve] set_resume races a
    fresh auto-dispatch`. The dead-on-arrival branch keeps clearing (that
    pointer is from this dispatch's own resolution and is the loop-prone
    case `_clear_stale_resume_fields` was designed for).
    """
    # Re-read the card from the DB before deciding whether to clear the
    # resume pointer. The `card` argument was loaded from the dispatch
    # tick's cached `cards` list; a `set_resume` MCP call that commits
    # after that read would be invisible to the stale snapshot, leading us
    # to strip an operator-stamped resume_session_id and defeat the very
    # fix that landed with this guard. The re-read is a single SELECT keyed
    # by primary key; only happens once per dead-claim release, not per
    # dispatch tick.
    fresh = await get_card(session, card.id)
    if fresh is not None:
        card = fresh

    age = _claim_age_seconds(card)
    dead_on_arrival = age is None or age < DEAD_ON_ARRIVAL_SECONDS

    if not dead_on_arrival:
        await _reset_dispatch_failures(session, card=card, project_key=project_key)
        # Preserve an operator-stamped resume_session_id; only clear when
        # the dead-on-arrival path below decides it's stale.
        if not card.resume_session_id:
            await _clear_stale_resume_fields(session, card=card, project_key=project_key)
        logger.info(
            "reaped stale claim on card %s (dead session %s, ran ~%.0fs — not dead-on-arrival)",
            card.id, session_name, age,
        )
    else:
        await _clear_stale_resume_fields(session, card=card, project_key=project_key)
        failures = await _bump_dispatch_failures(session, card=card, project_key=project_key)
        if failures >= MAX_DISPATCH_FAILURES:
            await _move_to_impediment_after_repeated_failures(
                session, card=card, project_key=project_key,
                session_name=session_name, failures=failures,
            )
        else:
            logger.info(
                "reaped stale claim on card %s (dead session %s, %d/%d consecutive failures)",
                card.id, session_name, failures, MAX_DISPATCH_FAILURES,
            )

    await apply_operation(
        session, op_type="release", entity_type="card",
        project_key=project_key, entity_id=card.id, payload={},
    )


async def dispatch_project(
    session, *, project_key: str, project_path: str, transport: SpawnTransport | None = None,
    live_sessions: set[str] | None = None,
    sandcastle_live: set[str] | None = None,
    headless_live: set[str] | None = None,
) -> dict | None:
    """Claim+move+spawn the next card for one project. Returns a result dict or
    None when there is nothing to do (no candidate card, or the per-column cap
    blocks dispatch for every candidate).

    When `live_sessions` is provided, stale `agent:` claims on Doing cards whose
    session is no longer alive are reaped first, so a dead session can never wedge
    a per-column cap slot. Passing None skips reaping (used by unit tests that
    exercise the cap directly).

    If transport is None, the appropriate transport is automatically selected based
    on the project's sandcastle configuration.

    **Provider routing:** any "what subscription would this card land on" decision
    in this path MUST go through
    ``resolve_effective_provider_and_model`` (the canonical 5-layer resolver —
    see its docstring) — including the manual-pause gate below, which routes
    through ``_card_is_manually_paused`` →
    ``_effective_provider_for_pause_gate``. Ad-hoc re-walks of even a subset
    of the chain slipped the original pause-gate through with the wrong
    provider (kaart f056b2888a…, fix commit ``77f5c8c``); the self-check
    ``scripts/check-dispatch-resolver-usage.sh`` flags any new such walk."""
    cards = await list_cards(session, project_key)
    if live_sessions is not None:
        if await reap_stale_claims(
            session, project_key=project_key, cards=cards, live_sessions=live_sessions,
            sandcastle_live=sandcastle_live, headless_live=headless_live,
            project_path=project_path,
        ):
            cards = await list_cards(session, project_key)

    # Transcript-tail sweep for mid-session rate limits: runs independently of
    # tmux liveness, so it catches the sessions reap_stale_claims's live/stuck
    # branching structurally cannot (see detect_transcript_rate_limits
    # docstring). Needs project_path to resolve a worktree's transcript file.
    if project_path is not None:
        if await detect_transcript_rate_limits(cards=cards, project_path=project_path):
            cards = await list_cards(session, project_key)

    # Progress-liveness sweep: second liveness source for cards whose tmux
    # pane still exists but whose transcript has stopped growing (kaart
    # f0953a11…). Runs independently of tmux liveness for the same reason as
    # the rate-limit sweep above — it must catch sessions whose pane is
    # alive but the agent itself is stuck (limit hit, crash loop, permission
    # prompt, network timeout). Idempotent with reap_stale_claims: a session
    # whose tmux pane is already gone was handled by the reaper above; this
    # sweep handles the inverse case (pane alive, agent not). Needs
    # project_path to resolve a worktree's transcript file.
    if project_path is not None and live_sessions is not None:
        if await check_progress_liveness(
            session, project_key=project_key, cards=cards,
            project_path=project_path,
            live_sessions=live_sessions,
            sandcastle_live=sandcastle_live,
            headless_live=headless_live,
        ):
            cards = await list_cards(session, project_key)

    column_caps = await _column_max_sessions(session, project_key)
    last_result: dict | None = None

    # Board-wide existence oracle, fetched once per tick: lets the dep gate
    # below tell a *dangling* depends_on (id resolves to no card anywhere) apart
    # from a healthy not-yet-Done dep, so the former is surfaced to Impediment
    # instead of being silently held forever. Board-wide (not the project-scoped
    # working set) so a cross-project dep is never mistaken for dangling.
    board_ids = await all_card_ids(session)

    # Record *why* every non-dispatchable card is sitting still, before we pick
    # one. This is the tick's only chance to say so: after the filters run, a
    # held card is indistinguishable from one that was never a candidate.
    await _persist_holds(session, cards, board_ids)

    # Fill every dispatchable card in this tick. The per-column cap (when set)
    # is the only structural limit at this level; the hardware/OS-level cap
    # checked inside the transport enforces the actual memory bound.
    while True:
        card = _next_card(cards, board_ids)
        if card is None:
            break

        # Per-column cap: if the candidate card's target column has a
        # per-column max_sessions, check it. The card's current column
        # (Backlog) is not its agent column yet; the target column is
        # resolved from its phase. Analyst phase always goes to "analyst";
        # executor phase uses the agent/persona/column resolution.
        phase = resolve_phase(card)
        target_column = "analyst" if phase == "analyst" else await _resolve_target_column(
            session, card, project_path=project_path, project_key=project_key,
        )
        col_cap = column_caps.get(target_column)
        if col_cap is not None:
            col_counts = _active_session_count_by_column(cards)
            if col_counts.get(target_column, 0) >= col_cap:
                # Column at its per-column cap — skip this card. Remove it from
                # the working set so _next_card doesn't pick it again this tick.
                cards = [c for c in cards if c.id != card.id]
                continue

        # Manual per-subscription pause (kaart f056b2888a…): the operator can
        # toggle a provider off in the toolbar, which must hold back every
        # card that would have spawned against that subscription — without
        # claiming or moving the card, so the resume path picks it up unchanged
        # once the operator toggles it back on. Resolved via the same
        # precedence the spawn actually uses (global override → pool choice →
        # per-card override → column default), so pausing "anthropic" holds
        # back cards the board is actually routing to anthropic — including
        # cards whose pool-choice falls through to anthropic. FCR-blokkade:
        # the previous _provider_for_card helper skipped the global-override
        # and pool layers and could gate against the wrong provider.
        if await _card_is_manually_paused(
            session, project_key=project_key, project_path=project_path,
            card=card, target_column=target_column,
        ):
            card_provider = await _effective_provider_for_pause_gate(
                session, project_key=project_key, project_path=project_path,
                card=card, target_column=target_column,
            )
            logger.info(
                "dispatch_project: skipping card %s (target_column=%s, "
                "provider=%s) — manually paused by operator",
                card.id, target_column, card_provider,
            )
            cards = [c for c in cards if c.id != card.id]
            continue

        # Skip child cards whose parents aren't Done yet — _next_card is rank/
        # priority-aware but doesn't know about depends_on, so the dep filter
        # is the dispatcher's responsibility (see app.kanban.dep_resolver).
        cards_by_id = {c.id: c for c in cards}
        if not meets_dep_prerequisites(card, cards_by_id):
            # Distinguish a *dangling* dep (id resolves to no card anywhere on
            # the board — a permanent, invisible fail-closed block) from a
            # healthy not-yet-Done dep (the depended-on card exists, just isn't
            # Done). Only the former needs human intervention: surface it to
            # Impediment with an actionable comment instead of silently holding
            # it forever. See docs/cockpit/dangling-depends-on-analyse.md §1.1.
            dangling = dangling_dep_ids(card, board_ids)
            if dangling:
                await _flag_dangling_dep_card(
                    session, card=card, project_key=project_key, dangling=dangling,
                )
                cards = await list_cards(session, project_key)
                continue
            # Healthy blocked card (dep exists, not yet Done): mark it as
            # 'skipped this tick' by removing it from the working set so
            # _next_card doesn't pick it again on the next iteration of the same
            # tick — _next_card would otherwise loop on it until the cap is
            # filled or we run out of cards.
            cards = [c for c in cards if c.id != card.id]
            continue

        # Same defence-in-depth for the business-gate path: _next_card already
        # filters gated cards out, but if a card races a `clear metadata` write
        # mid-tick we still want a second line of defence here. The actual gate
        # status is read fresh from the working-set copy in `cards_by_id` above.
        if _is_gated(card):
            cards = [c for c in cards if c.id != card.id]
            continue

        if transport is None:
            transport = await get_transport_for_project(project_path)

        # Revisit injection: a card that was reopened (Done → Backlog with
        # a `**Revisit:**` comment in the activity feed) needs the latest
        # rebuttal threaded into the prompt so the next agent sees it
        # (build_card_prompt renders the `## REVISIT` section only when
        # revisit_question is non-None). Extract here, in the auto-tick,
        # because this is where we have both the session and the project
        # path handy; manual dispatch paths don't pull this in by default.
        # Done cheaply (one short scan of an already-materialised op-log)
        # and only on cards that actually have a Revisit comment, so the
        # hot path is unaffected for ordinary cards.
        revisit_question, revisit_prior_decision = await _resolve_revisit(
            session, card,
        )

        # Impediment context: a card that came in via resolve-impediment lands
        # in Backlog with `**Impediment:**` + `**Resolution:**` comments on its
        # activity feed. Extract both so the spawned session's prompt renders
        # the `## IMPEDIMENT` section with the human's question + authoritative
        # answer. See kaart af951ad70... (resolve-impediment → Backlog, not
        # Doing) and `_resolve_impediment`'s docstring for the contract.
        impediment_question, impediment_answer = await _resolve_impediment(
            session, card,
        )

        # Best-effort resume stamp: when the prior session's worktree +
        # transcript are still on disk, persist `resume_session_id` /
        # `resume_project_folder` so the spawn below picks the resume
        # transport (claude --resume) instead of starting fresh. Failure
        # is silent — analyst cards routinely GC their worktree after
        # Done, so a None fallback is the common path; the dispatch still
        # runs as a fresh session, the agent just rebuilds context from
        # the `## REVISIT` prompt-injected material. Mirrors the same
        # graceful-degradation behaviour as redispatch_card.
        if revisit_question and not card.resume_session_id:
            await _stamp_resume_target(
                session, card=card, project_key=project_key,
                project_path=project_path,
            )

        # Two-phase dispatch: when the card has an analyst_agent_id and no
        # analyst_run_id yet, spawn the analyst first and persist the run id
        # so the next tick doesn't re-spawn. The executor waits for a later
        # tick — _next_card will pick this card again once it has analyst_run_id
        # set but no executor claim, and on that pass we fall through to the
        # executor branch.
        phase = resolve_phase(card)
        if phase == "analyst":
            last_result = await _run_card(
                session, card=card, project_key=project_key,
                project_path=project_path, transport=transport,
                phase="analyst",
                revisit_question=revisit_question,
                revisit_prior_decision=revisit_prior_decision,
                impediment_question=impediment_question,
                impediment_answer=impediment_answer,
                live_sessions=live_sessions,
                auto_dispatch=True,
            )
            if last_result is None:
                break  # dispatch failed (e.g. memory) — let the tick queue/retry
            if "session_name" in last_result:
                # Persist the analyst's run id via the op-log (not a direct
                # ORM setattr) so a future rematerialize() replay doesn't
                # silently drop this field. _run_card returns the session
                # under ``"session_name"`` (see dispatch.py return-shape at
                # the bottom of _run_card), not ``"session"`` — the
                # membership check must match the real key, otherwise this
                # branch is a silent no-op and the dispatcher would re-spawn
                # the analyst every tick until MAX_DISPATCH_FAILURES trips.
                await apply_operation(
                    session, op_type="update", entity_type="card",
                    project_key=project_key, entity_id=card.id,
                    payload={"analyst_run_id": last_result["session_name"]},
                )
            cards = await list_cards(session, project_key)
            continue

        last_result = await _run_card(
            session, card=card, project_key=project_key,
            project_path=project_path, transport=transport,
            phase="executor",
            revisit_question=revisit_question,
            revisit_prior_decision=revisit_prior_decision,
            impediment_question=impediment_question,
            impediment_answer=impediment_answer,
            live_sessions=live_sessions,
            auto_dispatch=True,
        )
        if last_result is None:
            break  # dispatch failed (e.g. memory) — let the tick queue/retry
        cards = await list_cards(session, project_key)

    return last_result


async def dispatch_card(
    session, *, card_id: str, project_path: str,
    transport: SpawnTransport | None = None,
    agent_override: str | None = None,
) -> dict | None:
    """Manually dispatch one specific card now, regardless of the auto-pick toggle or
    the busy cap. Returns the result dict or None if the card is missing or its claim
    was lost. If agent_override is provided, use that agent instead of the card's agent.

    Deliberately does NOT enforce the depends_on gate that ``dispatch_project``
    (the auto-dispatch tick) and the bulk paths ``dispatch_all_pending`` /
    ``redispatch_all_orphans`` enforce. The per-card "Dispatch" / "Redispatch"
    buttons are an explicit human override — the operator who clicked them has
    already seen the Blocked badge, decided the dependency should not block this
    run (e.g. testing a fix, picking up an unrelated piece of work, or working
    with stale ``depends_on`` data) and pressed the button. A silent refusion
    here would just push the operator to bypass the board entirely. If your use
    case needs the gate, use ``dispatch_project`` / ``dispatch_all_pending``
    instead — they are the right entry points."""
    card = await get_card(session, card_id)
    if card is None:
        return None
    # Auto-select transport if not provided
    if transport is None:
        transport = await get_transport_for_project(project_path)
    transport = get_transport_for_card(card, transport)
    # Manual "dispatch" on a multi-agent card must also pick the right phase:
    # a card with analyst_agent_id + no analyst_run_id is in analyst phase,
    # not executor. Otherwise the human override silently drops the
    # analyst step on first dispatch.
    phase = resolve_phase(card)
    return await _run_card(
        session, card=card, project_key=card.project_key,
        project_path=project_path, transport=transport,
        phase=phase,
        agent_override=agent_override,
    )


async def dispatch_impediment_card(
    session, *, card_id: str, project_path: str, target_agent: str,
    impediment_question: str,
    impediment_answer: str | None = None,
    transport: SpawnTransport | None = None,
) -> dict | None:
    """Resolve an impediment by parking the card on **Backlog** for the
    auto-dispatch tick to pick up — *not* by spawning a fresh session
    synchronously.

    Kaart af951ad70... ("Resolve impediment moet niet meteen naar engineer
    kolom maar eerst naar backlog"): when a human resolves an impediment, the
    previous behaviour moved the card straight to the engineer Doing column
    and immediately spawned a session, which flooded the dispatcher when the
    operator cleared a batch of impediments in one go. The fix routes the
    resolved card through the same Backlog queue ordinary cards use, so:

    1. The next auto-tick (``dispatch_project``) picks the card up at its
       own pace, applying the same per-column caps and depends_on gates as
       any other Backlog card.
    2. The human's question + answer reach the spawned session via
       ``_resolve_impediment`` (auto-tick reads the latest `**Impediment:**`
       and `**Resolution:**` comments from the activity feed and threads
       them into ``_run_card``'s ``impediment_question`` / ``impediment_answer``
       kwargs). ``build_card_prompt`` then renders the `## IMPEDIMENT`
       section so the resumed agent sees the human's decision as
       authoritative.

    The legacy arguments ``impediment_question`` / ``impediment_answer`` /
    ``target_agent`` / ``transport`` are kept on the signature (rather than
    removed) so the existing router contract — including its 409-Conflict on
    a None return — keeps working unchanged. They are no longer used to
    drive a synchronous spawn; the auto-tick reads the same information back
    from the activity feed on the next pass.

    Args:
        card_id: The ID of the impediment card
        project_path: Path to the project (kept for backwards compat with the
            router; the auto-tick that actually spawns reads it back from the
            card row / project config).
        target_agent: The agent that should pick up the card (analyst,
            engineer). Kept on the signature for the router contract but no
            longer drives a synchronous spawn.
        impediment_question: The question that needs to be answered. Kept on
            the signature for the router contract; the auto-tick re-reads it
            from the activity feed via ``_resolve_impediment``.
        impediment_answer: A human's answer/decision on the blocker. Same
            shape as ``impediment_question`` — preserved for the router
            contract; the auto-tick re-reads it from the feed.
        transport: The spawn transport to use (kept for backwards compat).

    Returns:
        ``{"session_name": None}`` on success — the resolved card is on
        Backlog and the next auto-tick owns the spawn. ``None`` when the
        card is missing or no longer in Impediment (the router turns this
        into a 409 Conflict).
    """
    card = await get_card(session, card_id)
    if card is None:
        return None

    if card.column != "Impediment":
        logger.warning("Card %s is not in Impediment column, cannot dispatch as impediment", card_id)
        return None

    # Park the card on Backlog so the auto-dispatch tick picks it up at its
    # own pace (per-column cap, depends_on gate, single-card-per-tick loop).
    # Previously this was a hard `column="Doing"` move followed by an
    # immediate `_run_card`, which bypassed every admission-control layer
    # and started a fresh session per resolved card — see the kaart's
    # "te veel sessies naast mekaar" complaint.
    #
    # `claimed_by` is cleared here for agent:* claims only — see below for
    # why this is load-bearing. `report_impediment` already released the
    # claim before parking the card on Impediment (see mcp_server.report_impediment)
    # for the normal path, so the typical input has nothing to clear. The
    # fixed→fixed case (Impediment → Backlog) is the gap the operations.py
    # agent→fixed guard never covers — both source and destination columns
    # are fixed, so the materialize path's guard does not fire. Without an
    # explicit release here, a card claimed by an agent while it sat on
    # Impediment (claim_card MCP against an Impediment card) lands on
    # Backlog still claimed and `_next_card` filters `not c.claimed_by`,
    # silently stranding the card. Kaart acc8f578… documents the incident.
    #
    # Human `me@ui` reservations are deliberately preserved: a deliberate
    # hold (operator clicked Reserve before resolving the Impediment) is an
    # intentional reservation, and stripping it on resolve would surprise
    # the operator who later noticed the card was already in flight. This
    # mirrors the operations.py:327-333 carve-out for non-agent claimants —
    # the guard also leaves `me@ui` claims alone because the stale-claim
    # reaper skips fixed columns on purpose.
    if card.claimed_by and card.claimed_by.startswith(CLAIMANT_PREFIX):
        await apply_operation(
            session, op_type="release", entity_type="card",
            project_key=card.project_key, entity_id=card.id, payload={},
        )
    await apply_operation(
        session, op_type="move", entity_type="card", project_key=card.project_key,
        entity_id=card.id, payload={"column": "Backlog"},
    )

    # Suppress the unused-argument linter: these are part of the router
    # contract and stay on the signature even though the auto-tick owns the
    # actual spawn. Without this assignment the linter would flag the
    # `impediment_*` / `target_agent` / `transport` parameters as unused.
    _ = (target_agent, impediment_question, impediment_answer, transport, project_path)

    return {"session_name": None}


async def redispatch_card(
    session, *, card_id: str, project_path: str,
    transport: SpawnTransport | None = None,
    agent_override: str | None = None,
    caller_source: str = "unspecified",
) -> dict | None:
    """Release a stuck card, optionally kill its session, and re-dispatch.

    Primarily the human override for cards stuck on agent columns — most
    commonly a session that hit its usage limit and never exited, so the
    dead-session reaper never noticed it. But this is also the shared
    re-dispatch path used by *automatic* startup session-recovery
    (`recover_interrupted_sessions`), so its callers range from a deliberate
    operator click to a fully automatic restart-recovery pass — see
    `caller_source`, and never assume "an operator did this". Before killing
    the existing tmux session, we check whether its worktree holds a resumable
    Claude transcript (same lookup the auto-recovery reaper uses). If one is
    found, the card is tagged with `resume_session_id`/`resume_project_folder`
    so the re-dispatch below picks the resume transport (`claude --resume`,
    same worktree) instead of discarding the conversation and starting a brand
    new session.

    Like ``dispatch_card``, deliberately does NOT enforce the depends_on gate
    the bulk paths / auto-dispatch tick use — redispatch from the CardDrawer is
    an explicit human override after the operator has already seen the Blocked
    badge. See ``dispatch_card`` for the full rationale.

    When transport is None, the appropriate transport is automatically selected
    based on the card's transport field (sandcastle/worktree) and the project's
    sandcastle configuration.

    Args:
        card_id: The ID of the card to redispatch
        project_path: Path to the project
        transport: The spawn transport to use (auto-selects based on card if None)
        agent_override: Optional agent to use instead of card's current agent
        caller_source: Free-form label identifying the entry-point that
            triggered this redispatch. Posted as a `**Note:** Redispatched via
            <source>` activity comment so future "who redispatched this?" hunts
            resolve from the activity feed alone. The three known entry-points
            each pass a distinguishing label:

            - ``ui`` — REST handler (``POST /cards/{cid}/redispatch``)
            - ``mcp:<session_id>`` — MCP-tool wrapper (`mcp_server.redispatch_card`)
            - ``bulk_orphans`` — `redispatch_all_orphans`
            - ``recover_interrupted_sessions`` — `session_recovery.recover_project`

            New entry-points SHOULD pick a ``<bucket>`` label and document it
            on this docstring so the activity-feed search space stays curated.
            ``unspecified`` is the back-compat default for in-process callers
            that haven't been retrofitted yet — once an entry-point is
            labelled, the operator can spot leftover anonymous calls by grepping
            for ``unspecified`` in the activity feed.

    Returns:
        Result dict or None if the card was not found
    """
    card = await get_card(session, card_id)
    if card is None:
        return None

    # Visibility — every redispatch must leave a trace in the activity feed so
    # "who redispatched this card?" is answerable without grepping the codebase.
    # The convention-prefix `**Note:**` matches the live-session-kill audit
    # comment just below so the two render as a uniform family.
    await apply_operation(
        session, op_type="comment", entity_type="comment",
        project_key=card.project_key, entity_id=card.id,
        payload={"text": f"**Note:** Redispatched via `{caller_source}`."},
    )

    # Kill existing tmux session if claimed by an agent
    session_name = _claimant_session(card)
    if session_name:
        # Visibility: `_kill_agent_session` below is unconditional — it
        # kills the tmux session even when it's still alive. That is the
        # right behaviour for a deliberate force-restart (operator clicked
        # Redispatch, an MCP-disconnected session asked for a fresh start,
        # etc.), but a kill of a still-productive process is exactly the
        # state change the activity feed must show. Without this comment,
        # the kill is invisible: `release_without_terminal_move` stays at
        # 0 (this path bypasses `release_card_claim` by design — see its
        # docstring for the carve-out), and a redispatch over a live
        # session looks identical to one over a long-dead one. Surface
        # the kill explicitly so an operator reading the activity feed
        # can tell the two cases apart.
        #
        # Liveness check is `name in _live_sessions()` — not an MCP-server
        # state read. The reaper's invariant (see
        # `reap_stale_claims`'s docstring) is that MCP connection state is
        # NOT a liveness source; we honour the same boundary here. The
        # comment text names MCP as the *common* cause for an operator-
        # triggered redispatch over a live session, but the decision to
        # kill is driven by the tmux snapshot alone.
        #
        # Kaart [self-improve] MCP-serverdisconnect → claim-release +
        # her-dispatch terwijl de sessie nog productief is (incident
        # observed on b00f3705…, 2026-07-21T19:17:22, Lemma-analyse).
        live = _live_sessions()
        if live is not None and session_name in live:
            # The audit text must not misattribute the restart. An operator- or
            # MCP-triggered redispatch (`ui` / `mcp:*` / `bulk_orphans`) is a
            # deliberate human/explicit choice. Automatic startup recovery
            # (`recover_interrupted_sessions`) chose nothing: it acts on a tmux
            # snapshot taken moments earlier that considered the session dead,
            # so seeing it live now means that snapshot went stale (a startup
            # race) — not that anyone decided to restart. Naming the wrong actor
            # ("the operator chose to restart") is exactly the documentation
            # defect that reopened kaart 4ed4edb9.
            if caller_source == "recover_interrupted_sessions":
                reason = (
                    "automatic startup session-recovery resumed it in place "
                    "because the recovery snapshot considered it dead; a fresh "
                    "tmux query now shows it live (a startup-race stale "
                    "snapshot). No operator chose to restart."
                )
            else:
                reason = (
                    "an operator or an explicit redispatch call "
                    f"(`{caller_source}`) chose to restart it — a common "
                    "trigger is the session's MCP-server connection briefly "
                    "dropping, which the reaper correctly ignores (MCP state "
                    "is not a liveness source)."
                )
            await apply_operation(
                session, op_type="comment", entity_type="comment",
                project_key=card.project_key, entity_id=card.id,
                payload={
                    "text": (
                        f"**Note:** Redispatching over live session "
                        f"`{session_name}` — the tmux session was still alive "
                        f"when redispatch was invoked: {reason} The kill below "
                        f"is intentional."
                    ),
                },
            )
        if not getattr(card, "resume_session_id", None) and card.transport != "sandcastle":
            from app.kanban.session_recovery import _resolve_resume_target

            target = _resolve_resume_target(project_path, session_name)
            if target is not None:
                resume_session_id, resume_project_folder = target
                await apply_operation(
                    session, op_type="update", entity_type="card",
                    project_key=card.project_key, entity_id=card.id,
                    payload={"resume_session_id": resume_session_id,
                             "resume_project_folder": resume_project_folder},
                )
                logger.info(
                    "redispatch: resuming card %s (session %s -> %s) instead of a fresh session",
                    card_id, session_name, resume_session_id,
                )
        _kill_agent_session(session_name)
        logger.info("killed old session %s for card %s", session_name, card_id)

    # Release the claim
    if card.claimed_by:
        await apply_operation(
            session, op_type="release", entity_type="card",
            project_key=card.project_key, entity_id=card.id, payload={},
        )

    # Auto-select transport if not provided
    if transport is None:
        transport = await get_transport_for_project(project_path)
    transport = get_transport_for_card(card, transport)

    # Pick the right phase: a multi-agent card with no analyst_run_id yet
    # must be re-spawned as the analyst (spec §8: crashed analyst
    # → user can redispatch_card), not the executor. resolve_phase is the
    # single source of truth shared with the dispatch tick.
    phase = resolve_phase(card)

    # Re-dispatch (bypasses busy cap since we just freed the project)
    return await _run_card(
        session, card=card, project_key=card.project_key,
        project_path=project_path, transport=transport,
        phase=phase,
        agent_override=agent_override,
    )


async def dispatch_all_pending(
    session, *, project_key: str, project_path: str,
    transport: SpawnTransport | None = None,
) -> list[dict]:
    """Dispatch all unclaimed dispatch-column cards for a project at once.

    Bypasses the busy cap so multiple cards can be dispatched concurrently.
    When transport is None, each card's transport is auto-selected based on its
    card.transport field and the project's sandcastle configuration.
    Returns a list of result dicts for each successfully dispatched card.

    Cards whose ``depends_on`` is not fully resolved to Done are skipped — same
    gate the auto-dispatch tick uses (see app.kanban.dep_resolver). They stay
    in Backlog so they get picked up on the next bulk call (or the next tick)
    once the dependency clears, instead of being silently dropped from the
    board. The per-card ``dispatch_card`` / ``redispatch_card`` calls bypass
    this gate by design — those are explicit human overrides from the
    CardDrawer.

    **Provider routing:** the manual-pause gate below routes through
    ``_card_is_manually_paused`` → ``_effective_provider_for_pause_gate``, which
    delegates to ``resolve_effective_provider_and_model`` (the canonical 5-layer
    resolver — see its docstring). New "what subscription would each of these
    cards land on" decisions in this path MUST go through the resolver rather
    than re-walking the chain ad-hoc — the self-check
    ``scripts/check-dispatch-resolver-usage.sh`` flags any new ad-hoc walk.
    """
    if transport is None:
        transport = await get_transport_for_project(project_path)
    from app.kanban.service import list_pending_cards
    pending = [c for c in await list_pending_cards(session, project_key) if _is_due(c)]
    # list_pending_cards returns rows ordered by rank, mixing both dispatch
    # columns. Stable-sort by `_dispatch_order_key` so an operator clicking
    # "Dispatch All" gets the same ordering the auto-tick gives: "To Resume"
    # ahead of "Backlog", priority descending within a column, and within-priority
    # ties keeping their existing rank order (stable sort).
    pending.sort(key=_dispatch_order_key)
    column_caps = await _column_max_sessions(session, project_key)
    results = []
    # Apply the same depends_on gate the auto-dispatch tick uses so a "Dispatch All"
    # button click can never spawn a Blocked card (the dispatch tick — also called
    # from `dispatch_project` — already does this; bulk paths must match it for the
    # UI's Blocked badge to mean anything). Skipped cards stay in Backlog so they're
    # picked up once the dependency clears, on the next bulk call or the next tick.
    cards_by_id = {c.id: c for c in await list_cards(session, project_key)}
    for card in pending:
        if not meets_dep_prerequisites(card, cards_by_id):
            logger.info(
                "dispatch_all_pending: skipping blocked card %s (depends_on %s not yet Done)",
                card.id, card.depends_on,
            )
            continue
        if _is_gated(card):
            logger.info(
                "dispatch_all_pending: skipping gated card %s (gated_on=%r)",
                card.id, (card.meta or {}).get("gated_on"),
            )
            continue
        # Respect per-column caps even in manual "Dispatch all" — the cap is a
        # structural limit, not a busy heuristic. Analyst phase always goes to
        # the "analyst" column; executor phase resolves via _resolve_target_column.
        phase = resolve_phase(card)
        target_column = "analyst" if phase == "analyst" else await _resolve_target_column(
            session, card, project_path=project_path, project_key=project_key,
        )
        col_cap = column_caps.get(target_column)
        if col_cap is not None:
            cards = await list_cards(session, project_key)
            col_counts = _active_session_count_by_column(cards)
            if col_counts.get(target_column, 0) >= col_cap:
                continue
        # Manual per-subscription pause (kaart f056b2888a…): the "Dispatch all"
        # button must not bypass the operator-toggle either, otherwise the
        # operator would toggle a provider off and see it immediately
        # re-engaged by the bulk path. Same effective-provider precedence as
        # the auto-tick gate above (global override → pool → per-card override
        # → column default).
        if await _card_is_manually_paused(
            session, project_key=project_key, project_path=project_path,
            card=card, target_column=target_column,
        ):
            card_provider = await _effective_provider_for_pause_gate(
                session, project_key=project_key, project_path=project_path,
                card=card, target_column=target_column,
            )
            logger.info(
                "dispatch_all_pending: skipping card %s "
                "(target_column=%s, provider=%s) — manually paused",
                card.id, target_column, card_provider,
            )
            continue
        try:
            card_transport = get_transport_for_card(card, transport)
            res = await _run_card(
                session, card=card, project_key=project_key,
                project_path=project_path, transport=card_transport,
            )
            if res is not None:
                results.append(res)
        except Exception:
            logger.exception("failed to dispatch card %s", card.id)
    return results


async def redispatch_all_orphans(
    session, *, project_key: str, project_path: str,
    transport: SpawnTransport | None = None,
) -> list[dict]:
    """Re-dispatch all orphaned cards (unclaimed on agent columns) for a project.

    Mirrors `dispatch_all_pending`'s depends_on gate: orphans whose `depends_on`
    is not fully resolved to Done are skipped (with a log line) so the bulk
    redispatch button never spawns a Blocked orphan. See app.kanban.dep_resolver
    for the rationale shared with the auto-dispatch tick.

    When transport is None, each card's transport is auto-selected.
    Returns a list of result dicts for each successfully dispatched card.

    **Provider routing:** the manual-pause gate below routes through
    ``_card_is_manually_paused`` → ``_effective_provider_for_pause_gate``, which
    delegates to ``resolve_effective_provider_and_model`` (the canonical 5-layer
    resolver — see its docstring). The single-card ``redispatch_card`` path
    (Card drawer) is an explicit per-card human override and stays unguarded,
    but any new bulk-path pause / quota / cost decision MUST go through the
    resolver rather than re-walking the chain ad-hoc — the self-check
    ``scripts/check-dispatch-resolver-usage.sh`` flags any new ad-hoc walk.
    """
    from app.kanban.service import list_orphaned_cards
    orphans = await list_orphaned_cards(session, project_key)
    # list_orphaned_cards returns rows ordered by rank. Stable-sort by priority
    # descending so the bulk orphan redispatch matches the auto-tick's
    # _next_card behaviour: high-priority orphans jump the queue, low sinks.
    orphans.sort(key=_priority_key, reverse=True)
    # Snapshot the lookup once for the gate — `orphan.column` may flip to a fixed
    # column mid-loop on a successful redispatch, so don't reuse the per-card
    # `orphans` iterable for "current column".
    cards_by_id = {c.id: c for c in await list_cards(session, project_key)}
    results = []
    for card in orphans:
        if not meets_dep_prerequisites(card, cards_by_id):
            logger.info(
                "redispatch_all_orphans: skipping blocked orphan %s (depends_on %s not yet Done)",
                card.id, card.depends_on,
            )
            continue
        if _is_gated(card):
            logger.info(
                "redispatch_all_orphans: skipping gated orphan %s (gated_on=%r)",
                card.id, (card.meta or {}).get("gated_on"),
            )
            continue
        # Manual per-subscription pause (kaart f056b2888a…): a bulk
        # "Redispatch all" must not bypass the operator's subscription
        # toggle — there's no per-card operator intent in the bulk path.
        # FCR-blokkade: the previous path skipped this check and let
        # ``redispatch_card`` spawn paused-subscription cards. The
        # single-card ``redispatch_card`` path (Card drawer) is an explicit
        # per-card override and stays unguarded; the bulk path doesn't
        # qualify, so it gets the gate. ``_resolve_target_column`` already
        # handles the work_type fallback to "analyst" so we use the same
        # column resolution as the auto-tick.
        redispatch_target = await _resolve_target_column(
            session, card, project_path=project_path, project_key=project_key,
        )
        if await _card_is_manually_paused(
            session, project_key=project_key, project_path=project_path,
            card=card, target_column=redispatch_target,
        ):
            paused_provider = await _effective_provider_for_pause_gate(
                session, project_key=project_key, project_path=project_path,
                card=card, target_column=redispatch_target,
            )
            logger.info(
                "redispatch_all_orphans: skipping orphan %s "
                "(target_column=%s, provider=%s) — manually paused",
                card.id, redispatch_target, paused_provider,
            )
            continue
        try:
            # Pass None so redispatch_card auto-selects per-card transport.
            # `bulk_orphans` is the canonical caller_source label for the
            # bulk orphan redispatch path so the activity feed tells the
            # operator every redispatch this loop triggered.
            res = await redispatch_card(
                session, card_id=card.id, project_path=project_path,
                transport=transport, caller_source="bulk_orphans",
            )
            if res is not None:
                results.append(res)
        except Exception:
            logger.exception("failed to redispatch orphan card %s", card.id)
    return results


# ---- project_key -> local path --------------------------------------------

def match_project_paths(
    project_keys: set[str],
    project_paths: Iterable[str],
    *,
    key_of: Callable[[str], str] = resolve_project_key,
) -> dict[str, str]:
    """Map each enabled board key to a local path on this device, by computing the
    project key of each registered path. First match wins.

    This is the *bulk many-key* mapper (single pass, O(n) key computations for
    the whole board). For the *single-key* reverse lookup use
    `app.kanban.project_key.resolve_project_path`; do not call it once per key
    here — that would turn one O(n) pass into O(k·n) with k separate DB fetches.
    """
    out: dict[str, str] = {}
    for path in project_paths:
        try:
            key = key_of(path)
        except Exception:
            continue
        if key in project_keys and key not in out:
            out[key] = path
    return out


async def run_dispatch_tick(*, transport: SpawnTransport | None = None) -> None:
    """One poll cycle: dispatch the next Analysis/Todo card for every enabled project
    that maps to a local path on this device.
    
    Also retries queued cards that were rejected due to memory limits.
    
    If transport is None, each project will automatically select the appropriate
    transport based on its sandcastle configuration.
    """
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dispatch_pause import is_dispatch_paused

    async with KanbanSessionLocal() as ks:
        if await is_dispatch_paused(ks):
            logger.info("dispatch tick skipped: paused after a Claude usage-limit hit")
            return

    # First, try to retry queued cards if memory is available
    await _retry_queued_cards(transport or worktree_transport)

    async with KanbanSessionLocal() as ks:
        enabled = set(await list_autodispatch_projects(ks))
    if not enabled:
        return

    # Portfolio-cap: gate the *sum* of agent-claims across all autodispatch-enabled
    # projects, so one busy project can't starve the rest of the shared budget. The
    # per-project/per-column caps only bound a single project. Off by default
    # (feature flag) so rollout is gradual. Manual UI sessions bypass this entirely —
    # the check only runs in the auto-dispatch tick.
    if settings.portfolio_cap_enabled:
        async with KanbanSessionLocal() as ks:
            active = 0
            for project_key in enabled:
                active += _active_session_count(await list_cards(ks, project_key))
        if active >= settings.portfolio_cap_value:
            logger.info(
                "portfolio-cap reached (%d/%d active sessions across %d projects); "
                "skipping tick",
                active, settings.portfolio_cap_value, len(enabled),
            )
            return

    paths = await _registered_project_paths()
    mapping = match_project_paths(enabled, paths)
    if not mapping:
        return

    live_sessions = _live_sessions()  # one tmux query per tick, shared across projects
    sandcastle_live = await _live_sandcastle_sessions()  # sandcastle liveness, shared
    headless_live = await _live_headless_sessions()  # headless subprocess liveness, shared

    for project_key, project_path in mapping.items():
        async with KanbanSessionLocal() as ks:
            try:
                result = await dispatch_project(
                    ks, project_key=project_key, project_path=project_path,
                    transport=transport, live_sessions=live_sessions,
                    sandcastle_live=sandcastle_live,
                    headless_live=headless_live,
                )
                await ks.commit()
                
                # If dispatch failed due to memory, queue the card for retry
                if result is None:
                    await _maybe_queue_next_card(
                        ks, project_key=project_key, project_path=project_path,
                    )
            except MemoryLimitExceeded as e:
                # ``e`` is the cause-aware message built by
                # ``SessionRegistry.build_limit_message`` (counter ceiling vs.
                # memory ceiling). It already explains which one fired — no
                # need to prepend a redundant "Memory limit reached" label
                # that would mislead on a counter-ceiling hit (bevinding 5).
                logger.warning(f"Session spawn rejected for {project_key}: {e}")
                await _queue_card_on_memory_limit(
                    ks, project_key=project_key, project_path=project_path,
                )
            except Exception:
                logger.exception("dispatch tick failed for %s", project_key)
                # _run_card's except block already applied compensating ops (release
                # the claim, clear a stale resume pointer, bump dispatch_failures,
                # move back / to Impediment) before re-raising -- those are flushed
                # to this session but NOT yet committed. Without this commit, `async
                # with`'s implicit close-without-commit silently discards every one
                # of them, so the card comes back exactly as it started: still
                # claimable, resume pointer still stale, failure count still zero.
                # That turned a designed one-shot compensation into a true infinite
                # loop -- the actual mechanism behind the "cards starting with
                # 'Create' never start" report for synchronous spawn failures.
                await ks.commit()


def _kill_agent_session(session_name: str) -> None:
    """Kill a tmux session belonging to an agent."""
    from app.services.scheduling.session_registry import session_registry
    from app.services.scheduling.session_signals import session_signals

    try:
        subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Forget the spawn tracking so the registry doesn't keep reporting this
    # name as a candidate for `get_stuck_sessions` after the reaper's own
    # kill (next card) -- or after a human-driven redispatch that kills the
    # old session before re-spawning under the same name.
    session_registry.clear_spawn(session_name)
    # Same reasoning for the structured-signal registry: a SessionStart or
    # rate-limit signal recorded by the *previous* occupant must not survive
    # into a re-spawn under the same name, or the new session could be
    # misclassified as already-started or rate-limited on its very first tick.
    session_signals.clear(session_name)


class MemoryLimitExceeded(Exception):
    """Raised when a spawn is rejected due to memory limits."""
    pass


async def get_transport_for_project(project_path: str) -> SpawnTransport:
    """Get the appropriate transport for a project.

    The authoritative source is the `transport:<project_key>` meta (worktree |
    sandcastle | headless), set via the project's Default-transport control.
    Worktree honors the per-project skip_permissions flag.
    """
    from app.kanban.db import KanbanSessionLocal

    project_key = safe_resolve_project_key(project_path)
    if project_key is None:
        return make_worktree_transport(skip_permissions=True)

    async with KanbanSessionLocal() as ks:
        transport_name = await get_default_transport(ks, project_key)
        if transport_name == "sandcastle":
            return sandcastle_transport
        if transport_name == "headless":
            from app.kanban.headless_runner import headless_transport
            return headless_transport
        skip = await get_skip_permissions(ks, project_key)

    return make_worktree_transport(skip_permissions=skip)


def make_resume_transport(session_id: str, project_folder: str | None = None,
                          skip_permissions: bool = True) -> SpawnTransport:
    """Factory that returns a transport that resumes an existing session.

    Unlike the worktree transport, this does NOT create a new git worktree.
    The ClaudeCodeCli resolves the working directory from the session's
    recorded cwd (via project_folder), and spawns with ``--resume session_id``.

    kaart 27317b4871… (FCR gap 7): ``_run_card`` forwards the
    anthropic-compatible ``endpoint_*`` kwargs to every ``SpawnTransport``
    the dispatcher dispatches against; the resume transport was
    the hold-out and the previous signature dropped them on the
    floor, leading to a ``TypeError`` at every compatible resume
    dispatch. Mirror the SpawnTransport protocol here and thread
    the carrier through ``SpawnCommandOptions`` so
    ``spawn_session`` builds the same env the worktree / headless
    paths produce.
    """
    def _transport(*, directory: str, prompt: str, session_name: str,
                   cli_id: str = "claude-code", provider: str = "anthropic",
                   model: str | None = None,
                   endpoint_name: str | None = None,
                   endpoint_base_url: str | None = None,
                   endpoint_auth_token: str | None = None,
                   # Accepted for SpawnTransport parity and deliberately
                   # unused: the RTK token-saver (kaart c31333bf…) is
                   # installed only by the worktree transport, which owns the
                   # fresh worktree its hook scripts are written into. A
                   # resume re-enters an existing worktree that already has
                   # (or lacks) the hook from its original spawn. Dropping
                   # these from the signature is what broke every resume
                   # dispatch with a TypeError — see
                   # tests/test_spawn_transport_signature_parity.py.
                   card_id: str | None = None,
                   column_name: str | None = None) -> dict:
        from app.services.agentic_cli.base import SpawnCommandOptions
        from app.services.runs.spawn import spawn_session
        from app.services.scheduling.session_registry import session_registry

        if not session_registry.can_add_session():
            # Cause-aware message — same builder as the worktree and sandcastle
            # transports, so a counter leak doesn't get mis-diagnosed as a
            # memory problem (bevinding 5 in
            # docs/cockpit/spawn-test-bridge-sessions-analyse.md).
            raise MemoryLimitExceeded(session_registry.build_limit_message())

        options = SpawnCommandOptions(
            directory=directory,
            mode="resume",
            session_id=session_id,
            project_folder=project_folder,
            prompt=prompt,
            # ``directory`` here is the project_root passed by the dispatcher
            # (``card_transport(directory=project_path, ...)`` at
            # dispatch.py:5375). ``spawn_session`` rewrites ``options.directory``
            # to the worktree via ``resolve_directory`` for resume mode, so the
            # worktree is what Claude Code actually sees as its cwd. Thread
            # ``repo_path`` so ``_project_mcp_config_args`` can fall back to
            # ``<repo-root>/.mcp.json`` when the worktree has no copy — the
            # external product-project case (untracked ``.mcp.json`` in the
            # repo-root; kaart ``bc123e2d…`` / route 2 from kaart ``3672c073…``).
            repo_path=directory,
            skip_permissions=skip_permissions,
            # Same product-lane wiring as make_worktree_transport — see the
            # comment on that factory for the rationale and the analysis
            # doc reference.
            permission_prompt_tool=(
                PERMISSION_PROMPT_TOOL_NAME if not skip_permissions else None
            ),
            provider=provider,
            model=model,
            endpoint_name=endpoint_name,
            endpoint_base_url=endpoint_base_url,
            endpoint_auth_token=endpoint_auth_token,
        )
        # Resolve project_key for the audit log when the directory is a
        # registered project root. Falls back to None on failure so the
        # resume still works; the audit hook will skip logging in that case.
        project_key = safe_resolve_project_key(directory)
        # A resume spawns a *fresh* tmux session whose env is rebuilt from
        # scratch via `-e` flags (spawn_session never inherits the backend's
        # os.environ, and the original spawn's env does not carry over to the
        # new process). So project-scoped secrets must be re-injected here or a
        # resumed session would silently lose them — the same wiring the
        # worktree and sandcastle transports already do. Best-effort: no store
        # / no passphrase -> {}.
        extra_env = _resolve_project_secrets(project_key)
        result = spawn_session(
            cli_id,
            options,
            session_name=session_name,
            project_key=project_key,
            runtime="worktree",
            extra_env=extra_env,
        )
        # Track the spawn so the dispatch reaper can detect a resumed session
        # that immediately hits a 429 Token Plan limit and never initialises
        # its hook scripts. See `make_worktree_transport` for the full rationale.
        session_registry.mark_spawned(session_name)
        return result

    return _transport


def get_transport_for_card(card: KanbanCard, default_transport: SpawnTransport) -> SpawnTransport:
    """Get the appropriate transport for a card based on its transport field.

    Transport priority:
    1. Card's resume_session_id (resume mode — no worktree created)
    2. Card's explicit transport setting (worktree | sandcastle | headless)
    3. Project's default transport (from sandcastle config)

    Returns the transport to use for this specific card.
    """
    # Resume takes priority: spawn with --resume rather than creating a worktree
    resume_id = getattr(card, "resume_session_id", None)
    if resume_id:
        project_folder = getattr(card, "resume_project_folder", None)
        return make_resume_transport(resume_id, project_folder)

    # If card has explicit transport setting, use it
    if card.transport == "sandcastle":
        return sandcastle_transport
    elif card.transport == "headless":
        from app.kanban.headless_runner import headless_transport
        return headless_transport
    elif card.transport == "worktree":
        return worktree_transport

    # Otherwise use the project default
    return default_transport


async def _retry_queued_cards(transport: SpawnTransport) -> None:
    """Attempt to dispatch queued cards when memory is available.

    **Provider routing:** the manual-pause gate below routes through
    ``_card_is_manually_paused`` → ``_effective_provider_for_pause_gate``, which
    delegates to ``resolve_effective_provider_and_model`` (the canonical 5-layer
    resolver — see its docstring). New "what subscription would each queued
    card land on" decisions in this path MUST go through the resolver rather
    than re-walking the chain ad-hoc — the self-check
    ``scripts/check-dispatch-resolver-usage.sh`` flags any new ad-hoc walk.
    """
    from app.kanban.db import KanbanSessionLocal
    from app.services.scheduling.pending_queue import pending_queue

    status = get_memory_status_cached()
    if status.is_critical:
        return  # Still under memory pressure

    retryable = pending_queue.get_retryable_cards()
    if not retryable:
        return

    logger.info(f"Memory available ({status.usage_percent:.0%} used), retrying {len(retryable)} queued cards")
    live_sessions = _live_sessions()

    for card in retryable:
        try:
            async with KanbanSessionLocal() as ks:
                card_data = await get_card(ks, card.card_id)
                if card_data is None:
                    logger.info(f"Card {card.card_id} no longer exists, removing from queue")
                    pending_queue.dequeue(card.card_id)
                    continue

                # Check if card is still in a dispatchable state: unclaimed, and
                # either a fresh Backlog/To-Resume card or an orphan left behind in
                # an agent column (mirrors _next_card's orphan fallback above --
                # otherwise a queued orphan gets silently dropped here instead of
                # retried).
                from app.kanban.schemas import COLUMNS
                dispatchable_column = (
                    card_data.column in _DISPATCH_COLUMNS
                    or card_data.column not in COLUMNS
                )
                if card_data.claimed_by or not dispatchable_column:
                    logger.info(f"Card {card.card_id} is no longer dispatchable, removing from queue")
                    pending_queue.dequeue(card.card_id)
                    continue

                # Per-column cap check: if the card's target column has a
                # per-column max_sessions, respect it before retrying. The cap
                # hold is not a failed dispatch, so leave the card untouched in
                # the queue (don't mark_retry, which would count toward
                # max_retries and eventually drop a card that is merely waiting
                # for a slot).
                cards = await list_cards(ks, card.project_key)
                column_caps = await _column_max_sessions(ks, card.project_key)
                if column_caps:
                    target_column = await _resolve_target_column(
                        ks, card_data, project_path=card.project_path,
                        project_key=card.project_key,
                        agent_override=card.agent_override,
                    )
                    col_cap = column_caps.get(target_column)
                    if col_cap is not None:
                        col_counts = _active_session_count_by_column(cards)
                        if col_counts.get(target_column, 0) >= col_cap:
                            logger.info(
                                f"Card {card.card_id} held back: column '{target_column}' "
                                f"at per-column cap ({col_cap})"
                            )
                            continue
                else:
                    # When the project has no per-column caps, we still need a
                    # target column for the manual-pause gate below. Resolve
                    # it unconditionally; the cost is one DB lookup and
                    # matches the auto-tick path.
                    target_column = await _resolve_target_column(
                        ks, card_data, project_path=card.project_path,
                        project_key=card.project_key,
                        agent_override=card.agent_override,
                    )

                # Manual per-subscription pause (kaart f056b2888a…): queued
                # memory retries must honour the operator toggle too, otherwise
                # a card that was queued before the operator paused Anthropic
                # would spawn against Anthropic on the next memory-available
                # tick. FCR-blokkade: the previous path only checked column
                # caps here and let the card straight through to _run_card.
                if await _card_is_manually_paused(
                    ks, project_key=card.project_key,
                    project_path=card.project_path,
                    card=card_data, target_column=target_column,
                ):
                    paused_provider = await _effective_provider_for_pause_gate(
                        ks, project_key=card.project_key,
                        project_path=card.project_path,
                        card=card_data, target_column=target_column,
                    )
                    logger.info(
                        f"Card {card.card_id} held back: subscription "
                        f"'{paused_provider}' manually paused by operator"
                    )
                    continue

                result = await _run_card(
                    ks, card=card_data, project_key=card.project_key,
                    project_path=card.project_path, transport=transport,
                    agent_override=card.agent_override,
                    impediment_question=card.impediment_question,
                    live_sessions=live_sessions,
                )
                await ks.commit()

                if result is not None:
                    pending_queue.dequeue(card.card_id)
                    logger.info(f"Successfully dispatched queued card {card.card_id}")
                else:
                    pending_queue.mark_retry(card.card_id)
        except Exception as e:
            logger.exception(f"Retry failed for card {card.card_id}: {e}")
            pending_queue.mark_retry(card.card_id)


async def _maybe_queue_next_card(
    session, *, project_key: str, project_path: str,
) -> None:
    """Check if we should queue the next card for this project."""
    status = get_memory_status_cached()
    if not status.is_warning:
        return  # Memory is fine, no need to queue

    # Get the next card that would have been dispatched
    cards = await list_cards(session, project_key)
    next_card = _next_card(cards)
    if next_card is None:
        return

    from app.services.scheduling.pending_queue import pending_queue
    pending_queue.enqueue(
        card_id=next_card.id,
        project_key=project_key,
        project_path=project_path,
    )


async def _queue_card_on_memory_limit(
    session, *, project_key: str, project_path: str,
) -> None:
    """Queue the next card when memory limit is reached."""
    cards = await list_cards(session, project_key)
    next_card = _next_card(cards)
    if next_card is None:
        return

    from app.services.scheduling.pending_queue import pending_queue
    pending_queue.enqueue(
        card_id=next_card.id,
        project_key=project_key,
        project_path=project_path,
    )


async def _registered_project_paths() -> list[str]:
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.database import Project

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Project.path))).scalars().all()
    return list(rows)
