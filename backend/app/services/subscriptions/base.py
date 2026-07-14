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
from dataclasses import dataclass
from typing import Literal

Betrouwbaarheid = Literal["exact", "schatting", "onbekend"]


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
    """

    subscription_id: str
    subscription_label: str
    beschikbaar: bool
    drempel_gebruikt: float | None
    bron: str
    betrouwbaarheid: Betrouwbaarheid


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