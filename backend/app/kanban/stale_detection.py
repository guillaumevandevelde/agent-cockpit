"""Stale-project detection: signal (never block) projects whose Backlog is stuck.

A product-project can sit untouched in Backlog for days — not because of a bug,
but because another project keeps winning the shared dispatch attention. This is
a scheduler-driven detector (see ``main.py`` lifespan / ``scheduler.py``) that,
for every autodispatch-enabled project with at least one Backlog card, checks
whether the project's last Done-move is older than ``stale_threshold_hours``. If
so it posts a single ``[portfolio-stale]`` comment on the oldest Backlog card.

Deliberately a **signal, not a blockade**: it never moves a card to Impediment
(that would freeze dispatch); a human or synthesis decides what to do. Dedup
state lives in ``KanbanMeta`` (``portfolio_stale:<project_key>:<card_id>:last_posted_at``)
so a still-stale project is flagged at most once per stale window.
"""
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.config import settings
from app.kanban.models import KanbanCard, KanbanMeta, KanbanOp
from app.kanban.operations import apply_operation
from app.utils.timeutils import ensure_aware

logger = logging.getLogger(__name__)

STALE_META_PREFIX = "portfolio_stale:"
STALE_COMMENT_PREFIX = "[portfolio-stale]"


def _stale_meta_key(project_key: str, card_id: str) -> str:
    return f"{STALE_META_PREFIX}{project_key}:{card_id}:last_posted_at"


async def _last_done_move_at(session, card_ids: list[str]) -> datetime | None:
    """Timestamp of the most recent move-to-Done op across the project's cards."""
    if not card_ids:
        return None
    rows = (
        await session.execute(
            select(KanbanOp.created_at, KanbanOp.payload).where(
                KanbanOp.entity_id.in_(card_ids),
                KanbanOp.op_type == "move",
            )
        )
    ).all()
    latest: datetime | None = None
    for created_at, payload in rows:
        if (payload or {}).get("column") != "Done":
            continue
        when = ensure_aware(created_at)
        if latest is None or when > latest:
            latest = when
    return latest


async def _check_project(session, project_key: str) -> bool:
    """Flag one project if stale. Returns True when a comment was posted."""
    from app.kanban.service import list_cards

    cards = await list_cards(session, project_key)
    backlog = [c for c in cards if c.column == "Backlog"]
    if not backlog:
        return False

    now = datetime.now(UTC)
    last_progress = await _last_done_move_at(session, [c.id for c in cards])
    if last_progress is None:
        # A project that has never completed anything: anchor to its oldest card
        # so a brand-new project isn't flagged the moment it gets a Backlog card,
        # while a long-idle never-finished project still trips the threshold.
        last_progress = min(ensure_aware(c.created_at) for c in cards)

    threshold = timedelta(hours=settings.stale_threshold_hours)
    if now - last_progress < threshold:
        return False

    oldest = min(backlog, key=lambda c: ensure_aware(c.created_at))
    meta_key = _stale_meta_key(project_key, oldest.id)
    existing = await session.get(KanbanMeta, meta_key)
    if existing is not None:
        try:
            last_posted = ensure_aware(datetime.fromisoformat(existing.value))
        except ValueError:
            last_posted = None
        # One comment per card per stale window — don't re-post while the last
        # signal is still fresh.
        if last_posted is not None and now - last_posted < threshold:
            return False

    hours = int((now - last_progress).total_seconds() // 3600)
    text = settings.stale_comment_template.format(hours=hours, backlog=len(backlog))
    await apply_operation(
        session, op_type="comment", entity_type="comment",
        project_key="", entity_id=oldest.id, payload={"text": text},
    )
    if existing is None:
        session.add(KanbanMeta(key=meta_key, value=now.isoformat()))
    else:
        existing.value = now.isoformat()
    return True


async def run_stale_detection_tick() -> None:
    """One detection cycle over every autodispatch-enabled project on this device."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dispatch import list_autodispatch_projects

    async with KanbanSessionLocal() as ks:
        enabled = await list_autodispatch_projects(ks)
        posted = 0
        for project_key in enabled:
            try:
                if await _check_project(ks, project_key):
                    posted += 1
            except Exception:
                logger.exception("stale-detection failed for %s", project_key)
        await ks.commit()
    if posted:
        logger.info(
            "stale-detection: posted %d %s comment(s)",
            posted, STALE_COMMENT_PREFIX,
        )
