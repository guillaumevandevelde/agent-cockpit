"""REST endpoints for RunService — sandboxed spawn of a built app.

Mounted at ``/runs`` so the wire path is ``/api/v1/runs/app`` (POST start,
GET list, DELETE stop, GET logs), per the kanban-card acceptance criteria.
The agent-bridge router already owns the ``/agent-bridge`` prefix and is a
different concept; we deliberately keep these prefixes apart to avoid
breaking existing clients."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.run_service import RunInstance, run_service

router = APIRouter(prefix="/runs", tags=["App Runs"])


class StartRequest(BaseModel):
    project_path: str
    command: list[str] = Field(min_length=1)
    env: dict[str, str] | None = None
    port: int | None = None
    health_path: str | None = None
    health_timeout_s: int = 30


def _serialize(instance: RunInstance) -> dict:
    return instance.model_dump(mode="json")


@router.post("/app")
async def start_run(request: StartRequest) -> dict:
    """Start a sandboxed instance of a built app.

    The HTTP response is returned as soon as the row is persisted and the
    background driver is launched — status will be ``starting`` and the
    caller can poll ``GET /runs/app/{instance_id}`` to follow progress."""
    try:
        instance = await run_service.start(
            project_path=request.project_path,
            command=request.command,
            env=request.env,
            port=request.port,
            health_path=request.health_path,
            health_timeout_s=request.health_timeout_s,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _serialize(instance)


@router.get("/app")
async def list_runs(project_path: Annotated[str, Query(...)]) -> dict:
    """List runs for one project, newest first."""
    rows = await run_service.list(project_path=project_path)
    return {"runs": [_serialize(r) for r in rows]}


@router.get("/app/{instance_id}")
async def get_run(instance_id: str) -> dict:
    instance = await run_service.get(instance_id)
    if instance is None:
        raise HTTPException(status_code=404, detail=f"Run {instance_id} not found")
    return _serialize(instance)


@router.get("/app/{instance_id}/logs")
async def get_logs(instance_id: str, offset: int = Query(0, ge=0)) -> dict:
    result = await run_service.logs(instance_id, offset=offset)
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail=f"Run {instance_id} not found")
    return result


@router.delete("/app/{instance_id}")
async def stop_run(instance_id: str) -> dict:
    stopped = await run_service.stop(instance_id)
    if not stopped:
        raise HTTPException(status_code=404, detail=f"Run {instance_id} not found or already stopped")
    return {"success": True, "instance_id": instance_id}