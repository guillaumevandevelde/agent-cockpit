"""Per-subscription overschot-signaal (fase 1a van usage-aware routing).

Zie ``docs/cockpit/subscriptions.md`` (per-provider usage) en
``docs/cockpit/subscription-flexibiliteit-analyse.md`` §5 / §8 voor de
ontwerpcontext. De abstractie mirrort ``services/agentic_cli/``:
één ``SubscriptionUsageProvider``-subclass per abonnement, zodat een
nieuwe provider een subclass wordt in plaats van een gedrifte ad-hoc
functie.

Belangrijk: het signaal is heterogeen van kwaliteit per abonnement
(analyse §2.4) — de output labelt ``betrouwbaarheid`` eerlijk
(``exact`` | ``schatting`` | ``onbekend``) en doet **geen** cross-vendor
normalisatie (analyse §6.2).
"""
from __future__ import annotations

from app.services.subscriptions.base import (
    SubscriptionUsage,
    SubscriptionUsageProvider,
)

__all__ = ["SubscriptionUsage", "SubscriptionUsageProvider"]