"""REST API for autonomy profiles and active mode."""
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.autonomy import AutonomyProfile
from app.models.autonomy_schemas import (
    AutonomyProfileCreate,
    AutonomyProfileUpdate,
    AutonomyProfileResponse,
    ActiveAutonomy,
    ActiveAutonomyUpdate,
)

router = APIRouter(prefix="/autonomy", tags=["Autonomy"])

# In-memory active mode (resets on restart; persisted via default profile)
_active_mode: str = "suggest"


@router.get("/profiles", response_model=list[AutonomyProfileResponse])
async def list_profiles():
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(AutonomyProfile).order_by(AutonomyProfile.name)
        )).scalars().all()
        return rows


@router.post("/profiles", response_model=AutonomyProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_profile(payload: AutonomyProfileCreate):
    async with AsyncSessionLocal() as s:
        existing = (await s.execute(
            select(AutonomyProfile).where(AutonomyProfile.name == payload.name)
        )).scalars().first()
        if existing:
            raise HTTPException(409, f"Profile '{payload.name}' already exists")
        if payload.is_default:
            await _clear_default(s)
        profile = AutonomyProfile(**payload.model_dump())
        s.add(profile)
        await s.commit()
        await s.refresh(profile)
        return profile


@router.get("/profiles/{profile_id}", response_model=AutonomyProfileResponse)
async def get_profile(profile_id: int):
    async with AsyncSessionLocal() as s:
        profile = await s.get(AutonomyProfile, profile_id)
        if not profile:
            raise HTTPException(404, "Profile not found")
        return profile


@router.patch("/profiles/{profile_id}", response_model=AutonomyProfileResponse)
async def update_profile(profile_id: int, payload: AutonomyProfileUpdate):
    async with AsyncSessionLocal() as s:
        profile = await s.get(AutonomyProfile, profile_id)
        if not profile:
            raise HTTPException(404, "Profile not found")
        data = payload.model_dump(exclude_unset=True)
        if data.get("is_default"):
            await _clear_default(s)
        for k, v in data.items():
            setattr(profile, k, v)
        await s.commit()
        await s.refresh(profile)
        return profile


@router.delete("/profiles/{profile_id}")
async def delete_profile(profile_id: int):
    async with AsyncSessionLocal() as s:
        profile = await s.get(AutonomyProfile, profile_id)
        if not profile:
            raise HTTPException(404, "Profile not found")
        await s.delete(profile)
        await s.commit()
        return {"deleted": True}


@router.get("/active", response_model=ActiveAutonomy)
async def get_active():
    global _active_mode
    async with AsyncSessionLocal() as s:
        default = (await s.execute(
            select(AutonomyProfile).where(AutonomyProfile.is_default == True)  # noqa: E712
        )).scalars().first()
        if default:
            _active_mode = default.mode
            return ActiveAutonomy(
                mode=default.mode,
                profile_name=default.name,
                description=default.description,
            )
    labels = {
        "plan": "Read-only planning",
        "suggest": "Interactive approval",
        "auto": "Full autonomy",
    }
    return ActiveAutonomy(
        mode=_active_mode,
        profile_name="Built-in",
        description=labels.get(_active_mode, "Interactive approval"),
    )


@router.put("/active")
async def set_active(payload: ActiveAutonomyUpdate):
    global _active_mode
    _active_mode = payload.mode
    return {"mode": _active_mode}


async def _clear_default(session):
    """Remove default flag from all profiles."""
    rows = (await session.execute(
        select(AutonomyProfile).where(AutonomyProfile.is_default == True)  # noqa: E712
    )).scalars().all()
    for row in rows:
        row.is_default = False
