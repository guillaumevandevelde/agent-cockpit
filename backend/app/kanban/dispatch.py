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
import subprocess
import uuid
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import yaml

from app.config import settings
from app.kanban import subscription_pool

# Local import so the dep-filter check inside the dispatch tick stays a pure
# helper (no DB / session state — see app.kanban.dep_resolver).
from app.kanban.dep_resolver import meets_dep_prerequisites
from app.kanban.models import KanbanCard, KanbanMeta
from app.kanban.operations import ClaimRejected, apply_operation
from app.kanban.project_key import resolve_project_key, safe_resolve_project_key
from app.kanban.service import (
    get_card,
    get_column_default_model,
    get_column_default_provider,
    list_cards,
)
from app.kanban.subscription_pool import (
    PoolEntry,
    get_subscription_pool,
)
from app.services.agentic_cli.provider_env import (
    PROVIDER_ANTHROPIC,
    PROVIDER_BEDROCK,
    PROVIDER_MINIMAX,
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


async def get_skip_permissions(session, project_key: str) -> bool:
    row = await session.get(KanbanMeta, SKIP_PERMISSIONS_PREFIX + project_key)
    if row is None:
        return True  # default: bypass permissions (existing behaviour)
    return row.value == "1"


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
        return row.value
    return DEFAULT_TRANSPORT


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
_ALLOWED_OVERRIDE_PROVIDERS = (
    PROVIDER_ANTHROPIC, PROVIDER_BEDROCK, PROVIDER_MINIMAX,
)


async def get_active_subscription_override(
    session, project_key: str,
) -> dict | None:
    """Return the board-wide subscription override for `project_key`, or None.

    Shape when set: ``{"provider": "anthropic|bedrock|minimax", "model": str|None}``.
    ``model`` is optional — an override that pins only the provider leaves the
    model to fall through to the column default / card model / persona
    frontmatter chain (same shape as a partial column-override, just one level
    higher in the precedence)."""
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
    return {"provider": provider, "model": model if isinstance(model, str) else None}


async def set_active_subscription_override(
    session, project_key: str, override: dict | None,
) -> None:
    """Persist (or clear, when `None`) the board-wide subscription override.

    ``override`` is validated against the allow-list before storage; an
    unknown provider raises ValueError so the caller surfaces a 422 instead
    of writing a row that the dispatcher would then refuse to honour.

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
    value = json.dumps({"provider": provider, "model": model if model else None})
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
    the returned dict — ``pick_subscription`` interprets absent as
    "no signal → available", which is exactly analyse §6.3.
    """
    from app.services.subscriptions import registry as _registry

    snapshots: dict[str, SubscriptionUsage] = {}
    for entry in entries:
        # ``get_provider_for`` is a synchronous dict lookup — awaiting it
        # raises ``TypeError: object … can't be used in 'await' expression``,
        # which the surrounding ``except Exception`` in ``_pick_pool_choice``
        # silently swallows → empty snapshot map → drempel branch is dead
        # code. Drop the ``await``. See kanban card ea7e038b… (D1).
        #
        # ``cli`` is no longer on ``PoolEntry`` (kaart 0b3ad6e2…) — the
        # pool always routes through the single supported CLI (see
        # ``subscription_pool.POOL_CLI``). The snapshot-lookup key keeps
        # the historical ``{cli}:{provider}`` shape so the registry's
        # default providers — registered as ``claude-code:{provider}``
        # in ``services/subscriptions/registry.py`` — still match.
        provider = _registry.get_provider_for(
            cli=subscription_pool.POOL_CLI, provider=entry.provider,
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


async def _paused_providers_for_pool(
    session,
) -> set[str]:
    """Return the set of providers whose per-provider pause is currently
    active. Wraps ``dispatch_pause.list_paused_providers`` so the pool
    router can consume the read-only shape directly."""
    from app.kanban import dispatch_pause
    paused = await dispatch_pause.list_paused_providers(session)
    return {p for p in paused if p}


async def _pick_pool_choice(
    session, entries: list[PoolEntry], *, project_key: str,
) -> PoolEntry | None:
    """Run the pure ``pick_subscription`` against the live usage snapshot
    map and the currently-paused providers.

    Returns the chosen ``PoolEntry`` or ``None`` when the pool is empty
    (handled by the pure router). A failure inside the live-snapshot
    gathering falls back to "no signal → first entry wins" so a flaky
    usage provider cannot block dispatch.
    """
    paused = await _paused_providers_for_pool(session)
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
    return subscription_pool.pick_subscription(entries, snapshots, paused_providers=paused)


async def _pool_spillover_available(
    session, *, project_key: str, limited_provider: str,
) -> bool:
    """Fase 2 (analyse §4 Optie B / §5): can the just-limited card spill
    over onto another subscription in the pool instead of waiting for
    ``limited_provider`` to reset?

    Threshold-/failover branch of the fase-1b pool router: mark
    ``limited_provider`` (plus every already-paused provider) as
    unavailable, then ask ``has_available_spillover`` whether the pool
    still offers a genuinely-available subscription to route to. Returns
    False when the project has no pool, or when every subscription is now
    paused/exhausted — the reactive limit path then keeps its existing
    behaviour (per-provider pause → "To Resume" + reset-time scheduled_at).

    ``limited_provider`` is added explicitly (not only read from the
    per-provider pause slots) so the decision is correct even when the
    caller sets the pause *after* this check — the just-hit provider is
    unavailable regardless of write ordering.
    """
    entries = await get_subscription_pool(session, project_key)
    if not entries:
        return False
    paused = await _paused_providers_for_pool(session)
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
    return subscription_pool.has_available_spillover(entries, snapshots, paused_providers=paused)


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
                     provider: str | None = None) -> str | None:
    """Precedence: per-column override (`card.column_overrides[<target>].model`)
    > card.model > column.default_model > persona frontmatter `model:` > None
    (no --model flag, provider default applies). The per-column override is the
    most explicit intent the card author can express for a specific agent
    column, so it wins over the card-global `model`. Empty strings are treated
    as unset, same as None.

    `provider` gates the persona-frontmatter fallback: a persona's `model:` is an
    Anthropic-subscription alias (e.g. `opus`), written before per-column provider
    selection existed. When the column routes to a non-Anthropic provider
    (minimax/bedrock), that alias is meaningless and — passed as `--model` — would
    override the provider env's native model (`ANTHROPIC_MODEL=MiniMax-M3`),
    silently running the wrong model against the wrong vendor. So the persona
    fallback only applies for Anthropic (or when provider is unknown). The explicit
    override/card/column-default models always win: those are deliberate choices
    that may legitimately name a provider-native model."""
    persona_fallback = persona_model if provider in (None, PROVIDER_ANTHROPIC) else None
    return override_model or card_model or column_default_model or persona_fallback or None


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


def build_card_prompt(card, *, persona: str | None, ship_mode: str,
                      phase: str = "executor",
                      impediment_question: str | None = None,
                      impediment_answer: str | None = None,
                      revisit_question: str | None = None,
                      revisit_prior_decision: dict | None = None) -> str:
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
            # A human answered the blocker via /resolve-impediment (or a
            # `**Resolution:**` comment). Surface it as authoritative so the
            # resumed session acts on the decision instead of re-asking.
            impediment_section += (
                "A human has since answered this — treat the answer as an "
                "authoritative decision and proceed accordingly:\n"
                f"> {impediment_answer}\n\n"
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
        ship_instructions = _build_ship_instructions(ship_mode)
    problem_flag_instructions = _build_problem_flag_instructions()
    mcp_fallback_instructions = _build_mcp_fallback_instructions()
    worktree_safety_callout = _build_worktree_safety_callout()
    attachments_section = _build_attachments_section(card)

    return (
        f"{preamble}"
        "You are picking up a Kanban card from the Claude Cockpit board. "
        'It is already claimed by you and moved to "Doing".\n\n'
        f"Host card id: {getattr(card, 'id', '') or ''}\n"
        f"# {card.title}\n"
        f"{getattr(card, 'description', '') or ''}\n"
        f"{attachments_section}"
        f"{impediment_section}\n"
        f"{revisit_section}\n"
        f"Ship mode: {ship_mode}\n\n"
        "Work autonomously to completion, following your role instructions above. "
        "Use the `cockpit-kanban` MCP tools (`move_card`, `attach_deliverable`, "
        "`comment`) to update the card exactly as those instructions direct. If you are "
        "blocked, use `report_impediment` with a clear question explaining what you need."
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
    kanban card 7b1d0a91 for the full postmortem."""
    return (
        "## If a `cockpit-kanban` MCP call fails with `-32602`\n"
        "`-32602` (Invalid request parameters) from a `mcp__cockpit-kanban__*` "
        "tool is an intermittent MCP handshake race, **not** a bad payload — "
        "retry the same call once. If it still fails, fall back to the REST API "
        "at `http://localhost:8000/api/v1/kanban` (same board, same effect):\n"
        "- `POST /cards/{id}/comment` — body `{\"text\": \"…\"}`\n"
        "- `POST /cards/{id}/move` — body `{\"column\": \"…\"}` (for Done/Impediment, "
        "follow with a `comment` carrying your summary — the REST move has no "
        "`summary` field)\n"
        "- `POST /cards/{id}/deliverables` — body `{\"kind\": \"branch|pr|commit|link|note\", \"ref\": \"…\"}`\n"
        "- `GET /cards?project_key=<key>&column=<col>` — list cards\n"
        "- `GET /project-key?project_path=<abs path>` — resolve the project key\n"
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


def _build_worktree_safety_callout() -> str:
    """Top-of-prompt callout forbidding writes to the canonical checkout path.

    Background — kanban card 513e37a1a86e41db8b6af8423292f6b6: a dispatched
    analyst session edited two docs via the absolute path
    ``/home/vdvgu/claude-cockpit/docs/cockpit/...`` instead of its worktree
    path. ``Edit`` succeeded because the committed content matched in both
    checkouts, so ``old_string`` resolved; the change landed on top of a
    concurrent session's uncommitted work in the main checkout. The persona
    doc already warns against ``cd /home/vdvgu/claude-cockpit/...`` for
    shell commands but says nothing about Write/Edit — an agent reading the
    card description (which references ``/home/vdvgu/claude-cockpit/...`` for
    canonical filenames) easily constructs an absolute *write* path that
    bypasses the worktree.

    This callout is the in-prompt mirror of the persona-doc guidance: it
    names the safe pattern, names the forbidden one, and names the tools that
    can clobber (``Write`` / ``Edit`` / ``MultiEdit``). It is rendered above
    the ``## Session-end workflow`` heading so it lands in the agent's
    early context, not buried under later steps — same parity principle as
    the ship-instructions inline.

    The exact path string (``/home/vdvgu/claude-cockpit``) is hard-coded
    because that is the canonical checkout location on this host; the
    guidance is that the worktree is
    ``<that-root>/.claude/worktrees/<branch>/`` and absolute writes outside
    it are forbidden.
    """
    return (
        "## Worktree scope — write only inside your worktree\n"
        "You were spawned in a git worktree at "
        "``/home/vdvgu/claude-cockpit/.claude/worktrees/<branch>/`` "
        "(see your shell's cwd). Your **only** writable surface is that "
        "worktree root. **Never** call ``Write``, ``Edit``, ``MultiEdit``, "
        "or ``NotebookEdit`` with an absolute path that resolves to "
        "``/home/vdvgu/claude-cockpit/...`` *outside* your worktree — that "
        "is the shared canonical checkout where ``master`` is checked out, "
        "and concurrent dispatched sessions may have uncommitted work there. "
        "A write to that path silently lands on top of someone else's "
        "changes (kanban card 513e37a1a86e41db8b6af8423292f6b6 was a "
        "near-clobber from exactly this).\n\n"
        "Concretely:\n"
        "- **Right:** ``docs/cockpit/foo.md``, ``backend/app/x.py``, or "
        "absolute "
        "``/home/vdvgu/claude-cockpit/.claude/worktrees/<branch>/docs/cockpit/foo.md``.\n"
        "- **Wrong:** ``/home/vdvgu/claude-cockpit/docs/cockpit/foo.md`` — "
        "this resolves to the *main* checkout, not your worktree, even "
        "though the file content is identical.\n\n"
        "Same rule for shell: don't ``cd /home/vdvgu/claude-cockpit/...`` "
        "and run a write from there — see the persona's *Werkomgeving in "
        "worktree* section for the broader cwd-safety rules. Read paths to "
        "the canonical checkout are fine; only writes are forbidden.\n"
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


def _build_ship_instructions(ship_mode: str) -> str:
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
        "kaart-beschrijving, en de committed diff tegen `origin/master`. Voer "
        "letterlijk deze prompt uit:\n\n"
        "   > Je reviewt een feature-implementatie tegen zijn oorspronkelijke\n"
        "   > specificatie. Inputs: de oorspronkelijke kaart-titel, -beschrijving, en\n"
        "   > de diff tegen `origin/master`. Vraag: doet de implementatie wat er\n"
        "   > gevraagd werd?\n"
        "   >\n"
        "   > Specifiek:\n"
        "   > - Elke requirement/bullet uit de beschrijving is geïmplementeerd.\n"
        "   > - De API/UI matcht de specificatie (naamgeving, gedrag, edge cases).\n"
        "   > - De implementatie integreert zonder siblings te breken.\n"
        "   > - Het deliverable dat in de samenvatting geclaimd wordt, is\n"
        "   >   daadwerkelijk aanwezig.\n"
        "   >\n"
        "   > Output: OK om te shippen, OF een lijst met blokkerende issues met\n"
        "   > `file:line`-refs. Dit is een **feature-compliance-check**, geen\n"
        "   > code-quality-check — die is al apart gelopen via `/code-review`.\n\n"
        "   **Resultaat interpreteren:** OK → ga door naar stap 1 hieronder. "
        "Blokkerende issues → fix die eerst in dezelfde sessie (geen nieuwe "
        "kaart — FCR-blokkades zijn van jou, niet van het bord), herhaal de "
        "FCR tot `OK`, en ga dan pas naar de ship-stappen.\n\n"
    )

    sync = (
        "1. **Sync** — `git fetch origin` so you are up to date with the remote.\n"
    )
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
        "symlink the main checkout's already-installed ``frontend/node_modules`` "
        "instead of paying a multi-minute ``npm ci``. Fall back to ``npm ci`` "
        "when the lockfile diverged (frontend deps changed) or main's "
        "``node_modules`` is absent / itself missing ``.bin/`` (partial).\n"
        "     # Card 15cc257d… also handled the partial-install trap: an "
        "interrupted ``npm ci`` leaves some scoped dirs but no ``.bin/``, which "
        "makes ``npm run lint`` die with ``eslint: not found`` and blocks a "
        "plain symlink. Move the partial aside (``mv``, not ``rm`` — ``rm`` is "
        "deny-listed) before bootstrapping.\n"
        "     ( cd frontend && \\\n"
        "       if [ -d node_modules ] && [ ! -d node_modules/.bin ]; then \\\n"
        "         mv node_modules \"../node_modules.partial-$(date +%s)\" && \\\n"
        "         echo \"moved partial node_modules aside (missing .bin/)\"; \\\n"
        "       fi && \\\n"
        "       if [ ! -d node_modules ]; then \\\n"
        "         BASE=$(git merge-base HEAD origin/master) && \\\n"
        "         if git diff --quiet \"$BASE\" origin/master -- frontend/package-lock.json \\\n"
        "            && [ -d /home/vdvgu/claude-cockpit/frontend/node_modules/.bin ]; then \\\n"
        "           ln -s /home/vdvgu/claude-cockpit/frontend/node_modules node_modules && \\\n"
        "           echo \"bootstrapped frontend/node_modules via symlink (lockfile matches master)\"; \\\n"
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
            "state. Uncommitted/untracked changes here would merge as a silent "
            "no-op (\"Everything up-to-date\") — abort so you commit them first.\n"
            "   if ! git diff --quiet HEAD || [ -n \"$(git ls-files --others "
            "--exclude-standard)\" ]; then\n"
            "     echo 'ERROR: uncommitted/untracked changes in this worktree — "
            "run git add + git commit (step 3), then re-run.' >&2; exit 1\n"
            "   fi\n"
            "   TMP=$(mktemp -d)\n"
            "   git worktree add --detach \"$TMP/merge-$$\" origin/master\n"
            "   if ! git -C \"$TMP/merge-$$\" merge --no-ff \"$BRANCH\" -m \"Merge $BRANCH\"; then\n"
            "     echo \"ERROR: merge conflict merging $BRANCH into master — not pushing.\" >&2\n"
            "     echo \"Conflicted worktree left at $TMP/merge-$$ for inspection (not removed).\" >&2\n"
            "     exit 1\n"
            "   fi\n"
            "   git -C \"$TMP/merge-$$\" push origin HEAD:master\n"
            "   git worktree remove --force \"$TMP/merge-$$\"\n"
            "   ```\n"
            "   If the merge itself reports ``CONFLICT`` (the block above exits 1 "
            "before pushing): do not resolve conflict markers by guessing — this "
            "means a concurrent session touched the same files after you branched. "
            "Re-``git fetch origin`` and retry the merge once, then if a real "
            "conflict remains, ``report_impediment`` naming the conflicting files "
            "so a human can resolve it; never force-push or discard either side.\n"
            "5. **Attach the deliverable** — ``attach_deliverable`` with "
            "``kind=\"branch\"`` and ``ref=<your-branch-name>``.\n"
            + retro_direct +
            "7. **Move the card to Done** — ``move_card`` with ``column=\"Done\"`` "
            "and ``summary=<what you did>``, a few sentences on the work you "
            "completed.  ``summary`` is required for this move; the call is "
            "rejected without it.  The backend will kill this session and remove "
            "the worktree.\n"
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
            "the call is rejected without it).  If the poll loop exited because a "
            "check failed, the PR was closed, or the wait timed out, call "
            "``report_impediment`` instead so a human can look at it — do not "
            "move to Done.\n"
        )

    return feature_compliance_review + sync + tests + commit + shipping


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
        "the worktree.\n"
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
                 model: str | None = None) -> dict: ...


def _known_cli_ids() -> set[str]:
    """Ids of the agentic CLIs the registry knows about (claude-code, codex-cli, …).

    Used to tell a CLI selection apart from a persona/column name, since
    `card.agent` overloads both."""
    from app.services.agentic_cli import get_agentic_clis
    return {p.id for p in get_agentic_clis()}


def make_worktree_transport(skip_permissions: bool = True) -> SpawnTransport:
    """Factory that returns a worktree transport with configurable permission bypass."""
    def _transport(*, directory: str, prompt: str, session_name: str,
                   cli_id: str = "claude-code", provider: str = "anthropic",
                   model: str | None = None) -> dict:
        """Create a worktree off origin/master, then spawn a `cli_id` session in it,
        against the given `provider` subscription (anthropic | bedrock | minimax).

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

        # Resolve project_key for env-injection (card `[security][D]
        # Per-project env-injectie in spawn_session`). Uses the safe
        # helper — a project without a git remote still spawns, just
        # without a `COCKPIT_PROJECT_KEY` to audit against.
        project_key = safe_resolve_project_key(repo)

        options = SpawnCommandOptions(
            directory=worktree_path, mode="plain", prompt=prompt,
            skip_permissions=skip_permissions, worktree_path=worktree_path, repo_path=repo,
            provider=provider, model=model,
        )
        try:
            result = spawn_session(
                cli_id,
                options,
                session_name=session_name,
                project_key=project_key,
                runtime="worktree",
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

    return _transport


# Default transport keeps existing behaviour (permissions bypassed)
worktree_transport = make_worktree_transport(skip_permissions=True)


# Strong references to in-flight sandcastle start tasks. asyncio only keeps weak
# references to tasks, so without this set a fire-and-forget task can be garbage
# collected mid-flight and the run silently never starts.
_sandcastle_start_tasks: set = set()


def sandcastle_transport(*, directory: str, prompt: str, session_name: str,
                         cli_id: str = "claude-code", provider: str = "anthropic",
                         model: str | None = None) -> dict:
    """Sandcastle transport: run the agent in an isolated sandbox via sandcastle.

    `cli_id`, `provider` and `model` are accepted for transport-signature
    parity but ignored: sandcastle runs use the per-project sandcastle config's
    `agent_provider`, not the card's, column's, or persona's.

    Runs the agent in a Docker/Podman container. The actual run is kicked off
    asynchronously; this function returns immediately after scheduling it.

    The dispatcher always calls transports from inside a running event loop, so we
    schedule the start as a tracked task (kept alive via `_sandcastle_start_tasks`)
    rather than blocking. `session_name` is stored as the run's branch so the reaper
    can recognise the live sandcastle session and not release its claim.

    Raises MemoryLimitExceeded if hardware memory limits are reached.
    """
    import asyncio

    from app.services.scheduling.session_registry import session_registry

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

    async def _start():
        try:
            return await sandcastle_service.start_run(
                project_path=directory,
                prompt=prompt,
                branch_name=session_name,
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
    return {r.name: r.max_sessions for r in rows if r.max_sessions is not None and r.max_sessions > 0}


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


def _is_rate_limited_session(pane_content: str) -> bool:
    """True if tmux pane content shows a 429 / rate-limit indicator.

    Matches a small set of well-known substrings (case-insensitive), reusing
    the canonical detector on AutoResumeService so the hook-event path and
    the reaper's pane scan stay in sync — a 429 detected via either source
    triggers the same dispatch pause, and we never risk the two drifting
    apart."""
    if not pane_content:
        return False
    from app.services.scheduling.auto_resume import auto_resume_service
    return auto_resume_service.is_limit_notification(pane_content)


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


async def _cleanup_stuck_session(
    session, *, card, project_key: str, session_name: str, pane_content: str,
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
    eventually move the card to Impediment instead of looping forever."""
    from datetime import UTC, datetime, timedelta

    from app.kanban.dispatch_pause import set_paused_until
    from app.services.scheduling.auto_resume import FALLBACK_PAUSE_HOURS

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
    # (column override → column default → Anthropic) gets gated, so a
    # bedrock outage doesn't freeze anthropic/minimax traffic. provider=None
    # only happens when the card/column context is missing -- then the
    # legacy global pause keeps today's behaviour intact.
    provider = await _provider_for_card(session, project_key, card, card.column)
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
_DISPATCH_COLUMNS = ("Backlog", "To Resume")  # new cards from Backlog, resumed cards from To Resume
_PRIORITY_RANK = {"high": 3, "medium": 2, "low": 1, "none": 0}


def _priority_key(card) -> int:
    """Sort key for priority-aware dispatch: higher value wins. Unknown / None
    priority collapses to 0 so the rank order still determines the tie-break
    (stable sort preserves within-priority rank order). Shared between the
    auto-tick's _next_card and the bulk paths (dispatch_all_pending,
    redispatch_all_orphans) so the three call sites can't drift."""
    return _PRIORITY_RANK.get(getattr(card, "priority", None), 0)


def _is_due(card: KanbanCard) -> bool:
    """True unless `card.scheduled_at` names a not-yet-reached future time.

    A missing or unparseable value is treated as due (fail open) rather than
    silently hiding a card from auto-dispatch forever over a bad timestamp.
    """
    scheduled_at = getattr(card, "scheduled_at", None)
    if not scheduled_at:
        return True
    try:
        fire_at = datetime.fromisoformat(scheduled_at)
    except ValueError:
        return True
    return ensure_aware(fire_at) <= datetime.now(UTC)


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
    """
    if not getattr(card, "parent_card_id", None):
        return False
    return not any(
        d.kind == "plan_ref" for d in getattr(card, "deliverables", []) or []
    )


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


def _next_card(cards: Iterable[KanbanCard]) -> KanbanCard | None:
    cards = list(cards)
    for col in _DISPATCH_COLUMNS:
        col_cards = [
            c for c in cards
            if c.column == col and not c.claimed_by and _is_due(c)
            and not _awaiting_plan_ref(c)
            and not _is_gated(c)
        ]
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
    orphans = [
        c for c in cards
        if c.column not in COLUMNS and not c.claimed_by and _is_due(c)
        and not _awaiting_plan_ref(c)
        and not _is_gated(c)
    ]
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
    activity feed shows the tick didn't force-dispatch early."""
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
    # Active-subscription-override (fase 0 of
    # docs/cockpit/subscription-flexibiliteit-analyse.md): a board-wide pin that
    # routes every dispatch onto one subscription regardless of column or card
    # defaults. Stored as a single KanbanMeta row keyed by project_key. When
    # set, it wins over BOTH the subscription pool (fase 1b) and the per-card
    # column_overrides and the column defaults below — the explicit human
    # "route everything to X" intent dominates any narrower per-card
    # configuration. None = no override (the default; preserves today's
    # dispatch behaviour exactly, see test_active_subscription_override).
    global_override = await get_active_subscription_override(session, project_key)
    # Subscription pool (fase 1b of the analyse): an ordered list of
    # subscriptions with per-subscription drempels, picked at dispatch time
    # against the per-subscription usage snapshot (analyse §4 / §5). When
    # set AND no global_override dominates, the pool's chosen entry wins
    # over both column_overrides and the column default for provider (and
    # optionally model — same partial-override shape as column_overrides).
    # When unset, dispatch falls through to the column-default chain
    # exactly as before (backward-compat).
    pool_entries = await get_subscription_pool(session, project_key)
    pool_choice: PoolEntry | None = None
    if pool_entries is not None and not global_override:
        pool_choice = await _pick_pool_choice(
            session, pool_entries, project_key=project_key,
        )
    # A per-card override for this phase's resolved target column (persona) wins
    # over the column defaults for BOTH provider and model. Because analyst and
    # executor phases both reach this point with their own `target_agent`, each
    # phase automatically picks up its own override entry (if any). See
    # KanbanCard.column_overrides. NOTE: when the pool is set, the pool's
    # choice beats the per-card override — a per-card override expresses
    # "this card wants X", but the operator's pool expresses "right now the
    # right answer for *all* cards is whatever the router picks". The
    # precedence (highest first) is therefore:
    #   1. global_override (board-wide pin)
    #   2. pool_choice    (ordered, usage-aware; this block)
    #   3. column_override
    #   4. column.default_provider / column.default_model / card.model / persona
    column_override = (card.column_overrides or {}).get(target_agent) or {}
    override_provider = column_override.get("provider") or None
    override_model = column_override.get("model") or None
    # The target column decides which subscription/vendor the spawn authenticates
    # against (see KanbanColumn.default_provider); unset means the dispatcher's own
    # default, the Anthropic subscription. The board-wide active-subscription
    # override (when set) takes precedence over everything below; the pool
    # (when set and no override dominates) sits between the global pin and the
    # per-card override.
    provider = (
        (global_override or {}).get("provider")
        or (pool_choice.provider if pool_choice else None)
        or override_provider
        or await get_column_default_provider(session, project_key, target_agent)
        or PROVIDER_ANTHROPIC
    )
    persona_model = _read_persona_model(project_path, f"{target_agent}.md")
    column_default_model = await get_column_default_model(session, project_key, target_agent)
    # Model precedence mirrors provider: global override > pool > column_override >
    # column.default_model > persona frontmatter. The global override only sets
    # the model when one is supplied (`None` falls through), so a
    # provider-only pin leaves the existing model chain intact — same shape as
    # a partial column-override, just one level higher in the precedence.
    # Likewise for the pool: ``pool_choice.model is None`` falls through to
    # the per-card / column / persona chain.
    effective_model_override = (
        (global_override or {}).get("model")
        or (pool_choice.model if pool_choice else None)
        or override_model
    )
    effective_model = _effective_model(
        effective_model_override, card.model, column_default_model, persona_model,
        provider=provider,
    )
    prompt = build_card_prompt(card, persona=persona, ship_mode=ship_mode,
        phase=phase,
        impediment_question=impediment_question,
        impediment_answer=impediment_answer,
        revisit_question=revisit_question,
        revisit_prior_decision=revisit_prior_decision)
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
    try:
        spawned = card_transport(directory=project_path, prompt=prompt, session_name=name,
                                 cli_id=cli_id, provider=provider, model=effective_model)
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
    """
    if card is None or not agent_column:
        return None
    column_override = (getattr(card, "column_overrides", None) or {}).get(agent_column) or {}
    override_provider = column_override.get("provider") or None
    return (
        override_provider
        or await get_column_default_provider(session, project_key, agent_column)
        or PROVIDER_ANTHROPIC
    )


async def _provider_for_cwd(cwd: str) -> str | None:
    """Resolve the provider for a card running in `cwd`, for callers that have
    only a cwd (the Notification hook path). Looks up the card the same way as
    `move_limited_session_to_resume` -- claimed by `agent:<session_name>`, on a
    non-terminal column -- then delegates to `_provider_for_card`. Returns None
    when no card can be matched, which is also the condition under which
    `move_limited_session_to_resume` would no-op, so the caller can safely
    fall back to the global pause.
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
        return await _provider_for_card(ks, project_key, card, card.column)


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
        limited_provider = await _provider_for_card(ks, project_key, card, card.column)
        spillover = False
        if limited_provider is not None:
            spillover = await _pool_spillover_available(
                ks, project_key=project_key, limited_provider=limited_provider,
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
                )
                reaped += 1
                continue
            pane = _capture_pane_content(name)
            if pane is not None and _is_rate_limited_session(pane):
                await _cleanup_stuck_session(
                    session, card=card, project_key=project_key,
                    session_name=name, pane_content=pane,
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
    on the project's sandcastle configuration."""
    cards = await list_cards(session, project_key)
    if live_sessions is not None:
        if await reap_stale_claims(
            session, project_key=project_key, cards=cards, live_sessions=live_sessions,
            sandcastle_live=sandcastle_live, headless_live=headless_live,
            project_path=project_path,
        ):
            cards = await list_cards(session, project_key)

    column_caps = await _column_max_sessions(session, project_key)
    last_result: dict | None = None

    # Fill every dispatchable card in this tick. The per-column cap (when set)
    # is the only structural limit at this level; the hardware/OS-level cap
    # checked inside the transport enforces the actual memory bound.
    while True:
        card = _next_card(cards)
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

        # Skip child cards whose parents aren't Done yet — _next_card is rank/
        # priority-aware but doesn't know about depends_on, so the dep filter
        # is the dispatcher's responsibility (see app.kanban.dep_resolver).
        cards_by_id = {c.id: c for c in cards}
        if not meets_dep_prerequisites(card, cards_by_id):
            # Mark the card as 'skipped this tick' by removing it from the
            # working set so _next_card doesn't pick it again on the next
            # iteration of the same tick — _next_card would otherwise loop on
            # it until the cap is filled or we run out of cards.
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
    """Dispatch an impediment card to a specific agent for resolution.

    Args:
        card_id: The ID of the impediment card
        project_path: Path to the project
        target_agent: The agent to dispatch to (analyst, engineer)
        impediment_question: The question that needs to be answered
        impediment_answer: A human's answer/decision on the blocker, injected
            into the `## IMPEDIMENT` section so the resumed session acts on it
        transport: The spawn transport to use (auto-selects based on card if None)

    Returns:
        Result dict or None if dispatch failed
    """
    card = await get_card(session, card_id)
    if card is None:
        return None
    
    if card.column != "Impediment":
        logger.warning("Card %s is not in Impediment column, cannot dispatch as impediment", card_id)
        return None
    
    # Move card to Doing for the target agent
    await apply_operation(
        session, op_type="move", entity_type="card", project_key=card.project_key,
        entity_id=card.id, payload={"column": "Doing"},
    )
    
    # Auto-select transport if not provided
    if transport is None:
        transport = await get_transport_for_project(project_path)
    transport = get_transport_for_card(card, transport)
    
    return await _run_card(
        session, card=card, project_key=card.project_key,
        project_path=project_path, transport=transport,
        impediment_question=impediment_question,
        impediment_answer=impediment_answer,
    )


async def redispatch_card(
    session, *, card_id: str, project_path: str,
    transport: SpawnTransport | None = None,
    agent_override: str | None = None,
) -> dict | None:
    """Release a stuck card, optionally kill its session, and re-dispatch.

    This is the human override for cards that are stuck on agent columns —
    most commonly a session that hit its usage limit and never exited, so the
    dead-session reaper never noticed it. Before killing the existing tmux
    session, we check whether its worktree holds a resumable Claude transcript
    (same lookup the auto-recovery reaper uses). If one is found, the card is
    tagged with `resume_session_id`/`resume_project_folder` so the re-dispatch
    below picks the resume transport (`claude --resume`, same worktree) instead
    of discarding the conversation and starting a brand new session.

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

    Returns:
        Result dict or None if the card was not found
    """
    card = await get_card(session, card_id)
    if card is None:
        return None

    # Kill existing tmux session if claimed by an agent
    session_name = _claimant_session(card)
    if session_name:
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
    """Dispatch all unclaimed Backlog cards for a project at once.

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
    """
    if transport is None:
        transport = await get_transport_for_project(project_path)
    from app.kanban.service import list_pending_cards
    pending = [c for c in await list_pending_cards(session, project_key) if _is_due(c)]
    # list_pending_cards returns rows ordered by rank. Stable-sort by priority
    # descending so an operator clicking "Dispatch All" gets the same priority-
    # aware ordering the auto-tick already gives — high jumps the queue, low
    # sinks. Within-priority ties keep their existing rank order (stable sort).
    pending.sort(key=_priority_key, reverse=True)
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
        try:
            # Pass None so redispatch_card auto-selects per-card transport
            res = await redispatch_card(
                session, card_id=card.id, project_path=project_path,
                transport=transport,
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
    project key of each registered path. First match wins."""
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
    """
    def _transport(*, directory: str, prompt: str, session_name: str,
                   cli_id: str = "claude-code", provider: str = "anthropic",
                   model: str | None = None) -> dict:
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
            skip_permissions=skip_permissions,
            provider=provider,
            model=model,
        )
        # Resolve project_key for the audit log when the directory is a
        # registered project root. Falls back to None on failure so the
        # resume still works; the audit hook will skip logging in that case.
        project_key = safe_resolve_project_key(directory)
        result = spawn_session(
            cli_id,
            options,
            session_name=session_name,
            project_key=project_key,
            runtime="worktree",
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
    """Attempt to dispatch queued cards when memory is available."""
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
