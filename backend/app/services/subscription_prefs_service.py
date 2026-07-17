"""Singleton service for the Subscriptions-pagina's Anthropic plan-tier pref.

Mirrors ``auto_backup_service.get_or_create_settings`` — a single row
(id=1), created lazily. Anthropic publishes no usage API for Pro/Max, so
the 5h-window token limit has to come from a user-chosen plan tier (or a
user-entered custom number) rather than a fetched value (kaart
9bce091a..., ``docs/cockpit/subscriptions.md``).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import SubscriptionPrefs
from app.services.subscriptions.anthropic import ANTHROPIC_PLAN_TIERS

CUSTOM_TIER = "custom"
VALID_TIERS = frozenset({*ANTHROPIC_PLAN_TIERS, CUSTOM_TIER})


async def get_or_create_prefs(db: AsyncSession) -> SubscriptionPrefs:
    """Return the singleton prefs row, creating it with defaults if absent."""
    prefs = (
        await db.execute(select(SubscriptionPrefs).where(SubscriptionPrefs.id == 1))
    ).scalar_one_or_none()
    if prefs is None:
        prefs = SubscriptionPrefs(id=1)
        db.add(prefs)
        await db.commit()
        await db.refresh(prefs)
    return prefs


async def set_anthropic_plan_tier(
    db: AsyncSession, *, tier: str | None, custom_limit_tokens: int | None
) -> SubscriptionPrefs:
    """Set (or clear, with ``tier=None``) the Anthropic plan-tier pref.

    Raises ``ValueError`` for an unknown tier key, or for ``tier="custom"``
    without a positive ``custom_limit_tokens`` — no fabrication, the caller
    must supply a real number for a custom budget.
    """
    if tier is not None and tier not in VALID_TIERS:
        raise ValueError(f"Unknown plan tier: {tier}")
    if tier == CUSTOM_TIER and (custom_limit_tokens is None or custom_limit_tokens <= 0):
        raise ValueError("custom tier requires a positive custom_limit_tokens")

    prefs = await get_or_create_prefs(db)
    prefs.anthropic_plan_tier = tier
    prefs.anthropic_custom_limit_tokens = custom_limit_tokens if tier == CUSTOM_TIER else None
    await db.commit()
    await db.refresh(prefs)
    return prefs


def resolve_anthropic_plan_tier_limit(
    tier: str | None, custom_limit_tokens: int | None
) -> int | None:
    """Resolve a stored tier into a 5h-window token limit, or None.

    None covers: no tier chosen, an unrecognised tier, or a "custom" tier
    without a stored number — the caller (``AnthropicUsageProvider``)
    already treats None as "no fabrication, show onbekend".
    """
    if tier is None:
        return None
    if tier == CUSTOM_TIER:
        return custom_limit_tokens if custom_limit_tokens and custom_limit_tokens > 0 else None
    tier_config = ANTHROPIC_PLAN_TIERS.get(tier)
    if tier_config is None:
        return None
    return tier_config["tokens_5h"]  # type: ignore[return-value]
