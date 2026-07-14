"""Tests for the pure ``pick_subscription()`` router (fase 1b).

Drives ``docs/cockpit/subscription-flexibiliteit-analyse.md`` §4 (Optie A) / §5
fase 1b / §8 #3: usage-aware dispatch on an ordered pool. The router is a
**pure function** — pool config + per-subscription usage snapshot in,
chosen subscription out (or None when the pool is empty / fully exhausted).
That contract is what the dispatch integration consumes; these tests pin it
without involving the kanban DB.

What the contract enforces (one test per edge case so a future refactor
can't silently invert any branch):

- entries are scanned in priority order (the order the user configured);
- a paused provider is skipped — falls through to the next entry;
- a subscription above its per-subscription drempel is skipped;
- a subscription without a usage signal (beschikbaar=True, onbekend)
  counts as available until the per-provider pause catches it (analyse
  §6.3 — the router must NOT refuse to dispatch a Codex card just because
  Codex has no usage signal today);
- when every entry is skipped, return the **last** entry as the fallback
  (analyse §4: "laatste val-terug") so dispatch still has a target — the
  pause / per-provider hard stop is what actually halts the spawn elsewhere;
- an empty pool returns None — there is nothing to pick.
"""
from __future__ import annotations

from app.services.subscriptions.base import SubscriptionUsage
from app.kanban.subscription_pool import (
    PoolEntry,
    has_available_spillover,
    pick_subscription,
)


def _entry(*, cli="claude-code", provider="anthropic", model=None, drempel=0.9):
    """Shorthand for a pool entry — the keys users actually care about."""
    return PoolEntry(cli=cli, provider=provider, model=model, drempel=drempel)


def _usage(*, subscription_id, beschikbaar=True, drempel_gebruikt=None,
           betrouwbaarheid="onbekend"):
    """Shorthand snapshot — same shape as ``SubscriptionUsage`` so the
    router can be tested without a real provider on the other end."""
    return SubscriptionUsage(
        subscription_id=subscription_id,
        subscription_label=subscription_id,
        beschikbaar=beschikbaar,
        drempel_gebruikt=drempel_gebruikt,
        bron="test",
        betrouwbaarheid=betrouwbaarheid,
    )


class TestPickSubscriptionPriority:
    """First matching entry in configured order wins."""

    def test_first_entry_under_threshold_is_chosen(self):
        entries = [_entry(provider="anthropic"), _entry(provider="minimax")]
        usages = {
            "claude-code:anthropic": _usage(
                subscription_id="claude-code:anthropic",
                drempel_gebruikt=0.3,
            ),
            "claude-code:minimax": _usage(
                subscription_id="claude-code:minimax",
                drempel_gebruikt=0.1,
            ),
        }
        chosen = pick_subscription(entries, usages, paused_providers=set())
        assert chosen is not None
        assert chosen.provider == "anthropic"

    def test_first_entry_at_or_above_threshold_falls_through(self):
        """Anthropic above the drempel → spill to MiniMax."""
        entries = [_entry(provider="anthropic"), _entry(provider="minimax")]
        usages = {
            "claude-code:anthropic": _usage(
                subscription_id="claude-code:anthropic",
                drempel_gebruikt=0.95,
            ),
            "claude-code:minimax": _usage(
                subscription_id="claude-code:minimax",
                drempel_gebruikt=0.1,
            ),
        }
        chosen = pick_subscription(entries, usages, paused_providers=set())
        assert chosen is not None
        assert chosen.provider == "minimax"

    def test_first_entry_paused_falls_through_to_next(self):
        entries = [_entry(provider="anthropic"), _entry(provider="minimax")]
        usages = {
            "claude-code:anthropic": _usage(
                subscription_id="claude-code:anthropic",
                drempel_gebruikt=0.1,
            ),
            "claude-code:minimax": _usage(
                subscription_id="claude-code:minimax",
                drempel_gebruikt=0.1,
            ),
        }
        chosen = pick_subscription(entries, usages, paused_providers={"anthropic"})
        assert chosen is not None
        assert chosen.provider == "minimax"

    def test_first_entry_explicitly_unavailable_falls_through(self):
        """Provider returned ``beschikbaar=False`` (hard limit hit) → skip."""
        entries = [_entry(provider="anthropic"), _entry(provider="minimax")]
        usages = {
            "claude-code:anthropic": _usage(
                subscription_id="claude-code:anthropic",
                beschikbaar=False,
                drempel_gebruikt=1.2,
            ),
            "claude-code:minimax": _usage(
                subscription_id="claude-code:minimax",
                drempel_gebruikt=0.1,
            ),
        }
        chosen = pick_subscription(entries, usages, paused_providers=set())
        assert chosen is not None
        assert chosen.provider == "minimax"


class TestPickSubscriptionFallback:
    """When every entry is exhausted, return the last as fallback (analyse §4)."""

    def test_all_above_threshold_returns_last_entry(self):
        """All over drempel → last entry as 'laatste val-terug'.

        Matches analyse §4: 'eerste subscription in prioriteitsvolgorde die
        nog onder z'n drempel zit, en zakt anders door. ... alles vol →
        laatste val-terug of pause.' We pick last as the last resort so the
        per-provider pause has one specific slot to actually pause (if even
        that is over drempel, the pause is the next-line gate, not ours)."""
        entries = [_entry(provider="anthropic"), _entry(provider="minimax")]
        usages = {
            "claude-code:anthropic": _usage(
                subscription_id="claude-code:anthropic",
                drempel_gebruikt=0.95,
            ),
            "claude-code:minimax": _usage(
                subscription_id="claude-code:minimax",
                drempel_gebruikt=0.95,
            ),
        }
        chosen = pick_subscription(entries, usages, paused_providers=set())
        assert chosen is not None
        assert chosen.provider == "minimax"

    def test_all_paused_returns_last_entry(self):
        """Every provider paused → last entry as fallback.

        The per-provider pause check at dispatch time is what actually
        halts the spawn when even the fallback is paused; the router stays
        deterministic and 'pick last' so the caller can log a clear
        'fallback chosen; provider paused' if it wants to."""
        entries = [_entry(provider="anthropic"), _entry(provider="minimax")]
        chosen = pick_subscription(
            entries, usages={},
            paused_providers={"anthropic", "minimax"},
        )
        assert chosen is not None
        assert chosen.provider == "minimax"

    def test_empty_pool_returns_none(self):
        """No entries → no decision the router can make."""
        assert pick_subscription([], usages={}, paused_providers=set()) is None

    def test_single_entry_above_threshold_is_still_returned(self):
        """A 1-entry pool: above-threshold still returns the entry (we never
        return None when there is at least one entry — the caller decides)."""
        entries = [_entry(provider="anthropic")]
        usages = {
            "claude-code:anthropic": _usage(
                subscription_id="claude-code:anthropic",
                drempel_gebruikt=0.95,
            ),
        }
        chosen = pick_subscription(entries, usages, paused_providers=set())
        assert chosen is not None
        assert chosen.provider == "anthropic"


class TestPickSubscriptionNoSignal:
    """Analyse §6.3: a subscription without a usage signal is treated as
    'available until the per-provider pause catches it'. The router must
    NOT refuse to dispatch a Codex card just because Codex has no usage
    signal today."""

    def test_unknown_signal_does_not_block_dispatch(self):
        """Subscription without signal (drempel_gebruikt=None) is usable."""
        entries = [_entry(provider="anthropic", drempel=0.9),
                   _entry(provider="codex", drempel=0.9)]
        usages = {
            # Anthropic: above drempel → skip
            "claude-code:anthropic": _usage(
                subscription_id="claude-code:anthropic",
                drempel_gebruikt=0.95,
            ),
            # Codex: NO signal (drempel_gebruikt=None, betrouwbaarheid=onbekend)
            "codex-cli:codex": _usage(
                subscription_id="codex-cli:codex",
                beschikbaar=True,
                drempel_gebruikt=None,
                betrouwbaarheid="onbekend",
            ),
        }
        chosen = pick_subscription(entries, usages, paused_providers=set())
        assert chosen is not None
        assert chosen.provider == "codex"

    def test_missing_usage_snapshot_for_entry_does_not_block(self):
        """Even when no snapshot exists at all for a subscription, the
        router treats it as available (no signal = analyse §6.3)."""
        entries = [_entry(provider="anthropic", drempel=0.9),
                   _entry(provider="minimax", drempel=0.9)]
        # No usages passed at all.
        chosen = pick_subscription(entries, usages={}, paused_providers=set())
        assert chosen is not None
        # First wins because there's no signal to overrule it.
        assert chosen.provider == "anthropic"


class TestPickSubscriptionEntryShape:
    """Return value shape matches the existing dispatch injection point
    (cli / provider / model)."""

    def test_returns_cli_provider_model(self):
        entries = [PoolEntry(cli="claude-code", provider="anthropic",
                             model="opus", drempel=0.9)]
        chosen = pick_subscription(
            entries, usages={}, paused_providers=set(),
        )
        assert chosen is not None
        assert chosen.cli == "claude-code"
        assert chosen.provider == "anthropic"
        assert chosen.model == "opus"

    def test_model_none_falls_through_to_dispatch_chain(self):
        """``model=None`` on the entry means 'no model pin' — dispatch
        will fall through to column/card/persona precedence."""
        entries = [_entry(model=None)]
        chosen = pick_subscription(
            entries, usages={}, paused_providers=set(),
        )
        assert chosen is not None
        assert chosen.model is None


class TestHasAvailableSpillover:
    """Fase 2 (analyse §4 Optie B / §5): the threshold-/failover branch of
    the pool router. When a subscription hits its limit the reactive path
    marks its provider paused and asks whether the pool still offers a
    genuinely-available subscription to spill to (True) or every entry is
    now exhausted and the card must wait for a reset (False)."""

    def test_spillover_available_when_next_entry_free(self):
        """Anthropic just hit its limit (paused) → MiniMax is still free →
        spill over instead of waiting."""
        entries = [_entry(provider="anthropic"), _entry(provider="minimax")]
        assert has_available_spillover(
            entries, usages={}, paused_providers={"anthropic"},
        ) is True

    def test_no_spillover_when_all_paused(self):
        """Every provider paused → nothing to spill to → wait for reset."""
        entries = [_entry(provider="anthropic"), _entry(provider="minimax")]
        assert has_available_spillover(
            entries, usages={}, paused_providers={"anthropic", "minimax"},
        ) is False

    def test_no_spillover_when_only_free_entry_is_above_threshold(self):
        """The one non-paused entry is estimated full (above drempel) → it
        is not a real spillover target (would just re-hit a wall)."""
        entries = [_entry(provider="anthropic"), _entry(provider="minimax")]
        usages = {
            "claude-code:minimax": _usage(
                subscription_id="claude-code:minimax",
                drempel_gebruikt=0.95,
            ),
        }
        assert has_available_spillover(
            entries, usages, paused_providers={"anthropic"},
        ) is False

    def test_no_spillover_for_empty_pool(self):
        """No pool configured → no spillover; caller keeps reset-time pause."""
        assert has_available_spillover(
            [], usages={}, paused_providers={"anthropic"},
        ) is False

    def test_no_spillover_for_single_exhausted_entry(self):
        """A 1-entry pool whose only provider just hit its limit has no
        fallback — even though ``pick_subscription`` returns that entry as
        its deterministic 'laatste val-terug', it is paused, so no spill."""
        entries = [_entry(provider="anthropic")]
        assert has_available_spillover(
            entries, usages={}, paused_providers={"anthropic"},
        ) is False

    def test_spillover_uses_priority_order(self):
        """With the limited provider paused, the first *remaining* under-
        threshold entry counts as the spillover target."""
        entries = [
            _entry(provider="anthropic"),
            _entry(provider="minimax"),
            _entry(provider="bedrock"),
        ]
        assert has_available_spillover(
            entries, usages={}, paused_providers={"anthropic"},
        ) is True
