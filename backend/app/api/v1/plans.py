"""Plan history browser API endpoints."""

from fastapi import APIRouter, HTTPException, Query

from app.models.schemas import (
    PlanCreate,
    PlanDetailResponse,
    PlanListResponse,
    PlanSearchResponse,
    PlanStatsResponse,
    PlanUpdate,
)
from app.services.plan_service import PlanService

router = APIRouter()


@router.get("/plans", response_model=PlanListResponse)
async def list_plans(
    project_path: str | None = Query(None, description="Active project path for settings resolution"),
):
    """List all plan files sorted by modification time (newest first)."""
    try:
        plans_dir = PlanService.resolve_plans_dir(project_path)
        plans = PlanService.list_plans(plans_dir)
        return {"plans": plans, "total": len(plans)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list plans: {str(e)}")


@router.get("/plans/stats", response_model=PlanStatsResponse)
async def get_plan_stats(
    project_path: str | None = Query(None, description="Active project path"),
):
    """Get plan statistics for dashboard."""
    try:
        plans_dir = PlanService.resolve_plans_dir(project_path)
        return PlanService.get_plan_stats(plans_dir)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get plan stats: {str(e)}")


@router.get("/plans/search", response_model=PlanSearchResponse)
async def search_plans(
    q: str = Query(..., min_length=1, description="Search query"),
    project_path: str | None = Query(None, description="Active project path"),
):
    """Search plans by title and content."""
    try:
        plans_dir = PlanService.resolve_plans_dir(project_path)
        results = PlanService.search_plans(plans_dir, q)
        return {"results": results, "query": q, "total": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to search plans: {str(e)}")


@router.get("/plans/{filename}", response_model=PlanDetailResponse)
async def get_plan_detail(
    filename: str,
    project_path: str | None = Query(None, description="Active project path"),
):
    """Get full plan detail with linked sessions."""
    try:
        plans_dir = PlanService.resolve_plans_dir(project_path)
        plan_data = PlanService.get_plan(plans_dir, filename)

        if not plan_data:
            raise HTTPException(status_code=404, detail="Plan not found")

        linked_sessions = PlanService.get_plan_sessions(plan_data["slug"])
        plan_data["linked_sessions"] = linked_sessions
        return {"plan": plan_data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get plan: {str(e)}")


@router.post("/plans", response_model=PlanDetailResponse, status_code=201)
async def create_plan(
    payload: PlanCreate,
    project_path: str | None = Query(None, description="Active project path"),
):
    """Create a new plan file."""
    try:
        plans_dir = PlanService.resolve_plans_dir(project_path)
        plan_data = PlanService.create_plan(plans_dir, payload.filename, payload.content)
        plan_data["linked_sessions"] = PlanService.get_plan_sessions(plan_data["slug"])
        return {"plan": plan_data}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create plan: {str(e)}")


@router.put("/plans/{filename}", response_model=PlanDetailResponse)
async def update_plan(
    filename: str,
    payload: PlanUpdate,
    project_path: str | None = Query(None, description="Active project path"),
):
    """Update an existing plan's content."""
    try:
        plans_dir = PlanService.resolve_plans_dir(project_path)
        plan_data = PlanService.update_plan(plans_dir, filename, payload.content)
        if plan_data is None:
            raise HTTPException(status_code=404, detail="Plan not found")
        plan_data["linked_sessions"] = PlanService.get_plan_sessions(plan_data["slug"])
        return {"plan": plan_data}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update plan: {str(e)}")


@router.delete("/plans/{filename}", status_code=204)
async def delete_plan(
    filename: str,
    project_path: str | None = Query(None, description="Active project path"),
):
    """Delete a plan file."""
    try:
        plans_dir = PlanService.resolve_plans_dir(project_path)
        deleted = PlanService.delete_plan(plans_dir, filename)
        if not deleted:
            raise HTTPException(status_code=404, detail="Plan not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete plan: {str(e)}")
