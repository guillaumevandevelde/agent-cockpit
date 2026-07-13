"""Sandcastle API endpoints."""

from datetime import UTC

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, field_validator

from app.services.sandcastle_service import sandcastle_service

router = APIRouter(prefix="/sandcastle", tags=["sandcastle"])

_NETWORK_MODES = {"none", "bridge", "restricted"}


class SandcastleConfigUpdate(BaseModel):
    """Request to update sandcastle config."""
    enabled: bool | None = None
    sandbox_provider: str | None = None
    agent_provider: str | None = None
    model: str | None = None
    branch_strategy: str | None = None
    docker_image: str | None = None
    max_iterations: int | None = None
    idle_timeout_seconds: int | None = None
    permission_mode: str | None = None
    memory_limit_mb: int | None = None
    cpu_quota: float | None = None
    pids_limit: int | None = None
    read_only_rootfs: bool | None = None
    network_mode: str | None = None
    egress_allowlist: list[str] | None = None

    @field_validator("network_mode")
    @classmethod
    def _validate_network_mode(cls, v: str | None) -> str | None:
        if v is not None and v not in _NETWORK_MODES:
            raise ValueError(f"network_mode must be one of {sorted(_NETWORK_MODES)}")
        return v

    @field_validator("memory_limit_mb", "pids_limit")
    @classmethod
    def _validate_positive(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("must be a positive integer")
        return v

    @field_validator("cpu_quota")
    @classmethod
    def _validate_cpu_quota(cls, v: float | None) -> float | None:
        if v is not None and v <= 0:
            raise ValueError("cpu_quota must be > 0")
        return v


def _serialize_config(config) -> dict:
    """Full JSON view of a SandcastleConfig ORM row."""
    return {
        "id": config.id,
        "project_path": config.project_path,
        "enabled": config.enabled,
        "sandbox_provider": config.sandbox_provider,
        "agent_provider": config.agent_provider,
        "model": config.model,
        "branch_strategy": config.branch_strategy,
        "docker_image": config.docker_image,
        "max_iterations": config.max_iterations,
        "idle_timeout_seconds": config.idle_timeout_seconds,
        "permission_mode": config.permission_mode,
        "memory_limit_mb": config.memory_limit_mb,
        "cpu_quota": config.cpu_quota,
        "pids_limit": config.pids_limit,
        "read_only_rootfs": config.read_only_rootfs,
        "network_mode": config.network_mode,
        "egress_allowlist": config.egress_allowlist,
        "created_at": config.created_at.isoformat() if config.created_at else None,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
    }


class SandcastleRunRequest(BaseModel):
    """Request to start a sandcastle run."""
    prompt: str
    config_id: int | None = None
    branch_name: str | None = None
    max_iterations: int | None = None


@router.get("/health")
async def check_health():
    """Check sandcastle health: Docker/Podman availability, Node.js, etc."""
    return await sandcastle_service.check_health()


class BuildImageRequest(BaseModel):
    """Request to build Docker image."""
    image_name: str = "sandcastle:local"
    runtime: str | None = None  # "docker" | "podman" | None (auto-detect)
    force: bool = False  # rebuild even if the image already exists


@router.post("/build-image")
async def build_image(request: BuildImageRequest = BuildImageRequest()):
    """Build the sandcastle image with the given (or auto-detected) container runtime.

    Idempotent: a no-op returning success if the image already exists (pass
    force=True to rebuild)."""
    return await sandcastle_service.build_docker_image(
        request.image_name, request.runtime, request.force
    )


@router.get("/config")
async def get_config(project_path: str = Query(...)):
    """Get sandcastle config for a project."""
    config = await sandcastle_service.get_config(project_path)
    if not config:
        # Return default config
        return {
            "id": None,
            "project_path": project_path,
            "enabled": False,
            "sandbox_provider": "no-sandbox",
            "agent_provider": "claude-code",
            "model": "sonnet",
            "branch_strategy": "merge-to-head",
            "docker_image": None,
            "max_iterations": 1,
            "idle_timeout_seconds": 600,
            "permission_mode": "acceptEdits",
            "memory_limit_mb": None,
            "cpu_quota": None,
            "pids_limit": None,
            "read_only_rootfs": False,
            "network_mode": "bridge",
            "egress_allowlist": None,
        }
    return _serialize_config(config)


@router.put("/config")
async def update_config(project_path: str = Query(...), request: SandcastleConfigUpdate = ...):
    """Create or update sandcastle config for a project."""
    updates = request.model_dump(exclude_none=True)
    config = await sandcastle_service.update_config(project_path, updates)
    return _serialize_config(config)


@router.patch("/config/{config_id}/toggle")
async def toggle_config(config_id: int):
    """Toggle enabled status for a config."""
    try:
        config = await sandcastle_service.toggle_config(config_id)
        return {
            "id": config.id,
            "project_path": config.project_path,
            "enabled": config.enabled,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/configs")
async def list_configs():
    """List all sandcastle configs."""
    configs = await sandcastle_service.list_configs()
    return {
        "configs": [
            {
                "id": c.id,
                "project_path": c.project_path,
                "enabled": c.enabled,
                "sandbox_provider": c.sandbox_provider,
                "agent_provider": c.agent_provider,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in configs
        ]
    }


@router.get("/stats")
async def get_stats():
    """Get sandcastle run statistics."""
    from sqlalchemy import func, select

    from app.database import AsyncSessionLocal
    from app.models.sandcastle import SandcastleRun
    
    async with AsyncSessionLocal() as session:
        # Total runs
        total_result = await session.execute(
            select(func.count(SandcastleRun.id))
        )
        total_runs = total_result.scalar() or 0
        
        # Runs by status
        status_result = await session.execute(
            select(SandcastleRun.status, func.count(SandcastleRun.id))
            .group_by(SandcastleRun.status)
        )
        runs_by_status = {row[0]: row[1] for row in status_result.all()}
        
        # Recent runs (last 24 hours)
        from datetime import datetime, timedelta
        cutoff = datetime.now(UTC) - timedelta(hours=24)
        recent_result = await session.execute(
            select(func.count(SandcastleRun.id))
            .where(SandcastleRun.created_at >= cutoff)
        )
        recent_runs = recent_result.scalar() or 0
        
        # Active runs
        active_result = await session.execute(
            select(func.count(SandcastleRun.id))
            .where(SandcastleRun.status == "running")
        )
        active_runs = active_result.scalar() or 0
        
        return {
            "total_runs": total_runs,
            "runs_by_status": runs_by_status,
            "recent_runs_24h": recent_runs,
            "active_runs": active_runs,
        }


@router.post("/runs")
async def start_run(project_path: str = Query(...), request: SandcastleRunRequest = ...):
    """Start a new sandcastle run."""
    try:
        run = await sandcastle_service.start_run(
            project_path=project_path,
            prompt=request.prompt,
            config_id=request.config_id,
            branch_name=request.branch_name,
            max_iterations=request.max_iterations,
        )
        return {
            "id": run.id,
            "project_path": run.project_path,
            "prompt": run.prompt,
            "status": run.status,
            "created_at": run.created_at.isoformat() if run.created_at else None,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class ParallelRunRequest(BaseModel):
    """Request to start parallel sandcastle runs."""
    prompts: list[dict[str, str]]  # [{"prompt": "...", "branch_name": "..."}]
    config_id: int | None = None
    use_shared_sandbox: bool = False


@router.post("/runs/parallel")
async def start_parallel_runs(project_path: str = Query(...), request: ParallelRunRequest = ...):
    """Start multiple sandcastle runs in parallel."""
    try:
        runs = await sandcastle_service.start_parallel_runs(
            project_path=project_path,
            prompts=request.prompts,
            config_id=request.config_id,
            use_shared_sandbox=request.use_shared_sandbox,
        )
        return {
            "runs": [
                {
                    "id": r.id,
                    "project_path": r.project_path,
                    "prompt": r.prompt,
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in runs
            ]
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/runs")
async def list_runs(
    project_path: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=100),
):
    """List sandcastle runs with optional filters."""
    runs = await sandcastle_service.list_runs(
        project_path=project_path,
        status=status,
        limit=limit,
    )
    return {
        "runs": [
            {
                "id": r.id,
                "project_path": r.project_path,
                "prompt": r.prompt,
                "status": r.status,
                "branch": r.branch,
                "commits": r.commits,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in runs
        ]
    }


@router.get("/runs/graph")
async def get_run_graph(project_path: str = Query(...)):
    """Group a project's runs into a lightweight DAG (batch fan-out) for the run-graph view."""
    return await sandcastle_service.get_run_graph(project_path)


@router.get("/runs/{run_id}")
async def get_run(run_id: int):
    """Get a sandcastle run by ID."""
    run = await sandcastle_service.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return {
        "id": run.id,
        "project_path": run.project_path,
        "prompt": run.prompt,
        "status": run.status,
        "branch": run.branch,
        "commits": run.commits,
        "stdout": run.stdout,
        "stderr": run.stderr,
        "error": run.error,
        "pid": run.pid,
        "log_file_path": run.log_file_path,
        "output": run.output,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
    }


@router.get("/runs/{run_id}/logs")
async def get_run_logs(run_id: int, offset: int = 0):
    """Get logs for a sandcastle run with optional offset for streaming."""
    logs = await sandcastle_service.get_run_logs(run_id, offset)
    if "error" in logs and logs["error"] == "Run not found":
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return logs


import asyncio
import json

from fastapi.responses import StreamingResponse

# Absolute cap so a wedged run (e.g. one stuck in "running") can't keep an SSE
# connection — and its server task — alive forever. 2h at 1s/poll.
_STREAM_MAX_POLLS = 7200


@router.get("/runs/{run_id}/stream")
async def stream_run_logs(run_id: int, request: Request):
    """Stream logs for a sandcastle run via Server-Sent Events."""
    async def event_generator():
        offset = 0
        for _ in range(_STREAM_MAX_POLLS):
            # Stop promptly if the client navigated away / closed the tab.
            if await request.is_disconnected():
                break

            logs = await sandcastle_service.get_run_logs(run_id, offset)

            if "error" in logs and logs["error"] == "Run not found":
                yield f"data: {json.dumps({'error': 'Run not found'})}\n\n"
                break

            # Send log update
            yield f"data: {json.dumps(logs)}\n\n"

            # Update offset for next iteration
            if "log_offset" in logs:
                offset = logs["log_offset"]

            # Check if run is complete
            if logs.get("status") in ("completed", "failed", "cancelled"):
                yield f"data: {json.dumps({'status': 'done'})}\n\n"
                break

            # Wait before next poll
            await asyncio.sleep(1)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: int):
    """Cancel a running sandcastle run (leaves the record in place)."""
    success = await sandcastle_service.cancel_run(run_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found or not running")
    return {"success": True}


@router.delete("/runs")
async def clear_runs(
    project_path: str | None = Query(None),
    include_running: bool = Query(False),
):
    """Bulk-delete run records. Terminal runs only unless include_running=true."""
    deleted = await sandcastle_service.clear_runs(
        project_path=project_path, include_running=include_running
    )
    return {"deleted": deleted}


@router.delete("/runs/{run_id}")
async def delete_run(run_id: int):
    """Delete a single run record (cancels it first if still active)."""
    success = await sandcastle_service.delete_run(run_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return {"success": True}


@router.get("/containers")
async def list_containers():
    """List running Docker/Podman sandcastle containers."""
    return await sandcastle_service.list_running_containers()


@router.get("/containers/{name}/logs/stream")
async def stream_container_logs(name: str, request: Request, runtime: str = Query(...)):
    """Stream a running sandcastle container's own stdout/stderr via SSE (`logs -f`)."""
    async def event_generator():
        try:
            async for line in sandcastle_service.stream_container_logs(name, runtime):
                if await request.is_disconnected():
                    break
                yield f"data: {json.dumps({'line': line})}\n\n"
        except ValueError as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
