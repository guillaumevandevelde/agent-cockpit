"""SubscriptionPref DB helpers (read/write the plan tier)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import SubscriptionPref


VALID_TIERS = {"pro", "max_5x", "max_20x", "team"}


async def get_pref(db: AsyncSession, provider_id: str, key: str) -> str | None:
    result = await db.execute(
        select(SubscriptionPref).where(
            SubscriptionPref.provider_id == provider_id,
            SubscriptionPref.key == key,
        )
    )
    row = result.scalar_one_or_none()
    return row.value if row else None


async def set_pref(db: AsyncSession, provider_id: str, key: str, value: str | None) -> None:
    if value is not None and key == "plan_tier" and value not in VALID_TIERS:
        raise ValueError(f"Unknown plan tier: {value}")
    result = await db.execute(
        select(SubscriptionPref).where(
            SubscriptionPref.provider_id == provider_id,
            SubscriptionPref.key == key,
        )
    )
    row = result.scalar_one_or_none()
    if value is None:
        if row is not None:
            await db.delete(row)
            await db.commit()
        return
    if row is not None:
        row.value = value
    else:
        db.add(SubscriptionPref(provider_id=provider_id, key=key, value=value))
    await db.commit()
