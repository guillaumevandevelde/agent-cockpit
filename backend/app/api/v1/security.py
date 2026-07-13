"""REST endpoints for per-project security profiles.

Mounted at ``/security`` (so the full path is ``/api/v1/security/profiles``).
``project_path`` is a required query parameter to keep the URL contract
simple — there's no numeric ID, the security profile is keyed by the
project's filesystem path which the rest of the API also uses.

Behavioural notes:
- GET on an unknown project lazy-creates the safe-by-default row (200) and
  persists it, so subsequent PATCH/DELETE work without a 404 dance.
- DELETE on an unknown project returns 200 with ``deleted=False`` rather
  than 404 — the contract is "the project now has the default profile"
  either way and clients shouldn't need to special-case.
- PATCH on an unknown project returns 404 — a partial update against an
  absent profile is genuinely ambiguous and shouldn't auto-create.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.security_profile_schemas import (
    SecurityProfileDeleteResponse,
    SecurityProfilePatch,
    SecurityProfileResponse,
    SecurityProfileUpsert,
    to_response,
)
from app.services.security_profile_service import SecurityProfileService

router = APIRouter(prefix="/security", tags=["Security"])


@router.get("/profiles", response_model=SecurityProfileResponse)
async def get_profile(
    project_path: str = Query(..., description="Absolute filesystem path of the project"),
    db: AsyncSession = Depends(get_db),
) -> SecurityProfileResponse:
    """Return the security profile, lazy-creating the safe-by-default if absent."""
    svc = SecurityProfileService(db)
    row = await svc.get_or_create_for_project(project_path)
    return to_response(row)


@router.put("/profiles", response_model=SecurityProfileResponse)
async def put_profile(
    payload: SecurityProfileUpsert,
    project_path: str = Query(..., description="Absolute filesystem path of the project"),
    db: AsyncSession = Depends(get_db),
) -> SecurityProfileResponse:
    """Idempotent full-replace upsert."""
    svc = SecurityProfileService(db)
    row = await svc.upsert(project_path, payload.model_dump())
    return to_response(row)


@router.patch("/profiles", response_model=SecurityProfileResponse)
async def patch_profile(
    payload: SecurityProfilePatch,
    project_path: str = Query(..., description="Absolute filesystem path of the project"),
    db: AsyncSession = Depends(get_db),
) -> SecurityProfileResponse:
    """Partial update — only fields present in the body are applied."""
    svc = SecurityProfileService(db)
    row = await svc.patch(project_path, payload.model_dump(exclude_unset=True))
    if row is None:
        raise HTTPException(status_code=404, detail="Security profile not found")
    return to_response(row)


@router.delete("/profiles", response_model=SecurityProfileDeleteResponse)
async def delete_profile(
    project_path: str = Query(..., description="Absolute filesystem path of the project"),
    db: AsyncSession = Depends(get_db),
) -> SecurityProfileDeleteResponse:
    """Drop the stored profile and return the safe-by-default that takes its place."""
    svc = SecurityProfileService(db)
    deleted = await svc.delete(project_path)
    recreated = await svc.get_or_create_for_project(project_path)
    return SecurityProfileDeleteResponse(
        project_path=project_path,
        deleted=deleted,
        recreated_default=to_response(recreated),
    )