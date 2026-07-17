"""Subscriptions-pagina API — per-subscription usage (kaart 9bce091a...).

One row per known ``{cli}:{provider}`` subscription. Anthropic and
MiniMax get their real ``SubscriptionUsageProvider``; subscriptions
without a usable local signal (Bedrock — unverified attribution, analyse
§4.4/§7.1; Codex/Copilot/OpenCode — no stable usage source) get the
honest ``UnknownUsageProvider`` fallback. No cross-vendor normalisation:
each row keeps its own ``betrouwbaarheid`` and raw ``verbruikt``/``limiet``
(subscriptions.md).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.schemas import (
    AnthropicPlanTierOption,
    AnthropicPlanTierOptionsResponse,
    AnthropicPlanTierResponse,
    AnthropicPlanTierUpdateRequest,
    SubscriptionUsageListResponse,
    SubscriptionUsageRow,
)
from app.services.subscription_prefs_service import (
    get_or_create_prefs,
    resolve_anthropic_plan_tier_limit,
    set_anthropic_plan_tier,
)
from app.services.subscriptions.anthropic import ANTHROPIC_PLAN_TIERS, AnthropicUsageProvider
from app.services.subscriptions.base import SubscriptionUsage
from app.services.subscriptions.minimax import MinimaxUsageProvider
from app.services.subscriptions.registry import get_unknown_provider
from app.services.usage_service import UsageService

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])

# The known subscription pool (decisions.md 2026-07-14) plus the
# non-pool CLIs the card explicitly calls out for an honest empty state.
KNOWN_SUBSCRIPTIONS: tuple[tuple[str, str], ...] = (
    ("claude-code", "anthropic"),
    ("claude-code", "minimax"),
    ("claude-code", "bedrock"),
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
    prefs = await get_or_create_prefs(db)
    plan_limit = resolve_anthropic_plan_tier_limit(
        prefs.anthropic_plan_tier, prefs.anthropic_custom_limit_tokens
    )
    usage_service = UsageService(db)

    rows: list[SubscriptionUsageRow] = []
    for cli, provider_name in KNOWN_SUBSCRIPTIONS:
        if cli == "claude-code" and provider_name == "anthropic":
            provider = AnthropicUsageProvider(
                usage_service=usage_service, plan_tier_limit_tokens=plan_limit
            )
        elif cli == "claude-code" and provider_name == "minimax":
            provider = MinimaxUsageProvider(
                api_key=settings.minimax_api_key, probe_url=None
            )
        else:
            provider = get_unknown_provider(cli, provider_name)
        rows.append(_to_row(await provider.get_usage()))

    return SubscriptionUsageListResponse(subscriptions=rows)


@router.get("/anthropic/plan-tiers", response_model=AnthropicPlanTierOptionsResponse)
async def get_anthropic_plan_tier_options():
    return AnthropicPlanTierOptionsResponse(
        tiers=[
            AnthropicPlanTierOption(key=key, label=cfg["label"], tokens_5h=cfg["tokens_5h"])  # type: ignore[arg-type]
            for key, cfg in ANTHROPIC_PLAN_TIERS.items()
        ]
    )


@router.get("/anthropic/plan-tier", response_model=AnthropicPlanTierResponse)
async def get_anthropic_plan_tier(db: AsyncSession = Depends(get_db)):
    prefs = await get_or_create_prefs(db)
    return AnthropicPlanTierResponse(
        tier=prefs.anthropic_plan_tier,
        custom_limit_tokens=prefs.anthropic_custom_limit_tokens,
    )


@router.put("/anthropic/plan-tier", response_model=AnthropicPlanTierResponse)
async def put_anthropic_plan_tier(
    body: AnthropicPlanTierUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        prefs = await set_anthropic_plan_tier(
            db, tier=body.tier, custom_limit_tokens=body.custom_limit_tokens
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return AnthropicPlanTierResponse(
        tier=prefs.anthropic_plan_tier,
        custom_limit_tokens=prefs.anthropic_custom_limit_tokens,
    )
