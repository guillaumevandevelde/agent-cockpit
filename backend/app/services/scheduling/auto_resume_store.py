"""Duurzame opslag voor de auto-resume-instellingen.

``AutoResumeService`` houdt ``_enabled`` en ``_messages`` in geheugen-dicts.
Dat is prima als cache, maar het wás ook de enige plek: auto-resume aanzetten
via de API overleefde geen herstart van de backend, en de gebruiker kreeg geen
enkel signaal — de sessie werd gewoon nooit hervat.

Zelfde regel als bij de scheduler: **de database is de waarheid, het geheugen
is een cache.** Schrijven gaat door naar de tabel, en bij het opstarten leest
``reconciler.hydrate_auto_resume`` de rijen terug in de dicts.
"""
import logging

logger = logging.getLogger(__name__)


async def load_all() -> list[tuple[str, bool, str | None]]:
    """Alle opgeslagen instellingen als ``(cwd, enabled, message)``."""
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.auto_resume import AutoResumeConfig

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(AutoResumeConfig))).scalars().all()
        return [(r.cwd, bool(r.enabled), r.message) for r in rows]


async def save(cwd: str, *, enabled: bool | None = None, message: str | None = None) -> None:
    """Leg de instelling vast. Alleen de meegegeven velden wijzigen.

    Idempotent; maakt de rij aan als hij nog niet bestaat.
    """
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.auto_resume import AutoResumeConfig

    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            select(AutoResumeConfig).where(AutoResumeConfig.cwd == cwd)
        )).scalar_one_or_none()
        if row is None:
            row = AutoResumeConfig(cwd=cwd, enabled=False, message=None)
            session.add(row)
        if enabled is not None:
            row.enabled = enabled
        if message is not None:
            row.message = message
        await session.commit()
