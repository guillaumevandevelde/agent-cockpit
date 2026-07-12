"""One-shot migration: ``~/.claude/plans/*.md`` → kanban-DB ``kanban_plans``.

Kanban card 727470a8 / ``docs/cockpit/00-orientation.md`` §3 *drie-bomen-regel*
— moves existing plan files into the same canonical store the rest of the
kanban uses.

Properties:
- **Idempotent**: re-running with no source-file changes inserts zero rows;
  mutating a source file's content but keeping the slug also inserts zero
  rows (uniqueness on ``(project_key, slug)`` is the gate). Existing rows
  are *not* overwritten, matching the on-disk-as-truth-of-record semantics
  the legacy ``PlanService`` had (the source file is the high-water mark,
  not the destination).
- **Project-scoped**: every plan lands in the same ``project_key`` bucket.
  The default bucket is ``slug:global-plans`` to match
  ``app/api/v1/plans.py``'s fallback; pass ``--project-key`` (or rely on
  the CLI flag) to choose a different bucket. For multi-project setups
  the operator runs the script once per bucket.
- **Safe-by-default**: ``--dry-run`` prints the plan but writes nothing.

Usage (from the backend dir):
    python -m scripts.migrate_plans_to_kanban --dry-run
    python -m scripts.migrate_plans_to_kanban
    python -m scripts.migrate_plans_to_kanban --project-key git:github.com/foo/bar
    python -m scripts.migrate_plans_to_kanban --source-dir /custom/plans/dir
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.kanban.db import KanbanSessionLocal
from app.kanban.models import KanbanPlan
from app.utils.path_utils import get_claude_plans_dir

logger = logging.getLogger(__name__)

# Same default as ``app/api/v1/plans.py::_GLOBAL_PLANS_KEY`` — keeps a CLI
# invocation without ``--project-key`` landing in the same bucket the
# API serves when ``project_path`` is absent.
DEFAULT_PROJECT_KEY = "slug:global-plans"


def slug_from_filename(path: Path) -> str:
    """Strip ``.md`` so the slug matches what the API normalises to.

    ``my-plan.md`` → ``my-plan``; ``notes.txt`` is returned unchanged
    (the script only globs ``*.md`` but the helper stays permissive for
    unit-test use).
    """
    if path.suffix == ".md":
        return path.stem
    return path.name


def derive_title(content: str) -> str:
    """Mirror KanbanPlanService.extract_title's contract so a migrated plan
    has the same ``title`` as a freshly created one in the API."""
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


def collect_plan_files(source_dir: Path) -> list[Path]:
    """List ``*.md`` files in ``source_dir`` (non-recursive). Missing dir
    is an empty list — the script is meant to be safe to run before any
    plans have been written.
    """
    if not source_dir.exists() or not source_dir.is_dir():
        return []
    return sorted(source_dir.glob("*.md"))


async def migrate_one(
    session,
    source_file: Path,
    *,
    project_key: str,
    source_root: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Insert one plan, or report a skip/error.

    Returns a dict with:
      - ``status``: ``inserted`` | ``skipped`` | ``would_insert`` | ``error``
      - ``slug``: the destination row's slug
      - ``reason``: only on ``skipped`` / ``error``
    """
    slug = slug_from_filename(source_file)
    try:
        # Skip-if-exists check before reading the file body — speeds up the
        # common "already migrated" case where we don't even need to open
        # the file.
        if not dry_run:
            existing = (await session.execute(
                select(KanbanPlan)
                .where(KanbanPlan.project_key == project_key)
                .where(KanbanPlan.slug == slug)
            )).scalar_one_or_none()
            if existing is not None:
                return {
                    "status": "skipped",
                    "slug": slug,
                    "project_key": project_key,
                    "reason": "already exists",
                    "source": str(source_file),
                }

        content = source_file.read_text(encoding="utf-8")
        title = derive_title(content)

        if dry_run:
            return {
                "status": "would_insert",
                "slug": slug,
                "project_key": project_key,
                "title": title,
                "size_bytes": len(content.encode("utf-8")),
                "source": str(source_file),
            }

        session.add(KanbanPlan(
            id=_new_id(),
            project_key=project_key,
            slug=slug,
            title=title,
            content=content,
        ))
        await session.flush()
        return {
            "status": "inserted",
            "slug": slug,
            "project_key": project_key,
            "title": title,
            "source": str(source_file),
        }
    except Exception as e:
        logger.exception("migration failed for %s", source_file)
        return {
            "status": "error",
            "slug": slug,
            "project_key": project_key,
            "reason": str(e),
            "source": str(source_file),
        }


async def run_migration(
    *,
    source_dir: Path,
    project_key: str,
    session,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Migrate every ``*.md`` in ``source_dir`` into ``project_key``.

    A missing ``source_dir`` is treated as a no-op (the script is meant
    to be safe to run any time, even on a fresh install). Per-file
    failures don't abort the rest of the migration — they show up in
    ``report['errors']`` so an operator can re-run after fixing.
    """
    files = collect_plan_files(source_dir)
    report: dict[str, Any] = {
        "inserted": 0,
        "skipped": 0,
        "would_insert": 0,
        "errors": [],
        "slugs": [],
    }
    for f in files:
        result = await migrate_one(
            session, f,
            project_key=project_key, source_root=source_dir,
            dry_run=dry_run,
        )
        if result["status"] == "inserted":
            report["inserted"] += 1
            report["slugs"].append(result["slug"])
        elif result["status"] == "would_insert":
            report["would_insert"] += 1
        elif result["status"] == "skipped":
            report["skipped"] += 1
        else:
            report["errors"].append(result)
    return report


def _new_id() -> str:
    import uuid
    return uuid.uuid4().hex


# ---- CLI ------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "One-shot migration: ~/.claude/plans/*.md → kanban-DB kanban_plans."
        ),
    )
    p.add_argument(
        "--source-dir",
        type=Path,
        default=None,
        help="Source directory (default: ~/.claude/plans/)",
    )
    p.add_argument(
        "--project-key",
        default=DEFAULT_PROJECT_KEY,
        help=(
            f"Kanban project_key to attribute the migrated plans to "
            f"(default: {DEFAULT_PROJECT_KEY!r}). Run once per project for "
            "multi-project setups."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be migrated without writing anything.",
    )
    return p.parse_args(argv)


async def _main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = _parse_args(argv)

    # Idempotent schema bring-up — mirrors ``app.main:init_kanban_db`` on
    # backend startup. Without it, a fresh ``~/.claude-registry/kanban.db``
    # (e.g. on first migration ever) has no ``kanban_plans`` table and
    # every insert errors out. ``init_kanban_db`` is itself additive, so
    # calling it twice on a populated DB is a no-op.
    from app.kanban.db import init_kanban_db
    await init_kanban_db()

    source_dir = args.source_dir or get_claude_plans_dir()
    if not source_dir.exists():
        logger.info(
            "source dir %s does not exist — nothing to migrate", source_dir,
        )
        return 0

    logger.info(
        "migrating plans from %s into kanban project_key=%r%s",
        source_dir, args.project_key,
        " (dry run)" if args.dry_run else "",
    )

    async with KanbanSessionLocal() as s:
        report = await run_migration(
            source_dir=source_dir,
            project_key=args.project_key,
            session=s,
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            await s.commit()

    print(json.dumps({
        "source_dir": str(source_dir),
        "project_key": args.project_key,
        "dry_run": args.dry_run,
        **report,
    }, indent=2))

    if report["errors"]:
        return 2  # partial-failure exit code so CI / cron can detect it
    return 0


def main() -> None:
    sys.exit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()