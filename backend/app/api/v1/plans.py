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

In addition to the CRUD endpoints, ``GET /plans/overview`` (kanban card
885d0b61, Optie B, stap 1) returns a read-only B+C aggregate: B is the
set of ``plan``/``plan_ref`` deliverables on cards scoped to
``project_key``, C is the repo-wide ``docs/cockpit/*.md`` filesystem
index. The two sections are returned side-by-side without correlation —
the ``spec_doc`` join is a deferred follow-up (kanban card bb1f61aa)
and intentionally not implemented here.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.config import PROJECT_ROOT
from app.kanban.db import KanbanSessionLocal
from app.kanban.models import KanbanCard, KanbanDeliverable
from app.kanban.project_key import resolve_project_key
from app.models.schemas import (
    CardPlanItem,
    DocSpecItem,
    PlanCreate,
    PlanDetailResponse,
    PlanListResponse,
    PlanSearchResponse,
    PlansOverviewResponse,
    PlanStatsResponse,
    PlanUpdate,
)
from app.services.kanban_plan_service import KanbanPlanService
from app.utils.path_utils import get_claude_plans_dir

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# Plans overview — B+C aggregator (kanban card 885d0b61, Optie B, stap 1)
# ---------------------------------------------------------------------------
#
# Two read-only sections returned as siblings, NOT joined:
#   * B — ``plan``/``plan_ref`` deliverables on cards scoped to the
#     resolved project_key. Source of truth: the kanban DB.
#   * C — repo-wide ``docs/cockpit/*.md`` filesystem index. Source of
#     truth: the git tree (no DB read). Repo-wide because the docs tree
#     is the platform's SSOT, shared across every project the SPA asks
#     about — see ``docs/cockpit/plans-feature-decision.md`` §6.
#
# The ``spec_doc`` join (an anchor that today has 0× producers) is
# deliberately NOT implemented here; see kanban card bb1f61aa for the
# deferred follow-up. The shape is intentionally flat so the SPA can
# render B and C without a join step in the client either.

# How much of the deliverable's ``ref`` to surface in the row excerpt.
# 240 chars mirrors the existing ``PlanSummary.excerpt`` budget and
# keeps one row's payload well under a kilobyte.
_EXCERPT_MAX_CHARS = 240

# Where the platform's SSOT decision/spec docs live. Computed from
# ``PROJECT_ROOT`` so a mounted deploy / different checkout root keeps
# working — we never want a hardcoded ``/home/...`` path here.
_COCKPIT_DOCS_DIR = PROJECT_ROOT / "docs" / "cockpit"

# Stable filenames in C are the H1 of the first line. Cap at 200 chars
# to mirror the row budget; we don't expect a title longer than that,
# but a runaway H1 should not blow the response.
_DOC_TITLE_MAX_CHARS = 200


def _excerpt(ref: str) -> str:
    """Return a single-line preview of a deliverable's ``ref``.

    A ``plan`` deliverable's ref is markdown (multi-line body), so we
    strip newlines + collapse whitespace and cap the length. A
    ``plan_ref`` ref is a JSON envelope; we surface it verbatim (it's
    short by construction). Both forms are text-safe in JSON — no
    escaping required beyond what ``json.dumps`` does for us.
    """
    text = " ".join(ref.split())  # collapse all whitespace, incl. newlines
    if len(text) > _EXCERPT_MAX_CHARS:
        return text[: _EXCERPT_MAX_CHARS - 1] + "…"
    return text


def _list_cockpit_docs() -> list[DocSpecItem]:
    """Scan ``docs/cockpit/*.md`` and emit one ``DocSpecItem`` per file.

    Kept as a module-level function (not nested in the handler) so the
    ``test_overview_sections_are_independent`` test can monkey-patch it
    in place — see that test for why this is the right seam. The scan is
    intentional: the docs tree is small (78 files today), a flat glob is
    fast enough at request time, and pulling it into a cache would mask
    "doc added today" from showing up in the overview without a
    separate cache-bust.
    """
    items: list[DocSpecItem] = []
    if not _COCKPIT_DOCS_DIR.is_dir():
        # The tree could be absent in a stub checkout (e.g. a CI clone
        # with sparse docs). Treat that as "no docs" rather than 500.
        return items
    for path in sorted(_COCKPIT_DOCS_DIR.glob("*.md")):
        try:
            stat = path.stat()
        except OSError:
            # A file that disappears between glob and stat (concurrent
            # editor) is not interesting enough to 500 the whole overview.
            continue
        try:
            first_line = path.read_text(encoding="utf-8").splitlines()[:1]
        except (OSError, UnicodeDecodeError):
            first_line = []
        title = first_line[0] if first_line else f"# {path.name}"
        if len(title) > _DOC_TITLE_MAX_CHARS:
            title = title[: _DOC_TITLE_MAX_CHARS - 1] + "…"
        items.append(DocSpecItem(
            path=f"docs/cockpit/{path.name}",
            title=title,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
            size_bytes=stat.st_size,
        ))
    return items


async def _list_card_plan_items(project_key: str) -> list[CardPlanItem]:
    """Project-scoped list of ``plan``/``plan_ref`` deliverables.

    Single SQL round-trip: ``KanbanCard`` join with ``KanbanDeliverable``
    filtered by ``kind IN ('plan','plan_ref')`` and ``project_key``.
    ``selectinload`` would force a second query for each card — overkill
    here because we only need one column off the card (``title``), so
    a plain join suffices and keeps the test-fixture ergonomics obvious.
    """
    async with KanbanSessionLocal() as s:
        rows = (await s.execute(
            select(KanbanDeliverable, KanbanCard.title)
            .join(KanbanCard, KanbanDeliverable.card_id == KanbanCard.id)
            .where(KanbanCard.project_key == project_key)
            .where(KanbanDeliverable.kind.in_(("plan", "plan_ref")))
            .order_by(KanbanDeliverable.created_at.desc())
        )).all()
    return [
        CardPlanItem(
            deliverable_id=d.id,
            kind=d.kind,
            card_id=d.card_id,
            card_title=title or "",
            excerpt=_excerpt(d.ref),
            created_at=d.created_at,
        )
        for (d, title) in rows
    ]


@router.get("/plans/overview", response_model=PlansOverviewResponse)
async def get_plans_overview(
    project_path: str | None = Query(
        None,
        description=(
            "Active project path. Resolved to a project_key the same way "
            "the rest of the /plans endpoints do (git remote > path slug > "
            "global bucket)."
        ),
    ),
):
    """Return the B + C aggregator as two independent sections.

    Cards (``B``) are scoped to ``project_key``; docs (``C``) are
    repo-wide because ``docs/cockpit/`` is the platform's SSOT tree
    shared across every project (see the per-section rationale above).
    """
    project_key = _resolve_project_key(project_path)
    try:
        cards = await _list_card_plan_items(project_key)
        docs = _list_cockpit_docs()
    except Exception as e:
        # Two failure surfaces share this guard: the kanban-DB read can
        # raise on a connection/session error, and the filesystem scan
        # can raise on a permission error. Both surface as a single 500;
        # the log line carries the full traceback so an operator can
        # tell which side blew up.
        logger.exception("plans/overview aggregation failed")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to build plans overview: {str(e)}",
        )
    return PlansOverviewResponse(
        project_key=project_key, cards=cards, docs=docs,
    )


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