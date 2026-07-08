"""Endpoints for /api/v1/agent-bridge/subscriptions/*."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.schemas import (
    AnthropicPlanTierResponse,
    AnthropicPlanTierUpdateRequest,
    PeriodUsageResponse,
    SubscriptionUsageResponse,
)
# Side-effect import: registers PlaceholderAnthropicProvider and
# PlaceholderMinimaxProvider at app startup. Real providers in Tasks 4
# and 5 overwrite these via register_usage_provider at import time.
from app.services.subscriptions import (  # noqa: F401
    get_snapshot_cache,
    get_usage_provider,
    invalidate_snapshot_cache,
    placeholders,  # noqa: F401
    put_snapshot_cache,
)
from app.services.subscriptions.anthropic import build_anthropic_provider
from app.services.subscriptions.storage import VALID_TIERS, get_pref, set_pref

router = APIRouter()


def _to_response(snap) -> SubscriptionUsageResponse:
    return SubscriptionUsageResponse(
        provider=snap.provider,
        plan_label=snap.plan_label,
        periods=[
            PeriodUsageResponse(
                label=p.label,
                used=p.used,
                limit=p.limit,
                unit=p.unit,
                reset_at=p.reset_at,
                source=p.source,
                note=p.note,
            )
            for p in snap.periods
        ],
        fetched_at=snap.fetched_at,
        error=snap.error,
        error_code=snap.error_code,
    )


@router.get("/subscriptions/{provider_id}/usage", response_model=SubscriptionUsageResponse)
async def get_usage(provider_id: str, db: AsyncSession = Depends(get_db)):
    if provider_id == "anthropic":
        cached = await get_snapshot_cache("anthropic")
        if cached is not None:
            return _to_response(cached)
        snap = await build_anthropic_provider(db).get_snapshot()
        await put_snapshot_cache(snap)
        return _to_response(snap)

    try:
        provider = get_usage_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_subscription_provider", "message": str(exc)},
        )

    cached = await get_snapshot_cache(provider_id)
    if cached is not None:
        return _to_response(cached)

    snap = await provider.get_snapshot()
    await put_snapshot_cache(snap)
    return _to_response(snap)


@router.get("/subscriptions/anthropic/plan-tier", response_model=AnthropicPlanTierResponse)
async def get_anthropic_plan_tier(db: AsyncSession = Depends(get_db)):
    raw = await get_pref(db, "anthropic", "plan_tier")
    if raw is None:
        return AnthropicPlanTierResponse(tier=None)
    if raw not in VALID_TIERS:
        return AnthropicPlanTierResponse(tier=None)
    return AnthropicPlanTierResponse(tier=raw)  # type: ignore[arg-type]


@router.put("/subscriptions/anthropic/plan-tier", response_model=AnthropicPlanTierResponse)
async def put_anthropic_plan_tier(
    body: AnthropicPlanTierUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    if body.tier is not None and body.tier not in VALID_TIERS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown plan tier: {body.tier}",
        )
    await set_pref(db, "anthropic", "plan_tier", body.tier)
    invalidate_snapshot_cache("anthropic")
    return AnthropicPlanTierResponse(tier=body.tier)  # type: ignore[arg-type]
