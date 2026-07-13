"""REST CRUD for the CI-template engine (`CITemplateService`).

Endpoints:
- ``GET  /api/v1/ci/templates``                       — list registered profiles
- ``POST /api/v1/ci/templates/{profile}/apply``       — render + write a workflow

The apply endpoint is **idempotent** (an existing workflow file is left
alone unless ``force=true``) and writes only under
``<project>/.github/workflows/<profile>.yml`` — no other files are touched.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.models.schemas import (
    CITemplateApplyRequest,
    CITemplateApplyResponse,
    CITemplateInfo,
    CITemplateListResponse,
    CITemplateParameterInfo,
)
from app.services.ci_templates import (
    CITemplateApplyResult,
    CITemplateError,
    CITemplateProfileUnknown,
    CITemplateRenderFailed,
    CITemplateService,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/ci/templates", tags=["CI Templates"])


def _service() -> CITemplateService:
    """Per-request service. Cheap to construct (no I/O until first apply)."""
    return CITemplateService()


def _info_to_response(info) -> CITemplateInfo:
    """Map the service-layer dataclass to the Pydantic API model.

    Pydantic coerces the dataclass's `tuple[str, ...]` `parameters` into a
    list of `CITemplateParameterInfo` (default `None` so the API surface
    can grow `default` values without breaking the dataclass).
    """
    return CITemplateInfo(
        name=info.name,
        description=info.description,
        filename=info.filename,
        parameters=[CITemplateParameterInfo(name=p) for p in info.parameters],
    )


def _result_to_response(result: CITemplateApplyResult) -> CITemplateApplyResponse:
    return CITemplateApplyResponse(
        profile=result.profile,
        project_path=result.project_path,
        written_file=result.written_file,
        skipped_existing=result.skipped_existing,
        force=result.force,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=CITemplateListResponse)
async def list_ci_templates() -> CITemplateListResponse:
    """Return the registered CI profiles (sorted by name)."""
    try:
        templates = _service().list_templates()
    except Exception as e:  # noqa: BLE001 — surface any read failure as 500
        logger.exception("CI templates list failed")
        raise HTTPException(status_code=500, detail=f"Failed to list CI templates: {e}")
    return CITemplateListResponse(templates=[_info_to_response(t) for t in templates])


@router.post("/{profile}/apply", response_model=CITemplateApplyResponse)
async def apply_ci_template(
    profile: str, payload: CITemplateApplyRequest,
) -> CITemplateApplyResponse:
    """Render ``profile`` and write it to ``<project>/.github/workflows/``.

    See `CITemplateService.apply` for the on-disk behaviour; this endpoint
    only maps errors to HTTP status codes.
    """
    try:
        result = _service().apply(
            payload.project_path,
            profile,
            force=payload.force,
            **payload.parameters,
        )
    except CITemplateProfileUnknown as e:
        raise HTTPException(status_code=404, detail=str(e))
    except CITemplateRenderFailed as e:
        # Missing parameter or Jinja syntax error — surface as 400.
        raise HTTPException(status_code=400, detail=str(e))
    except CITemplateError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:  # noqa: BLE001 — surface any engine failure as 500
        logger.exception("CI template apply failed for %s at %s", profile, payload.project_path)
        raise HTTPException(status_code=500, detail=f"Apply failed: {e}")

    return _result_to_response(result)