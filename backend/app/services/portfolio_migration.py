"""Read-only meta-vs-product classification pass over registered projects.

See ``docs/cockpit/portfolio-migration-plan.md``. Per registered ``Project`` it
derives ``kind=meta`` iff ``resolve_project_key(project.path)`` matches the LIVE
cockpit key (``resolve_project_key(<cockpit-checkout>)``, never a hardcoded
string) or appears in the ``COCKPIT_META_PROJECT_KEYS`` env-override; everything
else stays ``product`` (the ``server_default``).

The pass performs **no** write to ``projects.kind`` — a human flips via
``PATCH /projects/{id}`` ``{"kind":"meta"}``. Instead, per candidate it posts one
idempotent ``[portfolio-migration]`` comment on the oldest open card of that
``project_key`` (mirroring how ``stale_detection`` anchors to the oldest Backlog
card), and returns the candidate list with an open-card count so a human has one
overview. Re-running the pass does not double-post: a candidate is skipped when
the most recent ``[portfolio-migration]`` comment on the card already proposes
the same ``derived_kind``.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel
from sqlalchemy import select

from app.config import PROJECT_ROOT, settings
from app.kanban.models import KanbanCard, KanbanOp
from app.kanban.operations import apply_operation
from app.kanban.project_key import resolve_project_key
from app.models.database import Project
from app.utils.timeutils import ensure_aware

logger = logging.getLogger(__name__)

MIGRATION_COMMENT_PREFIX = "[portfolio-migration]"


class MigrationCandidate(BaseModel):
    project_id: int | None
    project_name: str
    project_path: str
    project_key: str
    current_kind: str
    derived_kind: str
    evidence: str  # "remote-match" | "config-override"
    open_cards: int = 0
    comment_posted: bool = False
    comment_card_id: str | None = None


def _comment_text(candidate: MigrationCandidate) -> str:
    if candidate.evidence == "remote-match":
        evidence = (
            f"remote-match — resolve_project_key(project.path) == de live "
            f"cockpit-key `{candidate.project_key}`"
        )
    else:
        evidence = (
            f"config-override — `{candidate.project_key}` staat in "
            f"COCKPIT_META_PROJECT_KEYS"
        )
    return (
        f"{MIGRATION_COMMENT_PREFIX} Voorgestelde classificatie: "
        f"`kind={candidate.derived_kind}` (huidige DB-waarde: "
        f"`{candidate.current_kind}`).\n"
        f"Bewijs: {evidence}.\n"
        f"Read-only pass — er is niets automatisch gewijzigd. Mens beoordeelt en "
        f"flipt handmatig via `PATCH /projects/{candidate.project_id}` "
        f'`{{"kind":"{candidate.derived_kind}"}}`.'
    )


async def _open_cards(kanban, project_key: str) -> list[KanbanCard]:
    """Every non-Done card for a project_key, oldest-first by created_at."""
    rows = list(
        (
            await kanban.execute(
                select(KanbanCard).where(KanbanCard.project_key == project_key)
            )
        )
        .scalars()
        .all()
    )
    open_cards = [c for c in rows if c.column != "Done"]
    open_cards.sort(key=lambda c: ensure_aware(c.created_at))
    return open_cards


async def _already_proposed(kanban, card_id: str, derived_kind: str) -> bool:
    """True when the newest ``[portfolio-migration]`` comment on the card already
    proposes ``derived_kind`` — the idempotency guard for re-runs."""
    rows = (
        await kanban.execute(
            select(KanbanOp.hlc, KanbanOp.payload).where(
                KanbanOp.entity_id == card_id,
                KanbanOp.op_type == "comment",
            )
        )
    ).all()
    latest_text: str | None = None
    latest_hlc: str | None = None
    for hlc, payload in rows:
        text = (payload or {}).get("text", "") or ""
        if not text.startswith(MIGRATION_COMMENT_PREFIX):
            continue
        if latest_hlc is None or hlc > latest_hlc:
            latest_hlc = hlc
            latest_text = text
    return latest_text is not None and f"kind={derived_kind}" in latest_text


async def classify_projects(
    db,
    kanban,
    *,
    cockpit_checkout_path: str,
    extra_meta_keys: list[str],
    key_resolver=None,
) -> list[MigrationCandidate]:
    """Read-only: derive meta-vs-product per registered project.

    Only projects whose derived kind *differs* from the stored ``kind`` become
    candidates (a real proposal); matchers already tagged ``meta`` and the
    ``product`` default are silent. Never writes.
    """
    resolver = key_resolver or resolve_project_key
    cockpit_key = resolver(cockpit_checkout_path)
    override = {k for k in extra_meta_keys if k}

    projects = list((await db.execute(select(Project))).scalars().all())
    candidates: list[MigrationCandidate] = []
    for project in projects:
        key = resolver(project.path)
        is_meta = key == cockpit_key or key in override
        derived = "meta" if is_meta else "product"
        if derived == project.kind:
            continue
        evidence = "remote-match" if key == cockpit_key else "config-override"
        open_cards = await _open_cards(kanban, key)
        candidates.append(
            MigrationCandidate(
                project_id=project.id,
                project_name=project.name,
                project_path=project.path,
                project_key=key,
                current_kind=project.kind,
                derived_kind=derived,
                evidence=evidence,
                open_cards=len(open_cards),
            )
        )
    return candidates


async def run_migration_pass(
    db,
    kanban,
    *,
    cockpit_checkout_path: str | None = None,
    extra_meta_keys: list[str] | None = None,
    key_resolver=None,
    post_comments: bool = True,
) -> list[MigrationCandidate]:
    """Classify, then post one idempotent audit-comment per candidate.

    Read-only w.r.t. ``projects.kind``; the only writes are ``[portfolio-migration]``
    comment-ops on the oldest open card of each candidate project_key. Returns the
    candidate list (with ``comment_posted`` / ``comment_card_id`` filled in) for a
    single human-facing overview.
    """
    cockpit_checkout_path = cockpit_checkout_path or str(PROJECT_ROOT)
    if extra_meta_keys is None:
        extra_meta_keys = settings.meta_project_keys

    candidates = await classify_projects(
        db,
        kanban,
        cockpit_checkout_path=cockpit_checkout_path,
        extra_meta_keys=extra_meta_keys,
        key_resolver=key_resolver,
    )
    if not post_comments:
        return candidates

    posted = 0
    for candidate in candidates:
        open_cards = await _open_cards(kanban, candidate.project_key)
        if not open_cards:
            # No card to anchor the audit-comment to; still reported in the list.
            continue
        oldest = open_cards[0]
        candidate.comment_card_id = oldest.id
        if await _already_proposed(kanban, oldest.id, candidate.derived_kind):
            continue
        await apply_operation(
            kanban,
            op_type="comment",
            entity_type="comment",
            project_key="",
            entity_id=oldest.id,
            payload={"text": _comment_text(candidate)},
        )
        candidate.comment_posted = True
        posted += 1
    await kanban.commit()
    if posted:
        logger.info(
            "portfolio-migration: posted %d %s comment(s)",
            posted, MIGRATION_COMMENT_PREFIX,
        )
    return candidates
