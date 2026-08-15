"""Rebuild scheduled work from the database at startup.

The scheduler runs on APScheduler's default in-memory jobstore, so every job
dies with the process. For a recurring job that is harmless — the next boot
installs it again. For a **one-shot** job it is not: a pane-resume scheduled at
a rate-limit reset time simply never fires after a restart, and the card stays
claimed with ``pane_resume_pending=True`` and nobody left to nudge it. That is
the incident this module exists to prevent.

The rule this establishes: **the database is the truth, the scheduler is a
cache.** Every durable commitment is a row; at boot one routine reads the rows
and installs the jobs again. Overdue commitments fire straight away.

This generalises a pattern that already proved itself here:
``recurring_triggers.run_boot_inhaal`` does exactly this for cron triggers,
written after a scheduled message missed its Monday. Rather than add a second
mechanism next to it, new commitments are reconciled the same way.
"""
import logging

logger = logging.getLogger(__name__)


async def reinstall_pending_pane_resumes() -> int:
    """Re-schedule every pane resume that a restart dropped.

    Returns the number of resumes re-installed. Zero is the common case.

    Re-uses ``try_pane_resume`` rather than re-implementing the scheduling: it
    already clamps a reset time that has passed to "one second from now"
    (a restart replaying an old transcript tail produces exactly that), and it
    rewrites the same metadata, so running it twice is a no-op.
    """
    from sqlalchemy import select

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dispatch import try_pane_resume
    from app.kanban.models import KanbanCard

    pending: list[dict] = []
    async with KanbanSessionLocal() as session:
        cards = (await session.execute(select(KanbanCard))).scalars().all()
        for card in cards:
            meta = card.meta or {}
            if not meta.get("pane_resume_pending") or meta.get("pane_resume_fired"):
                continue
            cwd = meta.get("pane_resume_cwd")
            reset_at = meta.get("pane_resume_reset_at")
            if not cwd or not reset_at:
                # Written before cwd/message were persisted (or hand-edited).
                # Nothing to rebuild from; leave it for the reaper to notice.
                logger.warning(
                    "card %s has pane_resume_pending but no cwd/reset_at to rebuild from",
                    card.id,
                )
                continue
            pending.append({
                "card_id": card.id,
                "cwd": cwd,
                "message": meta.get("pane_resume_message") or "",
                "reset_at": reset_at,
                "attempts": meta.get("pane_resume_attempts", 1),
            })

    installed = 0
    for item in pending:
        try:
            from datetime import datetime

            reset_time = datetime.fromisoformat(item["reset_at"])
            ok = await try_pane_resume(
                item["cwd"],
                reset_time,
                item["message"],
                attempts=item["attempts"],
            )
            if ok:
                installed += 1
            else:
                # The pane is gone; there is nothing to resume into. The card
                # keeps its flag so the existing reaper path can close it out.
                logger.info(
                    "pane for card %s no longer exists — not re-installing",
                    item["card_id"],
                )
        except Exception:
            logger.exception("failed to re-install pane resume for card %s", item["card_id"])

    if installed:
        logger.info("reconciler re-installed %d pane resume(s) after restart", installed)
    return installed
