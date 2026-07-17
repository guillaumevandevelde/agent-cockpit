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

from app.services.agentic_cli.provider_env import (
    PROVIDER_ANTHROPIC,
    PROVIDER_BEDROCK,
    PROVIDER_MINIMAX,
)
from app.services.subscriptions.base import SubscriptionUsageProvider
from app.services.subscriptions.unknown import UnknownUsageProvider

# Concrete providers — one per (cli, provider) that has a real signal
# source. Pairs that don't appear here fall back to ``get_provider_for``
# returning ``None``, which the router treats as "no signal".
#
# ``claude-code:anthropic`` is upgraded from the stub to a real
# ``AnthropicUsageProvider`` by
# ``subscription_prefs_service.sync_anthropic_provider_registration``
# (kaart d404a11f...) once the user has configured a plan tier — called
# at app startup and again on every ``PUT /subscriptions/anthropic/plan
# -tier``. ``claude-code:minimax`` stays on the ``UnknownUsageProvider``
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
# with a real ``AnthropicUsageProvider`` (e.g. once a plan-tier is
# configured) replaces the stub by id without ceremony.
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


def register_default_providers() -> None:
    """Populate the registry with honest no-signal stubs for the known
    ``(cli, provider)`` pairs Claude Code supports today.

    Called once at app startup (``main.lifespan``) so the registry is
    never empty. Each stub is an ``UnknownUsageProvider`` — its
    ``get_usage()`` returns ``betrouwbaarheid="onbekend"``,
    ``drempel_gebruikt=None``, ``beschikbaar=True`` (analyse §6.3 "no
    fabrication"). The router treats these snapshots as
    "available until the per-provider pause catches them" — i.e. the
    exact pre-D2 behaviour, but the snapshot path now actually fires
    end-to-end instead of short-circuiting on ``get_provider_for ->
    None``.

    Idempotent: ``register_provider`` replaces by id, so a later call
    with a real ``AnthropicUsageProvider`` / ``MinimaxUsageProvider``
    (configured at runtime) cleanly takes over without code changes
    here. The default registration only seeds; it doesn't lock the
    registry in."""
    for prov in (PROVIDER_ANTHROPIC, PROVIDER_BEDROCK, PROVIDER_MINIMAX):
        register_provider(UnknownUsageProvider(
            subscription_id=f"claude-code:{prov}",
            subscription_label=f"Claude Code ({prov} — geen signaal-bron)",
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