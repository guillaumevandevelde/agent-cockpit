"""Subscriptions-pagina API — per-subscription usage (kaart 9bce091a...).

One row per **actually held** subscription. The list used to carry seven
rows, of which three described subscriptions nobody owns: ``bedrock``
(never configured), ``copilot-cli`` (no such plan) and the router row
``anthropic-compatible`` (an endpoint shape, not a subscription). They
could only ever render "no signal", so six of seven rows were noise
burying the one row with a number. They were dropped rather than kept as
honest-empty placeholders — an empty state for a thing you do not own is
not honesty, it is clutter.

What remains is the four real subscriptions, and each has a measured
quota source (verified 2026-08-14, see
``docs/cockpit/subscription-usage-decision.md``):

======================  =====================  =========================
``claude-code:anthropic``  5h + 7d             statusline ``rate_limits``
``claude-code:minimax``    5h + weekly         ``/v1/token_plan/remains``
``codex-cli:codex``        30d (Go: no 5h!)    rollout ``token_count``
``open-code:open-code``    5h + week + month   local cost ÷ published cap
======================  =====================  =========================

Rows whose provider is not wired yet still return an honest
``betrouwbaarheid="onbekend"`` — no fabrication. No cross-vendor
normalisation: elke rij behoudt zijn eigen ``betrouwbaarheid`` en eigen
``verbruikt``/``limiet`` (subscriptions.md).
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
    UsageWindowRow,
)
from app.services.subscriptions.anthropic import AnthropicUsageProvider
from app.services.subscriptions.base import (
    SubscriptionUsage,
    SubscriptionUsageProvider,
)
from app.services.subscriptions.codex import CodexUsageProvider
from app.services.subscriptions.minimax import MinimaxUsageProvider
from app.services.subscriptions.opencode_go import OpencodeGoUsageProvider
from app.services.subscriptions.registry import get_provider_for
from app.services.subscriptions.unknown import UnknownUsageProvider
from app.services.usage_service import UsageService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])

# The subscriptions actually held on this machine. Adding a row here is
# a claim that someone pays for it — not that the CLI exists. A CLI with
# no subscription behind it belongs in the ``agentic_cli`` registry only.
KNOWN_SUBSCRIPTIONS: tuple[tuple[str, str], ...] = (
    ("claude-code", "anthropic"),
    ("claude-code", "minimax"),
    ("codex-cli", "codex"),
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
        windows=[
            UsageWindowRow(
                label=w.label,
                used_fraction=w.used_fraction,
                resets_at=w.resets_at,
                verbruikt=w.verbruikt,
                limiet=w.limiet,
                eenheid=w.eenheid,
            )
            for w in usage.windows
        ],
    )


@router.get("/usage", response_model=SubscriptionUsageListResponse)
async def get_subscription_usage(db: AsyncSession = Depends(get_db)):
    usage_service = UsageService(db)

    rows: list[SubscriptionUsageRow] = []
    for cli, provider_name in KNOWN_SUBSCRIPTIONS:
        # Annotated to the ABC: without it mypy pins ``provider`` to the
        # first branch's concrete type and rejects every later branch.
        provider: SubscriptionUsageProvider
        if cli == "claude-code" and provider_name == "anthropic":
            provider = AnthropicUsageProvider(usage_service=usage_service)
        elif cli == "claude-code" and provider_name == "minimax":
            # ``probe_url`` defaults to the international quota endpoint.
            # It was pinned to None here for as long as we believed
            # MiniMax published no usage API; it does, and the row was
            # rendering "no signal" over a live 44%-consumed week.
            provider = MinimaxUsageProvider(api_key=settings.minimax_api_key)
        elif cli == "open-code" and provider_name == "open-code":
            provider = OpencodeGoUsageProvider()
        elif cli == "codex-cli" and provider_name == "codex":
            provider = CodexUsageProvider()
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
