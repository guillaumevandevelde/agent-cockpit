"""Subscription-pool data model + the pure ``pick_subscription()`` router.

Fase 1b of ``docs/cockpit/subscription-flexibiliteit-analyse.md``: a
geordende pool van subscriptions met per-subscription drempels. The
router chooses the **first entry in priority order** that is not paused
and not above its drempel; when every entry is exhausted, it falls back
to the last entry so the per-provider pause / dispatch-time gate has one
specific slot to halt on.

This module is intentionally pure and side-effect free — no DB, no
session, no provider lookups. The dispatch integration passes in the
precomputed usage snapshot dict and the set of paused providers (both
of which the caller already has). That keeps the function trivially
unit-testable (see ``tests/test_subscription_pool_pick.py``) and makes the
contract obvious: "given this state, pick this subscription".

Subscription identity follows analyse §3: a subscription is a
``{cli, provider}`` pair (vendor-diverse — same-vendor multi-account is
out of scope per the analyse §7 fork decision 2026-07-14). ``model`` is
optional — a ``None`` model leaves the dispatch precedence chain (column
default / card model / persona frontmatter) to fill it in, matching the
shape of the existing per-card ``column_overrides[col]``.

Storage: a project-scoped pool lives in the ``KanbanMeta`` key-value
table under ``subscription_pool:<project_key>`` (same shape as the
board-wide active-subscription-override from fase 0 — see
``dispatch.SUBSCRIPTION_OVERRIDE_PREFIX``). That keeps the dispatcher
free of schema migrations and keeps the precedence logic discoverable
in one module.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.kanban.models import KanbanMeta
from app.services.agentic_cli.provider_env import (
    PROVIDER_ANTHROPIC, PROVIDER_BEDROCK, PROVIDER_MINIMAX,
)
from app.services.subscriptions.base import SubscriptionUsage

logger = logging.getLogger(__name__)

SUBSCRIPTION_POOL_PREFIX = "subscription_pool:"

# Mirror the active-subscription-override allow-list so both knobs stay
# consistent. Adding a new provider is one edit (provider_env.py) plus
# this tuple — both surfaces share the same source of truth.
_ALLOWED_POOL_PROVIDERS = (
    PROVIDER_ANTHROPIC, PROVIDER_BEDROCK, PROVIDER_MINIMAX,
)


@dataclass(frozen=True)
class PoolEntry:
    """Eén subscription in de pool.

    Fields:
        cli: the agentic CLI to spawn (e.g. ``"claude-code"``,
            ``"codex-cli"``). Mirrors ``agentic_cli.registry``.
        provider: which vendor the CLI authenticates against
            (``"anthropic"`` | ``"bedrock"`` | ``"minimax"`` for
            claude-code; for other CLIs this is the CLI's native vendor
            identifier).
        model: optional model pin. ``None`` = no model pin — dispatch
            falls through to the column/card/persona precedence chain.
            This mirrors the partial-override shape of the existing
            per-card ``column_overrides[col].model``.
        drempel: fraction (0..1) above which this subscription is
            considered "full" and the router spills to the next entry.
            0.9 means "skip when the snapshot shows 90%+ consumed";
            1.0 means "use until the per-provider pause hits". This
            lives on the entry (per-subscription), not in the provider,
            because providers don't bake a threshold into their snapshot
            — see ``SubscriptionUsage.drempel_gebruikt``.
    """

    cli: str
    provider: str
    model: str | None
    drempel: float


def _is_above_threshold(
    entry: PoolEntry, usage: SubscriptionUsage | None,
) -> bool:
    """True wanneer de subscription boven z'n drempel zit en overgeslagen
    moet worden.

    Drie redenen om **niet** above-threshold te zijn:

    1. Geen snapshot (geen signaal — analyse §6.3: behandel als
       beschikbaar tot de per-provider pause hem raakt).
    2. Snapshot zonder ``drempel_gebruikt`` (geen getal — idem).
    3. ``drempel_gebruikt`` is een getal maar onder de entry's drempel.
    """
    if usage is None:
        return False
    if usage.drempel_gebruikt is None:
        return False
    return usage.drempel_gebruikt >= entry.drempel


def pick_subscription(
    entries: list[PoolEntry],
    usages: dict[str, SubscriptionUsage],
    *,
    paused_providers: set[str],
) -> PoolEntry | None:
    """Kies de eerste subscription in prioriteitsvolgorde die nog
    beschikbaar is; valt terug op de laatste entry wanneer alles vol of
    gepauzeerd is (analyse §4 "laatste val-terug").

    Args:
        entries: de geordende pool (volgorde = prioriteit). Een lege lijst
            betekent "geen pool geconfigureerd" → return None.
        usages: mapping ``{subscription_id: SubscriptionUsage}``. Een
            entry zonder entry in deze map wordt behandeld als "geen
            signaal" (analyse §6.3).
        paused_providers: providers waarvan de per-provider pause nog
            loopt. Een entry met een paused provider wordt overgeslagen —
            ongeacht de usage-snapshot.

    Returns:
        De gekozen ``PoolEntry``, of None wanneer de pool leeg is.
        Wanneer de pool niet leeg is wordt er altijd een entry terug­
        gegeven, ook als élke entry boven drempel of gepauzeerd is — de
        "laatste val-terug"-tak uit analyse §4. De uiteindelijke gate
        tegen een gepauzeerde fallback is de per-provider pause die de
        dispatch zelf afvangt; deze functie geeft een deterministisch
        "als ik móét kiezen, dan deze" terug zodat de caller exact weet
        welk pad de spawn heeft gekozen (en kan loggen waarom).
    """
    if not entries:
        return None

    chosen: PoolEntry | None = None
    for entry in entries:
        # Zodra een entry zowel onder drempel als niet-paauze is,
        # is dit de winnaar — eerste in prioriteit wint.
        if entry.provider in paused_providers:
            chosen = entry  # val terug op de laatst geziene entry
            continue
        usage = usages.get(f"{entry.cli}:{entry.provider}")
        if _is_above_threshold(entry, usage):
            chosen = entry
            continue
        return entry
    return chosen


def has_available_spillover(
    entries: list[PoolEntry],
    usages: dict[str, SubscriptionUsage],
    *,
    paused_providers: set[str],
) -> bool:
    """Fase 2 (analyse §4 Optie B / §5): is er nog een subscription om naar
    over te *spillen* wanneer het huidige abonnement zijn limiet raakt?

    Dit is de drempel-/failover-tak van de pool-router: de reactieve
    limiet-lus (``dispatch.move_limited_session_to_resume``) voegt de
    zojuist-gelimiteerde provider toe aan ``paused_providers`` en vraagt
    hier of er dán nog een échte val-terug is. "Echt beschikbaar" betekent
    de *schone* keuze-tak van ``pick_subscription`` — een entry die niet
    gepauzeerd is én niet boven zijn drempel — niet louter de "laatste
    val-terug" (die geeft ``pick_subscription`` ook terug als álles
    uitgeput is, puur zodat de caller een deterministisch slot heeft).

    Returns:
        True  → er is een niet-gepauzeerde, onder-drempel subscription:
                de kaart kan meteen doorschuiven i.p.v. te wachten op de
                reset (analyse §2.3 "sluit de reactieve failover-lus").
        False → lege pool, of elke subscription is nu gepauzeerd/uitgeput:
                val terug op de bestaande per-provider pause (wachten tot
                reset).
    """
    chosen = pick_subscription(entries, usages, paused_providers=paused_providers)
    if chosen is None:
        return False
    if chosen.provider in paused_providers:
        # ``pick_subscription`` viel terug op de "laatste val-terug" — die
        # provider is zelf gepauzeerd, dus er is geen echte spillover-target.
        return False
    usage = usages.get(f"{chosen.cli}:{chosen.provider}")
    # De fallback kan ook een niet-gepauzeerde maar bóven-drempel entry zijn
    # (alles vol); dat telt niet als beschikbare spillover.
    return not _is_above_threshold(chosen, usage)


# ---- storage (KanbanMeta wrapper) ------------------------------------------
#
# A project's pool lives in a single JSON-encoded row keyed by
# ``subscription_pool:<project_key>``. The shape is the natural JSON
# serialization of a list of PoolEntry: ``[{"cli": ..., "provider": ...,
# "model": ..., "drempel": ...}, ...]``. ``None`` deletes the row so a
# follow-up read sees no pool and the dispatcher falls through to the
# column-default chain — the same backward-compat clause as the
# active-subscription-override (`dispatch.set_active_subscription_override`).


def _validate_entries(entries: list[PoolEntry]) -> None:
    """Validate the pool up front so a corrupt row never reaches
    ``pick_subscription``. Raises ``ValueError`` with a concrete message
    the API layer surfaces as 422.

    - Empty pool is rejected (use ``None`` to clear).
    - Each entry's provider must be on the allow-list.
    - ``drempel`` must be in ``(0, 1]`` — 0 would always be "above
      threshold" (silently disable the entry) and >1 disables the
      spillover entirely.
    - ``cli`` must be non-empty (used as the lookup key for usage
      snapshots).
    """
    if not entries:
        raise ValueError("subscription pool must not be empty (use null to clear)")
    for entry in entries:
        if entry.provider not in _ALLOWED_POOL_PROVIDERS:
            raise ValueError(
                f"unknown provider: {entry.provider!r}; "
                f"expected one of {_ALLOWED_POOL_PROVIDERS}",
            )
        if not entry.cli:
            raise ValueError("subscription pool entry.cli must be non-empty")
        if entry.drempel <= 0 or entry.drempel > 1:
            raise ValueError(
                f"subscription pool entry.drempel must be in (0, 1]; "
                f"got {entry.drempel!r}",
            )


def _serialize_entries(entries: list[PoolEntry]) -> str:
    payload = [
        {
            "cli": e.cli, "provider": e.provider,
            "model": e.model, "drempel": e.drempel,
        }
        for e in entries
    ]
    return json.dumps(payload)


def _deserialize_entries(value: str) -> list[PoolEntry] | None:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        # Corrupt row — treat as no pool rather than wedging dispatch.
        logger.warning("corrupt subscription_pool row; ignoring")
        return None
    if not isinstance(parsed, list) or not parsed:
        return None
    out: list[PoolEntry] = []
    for raw in parsed:
        if not isinstance(raw, dict):
            return None
        cli = raw.get("cli")
        provider = raw.get("provider")
        model = raw.get("model")
        drempel = raw.get("drempel")
        if not isinstance(cli, str) or not isinstance(provider, str):
            return None
        if model is not None and not isinstance(model, str):
            return None
        if not isinstance(drempel, (int, float)):
            return None
        out.append(PoolEntry(
            cli=cli, provider=provider, model=model, drempel=float(drempel),
        ))
    return out


async def get_subscription_pool(
    session, project_key: str,
) -> list[PoolEntry] | None:
    """Return the subscription pool for ``project_key``, or None when no
    pool is configured.

    None means "fall through to today's dispatch behaviour" — the
    column-default chain stays authoritative. This is the
    backward-compat clause from the acceptance criteria.
    """
    row = await session.get(
        KanbanMeta, SUBSCRIPTION_POOL_PREFIX + project_key,
    )
    if row is None:
        return None
    return _deserialize_entries(row.value)


async def set_subscription_pool(
    session, project_key: str,
    entries: list[PoolEntry] | None,
) -> None:
    """Persist (or clear, when ``None``) the subscription pool.

    Validates ``entries`` before storage; an invalid pool raises
    ``ValueError`` so the caller surfaces a 422 instead of writing a
    row that the dispatcher would then refuse to honour. Storing
    ``None`` deletes the row entirely so a follow-up read sees no pool
    and falls through to the column-default precedence — keeping the
    "unset = exact pre-feature behaviour" contract testable.
    """
    key = SUBSCRIPTION_POOL_PREFIX + project_key
    if entries is None:
        row = await session.get(KanbanMeta, key)
        if row is not None:
            await session.delete(row)
            await session.flush()
        return
    _validate_entries(entries)
    value = _serialize_entries(entries)
    row = await session.get(KanbanMeta, key)
    if row is None:
        session.add(KanbanMeta(key=key, value=value))
    else:
        row.value = value
    await session.flush()