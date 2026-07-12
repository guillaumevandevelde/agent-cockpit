"""Plan history browser API endpoints.

Kanban-DB-backed (kanban card 727470a8). The legacy file-backed
``PlanService`` is no longer used by these endpoints; it stays in the
codebase as a reference for ``get_plan_sessions`` only (which is
folder-scan logic, not CRUD). All CRUD goes through
:class:`app.services.kanban_plan_service.KanbanPlanService` against the
``kanban_plans`` table — the same store the rest of the kanban uses,
satisfying ``docs/cockpit/00-orientation.md`` §3 *drie-bomen-regel*.

``project_path`` is still accepted (so the SPA can pass the active
project's path unchanged) and resolved to a ``project_key`` via
``resolve_project_key``. When no path is supplied, all plans land in the
"global" ``slug:global-plans`` bucket; the SPA always passes a path
through ``useProjectContext``, but this fallback matches the legacy
contract that ``project_path`` was optional.
"""
from fastapi import APIRouter, HTTPException, Query

from app.kanban.db import KanbanSessionLocal
from app.kanban.project_key import resolve_project_key
from app.models.schemas import (
    PlanCreate,
    PlanDetailResponse,
    PlanListResponse,
    PlanSearchResponse,
    PlanStatsResponse,
    PlanUpdate,
)
from app.services.kanban_plan_service import KanbanPlanService
from app.utils.path_utils import get_claude_plans_dir

router = APIRouter()

# Bucket for plans with no associated project. The SPA always sends a
# ``project_path`` via ``useProjectContext``, but the legacy endpoint
# contract allowed omitting it — preserve that fallback so old callers
# (curl, scripted REST) keep working.
_GLOBAL_PLANS_KEY = "slug:global-plans"


def _resolve_project_key(project_path: str | None) -> str:
    """Map a frontend-supplied project path to a kanban project_key.

    Mirrors ``app/kanban/router.py``'s enable/disable path resolution:
    a real git remote wins; a path with no remote falls back to a slug
    derived from the directory name. ``None`` → global bucket.
    """
    if not project_path:
        return _GLOBAL_PLANS_KEY
    try:
        return resolve_project_key(project_path)
    except Exception:
        # Path resolution can fail on non-git or non-existent paths —
        # keep the API usable by falling back to the global bucket
        # instead of 500-ing on a bad path.
        return _GLOBAL_PLANS_KEY


@router.get("/plans", response_model=PlanListResponse)
async def list_plans(
    project_path: str | None = Query(None, description="Active project path for kanban-key resolution"),
):
    """List all plans in the project's kanban bucket, newest first."""
    project_key = _resolve_project_key(project_path)
    try:
        async with KanbanSessionLocal() as s:
            plans = await KanbanPlanService.list_plans(s, project_key)
        return {"plans": plans, "total": len(plans)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list plans: {str(e)}")


@router.get("/plans/stats", response_model=PlanStatsResponse)
async def get_plan_stats(
    project_path: str | None = Query(None, description="Active project path"),
):
    """Get plan statistics for the project's kanban bucket."""
    project_key = _resolve_project_key(project_path)
    try:
        async with KanbanSessionLocal() as s:
            return await KanbanPlanService.get_plan_stats(s, project_key)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get plan stats: {str(e)}")


@router.get("/plans/search", response_model=PlanSearchResponse)
async def search_plans(
    q: str = Query(..., min_length=1, description="Search query"),
    project_path: str | None = Query(None, description="Active project path"),
):
    """Search plans by title and content within the project's bucket."""
    project_key = _resolve_project_key(project_path)
    try:
        async with KanbanSessionLocal() as s:
            results = await KanbanPlanService.search_plans(s, project_key, q)
        return {"results": results, "query": q, "total": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to search plans: {str(e)}")


@router.get("/plans/{filename}", response_model=PlanDetailResponse)
async def get_plan_detail(
    filename: str,
    project_path: str | None = Query(None, description="Active project path"),
):
    """Get full plan detail (content + linked sessions)."""
    project_key = _resolve_project_key(project_path)
    try:
        slug = KanbanPlanService.normalize_slug(filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        async with KanbanSessionLocal() as s:
            plan_data = await KanbanPlanService.get_plan(s, project_key, slug)
        if not plan_data:
            raise HTTPException(status_code=404, detail="Plan not found")
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
    """Create a new plan in the project's kanban bucket."""
    project_key = _resolve_project_key(project_path)
    try:
        async with KanbanSessionLocal() as s:
            plan_data = await KanbanPlanService.create_plan(
                s, project_key=project_key,
                slug=payload.filename, content=payload.content,
            )
            await s.commit()
        # linked_sessions is set by get_plan; re-fetch for consistency with
        # the legacy endpoint contract.
        async with KanbanSessionLocal() as s:
            plan_data = await KanbanPlanService.get_plan(s, project_key, plan_data["slug"])
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
    """Update an existing plan's content (kanban-DB row)."""
    project_key = _resolve_project_key(project_path)
    try:
        slug = KanbanPlanService.normalize_slug(filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        async with KanbanSessionLocal() as s:
            plan_data = await KanbanPlanService.update_plan(
                s, project_key=project_key, slug=slug, content=payload.content,
            )
            await s.commit()
        if plan_data is None:
            raise HTTPException(status_code=404, detail="Plan not found")
        # Refresh to include linked_sessions, matching the legacy shape.
        async with KanbanSessionLocal() as s:
            plan_data = await KanbanPlanService.get_plan(s, project_key, slug)
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
    """Delete a plan from the kanban-DB row."""
    project_key = _resolve_project_key(project_path)
    try:
        slug = KanbanPlanService.normalize_slug(filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        async with KanbanSessionLocal() as s:
            deleted = await KanbanPlanService.delete_plan(s, project_key, slug)
            await s.commit()
        if not deleted:
            raise HTTPException(status_code=404, detail="Plan not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete plan: {str(e)}")


# Reference to the legacy plans directory resolver. Kept so any external
# tooling that imported it from this module doesn't break; not used by the
# kanban-DB-backed endpoints above. Remove once we're confident no one
# outside the repo reads ``app.api.v1.plans.resolve_plans_dir``.
def resolve_plans_dir(project_path: str | None = None):
    """Legacy shim — see kanban card 727470a8. Returns the default
    ``~/.claude/plans/`` path for callers that still want to know where
    *would* a legacy file-backed plan live. The kanban-DB endpoints above
    no longer read this directory at request time.
    """
    return get_claude_plans_dir()