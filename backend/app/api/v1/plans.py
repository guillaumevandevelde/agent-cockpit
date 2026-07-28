"""Plans & Specs read-only aggregator API endpoints.

``GET /plans/overview`` (kanban card 885d0b61, Optie B, stap 1) returns a
read-only B+C aggregate: B is the set of ``plan``/``plan_ref``
deliverables on cards scoped to ``project_key``, C is the repo-wide
``docs/cockpit/*.md`` filesystem index. The two sections are correlated
by ``card.metadata["spec_doc"]`` (kanban plan 2026-07-28-plans-b-c-
correlation, Task 1): each B row carries the card's ``spec_doc`` anchor,
each C row lists the cards that claim it via ``implemented_by``. URL
``spec_doc`` values and missing-path matches are filtered out so the
correlation never lies. ``GET /plans/overview/docs/{path}`` fetches a
single doc's body for the detail view.

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
from sqlalchemy import and_, select

from app.config import PROJECT_ROOT
from app.kanban.db import KanbanSessionLocal
from app.kanban.models import KanbanCard, KanbanDeliverable
from app.kanban.project_key import resolve_project_key
from app.kanban.schemas import SPEC_DOC_META_KEY
from app.models.schemas import (
    CardPlanItem,
    CorrelatedCardItem,
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
# Two read-only sections returned as siblings, correlated per row:
#   * B — ``plan``/``plan_ref`` deliverables on cards scoped to the
#     resolved project_key. Source of truth: the kanban DB.
#   * C — repo-wide ``docs/cockpit/*.md`` filesystem index. Source of
#     truth: the git tree (no DB read). Repo-wide because the docs tree
#     is the platform's SSOT, shared across every project the SPA asks
#     about — see ``docs/cockpit/plans-feature-decision.md`` §6.
#
# The B↔C correlation (kanban plan 2026-07-28-plans-b-c-correlation,
# Task 1) joins on ``card.metadata["spec_doc"] == c.path`` and ships
# inside each row (``CardPlanItem.spec_doc`` / ``DocSpecItem.implemented_by``),
# not at the top level — so the SPA can render the link without a
# client-side join. The contract on both sides is the same:
# ``spec_doc`` is the *correlatable* anchor. URL-anchored ``spec_doc``
# values (no repo-relative path to match) are normalised to ``None``
# on the B side AND excluded from the C-side ``implemented_by`` list.
# A card with a bogus (non-URL, non-empty) path lands as ``spec_doc``
# populated on the B row (so the SPA can show the anchor verbatim)
# but does NOT match any C row, so its C-side contribution is empty.

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

# URL schemes that disqualify a ``spec_doc`` value from the C-side
# correlation (the doc lives outside the repo, so we cannot link a card
# back to a C row). Both forms appear in real cards today (the Fase-1
# ``SPEC_DOC_META_KEY`` schema permits them), so the filter is a
# runtime concern, not a writer-side constraint.
_NON_CORRELATABLE_URL_PREFIXES = ("http://", "https://")


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


def _correlation_spec_doc(meta: dict | None) -> str | None:
    """Return a card's ``spec_doc`` value if it is correlatable, else ``None``.

    The Fase-1 ``SPEC_DOC_META_KEY`` schema allows the anchor to be
    either a repo-relative path or a URL; only the former can match a
    ``docs/cockpit/*.md`` C row. Reads the metadata bag defensively:
    non-dict / missing / non-string / empty / URL values all return
    ``None``. Whitespace is preserved verbatim (no normalisation that
    could silently turn a typo into a match) — equality with the C path
    is exact, by design.
    """
    if not isinstance(meta, dict):
        return None
    raw = meta.get(SPEC_DOC_META_KEY)
    if not isinstance(raw, str):
        return None
    candidate = raw  # exact, no strip: equality with C-path is the contract
    if not candidate:
        return None
    if candidate.startswith(_NON_CORRELATABLE_URL_PREFIXES):
        return None
    return candidate


def _list_cockpit_docs(
    correlations: dict[str, list[CorrelatedCardItem]] | None = None,
) -> list[DocSpecItem]:
    """Scan ``docs/cockpit/*.md`` and emit one ``DocSpecItem`` per file.

    Kept as a module-level function (not nested in the handler) so the
    ``test_overview_sections_are_independent`` test can monkey-patch it
    in place — see that test for why this is the right seam. The scan is
    intentional: the docs tree is small (78 files today), a flat glob is
    fast enough at request time, and pulling it into a cache would mask
    "doc added today" from showing up in the overview without a
    separate cache-bust.

    ``correlations`` is an optional ``path -> [card, ...]`` mapping (built
    by ``_list_plan_overview_data`` from the same kanban-DB read). Each
    row carries its matched cards in ``implemented_by`` so the SPA can
    render "implemented by cards" chips without a second round-trip. The
    argument defaults to ``None`` for legacy callers (and the legacy
    test, which monkey-patches this as a no-arg function) — passing
    ``None`` produces empty ``implemented_by`` lists, which is the
    pre-correlation contract.
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
        rel_path = f"docs/cockpit/{path.name}"
        items.append(DocSpecItem(
            path=rel_path,
            title=title,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
            size_bytes=stat.st_size,
            implemented_by=list(correlations.get(rel_path, []))
            if correlations else [],
        ))
    return items


async def _list_plan_overview_data(
    project_key: str,
) -> tuple[list[CardPlanItem], dict[str, list[CorrelatedCardItem]]]:
    """One SQL round-trip producing both B rows and the B↔C correlation map.

    Builds the B section (plan/plan_ref deliverables, project-scoped)
    AND a ``path -> [card, ...]`` correlation map keyed by C doc-path
    from a single ``LEFT JOIN`` of project cards against their plan
    deliverables. The deliverable-kind filter lives in the ON clause
    (not WHERE), so cards with no plan deliverable still appear in the
    result with NULL deliverable fields — that lets them feed the
    correlation map without producing a stray B row.

    Output:
      * ``cards`` — one ``CardPlanItem`` per ``plan``/``plan_ref``
        deliverable in the project, ordered newest-first, with
        ``spec_doc`` mirrored from the card's metadata when the value
        is a non-empty, non-URL string (``None`` otherwise — the SPA
        can show "no matching doc" / "external spec" affordances
        without breaking the correlation contract).
      * ``correlations`` — ``docs/cockpit/<file>.md`` -> list of cards
        whose ``metadata.spec_doc`` exactly equals that path. Cards
        are deduplicated per path (a card can only link once to a
        doc) and sorted by ``card_id`` so the rendered chip order is
        stable across requests. URLs and missing paths contribute
        nothing — that's the whole point of the filter.

    The map's keys are *not* the C-section's full set: it only carries
    paths that have at least one matching card. ``_list_cockpit_docs``
    fills ``implemented_by = []`` for any C row whose path is absent.
    """
    async with KanbanSessionLocal() as s:
        # LEFT JOIN: project cards on the left, plan deliverables on the
        # right (filtered to ``plan``/``plan_ref`` in the ON clause).
        # Cards with no matching deliverable appear once with NULL
        # deliverable fields; cards with one match appear once; cards
        # with N matches appear N times (one B row per deliverable).
        #
        # Project only the columns we actually read: ``id``, ``title``
        # and ``meta`` off the card (the correlation map needs all
        # three; nothing else is touched); all columns off the
        # deliverable (small row, every field is consumed). Selecting
        # whole ORM entities would hydrate ~17 unused columns per card
        # across the project — wasteful on projects with hundreds of
        # cards and a non-trivial latency hit on the test fixtures.
        rows = (await s.execute(
            select(
                KanbanCard.id,
                KanbanCard.title,
                KanbanCard.meta,
                KanbanDeliverable,
            )
            .outerjoin(
                KanbanDeliverable,
                and_(
                    KanbanDeliverable.card_id == KanbanCard.id,
                    KanbanDeliverable.kind.in_(("plan", "plan_ref")),
                ),
            )
            .where(KanbanCard.project_key == project_key)
            .order_by(
                # Deliverable rows sort newest-first; null-deliverable
                # rows (cards with no plan/plan_ref) come last in
                # iteration order because the Python loop short-circuits
                # them before they reach the cards list, so an
                # explicit NULLS clause would only cost portability on
                # older SQLite (3.30+).
                KanbanDeliverable.created_at.desc(),
                KanbanCard.id.asc(),
            )
        )).all()

    cards: list[CardPlanItem] = []
    # Order-preserving dict: cards that share a path keep the iteration
    # order in which they first appear, then a deterministic sort by
    # ``card_id`` is applied per path before the map ships out.
    correlations_in_order: dict[str, dict[str, str]] = {}

    for card_id, card_title, card_meta, deliverable in rows:
        spec_doc = _correlation_spec_doc(card_meta)
        if spec_doc is not None:
            bucket = correlations_in_order.setdefault(spec_doc, {})
            # Dedup per (path, card_id): a card can only claim a doc once.
            bucket[card_id] = card_title or ""

        if deliverable is None:
            # LEFT JOIN row with no matching deliverable — feeds the
            # correlation map only; not a B row.
            continue

        cards.append(CardPlanItem(
            deliverable_id=deliverable.id,
            kind=deliverable.kind,
            card_id=card_id,
            card_title=card_title or "",
            excerpt=_excerpt(deliverable.ref),
            created_at=deliverable.created_at,
            spec_doc=spec_doc,
        ))

    correlations: dict[str, list[CorrelatedCardItem]] = {
        path: [
            CorrelatedCardItem(card_id=cid, card_title=title)
            for cid, title in sorted(by_id.items())
        ]
        for path, by_id in correlations_in_order.items()
    }
    return cards, correlations


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
    The B↔C correlation lives INSIDE each row (``CardPlanItem.spec_doc`` /
    ``DocSpecItem.implemented_by``) and is built from the same kanban-DB
    read that powers B, so a single round-trip produces the entire
    response — see ``_list_plan_overview_data`` for the join shape.
    """
    project_key = _resolve_project_key(project_path)
    try:
        cards, correlations = await _list_plan_overview_data(project_key)
        docs = _list_cockpit_docs(correlations)
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