"""REST endpoints for the DeployTarget abstraction.

Endpoints (under ``/api/v1/deploy/``):

- ``GET  /targets``                    — list registered deploy targets
- ``POST /targets/{target_id}/invoke`` — run a deploy against ``target_id``

The invoke endpoint is fire-and-forget from the operator's perspective:
it blocks on the deploy itself (which can take minutes for a big
``docker buildx``) and returns the full ``DeployResult``. A future
streaming variant (WebSocket log tail) would re-use the same
``DeployTarget.deploy`` coroutine without changing this contract — see
the module docstring on ``app.services.deploy``.

Authentication for the GHCR target: callers pass
``credentials.ghcr_token`` in the request body, or omit it to fall back
to ``gh auth token``. Production callers should source the token from
the project-scoped ``SecretStore`` (``/api/v1/secrets``); this endpoint
does not look up secrets itself because the project↔target binding is
not yet modeled in the MVP.
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from app.services.deploy import get_target, list_targets

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/deploy", tags=["Deploy"])


# -- schemas ----------------------------------------------------------------


class DeployTargetInfo(BaseModel):
    """Metadata for one registered deploy target."""

    id: str
    target_type: str


class DeployTargetListResponse(BaseModel):
    """All registered deploy targets, sorted by id."""

    targets: list[DeployTargetInfo]


class DeployInvokeRequest(BaseModel):
    """Body for ``POST /targets/{target_id}/invoke``.

    ``project_path`` is the local directory ``docker buildx`` will be
    run from (also where we infer ``ghcr.io/<owner>/<repo>`` from).
    ``tag`` is the image tag (validated server-side — see
    ``app.services.deploy.GHCRDeployTarget._TAG_RE``). ``credentials``
    is target-specific; for GHCR, ``{"ghcr_token": "..."}`` overrides
    the ``gh auth token`` fallback.
    """

    project_path: Annotated[str, Field(min_length=1, max_length=4096)]
    tag: Annotated[str, Field(min_length=1, max_length=128)]
    credentials: dict[str, str] | None = None


class DeployInvokeResponse(BaseModel):
    """Result of a single deploy invocation.

    Mirrors ``DeployResult.to_dict()`` from the service module — see
    that docstring for the field semantics. We expose ``status`` as a
    string so the frontend can map it to coloured chips without an
    enum dependency.
    """

    status: str
    image_ref: str | None
    logs: str
    started_at: str
    completed_at: str | None
    error: str | None


# -- routes -----------------------------------------------------------------


@router.get("/targets", response_model=DeployTargetListResponse)
async def list_deploy_targets() -> DeployTargetListResponse:
    """Return the sorted registry of available deploy targets.

    Cheap — no I/O; the registry is built once at import time. Useful
    for the UI to render a target picker without hardcoding ids.
    """
    return DeployTargetListResponse(
        targets=[
            DeployTargetInfo(id=t.id, target_type=t.target_type)
            for t in list_targets()
        ]
    )


@router.post(
    "/targets/{target_id}/invoke",
    response_model=DeployInvokeResponse,
    responses={
        404: {"description": "Unknown deploy target id"},
        422: {"description": "Invalid request body (bad project_path or tag)"},
        500: {"description": "Deploy crashed unexpectedly (not the same as a failed deploy)"},
    },
)
async def invoke_deploy_target(
    target_id: Annotated[str, Path(min_length=1, max_length=64)],
    payload: DeployInvokeRequest,
) -> DeployInvokeResponse:
    """Run a deploy against ``target_id`` and return the full result.

    ``status`` in the response is one of ``pending | building | pushing
    | completed | failed`` (see ``DeployStatus``). The endpoint itself
    returns 200 even for failed deploys — the failure lives in the
    body, because "the deploy ran but the image push failed" is not an
    HTTP error, it's a deploy outcome.
    """
    try:
        target = get_target(target_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    logger.info(
        "deploy invoke target=%s project=%s tag=%s",
        target_id,
        payload.project_path,
        payload.tag,
    )
    try:
        result = await target.deploy(
            payload.project_path,
            payload.tag,
            credentials=payload.credentials,
        )
    except Exception as e:
        # ``deploy`` is documented to never raise; if it does, surface
        # the unexpected crash as a 500 (distinct from "deploy ran and
        # reported failed" which lands in the body).
        logger.exception(
            "deploy crashed target=%s project=%s tag=%s",
            target_id,
            payload.project_path,
            payload.tag,
        )
        raise HTTPException(status_code=500, detail=f"deploy crashed: {e}") from e

    body = result.to_dict()
    return DeployInvokeResponse(
        status=body["status"],
        image_ref=body["image_ref"],
        logs=body["logs"],
        started_at=body["started_at"],
        completed_at=body["completed_at"],
        error=body["error"],
    )