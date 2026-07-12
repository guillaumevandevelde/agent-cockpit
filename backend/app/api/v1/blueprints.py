"""REST CRUD for project blueprints.

Endpoints:
- ``GET    /api/v1/blueprints``                 — list all stored blueprints
- ``POST   /api/v1/blueprints``                 — create a new blueprint
- ``GET    /api/v1/blueprints/{name}``          — read one blueprint
- ``PUT    /api/v1/blueprints/{name}``          — partial update
- ``DELETE /api/v1/blueprints/{name}``          — delete a blueprint
- ``POST   /api/v1/blueprints/{name}/apply``    — apply a blueprint to a project

Persistence lives in `app.services.blueprint.store.BlueprintStore` (file-based
JSON under ``~/.claude-registry/blueprints/``); application is delegated to
`app.services.blueprint.BlueprintService.apply`.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    Blueprint,
    BlueprintAgent,
    BlueprintApplyRequest,
    BlueprintApplyResponse,
    BlueprintCreate,
    BlueprintListResponse,
    BlueprintSettings,
    BlueprintSkill,
    BlueprintUpdate,
)
from app.services.blueprint import (
    BlueprintService,
)
from app.services.blueprint.store import (
    BlueprintAlreadyExists,
    BlueprintNameError,
    BlueprintNotFound,
    BlueprintStore,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/blueprints", tags=["Blueprints"])


def _store() -> BlueprintStore:
    """Per-request store. Cheap to construct (no I/O until first CRUD call)."""
    return BlueprintStore()


def _audit_to_response(audit) -> BlueprintApplyResponse:
    return BlueprintApplyResponse(
        blueprint_name=audit.blueprint_name,
        project_path=audit.project_path,
        written_files=list(audit.written_files),
        created_dirs=list(audit.created_dirs),
        applied_skills=list(audit.applied_skills),
        applied_agents=list(audit.applied_agents),
        skipped_existing=audit.skipped_existing,
    )


def _merge_update(stored: Blueprint, update: BlueprintUpdate) -> Blueprint:
    """Apply a partial BlueprintUpdate to a stored Blueprint.

    Uses `model_fields_set` to distinguish "field omitted" (leave alone) from
    "field set to null" (clear). Lists are sent as `[]` to clear and as
    `null` to leave alone — same convention. ``name``, ``version`` and
    ``created_at`` are always preserved (they're identity, not content).

    Implementation note: we go through `model_copy(update=...)` rather than
    re-constructing with `Blueprint(**payload)`, because the latter would
    fall back to Pydantic defaults for every omitted field (turning
    skills=[...] into skills=[]). `model_copy` keeps the existing values and
    only overrides what the client actually sent.
    """
    payload: dict[str, Any] = {}
    for field_name in update.model_fields_set:
        payload[field_name] = getattr(update, field_name)
    return stored.model_copy(update=payload)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=BlueprintListResponse)
async def list_blueprints() -> BlueprintListResponse:
    """List all blueprints stored on this machine."""
    try:
        blueprints = _store().list()
    except Exception as e:
        logger.exception("blueprint list failed")
        raise HTTPException(status_code=500, detail=f"Failed to list blueprints: {e}")
    return BlueprintListResponse(blueprints=blueprints)


@router.post("", response_model=Blueprint, status_code=201)
async def create_blueprint(payload: BlueprintCreate) -> Blueprint:
    """Create and persist a new blueprint.

    ``name`` must be unique and match the slug pattern enforced by the
    store. A duplicate name returns 409; an invalid name returns 400.
    """
    # Materialise sub-models with sensible defaults so the API client can
    # omit `settings` entirely and still get a valid Blueprint.
    settings = payload.settings or BlueprintSettings()
    skills = payload.skills
    agents = payload.agents
    try:
        blueprint = Blueprint(
            name=payload.name,
            description=payload.description,
            settings=settings,
            skills=skills,
            agents=agents,
            statusline=payload.statusline,
            output_style=payload.output_style,
            claudemd=payload.claudemd,
        )
        return _store().save(blueprint)
    except BlueprintNameError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except BlueprintAlreadyExists as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        # Pydantic validation errors raise ValueError; surface as 400.
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{name}", response_model=Blueprint)
async def get_blueprint(name: str) -> Blueprint:
    """Read a single blueprint by name."""
    try:
        return _store().get(name)
    except BlueprintNameError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except BlueprintNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/{name}", response_model=Blueprint)
async def update_blueprint(name: str, payload: BlueprintUpdate) -> Blueprint:
    """Partial update. Field semantics:

    - field omitted from body → leave stored value alone
    - field set to ``null``  → clear (for nullable fields) / no-op (for list fields, see below)
    - field set to ``[]``    → clear the list (only meaningful for `skills`/`agents`)
    - field set to a value   → replace

    ``name``, ``version``, ``subdirs`` and ``created_at`` are immutable on
    the wire (see `BlueprintUpdate` schema docstring).
    """
    try:
        store = _store()
        stored = store.get(name)
        merged = _merge_update(stored, payload)
        return store.save(merged, overwrite=True)
    except BlueprintNameError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except BlueprintNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{name}", status_code=204)
async def delete_blueprint(name: str) -> None:
    """Delete a blueprint. 404 if it doesn't exist."""
    try:
        _store().delete(name)
    except BlueprintNameError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except BlueprintNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{name}/apply", response_model=BlueprintApplyResponse)
async def apply_blueprint(name: str, payload: BlueprintApplyRequest) -> BlueprintApplyResponse:
    """Apply a stored blueprint to a project on disk.

    The apply engine is atomic (writes go to ``<project>/.claude.tmp/`` and
    are renamed into place) and idempotent (a populated ``.claude/`` is
    left alone unless ``force=true``). See ``BlueprintService.apply``.
    """
    try:
        blueprint = _store().get(name)
    except BlueprintNameError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except BlueprintNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        audit = BlueprintService(blueprint=blueprint).apply(
            payload.project_path, force=payload.force,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:  # noqa: BLE001 — surface any engine failure as 500
        logger.exception("blueprint apply failed for %s at %s", name, payload.project_path)
        raise HTTPException(status_code=500, detail=f"Apply failed: {e}")

    return _audit_to_response(audit)