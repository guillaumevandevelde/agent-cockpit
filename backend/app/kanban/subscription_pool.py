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

Subscription identity follows analyse §3: a subscription is identified
by its ``{cli, provider}`` pair. Subscription pool entries carry both
fields — the per-entry ``cli`` (re-introduced in kanban card 8f40d443…)
is **consumed** by the router: the router filters the candidate entries
on the dispatch-resolved ``cli_id`` so an OpenCode-spawned card
consults only ``open-code:provider`` snapshots and a Codex-spawned card
consults only ``codex-cli:provider`` snapshots. ``cli=None`` on
construction defaults to ``DEFAULT_POOL_CLI`` (claude-code) so the
common case of a pre-kaart-8f40d443 pool keeps working unchanged.

``model`` is optional — a ``None`` model leaves the dispatch precedence
chain (column default / card model / persona frontmatter) to fill it in,
matching the shape of the existing per-card ``column_overrides[col]``.

Storage: a project-scoped pool lives in the ``KanbanMeta`` key-value
table under ``subscription_pool:<project_key>`` (same shape as the
board-wide active-subscription-override from fase 0 — see
``dispatch.SUBSCRIPTION_OVERRIDE_PREFIX``). That keeps the dispatcher
free of schema migrations and keeps the precedence logic discoverable
in one module. The JSON payload is ``[{"cli": ..., "provider": ...,
"model": ..., "drempel": ...}, ...]`` — rows without ``cli`` are
accepted and back-filled with ``DEFAULT_POOL_CLI`` on read so a
stored row written by a pre-kaart-8f40d443 build (or by an existing UI
that has not yet been refreshed) still loads without manual data
surgery.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum

from app.kanban.models import KanbanMeta
from app.services.agentic_cli.provider_env import (
    PROVIDER_ANTHROPIC,
    PROVIDER_BEDROCK,
    PROVIDER_COMPATIBLE,
    PROVIDER_MINIMAX,
)
from app.services.subscriptions.base import SubscriptionUsage

logger = logging.getLogger(__name__)

SUBSCRIPTION_POOL_PREFIX = "subscription_pool:"

# The CLI a ``PoolEntry(cli=None)`` falls back to. Pre-kaart-8f40d443
# pools were board-wide pinned to claude-code; this constant preserves
# that exact behaviour as the default so legacy rows and existing
# call sites keep matching. The dispatch integration can override
# ``cli`` per entry to route e.g. ``open-code`` or ``codex-cli``
# subscriptions — see ``pick_subscription_for_cli``.
DEFAULT_POOL_CLI = "claude-code"

# Mirror the active-subscription-override allow-list so both knobs stay
# consistent. Adding a new provider is one edit (provider_env.py) plus
# this tuple — both surfaces share the same source of truth.
_ALLOWED_POOL_PROVIDERS = (
    PROVIDER_ANTHROPIC, PROVIDER_BEDROCK, PROVIDER_MINIMAX,
    PROVIDER_COMPATIBLE,
)


def _known_pool_clis() -> frozenset[str]:
    """Return the set of CLI ids the router is willing to filter on.

    Derived from the agentic_cli registry (the single source of truth
    for which CLIs the spawn layer can dispatch — mirrors
    ``dispatch._known_cli_ids``). Re-derived on every call so a CLI
    register/unregister hook stays effective without restarting the
    process; the call is a cheap dict.get and only fires at pool
    construction + dispatch pick, never inside the per-card hot path.
    """
    try:
        from app.services.agentic_cli import get_agentic_clis
    except Exception:
        # Defensive: keep the router importable even when the agentic_cli
        # registry is unreachable (unit tests that import this module in
        # isolation). Fall back to the legacy hardcoded list — same
        # baseline as before the refactor.
        return frozenset({
            "claude-code", "codex-cli", "copilot-cli",
            "mimo-code", "open-code",
        })
    return frozenset(cli.id for cli in get_agentic_clis())


@dataclass(frozen=True)
class PoolEntry:
    """Eén subscription in de pool.

    Fields:
        cli: which spawn transport the entry targets (one of the
            agentic_cli registry ids — ``"claude-code"``,
            ``"codex-cli"``, ``"copilot-cli"``, ``"mimo-code"``,
            ``"open-code"``). ``cli=None`` falls back to
            ``DEFAULT_POOL_CLI`` so the common claude-code case keeps
            building without ceremony. The router **consumes** this
            field (kaart 8f40d443 + analyse §3): a ``open-code`` spawn
            consults only entries whose ``cli="open-code"`` so the
            per-CLI quota axis is honoured end-to-end.
        provider: which vendor the CLI authenticates against
            (``"anthropic"`` | ``"bedrock"`` | ``"minimax"``).
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

    provider: str
    model: str | None
    drempel: float
    cli: str | None = None

    def __post_init__(self) -> None:
        # ``frozen=True`` rejects ``self.cli = ...`` but
        # ``object.__setattr__`` still works on the underlying object.
        # Materialise the default into the public field so equality,
        # pickling, dataclass-based introspection all see the resolved
        # value — ``resolved_cli`` is just an alias that doubles as
        # the snapshot-lookup key for the router's hot path.
        resolved = self.cli or DEFAULT_POOL_CLI
        object.__setattr__(self, "cli", resolved)
        allowed = _known_pool_clis()
        if resolved not in allowed:
            raise ValueError(
                f"unknown cli: {resolved!r}; "
                f"expected one of {sorted(allowed)}",
            )

    @property
    def resolved_cli(self) -> str:
        """The CLI the router will use as the snapshot-lookup key.

        Falls back to ``DEFAULT_POOL_CLI`` when ``cli=None`` was
        passed at construction. Materialised in __post_init__ so the
        router can do a single string format per entry without a
        per-iteration default-resolution branch."""
        return self.cli  # always populated by __post_init__


class SignalStatus(Enum):
    """Explicit snapshot-side status for one ``(entry, usage)`` pair.

    Kaart 8f40d443… (quota-pool CLI-agnostisch): the router must
    distinguish "no signal at all" from "0% used" — both are
    mathematically *below threshold* but they tell the operator
    completely different stories (analyse §6.1 "no fabrication" +
    §6.3 "no signal → available"). Returning a status alongside the
    choice lets the Subscriptions-pagina UI render an honest
    badge ("geen signaal" vs "0% gebruikt") instead of silently
    collapsing the two.

    The router itself keeps the analyse §6.3 behaviour — both
    ``AVAILABLE_NO_SIGNAL`` and ``AVAILABLE_WITH_SIGNAL`` keep the
    entry eligible. The status is informational; the gate against a
    real hard stop is the per-provider pause downstream.
    """

    AVAILABLE_WITH_SIGNAL = "available_with_signal"
    """Snapshot present, ``beschikbaar=True``, ``drempel_gebruikt``
    is a real number under the entry's drempel. The router is happy
    to pick this and the operator sees a normal "X% used" badge."""

    AVAILABLE_NO_SIGNAL = "available_no_signal"
    """No snapshot registered for ``{entry.cli}:{entry.provider}``,
    or the snapshot's ``drempel_gebruikt`` is ``None``. The router
    treats this as "available until the per-provider pause catches
    it" (analyse §6.3) and the UI must show a "geen
    signaal-bron"-badge so the operator doesn't confuse it with a
    healthy low-usage subscription. Distinct from
    ``AVAILABLE_WITH_SIGNAL`` even though both are eligible."""

    UNAVAILABLE_ABOVE_THRESHOLD = "unavailable_above_threshold"
    """Snapshot present with ``drempel_gebruikt >= entry.drempel``.
    The router spills to the next entry; the UI shows the actual
    percentage so the operator can see *why* this entry skipped."""

    UNAVAILABLE_EXHAUSTED = "unavailable_exhausted"
    """Snapshot present with ``beschikbaar=False`` (provider's hard
    limit hit). Distinct from ``UNAVAILABLE_ABOVE_THRESHOLD`` because
    a hard-exhausted provider typically resets on a longer clock
    than a drempel-spill; the UI may show different copy ("limit
    bereikt" vs "X% used — boven drempel")."""

    @classmethod
    def classify(
        cls, entry: PoolEntry, usage: SubscriptionUsage | None,
    ) -> "SignalStatus":
        """Classify a single ``(entry, usage)`` pair into a status.

        Public helper so the Subscriptions-pagina UI can render the
        same status per row that the router saw at pick-time —
        without re-deriving the four-way case at the call site.
        Preserves the order: a hard ``beschikbaar=False`` overrides
        a high ``drempel_gebruikt`` because the operator's reset
        question is different."""
        if usage is None:
            return cls.AVAILABLE_NO_SIGNAL
        if not usage.beschikbaar:
            return cls.UNAVAILABLE_EXHAUSTED
        if usage.drempel_gebruikt is None:
            return cls.AVAILABLE_NO_SIGNAL
        if usage.drempel_gebruikt >= entry.drempel:
            return cls.UNAVAILABLE_ABOVE_THRESHOLD
        return cls.AVAILABLE_WITH_SIGNAL


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

    Kaart 8f40d443… (quota-pool CLI-agnostisch): the four-way
    available/unavailable distinction is surfaced separately via
    ``SignalStatus.classify`` (called from
    ``pick_subscription_with_status``) so the UI can render an honest
    badge. This function stays binary — analyse §6.3 says a missing
    signal is *available*, so we return False for both "0%" and
    "missing" without a quota-axis distinction. The status lives in a
    parallel channel so a future contributor can render the four-way
    case without rewriting the priority scan."""
    if usage is None:
        return False
    if usage.drempel_gebruikt is None:
        return False
    return usage.drempel_gebruikt >= entry.drempel


def pick_subscription_for_cli(
    entries: list[PoolEntry],
    usages: dict[str, SubscriptionUsage],
    *,
    paused_providers: set[str],
    cli_id: str,
) -> PoolEntry | None:
    """Kies de eerste subscription van ``cli_id`` in prioriteitsvolgorde
    die nog beschikbaar is; valt terug op de laatste entry wanneer alles
    vol of gepauzeerd is (analyse §4 "laatste val-terug").

    Kaart 8f40d443…: ``cli_id`` is the spawn transport the dispatched
    session will run under (resolved by ``dispatch._phase_cli_id`` /
    ``_run_card``). The router filters candidates on
    ``entry.cli == cli_id`` so an OpenCode spawn consults only
    ``open-code:{provider}`` snapshots — quotas are per ``{cli,
    provider}``, never per provider alone.

    Returns:
        De gekozen ``PoolEntry``, of None wanneer geen enkele entry
        van ``cli_id`` overleeft de priority scan (lege pool, of de
        pool heeft alleen entries van een andere CLI). De
        cli-discriminatie is hier bewust hard: "geen entry voor
        deze CLI" is een eigen geval (acceptatie-criterium) dat de
        dispatcher dwingt terug te vallen op de column-default chain.

        Wanneer de pool entries van ``cli_id`` bevat maar élk daarvan
        boven drempel of gepauzeerd is, wordt de laatste entry van
        die CLI teruggegeven — de "laatste val-terug"-tak uit
        analyse §4. De uiteindelijke gate tegen een gepauzeerde
        fallback is de per-provider pause die de dispatch zelf
        afvangt; deze functie geeft een deterministisch "als ik móét
        kiezen, dan deze" terug zodat de caller exact weet welk pad
        de spawn heeft gekozen (en kan loggen waarom).
    """
    cli_candidates = [e for e in entries if e.resolved_cli == cli_id]
    if not cli_candidates:
        return None

    chosen: PoolEntry | None = None
    for entry in cli_candidates:
        # Zodra een entry zowel onder drempel als niet-pauze is,
        # is dit de winnaar — eerste in prioriteit wint.
        if entry.provider in paused_providers:
            chosen = entry  # val terug op de laatst geziene entry
            continue
        usage = usages.get(f"{entry.resolved_cli}:{entry.provider}")
        if _is_above_threshold(entry, usage):
            chosen = entry
            continue
        return entry
    return chosen


def pick_subscription(
    entries: list[PoolEntry],
    usages: dict[str, SubscriptionUsage],
    *,
    paused_providers: set[str],
) -> PoolEntry | None:
    """Legacy entry point — delegates to ``pick_subscription_for_cli``
    with ``cli_id=DEFAULT_POOL_CLI``.

    Pre-kaart-8f40d443 callers (incl. the existing dispatch wiring and
    the historical pool tests) used the board-wide-pinned
    ``POOL_CLI = 'claude-code'`` behaviour. ``pick_subscription``
    preserves that exact contract so no caller needs to change. New
    wiring that resolves a per-card ``cli_id`` should call
    ``pick_subscription_for_cli`` directly.
    """
    return pick_subscription_for_cli(
        entries, usages,
        paused_providers=paused_providers,
        cli_id=DEFAULT_POOL_CLI,
    )


@dataclass(frozen=True)
class PoolChoice:
    """The router's decision bundled with the snapshot-side status.

    Kaart 8f40d443… (quota-pool CLI-agnostisch): a "no signal"
    snapshot (analyse §6.3) and a "0% used" snapshot are both
    *eligible* (the router keeps them) but the operator-facing
    meaning is different. This dataclass carries the choice *and*
    the ``SignalStatus`` so callers (the dispatcher + the
    Subscriptions-pagina UI) can render the distinction without
    re-classifying the same pair on every render.

    Kept as a separate return type so the existing
    ``pick_subscription_for_cli`` / ``pick_subscription`` stay
    ``PoolEntry | None`` — preserving the side-effect-free,
    pool-entry-shape contract every historical test pins. New
    wiring that wants the status calls ``pick_subscription_with_status``
    instead; legacy wiring that only needs the chosen entry keeps
    working unchanged."""
    entry: PoolEntry
    status: SignalStatus


def pick_subscription_with_status(
    entries: list[PoolEntry],
    usages: dict[str, SubscriptionUsage],
    *,
    paused_providers: set[str],
    cli_id: str = DEFAULT_POOL_CLI,
) -> PoolChoice | None:
    """CLI-aware pick that bundles the choice with the snapshot status.

    Same priority scan as ``pick_subscription_for_cli`` — first
    in-config-order entry of ``cli_id`` whose snapshot is below
    threshold and not paused wins, else the last entry of
    ``cli_id`` as the "laatste val-terug" (analyse §4). Returns
    ``None`` when no entry of ``cli_id`` exists in the pool
    (the "geen entry voor deze CLI"-case — acceptatie-criterium).

    Status semantics (see ``SignalStatus``):

    * ``AVAILABLE_WITH_SIGNAL`` — the chosen entry is below its
      drempel *and* has a real ``drempel_gebruikt`` snapshot. The
      UI shows a normal "X% used" badge.
    * ``AVAILABLE_NO_SIGNAL`` — the chosen entry has no snapshot
      at all, or the snapshot's ``drempel_gebruikt`` is ``None``.
      The router still picks it (analyse §6.3) but the UI shows a
      "geen signaal-bron"-badge.
    * ``UNAVAILABLE_ABOVE_THRESHOLD`` / ``UNAVAILABLE_EXHAUSTED`` —
      the router fell through to the "laatste val-terug"; the
      caller should treat this as a degraded pick (a hard stop
      downstream — the per-provider pause — is what actually
      halts the spawn). The UI distinguishes the two for the
      reset-copy the operator sees.

    Side-effect-free: identical to ``pick_subscription_for_cli`` in
    that it does no I/O. The status is derived from the same
    ``usages`` snapshot dict the caller already supplied, so a test
    pinning the four-way case stays trivial to write.
    """
    cli_candidates = [e for e in entries if e.resolved_cli == cli_id]
    if not cli_candidates:
        return None

    chosen_entry: PoolEntry | None = None
    chosen_status: SignalStatus | None = None
    for entry in cli_candidates:
        usage = usages.get(f"{entry.resolved_cli}:{entry.provider}")
        if entry.provider in paused_providers:
            chosen_entry = entry
            chosen_status = SignalStatus.classify(entry, usage)
            continue
        status = SignalStatus.classify(entry, usage)
        if status in (
            SignalStatus.AVAILABLE_WITH_SIGNAL,
            SignalStatus.AVAILABLE_NO_SIGNAL,
        ):
            return PoolChoice(entry=entry, status=status)
        chosen_entry = entry
        chosen_status = status
    if chosen_entry is None or chosen_status is None:
        return None
    return PoolChoice(entry=chosen_entry, status=chosen_status)


def has_available_spillover(
    entries: list[PoolEntry],
    usages: dict[str, SubscriptionUsage],
    *,
    paused_providers: set[str],
    cli_id: str = DEFAULT_POOL_CLI,
) -> bool:
    """Fase 2 (analyse §4 Optie B / §5): is er nog een subscription om naar
    over te *spillen* wanneer het huidige abonnement zijn limiet raakt?

    Kaart 8f40d443…: the spillover check is now per-CLI. The router
    filters candidates on the dispatched ``cli_id`` so a spillover for
    an OpenCode-spawned card only considers OpenCode pool entries;
    a Codex-spawned card considers Codex entries. ``cli_id`` defaults
    to ``DEFAULT_POOL_CLI`` so legacy callers that don't know about
    CLIs yet (notably the per-provider pause's reactive limit path)
    keep their contract.

    Dit is de drempel-/failover-tak van de pool-router: de reactieve
    limiet-lus (``dispatch.move_limited_session_to_resume``) voegt de
    zojuist-gelimiteerde provider toe aan ``paused_providers`` en vraagt
    hier of er dán nog een échte val-terug is. "Echt beschikbaar" betekent
    de *schone* keuze-tak van ``pick_subscription_for_cli`` — een entry
    die niet gepauzeerd is én niet boven zijn drempel — niet louter de
    "laatste val-terug" (die geeft de router ook terug als álles
    uitgeput is, puur zodat de caller een deterministisch slot heeft).

    Returns:
        True  → er is een niet-gepauzeerde, onder-drempel subscription:
                de kaart kan meteen doorschuiven i.p.v. te wachten op de
                reset (analyse §2.3 "sluit de reactieve failover-lus").
        False → lege pool, geen entry van ``cli_id``, of elke subscription
                van die CLI is nu gepauzeerd/uitgeput: val terug op de
                bestaande per-provider pause (wachten tot reset).
    """
    chosen = pick_subscription_for_cli(
        entries, usages,
        paused_providers=paused_providers, cli_id=cli_id,
    )
    if chosen is None:
        return False
    if chosen.provider in paused_providers:
        # ``pick_subscription_for_cli`` viel terug op de "laatste
        # val-terug" — die provider is zelf gepauzeerd, dus er is geen
        # echte spillover-target.
        return False
    usage = usages.get(f"{chosen.resolved_cli}:{chosen.provider}")
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
    ``pick_subscription_for_cli``. Raises ``ValueError`` with a
    concrete message the API layer surfaces as 422.

    - Empty pool is rejected (use ``None`` to clear).
    - Each entry's provider must be on the allow-list.
    - Each entry's ``cli`` (after the ``DEFAULT_POOL_CLI`` default)
      must be on the agentic_cli registry's CLI allow-list — keeps
      the per-CLI quota axis from silently degrading on a typo.
    - ``drempel`` must be in ``(0, 1]`` — 0 would always be "above
      threshold" (silently disable the entry) and >1 disables the
      spillover entirely.
    """
    if not entries:
        raise ValueError("subscription pool must not be empty (use null to clear)")
    for entry in entries:
        if entry.provider not in _ALLOWED_POOL_PROVIDERS:
            raise ValueError(
                f"unknown provider: {entry.provider!r}; "
                f"expected one of {_ALLOWED_POOL_PROVIDERS}",
            )
        if entry.drempel <= 0 or entry.drempel > 1:
            raise ValueError(
                f"subscription pool entry.drempel must be in (0, 1]; "
                f"got {entry.drempel!r}",
            )


def _serialize_entries(entries: list[PoolEntry]) -> str:
    payload = [
        {
            "cli": e.resolved_cli,
            "provider": e.provider,
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
        # kaart 8f40d443…: ``cli`` is again a first-class field on
        # ``PoolEntry``. Pre-kaart-8f40d443 rows that lack a ``cli``
        # key (or whose ``cli`` is ``None``) get back-filled with
        # ``DEFAULT_POOL_CLI`` so the historical claude-code-only
        # pools keep working without manual surgery.
        cli = raw.get("cli") or DEFAULT_POOL_CLI
        provider = raw.get("provider")
        model = raw.get("model")
        drempel = raw.get("drempel")
        if not isinstance(provider, str):
            return None
        if model is not None and not isinstance(model, str):
            return None
        if not isinstance(drempel, (int, float)):
            return None
        out.append(PoolEntry(
            cli=cli, provider=provider,
            model=model, drempel=float(drempel),
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
