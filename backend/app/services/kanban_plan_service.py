"""Project-scoped plan CRUD on the kanban DB.

Replaces the legacy file-backed :class:`app.services.plan_service.PlanService`
for the ``/api/v1/plans`` endpoints (kanban card 727470a8 /
``docs/cockpit/00-orientation.md`` §3 *drie-bomen-regel* — plans must live in
the same canonical storage as cards, not as loose ``.md`` files in
``~/.claude/plans/``).

Public API (all ``@staticmethod``, so callers wire their own session — same
pattern as ``app/kanban/service.py``):
    - ``create_plan(session, project_key, slug, content)``
    - ``list_plans(session, project_key)``
    - ``get_plan(session, project_key, slug)``
    - ``update_plan(session, project_key, slug, content)``
    - ``delete_plan(session, project_key, slug)``
    - ``search_plans(session, project_key, query)``
    - ``get_plan_stats(session, project_key)``
    - ``get_plan_sessions(slug)`` — *not* session-bound; it scans
      ``~/.claude/projects/*.jsonl`` for entries tagged with ``slug``, the
      same way the legacy PlanService did. Kept as-is so existing
      ``PlanLinkedSession`` callers (frontend ``PlanDetailPage``) keep
      working without a DB migration.

Slug validation is intentionally more permissive than the legacy
``_resolve_plan_path`` — there is no parent directory to escape — but we
still reject path separators and ``..`` so the slug can round-trip safely
in a ``{filename}`` URL segment.

The output dicts match the existing ``PlanSummary`` / ``PlanDetail`` /
``PlanSearchResult`` pydantic shapes so the API can serve them unchanged.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.kanban.models import KanbanPlan
from app.utils.path_utils import get_claude_projects_dir, get_project_display_name

logger = logging.getLogger(__name__)

_SLUG_MAX_LEN = 256


class KanbanPlanService:
    """Kanban-DB-backed plan CRUD, scoped to ``project_key``."""

    # ----- pure helpers (no DB) ------------------------------------------

    @staticmethod
    def normalize_slug(slug: str) -> str:
        """Coerce user-supplied slug into a safe, length-bounded form.

        Strips an optional trailing ``.md`` so callers can pass either
        ``my-plan`` or ``my-plan.md`` (matching the legacy PlanService
        contract). Rejects path separators and ``..`` traversal — the slug
        ends up in the API URL, so escaping it would be a CVE.
        """
        raw = (slug or "").strip()
        if not raw:
            raise ValueError("Plan slug is required")
        if raw.endswith(".md"):
            raw = raw[:-3]
        if raw != Path(raw).name:
            # Rejects paths containing "/", "\\", "..", or anything that
            # doesn't normalize to a single path segment.
            raise ValueError(f"Invalid plan slug: {slug!r}")
        if not raw:
            raise ValueError("Plan slug is required")
        if len(raw) > _SLUG_MAX_LEN:
            raw = raw[:_SLUG_MAX_LEN]
        return raw

    @staticmethod
    def extract_title(content: str) -> str:
        """Mirror the legacy ``PlanService._extract_title`` semantics so the
        frontend ``PlanSummary.title`` field stays stable. Title = first H1,
        with optional ``Plan:`` / ``Plan —`` prefix stripped.
        """
        for line in (content or "").split("\n"):
            stripped = line.strip()
            if not stripped.startswith("# "):
                continue
            title = stripped[2:].strip()
            lower = title.lower()
            if lower.startswith("plan:"):
                title = title[5:].strip()
            elif lower.startswith("plan —"):
                title = title[6:].strip()
            return title
        return "(untitled)"

    @staticmethod
    def extract_excerpt(content: str, max_len: int = 200) -> str:
        """First ~``max_len`` chars of body, skipping the title line. Mirrors
        the legacy implementation's quirks (skips ``---`` rules) so list
        views don't shift between code paths.
        """
        lines = (content or "").split("\n")
        body_lines: list[str] = []
        past_title = False
        for line in lines:
            stripped = line.strip()
            if not past_title:
                if stripped.startswith("# "):
                    past_title = True
                    continue
                if not stripped:
                    continue
                past_title = True
            if stripped and not stripped.startswith("---"):
                body_lines.append(stripped)
                if len(" ".join(body_lines)) >= max_len:
                    break
        excerpt = " ".join(body_lines)
        if len(excerpt) > max_len:
            excerpt = excerpt[: max_len - 3] + "..."
        return excerpt

    @staticmethod
    def _extract_headings(content: str) -> list[str]:
        headings: list[str] = []
        for line in (content or "").split("\n"):
            stripped = line.strip()
            if stripped.startswith("## ") or stripped.startswith("### "):
                headings.append(stripped.lstrip("#").strip())
        return headings

    @staticmethod
    def _count_code_blocks(content: str) -> int:
        return len(re.findall(r"^```", content or "", re.MULTILINE)) // 2

    @staticmethod
    def _count_tables(content: str) -> int:
        count = 0
        lines = (content or "").split("\n")
        for i, line in enumerate(lines):
            if "|" in line and i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if re.match(r"^\|[\s:|-]+\|$", next_line):
                    count += 1
        return count

    # ----- serialization -------------------------------------------------

    @staticmethod
    def _to_summary(row: KanbanPlan) -> dict[str, Any]:
        size = len(row.content.encode("utf-8"))
        return {
            "filename": f"{row.slug}.md",
            "slug": row.slug,
            "project_key": row.project_key,
            "title": KanbanPlanService.extract_title(row.content),
            "excerpt": KanbanPlanService.extract_excerpt(row.content),
            "modified_at": row.updated_at.isoformat(),
            "size_bytes": size,
        }

    @staticmethod
    def _to_detail(row: KanbanPlan, *, with_sessions: bool = False) -> dict[str, Any]:
        size = len(row.content.encode("utf-8"))
        detail: dict[str, Any] = {
            "filename": f"{row.slug}.md",
            "slug": row.slug,
            "project_key": row.project_key,
            "title": KanbanPlanService.extract_title(row.content),
            "content": row.content,
            "created_at": row.created_at.isoformat(),
            "modified_at": row.updated_at.isoformat(),
            "size_bytes": size,
            "headings": KanbanPlanService._extract_headings(row.content),
            "code_block_count": KanbanPlanService._count_code_blocks(row.content),
            "table_count": KanbanPlanService._count_tables(row.content),
        }
        if with_sessions:
            detail["linked_sessions"] = KanbanPlanService.get_plan_sessions(row.slug)
        else:
            detail["linked_sessions"] = []
        return detail

    # ----- CRUD ----------------------------------------------------------

    @staticmethod
    async def create_plan(
        session, *, project_key: str, slug: str, content: str,
    ) -> dict[str, Any]:
        """Insert a new plan row. ``ValueError`` if (project_key, slug) already
        exists. Returns the created plan as a detail-shaped dict.
        """
        normalized = KanbanPlanService.normalize_slug(slug)
        if not project_key:
            raise ValueError("project_key is required")
        row = KanbanPlan(
            id=uuid.uuid4().hex,
            project_key=project_key,
            slug=normalized,
            title=KanbanPlanService.extract_title(content),
            content=content,
        )
        session.add(row)
        try:
            await session.flush()
        except IntegrityError as e:
            await session.rollback()
            raise ValueError(
                f"Plan already exists: project={project_key!r}, slug={normalized!r}"
            ) from e
        return KanbanPlanService._to_detail(row)

    @staticmethod
    async def list_plans(session, project_key: str) -> list[dict[str, Any]]:
        rows = (await session.execute(
            select(KanbanPlan)
            .where(KanbanPlan.project_key == project_key)
            .order_by(KanbanPlan.updated_at.desc())
        )).scalars().all()
        return [KanbanPlanService._to_summary(r) for r in rows]

    @staticmethod
    async def get_plan(session, project_key: str, slug: str) -> dict[str, Any] | None:
        normalized = KanbanPlanService.normalize_slug(slug)
        row = (await session.execute(
            select(KanbanPlan)
            .where(KanbanPlan.project_key == project_key)
            .where(KanbanPlan.slug == normalized)
        )).scalar_one_or_none()
        if row is None:
            return None
        return KanbanPlanService._to_detail(row, with_sessions=True)

    @staticmethod
    async def update_plan(
        session, *, project_key: str, slug: str, content: str,
    ) -> dict[str, Any] | None:
        normalized = KanbanPlanService.normalize_slug(slug)
        row = (await session.execute(
            select(KanbanPlan)
            .where(KanbanPlan.project_key == project_key)
            .where(KanbanPlan.slug == normalized)
        )).scalar_one_or_none()
        if row is None:
            return None
        row.content = content
        row.title = KanbanPlanService.extract_title(content)
        await session.flush()
        return KanbanPlanService._to_detail(row, with_sessions=True)

    @staticmethod
    async def delete_plan(session, project_key: str, slug: str) -> bool:
        normalized = KanbanPlanService.normalize_slug(slug)
        row = (await session.execute(
            select(KanbanPlan)
            .where(KanbanPlan.project_key == project_key)
            .where(KanbanPlan.slug == normalized)
        )).scalar_one_or_none()
        if row is None:
            return False
        await session.delete(row)
        await session.flush()
        return True

    @staticmethod
    async def search_plans(
        session, project_key: str, query: str,
    ) -> list[dict[str, Any]]:
        """Substring search (case-insensitive) on title + content. Sorted by
        recency. Mirrors the legacy response shape (filename/slug/title/
        matches/modified_at) so the SPA's existing filter keeps working.
        """
        rows = (await session.execute(
            select(KanbanPlan)
            .where(KanbanPlan.project_key == project_key)
            .order_by(KanbanPlan.updated_at.desc())
        )).scalars().all()
        if not query or not rows:
            return []
        q = query.lower()
        results: list[dict[str, Any]] = []
        for r in rows:
            haystack = (r.title + "\n" + r.content).lower()
            if q not in haystack:
                continue
            # First 3 matching lines as snippets, like the legacy impl.
            matches: list[str] = []
            for line in (r.content or "").split("\n"):
                if q in line.lower():
                    snippet = line.strip()
                    if len(snippet) > 120:
                        idx = line.lower().index(q)
                        start = max(0, idx - 40)
                        end = min(len(snippet), idx + len(query) + 40)
                        snippet = (
                            ("..." if start > 0 else "")
                            + snippet[start:end]
                            + ("..." if end < len(line.strip()) else "")
                        )
                    matches.append(snippet)
                    if len(matches) >= 3:
                        break
            results.append({
                "filename": f"{r.slug}.md",
                "slug": r.slug,
                "title": r.title or KanbanPlanService.extract_title(r.content),
                "matches": matches,
                "modified_at": r.updated_at.isoformat(),
            })
        return results

    @staticmethod
    async def get_plan_stats(session, project_key: str) -> dict[str, Any]:
        rows = (await session.execute(
            select(KanbanPlan).where(KanbanPlan.project_key == project_key)
        )).scalars().all()
        if not rows:
            return {
                "total_plans": 0,
                "oldest_date": None,
                "newest_date": None,
                "total_size_bytes": 0,
            }
        sizes = [len(r.content.encode("utf-8")) for r in rows]
        return {
            "total_plans": len(rows),
            "oldest_date": min(r.created_at for r in rows).isoformat(),
            "newest_date": max(r.updated_at for r in rows).isoformat(),
            "total_size_bytes": sum(sizes),
        }

    # ----- plan-sessions scan (unchanged legacy behavior) ----------------

    @staticmethod
    def get_plan_sessions(slug: str) -> list[dict[str, Any]]:
        """Find sessions tagged with this plan's slug by scanning JSONL files.

        Kept verbatim from the legacy :class:`PlanService` so the API's
        ``PlanLinkedSession`` shape — and therefore the frontend's
        ``PlanDetailPage`` — keeps working without changes.
        """
        sessions: list[dict[str, Any]] = []
        projects_dir = get_claude_projects_dir()
        if not projects_dir.exists():
            return sessions

        for project_folder in projects_dir.iterdir():
            if not project_folder.is_dir():
                continue
            for jsonl_file in project_folder.glob("*.jsonl"):
                try:
                    session_info = KanbanPlanService._scan_jsonl_for_slug(
                        jsonl_file, slug, project_folder.name,
                    )
                except Exception:
                    continue
                if session_info:
                    sessions.append(session_info)
        sessions.sort(key=lambda s: s.get("last_seen", ""), reverse=True)
        return sessions

    @staticmethod
    def _scan_jsonl_for_slug(
        filepath: Path, slug: str, project_folder: str,
    ) -> dict[str, Any] | None:
        session_id = filepath.stem
        first_seen = None
        last_seen = None
        git_branch = None
        with open(filepath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("slug") != slug:
                    continue
                timestamp = obj.get("timestamp")
                if timestamp:
                    if first_seen is None:
                        first_seen = timestamp
                    last_seen = timestamp
                if not git_branch:
                    git_branch = obj.get("gitBranch")
        if first_seen is None:
            return None
        return {
            "session_id": session_id,
            "project_folder": project_folder,
            "project_name": get_project_display_name(project_folder),
            "git_branch": git_branch,
            "first_seen": first_seen,
            "last_seen": last_seen,
        }
