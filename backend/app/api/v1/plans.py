"""Plans & Specs read-only aggregator API endpoints.

``GET /plans/overview`` (kanban card 885d0b61, Optie B, stap 1) returns a
read-only B+C aggregate: B is the set of ``plan``/``plan_ref``
deliverables on cards scoped to ``project_key``, C is the repo-wide
``docs/cockpit/*.md`` filesystem index. The two sections are returned
side-by-side without correlation — the ``spec_doc`` join is a deferred
follow-up (kanban card bb1f61aa) and intentionally not implemented here.
``GET /plans/overview/docs/{path}`` fetches a single doc's body for the
detail view.

``project_path`` is accepted (so the SPA can pass the active project's
path unchanged) and resolved to a ``project_key`` via
``resolve_project_key``. When no path is supplied, the "global"
``slug:global-plans`` bucket is used.

The previous CRUD surface (``GET/POST/PUT/DELETE /plans``, backed by the
``kanban_plans`` table and ``KanbanPlanService``) was phased out (kanban
card 528c5ca2, Optie B stap 3 — see
``docs/cockpit/plans-feature-decision.md`` §5): the table had zero live
writers, and the frontend (``usePlansApi.ts``) already reads exclusively
from ``/plans/overview``. No external caller of the CRUD routes was
found (grepped the repo; only this feature's own now-removed tests
called ``POST /plans``).
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
    DocContentResponse,
    DocSpecItem,
    PlansOverviewResponse,
)

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


# ---------------------------------------------------------------------------
# Single doc fetch — supports the C-section detail view (kanban card
# 9e33a359, Optie B, stap 2).
# ---------------------------------------------------------------------------
#
# The ``/plans/overview`` list endpoint deliberately omits doc bodies to
# keep the aggregate response small (a 50 KB ``plans-feature-decision.md``
# would otherwise dominate the payload). The detail page opens this
# endpoint when a user expands a doc row.
#
# Path-traversal guard: the path MUST live directly under ``_COCKPIT_DOCS_DIR``.
# We both check the string prefix (rejects ``..``-style and other obvious
# bypasses) and resolve the candidate against ``PROJECT_ROOT`` with
# ``Path.resolve()`` + an ``is_relative_to`` check so a request like
# ``/plans/overview/docs/docs%2Fcockpit%2F..%2F..%2Fetc%2Fpasswd`` cannot
# escape the sandbox even if a future code change moves the docs tree.


async def _read_cockpit_doc(rel_path: str) -> DocContentResponse:
    """Read a single docs/cockpit/*.md file and return its body.

    Trailing-whitespace newlines are stripped so multi-MB files don't
    inflate the JSON with a deterministic suffix. The H1 title is
    computed the same way as in ``_list_cockpit_docs`` so detail rows
    match the list view (avoids the "title changed when I expanded it"
    surprise).
    """
    candidate = (PROJECT_ROOT / rel_path).resolve()
    # Reject anything that, after resolve, escapes the docs root. We
    # compare against the resolved root (PROJECT_ROOT may itself be a
    # symlink in some deploys) and require "docs/cockpit" as the next
    # segment — so ``docs/cockpit/../something`` is denied even if it
    # resolves back under PROJECT_ROOT.
    docs_root = _COCKPIT_DOCS_DIR.resolve()
    try:
        candidate.relative_to(docs_root)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Doc path must live under docs/cockpit/",
        )
    if not candidate.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"Doc not found: {rel_path}",
        )
    try:
        content = candidate.read_text(encoding="utf-8").strip("\n")
    except (OSError, UnicodeDecodeError) as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read doc: {e}",
        )
    first_line = content.splitlines()[:1]
    title = first_line[0] if first_line else f"# {candidate.name}"
    if len(title) > _DOC_TITLE_MAX_CHARS:
        title = title[: _DOC_TITLE_MAX_CHARS - 1] + "…"
    stat = candidate.stat()
    return DocContentResponse(
        path=f"docs/cockpit/{candidate.name}",
        title=title,
        content=content,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
        size_bytes=stat.st_size,
    )


@router.get(
    "/plans/overview/docs/{rel_path:path}",
    response_model=DocContentResponse,
)
async def get_plan_overview_doc(rel_path: str):
    """Return the full body of one ``docs/cockpit/*.md`` doc.

    Paired with ``/plans/overview`` (which ships only metadata) so the
    detail page can lazily read just the file the user opened. Lives
    under the same router for the same reason — there is no separate
    "specs" / "decisions" feature; the SSOT docs tree is the source of
    truth for spec-shaped content.
    """
    # Defensive normalize: callers may URL-encode the path; we already
    # got the decoded form from FastAPI's ``{rel_path:path}``, but a
    # leading slash can sneak in via ``/api/v1/plans/overview/docs//
    # docs/...``. Strip it so ``PROJECT_ROOT / rel_path`` resolves to
    # the right place without 404-ing on a stray separator.
    normalized = rel_path.lstrip("/")
    return await _read_cockpit_doc(normalized)