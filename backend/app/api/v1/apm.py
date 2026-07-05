"""APM (Agent Package Manager) API endpoints."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.apm_service import ApmService

router = APIRouter(prefix="/apm", tags=["APM"])


class ApmDependencyAddRequest(BaseModel):
    """Request to add an APM dependency."""
    name: str = Field(min_length=1, max_length=256, description="Package name")
    source: str = Field(min_length=1, max_length=512, description="Package source (e.g., owner/repo)")
    is_dev: bool = Field(default=False, description="Add as dev dependency")


class ApmDependencyRemoveRequest(BaseModel):
    """Request to remove an APM dependency."""
    name: str = Field(min_length=1, max_length=256, description="Package name to remove")


class ApmInstallRequest(BaseModel):
    """Request to run APM install."""
    frozen: bool = Field(default=False, description="Use --frozen flag for CI-safe install")


class ApmSyncRequest(BaseModel):
    """Request to sync dependencies between projects."""
    source_project: str = Field(min_length=1, description="Source project path")
    target_project: str = Field(min_length=1, description="Target project path")


@router.get("/status")
async def get_apm_status(
    project_path: str | None = Query(None, description="Project path"),
):
    """Get APM status for a project."""
    return ApmService.get_status(project_path)


@router.get("/deps")
async def list_dependencies(
    project_path: str | None = Query(None, description="Project path"),
):
    """List APM dependencies for a project."""
    return ApmService.list_dependencies(project_path)


@router.post("/deps")
async def add_dependency(
    request: ApmDependencyAddRequest,
    project_path: str | None = Query(None, description="Project path"),
):
    """Add a dependency to apm.yml."""
    result = ApmService.add_dependency(
        name=request.name,
        source=request.source,
        project_path=project_path,
        is_dev=request.is_dev,
    )
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.delete("/deps/{name}")
async def remove_dependency(
    name: str,
    project_path: str | None = Query(None, description="Project path"),
):
    """Remove a dependency from apm.yml."""
    result = ApmService.remove_dependency(name, project_path)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.post("/install")
async def install_dependencies(
    request: ApmInstallRequest,
    project_path: str | None = Query(None, description="Project path"),
):
    """Run APM install for a project."""
    result = ApmService.install_dependencies(project_path, request.frozen)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("message", "Install failed"))
    return result


@router.post("/sync")
async def sync_dependencies(request: ApmSyncRequest):
    """Sync dependencies from source to target project."""
    result = ApmService.sync_dependencies(
        request.source_project,
        request.target_project,
    )
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@router.get("/modules")
async def list_modules(
    project_path: str | None = Query(None, description="Project path"),
):
    """List installed APM modules."""
    return ApmService.get_installed_modules(project_path)
