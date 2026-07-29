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

from app.kanban.models import KanbanMeta
from app.services.agentic_cli.provider_env import (
    PROVIDER_ANTHROPIC,
    PROVIDER_BEDROCK,
    PROVIDER_COMPATIBLE,
    PROVIDER_MINIMAX,
    PROVIDER_OPENCODE_GO,
    PROVIDER_OPENCODE_ZEN,
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
    PROVIDER_OPENCODE_GO, PROVIDER_OPENCODE_ZEN,
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
            (``"anthropic"`` | ``"bedrock"`` | ``"minimax"`` |
            ``"anthropic-compatible"``).
        model: optional model pin. ``None`` = no model pin — dispatch
            falls through to the column/card/persona precedence chain.
            This mirrors the partial-override shape of the existing
            per-card ``column_overrides[col].model``.
        endpoint_name: optional endpoint slug from the project-scoped
            endpoint registry (``services.agentic_cli.endpoints``).
            Required when ``provider="anthropic-compatible"``: the
            dispatch path resolves the name to ``base_url`` +
            ``auth_token`` so ``build_provider_env`` always receives a
            non-empty URL. ``None`` for every other provider — the
            field is part of the JSON wire shape so legacy rows that
            predate this card round-trip as null and keep working.
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
    endpoint_name: str | None = None

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
    - For ``provider="anthropic-compatible"`` the entry must carry a
      non-empty ``endpoint_name`` and that name must resolve to a
      row in the project's endpoint registry (kaart 293d1faa…:
      fail-fast at storage so a misconfigured pool cannot loop the
      dispatcher through ``MAX_DISPATCH_FAILURES`` before the card
      lands in Impediment).
    - ``drempel`` must be in ``(0, 1]`` — 0 would always be "above
      threshold" (silently disable the entry) and >1 disables the
      spillover entirely.

    NOTE: the endpoint existence check is done by the storage caller
    (``set_subscription_pool``) which owns the project-keyed session —
    see ``_validate_compatible_endpoint`` there — because the pure
    function above has no DB access by design.
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
        if entry.provider == PROVIDER_COMPATIBLE and not entry.endpoint_name:
            raise ValueError(
                "anthropic-compatible pool entry requires endpoint_name; "
                "configure it via /api/v1/agent-bridge/platforms/endpoints",
            )


def _serialize_entries(entries: list[PoolEntry]) -> str:
    payload = [
        {
            "cli": e.resolved_cli,
            "provider": e.provider,
            "model": e.model, "drempel": e.drempel,
            "endpoint_name": e.endpoint_name,
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
        # ``endpoint_name`` is optional (None) for non-compatible
        # providers. kaart 293d1faa… introduced it as part of the
        # JSON carrier; legacy rows (pre-this-card) lack it and
        # deserialise cleanly as None.
        endpoint_name = raw.get("endpoint_name")
        if endpoint_name is not None and not isinstance(endpoint_name, str):
            return None
        if not isinstance(provider, str):
            return None
        if model is not None and not isinstance(model, str):
            return None
        if not isinstance(drempel, (int, float)):
            return None
        out.append(PoolEntry(
            cli=cli, provider=provider,
            model=model, drempel=float(drempel),
            endpoint_name=endpoint_name,
        ))
    return out


async def get_subscription_pool(
    session, project_key: str,
    *,
    column: str | None = None,
) -> list[PoolEntry] | None:
    """Return the subscription pool for ``project_key``, or None when no
    pool is configured.

    None means "fall through to today's dispatch behaviour" — the
    column-default chain stays authoritative. This is the
    backward-compat clause from the acceptance criteria.

    Kaart b36ca702…: with ``column`` set, the read first consults the
    column-specific row (``subscription_pool:<project_key>:<column>``)
    and falls back to the board-wide row when no column-specific row
    exists. An explicitly empty column-specific row (operator-set
    ``[]``) is a valid value and reads back as ``[]`` — distinct from
    "no row", which inherits the board-wide pool. ``column=None`` (the
    default) keeps the legacy board-wide-only read.
    """
    if column:
        col_key = f"{SUBSCRIPTION_POOL_PREFIX}{project_key}:{column}"
        col_row = await session.get(KanbanMeta, col_key)
        if col_row is not None:
            return _deserialize_column_entries(col_row.value)
    row = await session.get(
        KanbanMeta, SUBSCRIPTION_POOL_PREFIX + project_key,
    )
    if row is None:
        return None
    return _deserialize_entries(row.value)


async def set_subscription_pool(
    session, project_key: str,
    entries: list[PoolEntry] | None,
    *,
    column: str | None = None,
) -> None:
    """Persist (or clear, when ``None``) the subscription pool.

    Validates ``entries`` before storage; an invalid pool raises
    ``ValueError`` so the caller surfaces a 422 instead of writing a
    row that the dispatcher would then refuse to honour. Storing
    ``None`` deletes the row entirely so a follow-up read sees no pool
    and falls through to the column-default precedence — keeping the
    "unset = exact pre-feature behaviour" contract testable.

    kaart 293d1faa…: when an entry's provider is
    ``"anthropic-compatible"`` and ``endpoint_name`` is present, the
    name is checked against the project's endpoint registry (same
    helper the dispatch path uses) so a misconfigured pool is rejected
    at save time, not at spawn time. The pure ``_validate_entries``
    checks the absent / shape layer; this side-effect check makes sure
    the named endpoint actually exists.

    Kaart b36ca702…: with ``column`` set, the row is written under
    ``subscription_pool:<project_key>:<column>`` instead of the
    board-wide key. The per-column validator accepts an empty list
    ("nooit uitwijken") because an operator-set empty tail is a
    distinct, deliberate choice — it must be preserved as ``[]`` and
    not silently fall back to the board-wide pool. Storing ``None``
    deletes the column-specific row so the column inherits the
    board-wide pool again. ``column=None`` (default) keeps the legacy
    board-wide write semantics, including the "empty list rejected"
    rule that protects the UI from accidentally turning the dispatcher
    into a no-op while the row still shows the operator's last saved
    pool (see ``_validate_entries``).
    """
    if column:
        key = f"{SUBSCRIPTION_POOL_PREFIX}{project_key}:{column}"
    else:
        key = SUBSCRIPTION_POOL_PREFIX + project_key
    if entries is None:
        row = await session.get(KanbanMeta, key)
        if row is not None:
            await session.delete(row)
            await session.flush()
        return
    if column:
        # Per-column: empty list is a deliberate "nooit uitwijken"
        # choice and must round-trip as ``[]``. The board-wide path
        # still rejects empty lists (see ``_validate_entries``) so a
        # handler cannot accidentally turn the dispatcher into a
        # no-op while the row still shows the last saved board-wide
        # pool. The two paths are intentionally asymmetric: a UI that
        # forgets to clear its tail on a column is much less harmful
        # than a UI that forgets to clear its pool globally.
        _validate_column_entries(entries)
    else:
        _validate_entries(entries)
    # Fail-fast at storage: every anthropic-compatible entry's
    # endpoint_name must point at a registered endpoint. Done before
    # serialization so the error message names the project+endpoint
    # combo the operator is trying to wire up.
    for entry in entries:
        if entry.provider == PROVIDER_COMPATIBLE and entry.endpoint_name:
            from app.services.agentic_cli.endpoints import (
                get_endpoint as _get_endpoint,
            )
            from app.services.agentic_cli.endpoints import (
                resolve_compatible_endpoint as _resolve_compatible_endpoint,
            )
            endpoint = await _get_endpoint(
                session, project_key, entry.endpoint_name,
            )
            if endpoint is None:
                raise ValueError(
                    f"endpoint {entry.endpoint_name!r} is not registered "
                    f"for project {project_key!r}; configure it via "
                    f"/api/v1/agent-bridge/platforms/endpoints",
                )
            # kaart 27317b4871… (FCR gap 5): also exercise the
            # credential-resolution path so a registered endpoint whose
            # ``credential_name='minimax'`` lacks the matching API key
            # surfaces the error at save time, not after three retries
            # of the dispatch loop. Mirrors the
            # ``set_active_subscription_override`` change so both
            # save-carriers fail-fast through the same resolver.
            await _resolve_compatible_endpoint(
                session, project_key, entry.endpoint_name,
            )
    value = _serialize_entries(entries)
    row = await session.get(KanbanMeta, key)
    if row is None:
        session.add(KanbanMeta(key=key, value=value))
    else:
        row.value = value
    await session.flush()


def _validate_column_entries(entries: list[PoolEntry]) -> None:
    """Validate a per-column tail (kaart b36ca702…).

    Differs from ``_validate_entries`` in exactly one respect: an empty
    list is *valid*. The operator-set empty tail is the "nooit
    uitwijken" sentinel — a deliberate, reviewable choice that must be
    preserved verbatim through the JSON round-trip. Per-entry rules
    (provider allow-list, drempel range, compatible-with-endpoint)
    remain strict so a per-column tail can never smuggle in a typo'd
    provider; the asymmetry is only on the empty-list branch.
    """
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
        if entry.provider == PROVIDER_COMPATIBLE and not entry.endpoint_name:
            raise ValueError(
                "anthropic-compatible pool entry requires endpoint_name; "
                "configure it via /api/v1/agent-bridge/platforms/endpoints",
            )


def _deserialize_column_entries(value: str) -> list[PoolEntry] | None:
    """Deserialize a per-column pool row.

    Differs from ``_deserialize_entries`` in exactly one respect: an
    empty JSON list decodes to ``[]`` (the operator's "nooit
    uitwijken" sentinel), NOT ``None`` (which the board-wide
    deserialiser uses as "no row"). Per-entry shape checks remain the
    same so a corrupt row still degrades gracefully to ``None``.
    """
    import json as _json
    try:
        parsed = _json.loads(value)
    except (TypeError, ValueError):
        logger.warning("corrupt subscription_pool column row; ignoring")
        return None
    if not isinstance(parsed, list):
        return None
    # Per-column: empty list is a deliberate operator choice, not a
    # corrupt row. Surface it verbatim so the dispatch path can
    # distinguish "no tail" from "explicit empty tail".
    if not parsed:
        return []
    out: list[PoolEntry] = []
    for raw in parsed:
        if not isinstance(raw, dict):
            return None
        cli = raw.get("cli") or DEFAULT_POOL_CLI
        provider = raw.get("provider")
        model = raw.get("model")
        drempel = raw.get("drempel")
        endpoint_name = raw.get("endpoint_name")
        if endpoint_name is not None and not isinstance(endpoint_name, str):
            return None
        if not isinstance(provider, str):
            return None
        if model is not None and not isinstance(model, str):
            return None
        if not isinstance(drempel, (int, float)):
            return None
        out.append(PoolEntry(
            cli=cli, provider=provider,
            model=model, drempel=float(drempel),
            endpoint_name=endpoint_name,
        ))
    return out
