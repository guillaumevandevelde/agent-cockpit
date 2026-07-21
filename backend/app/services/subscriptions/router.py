"""Eerlijke ``onbekend`` voor router-eindpunten achter ``anthropic-compatible``.

Kaart 390756e6...: een router-provider (9router, LiteLLM, …) verbergt
meerdere upstreams achter één lokaal endpoint. Voor de
usage-attributie betekent dat twee dingen:

1. **Geen betrouwbare quota-bron.** De router is geen vendor-account
   met een gepubliceerd 5h-venster; hij is een doorgeefluik. Een
   eventueel toekomstig beheer-endpoint levert hooguit een aggregaat
   van meerdere upstreams, niet de eerlijke "Claude-Code deelde X van
   zijn 5h-window"-meting die ``AnthropicUsageProvider`` levert voor
   de directe Anthropic-subscription. Daarom: ``betrouwbaarheid=
   "onbekend"`` zolang er geen externe quota-bron geconfigureerd is —
   dezelfde "no fabrication"-discipline die ``UnknownUsageProvider``
   al oplegt voor Codex/Copilot/OpenCode.

2. **Geen her-attributie van verkeer.** Router-verkeer (een
   ``gpt-4o``-modelnaam in de JSONL, of een
   ``claude-*``-antwoord dat via de router-passthrough binnenkomt)
   wordt via de prefix-attributie in
   ``subscriptions.attribution.subscription_id_for_model`` óók NIET aan
   een concrete vendor-provider toegerekend — dat is kaart d160d13f...
   voor MiniMax en is voor de router analoog: dezelfde JSONL-tree,
   maar router-attributie wijst naar ``UNKNOWN_SUBSCRIPTION_ID``, niet
   naar ``claude-code:anthropic``. Deze provider voert dus de
   "router-niveau"-pool-rij; de "verkeer-stroom"-attributie loopt
   elders.

De provider staat bewust naast ``UnknownUsageProvider`` in plaats van
er een alias van te zijn: de ``bron`` is router-specifiek
(``router_eindpunt:geen_quota_bron``) zodat de UI later kan
uitlichten waarom deze rij onzeker is. Een generieke
``geen_signaal``-tekst zou de router-context verliezen in een rij
die tussen andere onzekere rijen staat (Codex/Copilot/etc.).
"""
from __future__ import annotations

from app.services.subscriptions.base import (
    SubscriptionUsage,
    SubscriptionUsageProvider,
)

# Stabile bron-string voor de router-rij. De UI kan dit patroon
# matchen om een "router"-tooltip te tonen zonder naar de
# subscription_id te hoeven kijken.
ROUTER_BR_LABEL = "router_eindpunt"


class RouterUsageProvider(SubscriptionUsageProvider):
    """Eerlijke ``onbekend`` voor router-eindpunten (kaart 390756e6...).

    Args:
        subscription_id: stable id zoals
            ``"claude-code:anthropic-compatible"``.
        subscription_label: human-readable label voor de UI.

    Note:
        Een toekomstige uitbreiding die een echte
        router-beheer-endpoint toevoegt (bv.
        ``GET /admin/quotas`` met upstream-numbers) kan hier een
        conditie op ``router_admin_url`` toevoegen en in dat geval
        ``betrouwbaarheid="schatting"`` teruggeven — voor nu is er
        geen stabiele bron en blijft het contract "no fabrication".
    """

    def __init__(self, subscription_id: str, subscription_label: str):
        self.id = subscription_id
        self.label = subscription_label

    async def get_usage(self) -> SubscriptionUsage:
        return SubscriptionUsage(
            subscription_id=self.id,
            subscription_label=self.label,
            beschikbaar=True,
            drempel_gebruikt=None,
            bron=f"{ROUTER_BR_LABEL}:geen_quota_bron",
            betrouwbaarheid="onbekend",
        )
