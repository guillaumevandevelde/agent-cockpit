"""Subscriptions-pagina API — per-subscription usage (kaart 9bce091a...).

One row per known ``{cli}:{provider}`` subscription. Anthropic and
MiniMax get their real ``SubscriptionUsageProvider``; subscriptions
without a usable local signal (Bedrock — unverified attribution, analyse
§4.4/§7.1; Codex/Copilot/OpenCode — no stable usage source; de
router-subscription ``claude-code:anthropic-compatible`` uit kaart
390756e6... — geen betrouwbare quota-bron omdat de router meerdere
upstreams verbergt) krijgen een eerlijke ``betrouwbaarheid="onbekend"``
terug. No cross-vendor normalisation: elke rij behoudt zijn eigen
``betrouwbaarheid`` en eigen ``verbruikt``/``limiet`` (subscriptions.md).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.schemas import (
    SubscriptionUsageListResponse,
    SubscriptionUsageRow,
)
from app.services.agentic_cli.provider_env import PROVIDER_COMPATIBLE
from app.services.subscriptions.anthropic import AnthropicUsageProvider
from app.services.subscriptions.base import SubscriptionUsage
from app.services.subscriptions.minimax import MinimaxUsageProvider
from app.services.subscriptions.registry import get_provider_for
from app.services.subscriptions.unknown import UnknownUsageProvider
from app.services.usage_service import UsageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])

# The known subscription pool (decisions.md 2026-07-14) plus the
# non-pool CLIs the card explicitly calls out for an honest empty state.
# Kaart 333af652e... voegde ``anthropic-compatible`` toe als
# provider-id voor data-driven eindpunten (9router / LiteLLM); kaart
# 390756e6... voegde de router-attributie-rij toe zodat de UI de
# onzekerheid expliciet toont in plaats van een verzonnen getal.
KNOWN_SUBSCRIPTIONS: tuple[tuple[str, str], ...] = (
    ("claude-code", "anthropic"),
    ("claude-code", "minimax"),
    ("claude-code", "bedrock"),
    ("claude-code", PROVIDER_COMPATIBLE),
    ("codex-cli", "codex"),
    ("copilot-cli", "copilot"),
    ("open-code", "open-code"),
)


def _to_row(usage: SubscriptionUsage) -> SubscriptionUsageRow:
    return SubscriptionUsageRow(
        subscription_id=usage.subscription_id,
        subscription_label=usage.subscription_label,
        beschikbaar=usage.beschikbaar,
        drempel_gebruikt=usage.drempel_gebruikt,
        bron=usage.bron,
        betrouwbaarheid=usage.betrouwbaarheid,
        verbruikt=usage.verbruikt,
        limiet=usage.limiet,
        eenheid=usage.eenheid,
        venster_label=usage.venster_label,
        reset_op=usage.reset_op,
    )


@router.get("/usage", response_model=SubscriptionUsageListResponse)
async def get_subscription_usage(db: AsyncSession = Depends(get_db)):
    usage_service = UsageService(db)

    rows: list[SubscriptionUsageRow] = []
    for cli, provider_name in KNOWN_SUBSCRIPTIONS:
        if cli == "claude-code" and provider_name == "anthropic":
            provider = AnthropicUsageProvider(usage_service=usage_service)
        elif cli == "claude-code" and provider_name == "minimax":
            provider = MinimaxUsageProvider(
                api_key=settings.minimax_api_key, probe_url=None
            )
        else:
            # Kaart 390756e6...: voor de router-rij
            # ``claude-code:anthropic-compatible`` (en elke andere
            # niet-specifiek-bekabelde subscription) raadplegen we
            # eerst het registry: als app-startup al een
            # ``RouterUsageProvider`` (kaart 390756e6...) of een
            # andere concrete provider heeft gezet, gebruiken we die
            # — anders valt de rij terug op een eerlijke
            # ``UnknownUsageProvider``. Dit houdt de endpoint-loop
            # robuust tegen runtime-registraties (de
            # ``register_default_providers``-seed loopt vóór de
            # eerste ``get_subscription_usage`` call maar is niet de
            # enige registrant).
            registry_provider = get_provider_for(
                cli=cli, provider=provider_name,
            )
            if registry_provider is not None:
                provider = registry_provider
            else:
                provider = UnknownUsageProvider(
                    subscription_id=f"{cli}:{provider_name}",
                    subscription_label=f"{cli} ({provider_name})",
                )
        rows.append(_to_row(await provider.get_usage()))

    return SubscriptionUsageListResponse(subscriptions=rows)
