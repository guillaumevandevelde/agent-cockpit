"""Aan/uit-schakelaar voor de zelfverbeteringsloop, per bord.

Eigen routermodule in plaats van een toevoeging aan ``kanban/router.py``: dat
bestand staat op 2368 regels en valt onder de omvangsratel
(``docs/cockpit/architectuur.md`` regel 3), dus het mag niet groeien. Een
nieuwe, kleine module is hier ook gewoon de betere vorm.

Waarom de schakelaar bestaat en wat hij raakt: ``app/kanban/self_improve.py``.
"""
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.kanban import self_improve
from app.kanban.db import KanbanSessionLocal

router = APIRouter(prefix="/kanban", tags=["kanban"])


class SelfImproveRequest(BaseModel):
    project_key: str
    enabled: bool


@router.get("/self-improve")
async def get_self_improve(project_key: str = Query(...)):
    """Lees de schakelaar. ``enabled=true`` betekent: de loop draait.

    Standaard aan — een bord zonder rij gedraagt zich zoals het altijd deed.
    """
    async with KanbanSessionLocal() as session:
        return {
            "project_key": project_key,
            "enabled": await self_improve.is_enabled(session, project_key),
        }


@router.post("/self-improve")
async def set_self_improve(payload: SelfImproveRequest):
    """Zet de schakelaar om. Idempotent.

    Werkt bij de volgende dispatch-tick, zonder herstart. Zet je hem uit, dan
    stopt de loop aan twee kanten: gedispatchte sessies krijgen de instructie
    geen retro te draaien en geen `[self-improve]`-kaarten te filen, en de
    dispatcher slaat bestaande kaarten van die soort over.
    """
    async with KanbanSessionLocal() as session:
        await self_improve.set_enabled(session, payload.project_key, payload.enabled)
        await session.commit()
    return {"project_key": payload.project_key, "enabled": payload.enabled}
