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

from app.kanban.subscription_pool import (
    DEFAULT_POOL_CLI,
    PoolEntry,
    SignalStatus,
    has_available_spillover,
    pick_subscription,
    pick_subscription_for_cli,
    pick_subscription_with_status,
)
from app.services.subscriptions.base import SubscriptionUsage


def _entry(*, provider="anthropic", model=None, drempel=0.9, cli=None):
    """Shorthand for a pool entry — the keys users actually care about.

    The ``cli`` kwarg is back (kaart 8f40d443…): a per-card dispatched
    CLI may differ from the board-wide default (e.g. an OpenCode
    session dispatched against an open-code-anchored entry), so the
    router must discriminate on ``cli`` to honour the per-CLI quota
    axis. ``cli=None`` falls back to ``DEFAULT_POOL_CLI`` so the
    vast-majority claude-code rows still build without ceremony.
    """
    if cli is None:
        return PoolEntry(provider=provider, model=model, drempel=drempel)
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
            f"{DEFAULT_POOL_CLI}:anthropic": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:anthropic",
                drempel_gebruikt=0.3,
            ),
            f"{DEFAULT_POOL_CLI}:minimax": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:minimax",
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
            f"{DEFAULT_POOL_CLI}:anthropic": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:anthropic",
                drempel_gebruikt=0.95,
            ),
            f"{DEFAULT_POOL_CLI}:minimax": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:minimax",
                drempel_gebruikt=0.1,
            ),
        }
        chosen = pick_subscription(entries, usages, paused_providers=set())
        assert chosen is not None
        assert chosen.provider == "minimax"

    def test_first_entry_paused_falls_through_to_next(self):
        entries = [_entry(provider="anthropic"), _entry(provider="minimax")]
        usages = {
            f"{DEFAULT_POOL_CLI}:anthropic": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:anthropic",
                drempel_gebruikt=0.1,
            ),
            f"{DEFAULT_POOL_CLI}:minimax": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:minimax",
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
            f"{DEFAULT_POOL_CLI}:anthropic": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:anthropic",
                beschikbaar=False,
                drempel_gebruikt=1.2,
            ),
            f"{DEFAULT_POOL_CLI}:minimax": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:minimax",
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
            f"{DEFAULT_POOL_CLI}:anthropic": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:anthropic",
                drempel_gebruikt=0.95,
            ),
            f"{DEFAULT_POOL_CLI}:minimax": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:minimax",
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
            f"{DEFAULT_POOL_CLI}:anthropic": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:anthropic",
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
        """Subscription without signal (drempel_gebruikt=None) is usable.

        Note: the snapshot-lookup key is now ``f"{POOL_CLI}:{provider}"``
        (i.e. ``claude-code:codex`` here) since the pool always routes
        through the single supported CLI (card 0b3ad6e2… / analysis
        §3 D3). ``codex`` as a provider value stands in for "any
        non-claude vendor with no live signal" in this unit test — the
        storage layer's allow-list is the gate that would reject it in
        practice; ``pick_subscription`` itself just exercises the no-
        signal branch."""
        entries = [_entry(provider="anthropic", drempel=0.9),
                   _entry(provider="codex", drempel=0.9)]
        usages = {
            # Anthropic: above drempel → skip
            f"{DEFAULT_POOL_CLI}:anthropic": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:anthropic",
                drempel_gebruikt=0.95,
            ),
            # No entry for codex → treated as "no signal"
            # (the key would now be ``claude-code:codex`` under the
            # POOL_CLI-based lookup; absent means "available until the
            # per-provider pause catches it" per analyse §6.3).
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
    (provider / model — the legacy ``cli`` field was dropped in card
    0b3ad6e2… / analysis §3 D3)."""

    def test_returns_provider_model(self):
        entries = [PoolEntry(provider="anthropic",
                             model="opus", drempel=0.9)]
        chosen = pick_subscription(
            entries, usages={}, paused_providers=set(),
        )
        assert chosen is not None
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
            f"{DEFAULT_POOL_CLI}:minimax": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:minimax",
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


class TestPickSubscriptionCliAware:
    """Kaart 8f40d443… — the pool must discriminate on ``cli``.

    The earlier pool was board-wide pinned to ``POOL_CLI =
    'claude-code'``, so four of the five registered CLI adapters
    (open-code, codex-cli, copilot-cli, mimo-code) dispatched outside
    every quota gate. The router now consumes the per-entry ``cli``
    field (re-introduced with explicit consumption) so a snapshot under
    e.g. ``open-code:anthropic`` is consulted only by entries whose
    ``cli='open-code'``.

    The acceptance criterion "if ``PoolEntry.cli`` returns, it is
    consumed" is enforced by the router accepting a ``cli_id`` arg and
    filtering entries on it — without that filter a single
    ``claude-code:anthropic`` snapshot would satisfy an ``open-code``
    entry, which is the exact pitfall (kaart 0b3ad6e2…)."""

    def test_entry_with_cli_consults_only_match_snapshot(self):
        """An ``open-code:anthropic`` entry looks up under
        ``open-code:anthropic`` — a ``claude-code:anthropic`` snapshot
        registered for the same provider does NOT count as a signal for
        the open-code entry (different subscription identity: analyse §3
        {cli, provider})."""
        entry = _entry(cli="open-code", provider="anthropic")
        usages = {
            "claude-code:anthropic": _usage(
                subscription_id="claude-code:anthropic",
                drempel_gebruikt=0.5,
            ),
            "open-code:anthropic": _usage(
                subscription_id="open-code:anthropic",
                drempel_gebruikt=0.95,
            ),
        }
        chosen = pick_subscription_for_cli(
            [entry], usages, paused_providers=set(),
            cli_id="open-code",
        )
        assert chosen is not None
        assert chosen.cli == "open-code"
        assert chosen.provider == "anthropic"
        # The router consulted open-code:anthropic (above threshold
        # 0.9), so the entry falls through — the pool returns it as the
        # 'laatste val-terug'. The fact that it falls through (instead
        # of being silently treated as available because of the
        # claude-code:anthropic snapshot) proves the cli filter ran.

    def test_default_cli_entry_uses_default_snapshot_key(self):
        """``cli=None`` (the common case for legacy claude-code pools)
        defaults to ``DEFAULT_POOL_CLI`` — the existing snapshot key
        ``claude-code:anthropic`` still matches. Regressietest for the
        acceptatie-criterium 'bestaand claude-code-gedrag ongewijzigd'."""
        entries = [_entry(provider="anthropic", cli=None)]
        usages = {
            f"{DEFAULT_POOL_CLI}:anthropic": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:anthropic",
                drempel_gebruikt=0.3,
            ),
        }
        chosen = pick_subscription(
            entries, usages, paused_providers=set(),
        )
        assert chosen is not None
        assert chosen.cli == DEFAULT_POOL_CLI
        assert chosen.provider == "anthropic"

    def test_mixed_cli_pool_isolates_signals(self):
        """Pool contains both claude-code and open-code entries; each
        one looks up under its own ``{cli}:{provider}`` key, ignoring
        snapshots keyed for the other CLI. This is the core acceptance
        criterion — without it the quota-axis mismatch returns
        silently."""
        entries = [
            _entry(cli="claude-code", provider="anthropic", drempel=0.9),
            _entry(cli="open-code", provider="anthropic", drempel=0.9),
        ]
        usages = {
            # claude-code:anthropic above drempel (skip)…
            f"{DEFAULT_POOL_CLI}:anthropic": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:anthropic",
                drempel_gebruikt=0.95,
            ),
            # …but open-code:anthropic is fine. The router picks the
            # second entry, keyed under open-code — the only snapshot
            # whose CLI matches.
            "open-code:anthropic": _usage(
                subscription_id="open-code:anthropic",
                drempel_gebruikt=0.1,
            ),
        }
        chosen = pick_subscription_for_cli(
            entries, usages, paused_providers=set(),
            cli_id="open-code",
        )
        assert chosen is not None
        assert chosen.cli == "open-code"
        assert chosen.provider == "anthropic"

    def test_no_snapshot_for_entry_cli_is_treated_as_no_signal(self):
        """An entry whose ``{cli}:{provider}`` has no registered snapshot
        is treated as 'no signal → available' (analyse §6.3). Without
        this graceful degradation an OpenCode session would block on a
        missing provider that simply has no live signal source yet."""
        entries = [_entry(cli="open-code", provider="anthropic", drempel=0.9)]
        chosen = pick_subscription_for_cli(
            entries, usages={}, paused_providers=set(),
            cli_id="open-code",
        )
        assert chosen is not None
        assert chosen.cli == "open-code"
        assert chosen.provider == "anthropic"

    def test_priority_order_within_mixed_cli_pool(self):
        """When several entries match the requested CLI, priority order
        wins — first matching entry in configured order under its
        drempel is the choice (same rule as the legacy pool, just with
        a CLI pre-filter)."""
        entries = [
            _entry(cli="open-code", provider="anthropic", drempel=0.9),
            _entry(cli="open-code", provider="minimax", drempel=0.9),
        ]
        usages = {
            "open-code:anthropic": _usage(
                subscription_id="open-code:anthropic",
                drempel_gebruikt=0.3,
            ),
            "open-code:minimax": _usage(
                subscription_id="open-code:minimax",
                drempel_gebruikt=0.1,
            ),
        }
        chosen = pick_subscription_for_cli(
            entries, usages, paused_providers=set(),
            cli_id="open-code",
        )
        assert chosen is not None
        assert chosen.cli == "open-code"
        assert chosen.provider == "anthropic"

    def test_pool_with_only_other_cli_entry_returns_none_choice_for_default(self):
        """A pool that has entries for an *unmatched* CLI (e.g. only
        ``open-code``) but the router is asked to pick for the default
        CLI: no entry matches → the router returns ``None`` so the
        caller falls through to the column-default chain (the
        acceptatie-criterium 'geen entry voor deze CLI' as a distinct
        case)."""
        entries = [_entry(cli="open-code", provider="anthropic", drempel=0.9)]
        chosen = pick_subscription_for_cli(
            entries, usages={},
            paused_providers=set(), cli_id="claude-code",
        )
        assert chosen is None


class TestPickSubscriptionForCliFilter:
    """The router's CLI-filter API lives behind a public helper —
    ``pick_subscription_for_cli(entries, usages, *, paused_providers,
    cli_id)`` — so the dispatch wiring can ask 'given this CLI, which
    pool entry wins?' without re-implementing the CLI discrimination
    logic. The legacy ``pick_subscription`` keeps its board-wide
    signature (default-CLI pool) for the existing call sites — it
    delegates to ``pick_subscription_for_cli`` with ``cli_id =
    DEFAULT_POOL_CLI``."""

    def test_legacy_pick_subscription_delegates_with_default_cli(self):
        """The original ``pick_subscription(entries, usages, *,
        paused_providers)`` keeps working — it now passes
        ``cli_id=DEFAULT_POOL_CLI`` through to the new helper, so all
        pre-kaart-8f40d443 call sites (esp. ``dispatch._pick_pool_choice``
        and ``has_available_spillover``) keep their contract."""
        entries = [_entry(provider="anthropic")]
        usages = {
            f"{DEFAULT_POOL_CLI}:anthropic": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:anthropic",
                drempel_gebruikt=0.1,
            ),
        }
        chosen = pick_subscription(
            entries, usages, paused_providers=set(),
        )
        assert chosen is not None
        assert chosen.cli == DEFAULT_POOL_CLI


class TestSignalStatusClassify:
    """Kaart 8f40d443… (quota-pool CLI-agnostisch): the four-way
    snapshot-side status — explicit "no signal" vs "0%" vs "above
    threshold" vs "hard exhausted". ``SignalStatus.classify`` is the
    pure helper that ``pick_subscription_with_status`` delegates to;
    the tests here pin the four branches so a future refactor can't
    silently collapse them."""

    def test_no_snapshot_is_no_signal(self):
        """No snapshot for the entry's ``{cli}:{provider}`` → ``AVAILABLE_NO_SIGNAL``.

        Distinct from ``AVAILABLE_WITH_SIGNAL``: the router keeps
        the entry eligible (analyse §6.3) but the UI must render a
        "geen signaal-bron"-badge so the operator doesn't confuse
        it with a healthy low-usage subscription."""
        entry = _entry(provider="anthropic", drempel=0.9)
        status = SignalStatus.classify(entry, None)
        assert status is SignalStatus.AVAILABLE_NO_SIGNAL

    def test_zero_percent_is_available_with_signal(self):
        """``drempel_gebruikt=0`` with ``beschikbaar=True`` → ``AVAILABLE_WITH_SIGNAL``.

        The signal is honest (a real number from a real provider);
        the operator sees "0% used" instead of the "geen signaal"-
        badge. Same eligibility as ``AVAILABLE_NO_SIGNAL`` but a
        different operator-facing copy — that distinction is the
        whole point of this enum (kaart 8f40d443…)."""
        entry = _entry(provider="anthropic", drempel=0.9)
        usage = _usage(
            subscription_id=f"{entry.resolved_cli}:anthropic",
            beschikbaar=True, drempel_gebruikt=0.0,
        )
        status = SignalStatus.classify(entry, usage)
        assert status is SignalStatus.AVAILABLE_WITH_SIGNAL

    def test_above_threshold_is_unavailable_above_threshold(self):
        """Real number at or above the entry's drempel → ``UNAVAILABLE_ABOVE_THRESHOLD``.

        The router spills to the next entry; the UI shows the
        actual percentage so the operator can see *why* this entry
        skipped."""
        entry = _entry(provider="anthropic", drempel=0.9)
        usage = _usage(
            subscription_id=f"{entry.resolved_cli}:anthropic",
            beschikbaar=True, drempel_gebruikt=0.95,
        )
        status = SignalStatus.classify(entry, usage)
        assert status is SignalStatus.UNAVAILABLE_ABOVE_THRESHOLD

    def test_hard_exhausted_is_unavailable_exhausted(self):
        """``beschikbaar=False`` → ``UNAVAILABLE_EXHAUSTED`` even when the
        drempel number is missing or low.

        Distinct from ``UNAVAILABLE_ABOVE_THRESHOLD`` because a
        hard-exhausted provider typically resets on a longer clock
        than a drempel-spill; the UI shows "limit bereikt" instead
        of "X% used — boven drempel"."""
        entry = _entry(provider="anthropic", drempel=0.9)
        usage = _usage(
            subscription_id=f"{entry.resolved_cli}:anthropic",
            beschikbaar=False, drempel_gebruikt=0.5,
        )
        status = SignalStatus.classify(entry, usage)
        assert status is SignalStatus.UNAVAILABLE_EXHAUSTED

    def test_signal_present_but_no_number_is_no_signal(self):
        """Snapshot with ``beschikbaar=True`` but ``drempel_gebruikt=None``
        → ``AVAILABLE_NO_SIGNAL``.

        The snapshot exists but the drempel-axis number is missing
        (analyse §6.3 — no fabrication). The router keeps the
        entry eligible, the UI shows "geen signaal" — distinct from
        ``AVAILABLE_WITH_SIGNAL`` even though both are eligible."""
        entry = _entry(provider="anthropic", drempel=0.9)
        usage = _usage(
            subscription_id=f"{entry.resolved_cli}:anthropic",
            beschikbaar=True, drempel_gebruikt=None,
        )
        status = SignalStatus.classify(entry, usage)
        assert status is SignalStatus.AVAILABLE_NO_SIGNAL


class TestPickSubscriptionWithStatus:
    """Kaart 8f40d443…: ``pick_subscription_with_status`` mirrors
    ``pick_subscription_for_cli`` but bundles the chosen entry with a
    ``SignalStatus``. The dispatch wiring can opt into the new shape
    to render an honest badge; the legacy ``pick_subscription_for_cli``
    callers keep their ``PoolEntry | None`` contract untouched."""

    def test_zero_percent_choice_carrys_available_with_signal(self):
        """A real ``0%`` snapshot is labelled ``AVAILABLE_WITH_SIGNAL`` —
        not ``AVAILABLE_NO_SIGNAL``. This is the operator-facing
        distinction the UI renders as "0% used" vs "geen signaal-bron"."""
        entry = _entry(provider="anthropic", drempel=0.9)
        usages = {
            f"{DEFAULT_POOL_CLI}:anthropic": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:anthropic",
                beschikbaar=True, drempel_gebruikt=0.0,
            ),
        }
        choice = pick_subscription_with_status(
            [entry], usages, paused_providers=set(),
        )
        assert choice is not None
        assert choice.entry is entry
        assert choice.status is SignalStatus.AVAILABLE_WITH_SIGNAL

    def test_missing_snapshot_choice_carrys_available_no_signal(self):
        """No snapshot at all → ``AVAILABLE_NO_SIGNAL``.

        Same eligibility as the 0%-case (the router keeps it) but
        a different status — the UI must surface this distinction
        honestly."""
        entry = _entry(provider="anthropic", drempel=0.9)
        choice = pick_subscription_with_status(
            [entry], usages={}, paused_providers=set(),
        )
        assert choice is not None
        assert choice.entry is entry
        assert choice.status is SignalStatus.AVAILABLE_NO_SIGNAL

    def test_no_signal_distinct_from_zero_percent(self):
        """The two available-states are distinct even though both
        keep the entry eligible.

        Pinning the four-way case here so a future refactor can't
        silently collapse ``AVAILABLE_WITH_SIGNAL`` and
        ``AVAILABLE_NO_SIGNAL`` into one — which would defeat the
        whole point of the explicit-degradation acceptance criterion.
        """
        zero = _entry(provider="anthropic", drempel=0.9)
        no_signal = _entry(provider="minimax", drempel=0.9)
        usages = {
            f"{DEFAULT_POOL_CLI}:anthropic": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:anthropic",
                beschikbaar=True, drempel_gebruikt=0.0,
            ),
            # No entry for claude-code:minimax → "no signal"
        }
        choice_zero = pick_subscription_with_status(
            [zero], usages, paused_providers=set(),
        )
        choice_none = pick_subscription_with_status(
            [no_signal], usages, paused_providers=set(),
        )
        assert choice_zero is not None
        assert choice_none is not None
        assert (
            choice_zero.status is SignalStatus.AVAILABLE_WITH_SIGNAL
        )
        assert (
            choice_none.status is SignalStatus.AVAILABLE_NO_SIGNAL
        )
        # And the enum values themselves differ — a future refactor
        # that conflates them would break this assertion.
        assert (
            SignalStatus.AVAILABLE_WITH_SIGNAL
            is not SignalStatus.AVAILABLE_NO_SIGNAL
        )

    def test_hard_exhausted_choice_carrys_unavailable_exhausted(self):
        """``beschikbaar=False`` with low drempel → the entry is
        selected as the "laatste val-terug" with status
        ``UNAVAILABLE_EXHAUSTED`` so the UI can show the right copy."""
        entry = _entry(provider="anthropic", drempel=0.9)
        usages = {
            f"{DEFAULT_POOL_CLI}:anthropic": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:anthropic",
                beschikbaar=False, drempel_gebruikt=0.1,
            ),
        }
        choice = pick_subscription_with_status(
            [entry], usages, paused_providers=set(),
        )
        assert choice is not None
        assert choice.entry is entry
        assert choice.status is SignalStatus.UNAVAILABLE_EXHAUSTED

    def test_above_threshold_choice_carrys_unavailable_above_threshold(self):
        entry = _entry(provider="anthropic", drempel=0.9)
        usages = {
            f"{DEFAULT_POOL_CLI}:anthropic": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:anthropic",
                beschikbaar=True, drempel_gebruikt=0.95,
            ),
        }
        choice = pick_subscription_with_status(
            [entry], usages, paused_providers=set(),
        )
        assert choice is not None
        assert choice.entry is entry
        assert choice.status is SignalStatus.UNAVAILABLE_ABOVE_THRESHOLD

    def test_cli_filter_still_applies_in_with_status(self):
        """The CLI-filter rule from ``pick_subscription_for_cli``
        carries through: a pool with only ``open-code`` entries
        returns ``None`` when the caller asks for the default CLI."""
        entries = [_entry(cli="open-code", provider="anthropic", drempel=0.9)]
        choice = pick_subscription_with_status(
            entries, usages={},
            paused_providers=set(), cli_id=DEFAULT_POOL_CLI,
        )
        assert choice is None

    def test_pure_no_side_effects(self):
        """``pick_subscription_with_status`` is side-effect-free —
        identical input shapes produce identical outputs across
        repeated calls. Pins the contract the dispatch wiring
        (which calls this per spawn) relies on."""
        entry = _entry(provider="anthropic", drempel=0.9)
        usages = {
            f"{DEFAULT_POOL_CLI}:anthropic": _usage(
                subscription_id=f"{DEFAULT_POOL_CLI}:anthropic",
                beschikbaar=True, drempel_gebruikt=0.0,
            ),
        }
        first = pick_subscription_with_status(
            [entry], usages, paused_providers=set(),
        )
        second = pick_subscription_with_status(
            [entry], usages, paused_providers=set(),
        )
        assert first is not None and second is not None
        assert first.entry is entry
        assert second.entry is entry
        assert first.status is second.status
