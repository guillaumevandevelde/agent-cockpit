"""``SubscriptionUsageProvider`` ABC and the ``SubscriptionUsage`` value type.

Phase 1a of usage-aware routing. The contract:

- ``get_usage()`` returns a normalised ``SubscriptionUsage`` snapshot per
  subscription. Callers (the fase 1b router, the Subscriptions-pagina)
  consume that snapshot without knowing which provider produced it.
- ``betrouwbaarheid`` is the honest quality label: ``exact`` only when
  the underlying source is authoritative (e.g. a remote API that
  returned a structured usage payload); ``schatting`` when the value is
  derived from a local proxy (Anthropic's 5h block); ``onbekend`` when
  there is no usable signal — **no fabrication** (analyse §6.1).
- Output is per-subscription. There is no method that turns multiple
  snapshots into a single comparable score (analyse §6.2).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Betrouwbaarheid = Literal["exact", "schatting", "onbekend"]


@dataclass(frozen=True)
class UsageWindow:
    """One rate window of a subscription, normalised to "fraction used".

    Every subscription meters against one or more windows, and the four
    real ones disagree on both count and kind — measured 2026-08-14:

    ===================  ========================================
    Claude Pro           5h + 7d, percent utilization
    MiniMax plus         5h + weekly, percent **remaining**
    ChatGPT Go           one 30d window, percent used
    opencode Go          5h + weekly + monthly, **dollars**
    ===================  ========================================

    So the provider — not the caller — normalises. Each provider inverts
    (MiniMax), divides (opencode: spend / published cap) or passes
    through (codex, Anthropic) at its own edge, and every window that
    leaves a provider means the same thing: ``used_fraction`` is the part
    already consumed.

    ``verbruikt``/``limiet``/``eenheid`` keep the provider's own raw
    numbers alongside, so the UI can show "$18 / $30" rather than a bare
    percentage. Per subscriptions.md there is still no cross-vendor
    normalisation: two windows being comparable *in shape* does not make
    them comparable *in value*.

    Fields:
        label: the provider's own name for the window ("5h", "weekly",
            "monthly") — shown verbatim, never re-derived.
        used_fraction: fraction consumed, 0-1+. Above 1.0 is legal:
            opencode Go's "Use balance" option lets spend exceed the cap
            instead of blocking.
        resets_at: when this window rolls over, or None when unknown.
        verbruikt: raw consumed amount in ``eenheid``, or None.
        limiet: raw limit in ``eenheid``, or None when not published.
        eenheid: unit for the raw pair — ``"%"``, ``"$"``, ``"tokens"``.
    """

    label: str
    used_fraction: float
    resets_at: datetime | None = None
    verbruikt: float | None = None
    limiet: float | None = None
    eenheid: str = "%"


@dataclass(frozen=True)
class SubscriptionUsage:
    """Normalised overschot-signaal for a single subscription.

    Fields:
        subscription_id: stable id like ``"claude-code:anthropic"`` —
            matches the analyse §3 ``{cli, provider}`` convention so the
            router can match it against dispatched sessions.
        subscription_label: human-readable label for the UI.
        beschikbaar: True wanneer de subscription nog ruimte heeft. Voor
            signalen met ``betrouwbaarheid="onbekend"`` is dit True
            (analyse §6.3 — de pool laat de per-provider pause de
            uiteindelijke gatekeeper zijn).
        drempel_gebruikt: fraction of the threshold/limit consumed (0-1+,
            or None when unknown). Callers use this to compute their own
            drempel (fase 1b: skip above 90%, etc.) — the provider does
            not bake a threshold into the snapshot.
        bron: where the signal came from, for traceability.
        betrouwbaarheid: ``"exact"`` | ``"schatting"`` | ``"onbekend"``.
        verbruikt: raw consumed amount in ``eenheid``, or None when the
            provider has no usable count (no fabrication — mirrors
            ``drempel_gebruikt``). Display-only; the router keeps using
            ``drempel_gebruikt``.
        limiet: raw limit in ``eenheid``, or None when the limit is not
            published / not configured (e.g. subscriptions.md: "limit not
            published" rather than a guessed number).
        eenheid: unit label for ``verbruikt``/``limiet`` (default
            ``"tokens"``) — providers with a different unit (e.g. a
            request count) override this.
        venster_label: the provider's own name for its rate window (e.g.
            "5h rate"), shown verbatim per subscriptions.md's "no faked
            cross-vendor equivalence" rule. None when unknown.
        reset_op: when the current window resets, or None when unknown.
        windows: every rate window this subscription meters against, in
            the provider's own order. Empty when there is no signal.

    ``drempel_gebruikt`` versus ``windows``: the router needs **one**
    number to compare against a pool entry's drempel, but a subscription
    can be comfortable in one window and nearly exhausted in another —
    MiniMax measured 0% of its 5h and 44% of its week in the same call.
    So ``drempel_gebruikt`` is the **worst** window (``max`` of
    ``used_fraction``), which is the only choice that cannot route a card
    onto a lane that is already out of room. ``windows`` keeps the detail
    the max throws away, for display. Use ``from_windows`` rather than
    setting both by hand — it keeps them consistent by construction.
    """

    subscription_id: str
    subscription_label: str
    beschikbaar: bool
    drempel_gebruikt: float | None
    bron: str
    betrouwbaarheid: Betrouwbaarheid
    verbruikt: float | None = None
    limiet: float | None = None
    eenheid: str = "tokens"
    venster_label: str | None = None
    reset_op: datetime | None = None
    windows: tuple[UsageWindow, ...] = ()

    @classmethod
    def from_windows(
        cls,
        *,
        subscription_id: str,
        subscription_label: str,
        bron: str,
        betrouwbaarheid: Betrouwbaarheid,
        windows: Sequence[UsageWindow],
    ) -> SubscriptionUsage:
        """Build a snapshot from measured windows, deriving the scalars.

        ``drempel_gebruikt`` becomes the worst window and the legacy
        scalar trio (``verbruikt``/``limiet``/``eenheid``/
        ``venster_label``/``reset_op``) mirrors that same worst window, so
        pre-``windows`` consumers keep reading a coherent number instead
        of an arbitrary one.

        ``beschikbaar`` stays True at exactly 1.0 and above: a window at
        its cap is not proof the lane is closed (opencode Go spills over
        into Zen balance, and every provider's real backstop is the pause
        fired by an actual rate-limit event). Callers decide their own
        threshold from ``drempel_gebruikt`` — the provider does not bake
        one in.
        """
        if not windows:
            raise ValueError("from_windows requires at least one window")
        worst = max(windows, key=lambda w: w.used_fraction)
        return cls(
            subscription_id=subscription_id,
            subscription_label=subscription_label,
            beschikbaar=True,
            drempel_gebruikt=worst.used_fraction,
            bron=bron,
            betrouwbaarheid=betrouwbaarheid,
            verbruikt=worst.verbruikt,
            limiet=worst.limiet,
            eenheid=worst.eenheid,
            venster_label=worst.label,
            reset_op=worst.resets_at,
            windows=tuple(windows),
        )


class SubscriptionUsageProvider(ABC):
    """Abstract base for per-subscription usage signals.

    One concrete subclass per provider (Anthropic, MiniMax, Codex, …).
    Mirrors the ``AgenticCli`` ABC under ``services/agentic_cli/`` — the
    same extend-by-subclass pattern, but for usage rather than spawn.
    """

    id: str
    label: str

    @abstractmethod
    async def get_usage(self) -> SubscriptionUsage:
        """Return the current usage snapshot for this subscription."""