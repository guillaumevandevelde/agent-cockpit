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

from app.services.subscriptions.base import SubscriptionUsageProvider
from app.services.subscriptions.unknown import UnknownUsageProvider

# Concrete providers — one per (cli, provider) that has a real signal
# source. Pairs that don't appear here fall back to ``get_provider_for``
# returning ``None``, which the router treats as "no signal".
#
# ``claude-code:anthropic`` + ``claude-code:minimax`` are wired as
# concrete providers in the kanban DB hooks elsewhere (see
# ``subscriptions_meta`` / ``subscription_prefs`` for the per-project
# Anthropic plan-tier + MiniMax credentials). The dispatcher here does
# not own that wiring — it just asks the registry. If no concrete
# provider is registered for a (cli, provider) pair, the entry is
# skipped from the snapshot map (analyse §6.3).
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


def get_provider_for(
    *, cli: str, provider: str,
) -> SubscriptionUsageProvider | None:
    """Return the registered provider for ``(cli, provider)``, or None.

    ``None`` is the "no signal" answer: ``pick_subscription`` treats a
    missing snapshot as "always available until the per-provider pause
    catches it" (analyse §6.3).
    """
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