"""One-shot migration: scan existing kanban cards for prose-only "gated"
markers and lift them to the new machine-readable ``metadata.gated_on`` form.

Triggered by kanban card `f8ef71a0…` ("[problem] Gepoorte kaarten worden
auto-gedispatcht zodra hun depends_on klaar is"). Before the
``_is_gated``-predicate landed in dispatch, the convention was to write the
business-trigger reason as prose in the title/description ("BEWUST NIET NU",
"GEPOORT", "activeert pas bij trigger X"). The dispatcher reads prose just as
well as humans — i.e. not at all — so every prose-gated card was a time bomb
waiting for its ``depends_on`` to land.

This helper **inventories** every card whose title/description still carries
one of the legacy prose markers and prints a structured report. It does not
mutate anything unless ``--apply`` is passed: applying sets
``metadata.gated_on`` on each match (using the *first* matched marker's
canonical trigger string) and posts a ``**Gate:** migrated from prose —
<marker>`` activity-feed comment so the migration is visible in the history.

Run from the kanban project's working copy (the one whose ``claude_registry.db``
or kanban DB you want to migrate); the helper reads through the same
``app.kanban.db`` plumbing the REST API uses, so DB path resolution is
identical to the running server.

Usage:
    # Inventory only (default — safe, no writes):
    python -m app.kanban.gate_migration

    # Apply the migration (writes metadata + posts audit comments):
    python -m app.kanban.gate_migration --apply

    # Restrict to a single project (multi-project hosts):
    python -m app.kanban.gate_migration --project-key git:example.com/me/repo

    # Restrict to a single card (interactive use after you've already triaged
    # which prose-gated cards you want migrated):
    python -m app.kanban.gate_migration --card-id a4a091fa… --apply

The output is JSON so a follow-up automation can pipe it into a kanban
activity-feed comment without re-parsing free text.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Iterable

from sqlalchemy import select

from app.kanban.db import KanbanSessionLocal
from app.kanban.models import KanbanCard
from app.kanban.operations import apply_operation

# The prose markers we recognise, plus the canonical ``gated_on`` value we
# synthesise for each. The matchers are case-insensitive on the title +
# description; the canonical value is what ``_is_gated`` will see next tick.
#
# Order matters: the *first* matching marker wins, so put the most specific
# trigger ("second-executor-provider-onboarded") before the catch-all
# "BEWUST NIET NU" generic. This keeps an explicit trigger from being
# overridden by a less informative generic marker on the same card.
_PROSE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"second[- ]executor[- ]provider[- ]onboard", re.IGNORECASE),
        "second-executor-provider-onboarded",
    ),
    (
        re.compile(r"\bactiveert pas bij\b", re.IGNORECASE),
        "trigger-described-in-description",
    ),
    (
        re.compile(r"\bwacht op\b", re.IGNORECASE),
        "trigger-described-in-description",
    ),
    (
        re.compile(r"\bgepoort\b|\bbewust niet nu\b|\bbewust niet dispatchen\b",
                   re.IGNORECASE),
        "prose-gate-marker",
    ),
)


def _match_prose_marker(card: KanbanCard) -> str | None:
    """Return the canonical ``gated_on`` value if the card's title or
    description carries a recognised prose gate marker, else None.

    Skips cards that already have a machine-readable ``gated_on`` set — those
    are not the legacy prose-gated set, they're the new-style gates. The
    re-application of a gate on top of an existing one would be surprising.
    """
    if (card.meta or {}).get("gated_on"):
        return None
    haystack = f"{card.title or ''}\n{card.description or ''}"
    for pattern, canonical in _PROSE_PATTERNS:
        if pattern.search(haystack):
            return canonical
    return None


async def _inventory(
    *,
    project_key: str | None = None,
    card_id: str | None = None,
) -> list[dict]:
    """Return a list of inventory rows: {id, title, column, depends_on,
    canonical_gated_on} for every card that still carries a legacy prose
    gate marker. Pure read — no DB writes."""
    rows: list[dict] = []
    async with KanbanSessionLocal() as s:
        if card_id is not None:
            stmt = select(KanbanCard).where(KanbanCard.id == card_id)
        elif project_key is not None:
            stmt = select(KanbanCard).where(KanbanCard.project_key == project_key)
        else:
            stmt = select(KanbanCard)
        cards = (await s.execute(stmt)).scalars().all()
        for card in cards:
            canonical = _match_prose_marker(card)
            if canonical is None:
                continue
            rows.append({
                "id": card.id,
                "title": card.title,
                "column": card.column,
                "depends_on": card.depends_on or [],
                "canonical_gated_on": canonical,
            })
    return rows


async def _apply(rows: Iterable[dict]) -> list[dict]:
    """For each inventory row, write ``metadata.gated_on`` via the op-log
    (so device replays don't drop the field) and post a ``**Gate:**``
    activity-feed comment so the migration is auditable.

    Returns a list of {id, action, error?} so the caller can confirm the
    writes landed without re-querying.
    """
    results: list[dict] = []
    async with KanbanSessionLocal() as s:
        for row in rows:
            cid = row["id"]
            canonical = row["canonical_gated_on"]
            try:
                card = await s.get(KanbanCard, cid)
                if card is None:
                    results.append({"id": cid, "action": "skipped",
                                    "error": "card-not-found"})
                    continue
                meta = dict(card.meta or {})
                meta["gated_on"] = canonical
                await apply_operation(
                    s, op_type="update", entity_type="card",
                    project_key=card.project_key or "", entity_id=cid,
                    payload={"metadata": meta},
                )
                await apply_operation(
                    s, op_type="comment", entity_type="comment",
                    project_key=card.project_key or "", entity_id=cid,
                    payload={
                        "text": f"**Gate:** migrated from prose — "
                                f"{canonical}",
                    },
                )
                await s.commit()
                results.append({"id": cid, "action": "applied",
                                "canonical_gated_on": canonical})
            except Exception as exc:
                # Don't let one bad row poison the whole migration: log
                # the failure, roll back the per-card work, and continue.
                await s.rollback()
                results.append({"id": cid, "action": "error",
                                "error": str(exc)})
    return results


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inventory / migrate prose-gated kanban cards to "
                    "metadata.gated_on",
    )
    parser.add_argument("--apply", action="store_true",
                        help="Actually set metadata.gated_on + post the "
                             "audit comment. Without this flag the helper "
                             "only reports (safe default).")
    parser.add_argument("--project-key", default=None,
                        help="Restrict scan to one project_key (default: "
                             "all projects in the kanban DB)")
    parser.add_argument("--card-id", default=None,
                        help="Restrict scan to one card id (overrides "
                             "--project-key)")
    return parser.parse_args(argv)


async def _main(argv: list[str]) -> int:
    args = _parse_args(argv)
    rows = await _inventory(project_key=args.project_key,
                            card_id=args.card_id)
    if not args.apply:
        print(json.dumps({"inventory": rows, "apply": False}, indent=2))
        return 0
    results = await _apply(rows)
    print(json.dumps({"inventory": rows, "apply": True, "results": results},
                     indent=2))
    failed = [r for r in results if r.get("action") == "error"]
    return 0 if not failed else 1


def main() -> None:
    rc = asyncio.run(_main(sys.argv[1:]))
    sys.exit(rc)


if __name__ == "__main__":
    main()