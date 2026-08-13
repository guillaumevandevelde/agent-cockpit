"""SubscriptionUsageProvider registry — one entry per (cli, provider) pair.

Mirrors ``services/agentic_cli/__init__.py``: a tiny in-process map
that the pool router (``dispatch._gather_pool_usage_snapshots``)
consults to find the right provider for each pool entry.

The registry is intentionally minimal — a function the caller awaits
that returns either the concrete provider or None. That keeps the
wiring discoverable (``get_provider_for`` is the only public symbol)
and lets the dispatcher stay defensive: an unwired ``{cli, provider}``
combination silently becomes "no signal → available", matching analyse
§6.3.
"""
from __future__ import annotations

from contextlib import contextmanager

from app.services.agentic_cli.provider_env import (
    OPEN_CODE_CLI_ID,
    PROVIDER_ANTHROPIC,
    PROVIDER_BEDROCK,
    PROVIDER_COMPATIBLE,
    PROVIDER_MINIMAX,
    PROVIDER_OPENCODE_GO,
    PROVIDER_OPENCODE_ZEN,
)
from app.services.subscriptions.base import SubscriptionUsageProvider
from app.services.subscriptions.router import RouterUsageProvider
from app.services.subscriptions.unknown import UnknownUsageProvider

# Concrete providers — one per (cli, provider) that has a real signal
# source. Pairs that don't appear here fall back to ``get_provider_for``
# returning ``None``, which the router treats as "no signal".
#
# ``claude-code:anthropic`` is upgraded from the stub to a real
# ``AnthropicUsageProvider`` by ``register_default_providers`` at app
# startup, unconditionally — it reads the 5h block from local session
# logs and needs no user configuration.
# ``claude-code:minimax`` stays on the ``UnknownUsageProvider``
# stub: ``MinimaxUsageProvider`` is fully implemented but has no
# confirmed usage/balance endpoint to probe yet (``probe_url`` is always
# ``None`` at its only call site, ``api/v1/subscriptions.py``) — wiring
# it into the registry today would just be a stub with extra steps, not
# a real signal. The dispatcher here does not own that wiring — it just
# asks the registry. If no concrete provider is registered for a
# (cli, provider) pair, the entry is skipped from the snapshot map
# (analyse §6.3).
#
# Populated at app startup via ``register_default_providers`` (kanban
# card ea7e038b… D2): a previously-empty registry meant
# ``get_provider_for`` always returned None even after the D1 await
# fix, so the drempel branch was structurally dead. The default
# registration uses ``UnknownUsageProvider`` (honest no-signal, no
# fabrication per analyse §6.1) — keeps the snapshot path "alive"
# without inventing data, so a later call to ``register_provider``
# with a real provider replaces the stub by id without ceremony.
#
# Kaart 390756e6... extends the seed with the
# ``claude-code:anthropic-compatible`` slot: 9router / LiteLLM
# eindpunten registeren als endpoint-rij onder ``anthropic-compatible``
# (zie ``agentic_cli/endpoints.py``), maar verborgen meerdere
# upstreams = geen betrouwbare quota-bron. De seed gebruikt
# ``RouterUsageProvider`` (specifiek ``bron`` met ``router_eindpunt``
# prefix) i.p.v. een generieke ``UnknownUsageProvider`` zodat de UI
# de context niet verliest tussen andere onzekere rijen.
_PROVIDERS: dict[str, SubscriptionUsageProvider] = {}


def register_provider(provider: SubscriptionUsageProvider) -> None:
    """Register a concrete ``SubscriptionUsageProvider`` keyed by its
    ``id`` (e.g. ``"claude-code:anthropic"``). Future calls to
    ``get_provider_for`` with the matching (cli, provider) will return
    this provider.

    Replaces any existing registration under the same ``id``. The
    registry has no thread/process guards — it is populated at app
    startup and read-only after that."""
    _PROVIDERS[provider.id] = provider


def _seedable_clis() -> tuple[tuple[str, str], ...]:
    """Return ``(cli_id, display_name)`` for every registered CLI adapter.

    Derived from the ``agentic_cli`` registry at call time — the same
    source of truth ``dispatch._known_cli_ids`` and
    ``subscription_pool._known_pool_clis`` use — so a newly registered
    CLI picks up its no-signal stubs on the next
    ``register_default_providers`` run without a second hardcoded list
    drifting out of sync.

    Falls back to the historical hardcoded baseline when the
    ``agentic_cli`` import fails, so a broken registry can never leave
    this one empty (that was the ea7e038b… D2 failure mode)."""
    try:
        from app.services.agentic_cli import get_agentic_clis
    except Exception:
        return (
            ("claude-code", "Claude Code"),
            ("codex-cli", "Codex CLI"),
            ("copilot-cli", "Copilot CLI"),
            ("mimo-code", "MiMo Code"),
            ("open-code", "OpenCode"),
        )
    return tuple(
        (cli.id, getattr(cli, "display_name", None) or cli.id)
        for cli in get_agentic_clis()
    )


def register_default_providers() -> None:
    """Populate the registry with honest no-signal stubs for the known
    ``(cli, provider)`` pairs every registered CLI supports today.

    Called once at app startup (``main.lifespan``) so the registry is
    never empty. Each stub is an ``UnknownUsageProvider`` — its
    ``get_usage()`` returns ``betrouwbaarheid="onbekend"``,
    ``drempel_gebruikt=None``, ``beschikbaar=True`` (analyse §6.3 "no
    fabrication"). The router treats these snapshots as
    "available until the per-provider pause catches them" — i.e. the
    exact pre-D2 behaviour, but the snapshot path now actually fires
    end-to-end instead of short-circuiting on ``get_provider_for ->
    None``.

    Kaart 8f40d443… (quota-pool CLI-agnostisch): the seed covers every
    **registered CLI**, not just ``claude-code``. The pool router
    discriminates per ``{cli, provider}`` (see
    ``subscription_pool.pick_subscription_for_cli``), so a pool entry
    for ``open-code:anthropic`` needs a row here — otherwise
    ``_gather_pool_usage_snapshots`` silently skips it and the operator
    cannot tell "this CLI has no quota source" from "this subscription
    is at 0% used". Both keep the entry eligible, but only the stub
    degrades *explicitly* (``betrouwbaarheid="onbekend"``, which
    ``SubscriptionUsageRowItem.tsx`` renders as an "Unknown" badge
    instead of a percentage).

    Idempotent: ``register_provider`` replaces by id, so a later call
    with a real ``AnthropicUsageProvider`` / ``MinimaxUsageProvider``
    (configured at runtime) cleanly takes over without code changes
    here. The default registration only seeds; it doesn't lock the
    registry in."""
    for cli_id, cli_label in _seedable_clis():
        for prov in (PROVIDER_ANTHROPIC, PROVIDER_BEDROCK, PROVIDER_MINIMAX):
            register_provider(UnknownUsageProvider(
                subscription_id=f"{cli_id}:{prov}",
                subscription_label=f"{cli_label} ({prov} — geen signaal-bron)",
            ))
        # ``claude-code:anthropic`` has a real local signal: the 5h block
        # summed from JSONL logs. Registering it here (over the stub just
        # seeded above) means the pool sees an absolute token count from
        # startup, with no user configuration step. It reports no ratio —
        # no published limit exists — so the pool treats it as available
        # and the per-provider rate-limit pause remains the real backstop.
        if cli_id == "claude-code":
            from app.services.subscriptions.anthropic import AnthropicUsageProvider
            from app.services.usage_service import UsageService
            register_provider(AnthropicUsageProvider(usage_service=UsageService()))
        # Kaart 390756e6... (router-eindpunt): ``anthropic-compatible`` is
        # de provider-id voor een data-driven eindpunt (zie
        # ``agentic_cli/endpoints.py`` — 9router / LiteLLM / etc. zijn
        # rijen). Een router verbergt meerdere upstreams achter één
        # endpoint, dus er is geen betrouwbare quota-bron — de seed is
        # een ``RouterUsageProvider`` (specifieke ``bron``) in plaats van
        # een generieke ``UnknownUsageProvider`` zodat de UI de
        # router-context niet verliest tussen andere onzekere rijen.
        #
        # Alleen voor claude-code: router-eindpunten zijn een
        # claude-code-concept (``agentic_cli/endpoints.py``). Voor de
        # overige CLI's blijft het een generieke
        # ``UnknownUsageProvider``, zodat de UI eerlijk "geen
        # signaal-bron" toont i.p.v. een onverdiende router-badge.
        if cli_id == "claude-code":
            register_provider(RouterUsageProvider(
                subscription_id=f"{cli_id}:{PROVIDER_COMPATIBLE}",
                subscription_label="Claude Code (Router — geen quota-bron)",
            ))
        else:
            register_provider(UnknownUsageProvider(
                subscription_id=f"{cli_id}:{PROVIDER_COMPATIBLE}",
                subscription_label=(
                    f"{cli_label} ({PROVIDER_COMPATIBLE} — geen signaal-bron)"
                ),
            ))
    # OpenCode's own hosted-subscription providers — built into the
    # OpenCode CLI's catalog. OpenCode publishes no authenticated
    # "remaining quota" surface for either Go (flat $10/mo budget) or
    # Zen (pay-as-you-go), so the honest answer for the pool router is
    # ``UnknownUsageProvider`` ("geen signaal — beschikbaar tot de
    # per-provider pause toeslaat"). Same discipline as the
    # ``claude-code:minimax`` stub: a future card with a real billing-API
    # signal can replace the entry by id without ceremony.
    for prov, label in (
        (PROVIDER_OPENCODE_GO, "OpenCode Go"),
        (PROVIDER_OPENCODE_ZEN, "OpenCode Zen"),
    ):
        register_provider(UnknownUsageProvider(
            subscription_id=f"{OPEN_CODE_CLI_ID}:{prov}",
            subscription_label=f"{label} (geen signaal-bron)",
        ))


def get_provider_for(
    *, cli: str, provider: str,
) -> SubscriptionUsageProvider | None:
    """Return the registered provider for ``(cli, provider)``, or None.

    ``None`` is the "no signal" answer: ``pick_subscription`` treats a
    missing snapshot as "always available until the per-provider pause
    catches it" (analyse §6.3).

    NOTE: synchronous. Awaiting it raises ``TypeError: object … can't
    be used in 'await' expression`` (kaart ea7e038b… D1 — the bug the
    dispatcher hit before the fix; the registry is intentionally sync
    to keep the lookup a ``dict.get``)."""
    return _PROVIDERS.get(f"{cli}:{provider}")


def get_unknown_provider(cli: str, provider: str) -> SubscriptionUsageProvider:
    """Return an ``UnknownUsageProvider`` shim for ``(cli, provider)``.

    Use only when the caller wants an explicit "no signal" provider
    (e.g. for the Subscriptions-pagina UI to render a placeholder row).
    The pool router uses ``get_provider_for`` (which may return None)
    instead — see the difference in the comment of
    ``_gather_pool_usage_snapshots``.
    """
    return UnknownUsageProvider(
        subscription_id=f"{cli}:{provider}",
        subscription_label=f"{cli} ({provider})",
    )


@contextmanager
def seeded_registry_for_tests():
    """Snapshot, clear, seed defaults, then restore — the canonical
    test-side mirror of ``main.lifespan``'s
    ``register_default_providers`` call.

    Self-improve kanban card 7a8788af... standardises the
    save/clear/seed/restore dance that was previously copy-pasted
    across ``tests/test_subscriptions_endpoint.py``,
    ``tests/test_subscription_prefs_service.py``,
    ``tests/test_subscription_usage_provider.py`` (its
    ``setup_method``/``teardown_method``) and the
    ``_registry_state`` contextmanager in
    ``tests/test_subscription_pool_dispatch.py``. Without this helper
    a future endpoint-test would copy the same dance again — and
    forget one of the four steps (usually the restore).

    Yield semantics — what a test sees while inside the ``with``:

    * the registry is empty and then seeded with the default
      providers (lifespan mirror — same lookup-table the production
      code reads),
    * ``get_provider_for(cli, provider)`` therefore resolves for
      every supported ``(cli, provider)`` pair (incl.
      ``anthropic-compatible`` → ``RouterUsageProvider``, kaart
      390756e6...).

    On exit (success *or* exception — the ``try/finally`` covers
    both), the registry is restored to whatever was registered
    before the context entered. That means:

    * tests that pre-seeded a custom fake get their fake back;
    * tests that ran on an empty registry (no lifespan fired under
      ``ASGITransport``) return to an empty registry;
    * tests that crashed mid-body don't leak the seeded defaults
      into the next test.

    Production code never calls this — it lives in the registry
    module so the registry owns the lifecycle of its own mutable
    state, and so a future contributor who adds a new mutable
    registry only has to update one file. Endpoint tests that need
    the realistic lifespan state (every ``ASGITransport``-based
    test in particular, since that client never triggers
    ``lifespan``) wrap this helper in a one-line autouse fixture —
    see ``tests/test_subscriptions_endpoint.py::_seed_registry``
    for the canonical pattern.
    """
    saved = dict(_PROVIDERS)
    _PROVIDERS.clear()
    try:
        register_default_providers()
        # Yield the module so callers can call ``reg_ctx.get_provider_for(...)``
        # without re-importing — keeps the helper self-contained. The
        # module has no public init, so re-binding here is fine.
        import app.services.subscriptions.registry as _reg_module
        yield _reg_module
    finally:
        _PROVIDERS.clear()
        _PROVIDERS.update(saved)


@contextmanager
def cleared_registry_for_tests():
    """Snapshot, clear, then restore — for tests that register exactly
    what they need without the lifespan-mirror noise.

    Sibling to ``seeded_registry_for_tests``: tests that want to
    register exactly what they need without the lifespan-mirror
    noise use this — a real ``AnthropicUsageProvider`` registered
    by the test body is the only row visible. The helper preserves
    that "register your own" shape without re-implementing the
    save/restore dance. Used by
    ``test_subscription_prefs_service::_isolated_registry`` and
    ``test_subscription_usage_provider::_isolated_registry`` (the
    latter replacing the older ``setup_method``/``teardown_method``
    pair).
    """
    saved = dict(_PROVIDERS)
    _PROVIDERS.clear()
    try:
        import app.services.subscriptions.registry as _reg_module
        yield _reg_module
    finally:
        _PROVIDERS.clear()
        _PROVIDERS.update(saved)