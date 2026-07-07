"""Resume interrupted agent sessions after a host/backend restart.

When the machine reboots, tmux dies but every card that an agent session was
working on stays in its agent column, still claimed by ``agent:<session>``. The
dispatch reaper would release those claims and orphan the cards; a subsequent
redispatch then builds a *fresh* worktree, throwing away the work-in-progress and
the Claude conversation.

Instead, at startup (before the dispatch scheduler runs, so the reaper never gets
to release the claim first) we detect cards whose agent session is gone but whose
worktree still holds a resumable Claude transcript, tag them with the recorded
session id, and re-dispatch them in *resume* mode. ``claude --resume`` then picks
the conversation back up in the original worktree — see ``dispatch.make_resume_transport``.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.kanban.dispatch import CLAIMANT_PREFIX, get_max_sessions
from app.utils.path_utils import convert_path_to_folder_name, get_claude_projects_dir

logger = logging.getLogger(__name__)

# (project_path, session_name) -> (session_id, project_folder) | None
ResolveFn = Callable[[str, str], tuple[str, str] | None]
# (session, *, card_id, project_path) -> result dict | None
RedispatchFn = Callable[..., Awaitable[dict | None]]


def _recoverable(card, live_sessions: set[str]) -> bool:
    """True when a card's agent session is dead and its work is worth resuming.

    Fixed columns (Backlog/Impediment/Done), human (`me@ui`) claims, unclaimed
    cards, and cards whose session is still live are all left alone. Sandcastle
    cards have no local worktree/transcript, so they can't be resumed this way.
    """
    from app.kanban.schemas import COLUMNS

    if card.column in COLUMNS:
        return False
    claimant = card.claimed_by or ""
    if not claimant.startswith(CLAIMANT_PREFIX):
        return False
    if getattr(card, "transport", None) == "sandcastle":
        return False
    name = claimant[len(CLAIMANT_PREFIX):]
    return name not in live_sessions


def _resolve_resume_target(
    project_path: str, session_name: str, *, projects_dir: Path | None = None,
) -> tuple[str, str] | None:
    """Find the Claude session to resume for a dead agent session.

    A dispatched session runs in ``<project_path>/.claude/worktrees/<session_name>``
    and writes its transcript to ``~/.claude/projects/<encoded-worktree>/<uuid>.jsonl``.
    Returns ``(session_id, project_folder)`` for the most recently modified transcript,
    or None when the worktree or a transcript is missing (nothing to resume).
    """
    worktree = Path(project_path) / ".claude" / "worktrees" / session_name
    if not worktree.exists():
        return None
    folder = convert_path_to_folder_name(str(worktree))
    base = projects_dir if projects_dir is not None else get_claude_projects_dir()
    folder_dir = Path(base) / folder
    if not folder_dir.is_dir():
        return None
    transcripts = sorted(
        folder_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not transcripts:
        return None
    return transcripts[0].stem, folder


async def recover_project(
    session, *, project_key: str, project_path: str, live_sessions: set[str],
    resolve: ResolveFn = _resolve_resume_target,
    redispatch: RedispatchFn | None = None,
) -> list[dict]:
    """Resume every recoverable interrupted session in one project, up to the
    project's configured session cap.

    For each dead-session card that still has a resumable transcript: persist the
    resume session id/folder, then re-dispatch (which selects the resume transport).
    Per-card failures are logged and skipped so one bad card can't block the rest.

    ``redispatch_card`` deliberately bypasses the per-project cap for its normal
    (single-card, human-triggered) use, so this loop must enforce the cap itself.
    Without it, a project that accumulated more dead claims than its cap allows
    (e.g. via repeated dev-server restarts) would burst-resume all of them at
    startup, blowing straight past "Max sessions: N". Cards already claimed by a
    session that's still live count against the budget too; cards left over once
    the budget is exhausted are untouched here -- the reaper picks them up (via
    the same ``_move_to_resume``) on the next cap-respecting dispatch tick.
    """
    from app.kanban.operations import apply_operation
    from app.kanban.schemas import COLUMNS
    from app.kanban.service import list_cards

    if redispatch is None:
        from app.kanban.dispatch import redispatch_card as redispatch

    recovered: list[dict] = []
    cards = await list_cards(session, project_key)

    cap = await get_max_sessions(session, project_key)
    live_active = sum(
        1 for c in cards
        if c.column not in COLUMNS
        and (c.claimed_by or "").startswith(CLAIMANT_PREFIX)
        and (c.claimed_by or "")[len(CLAIMANT_PREFIX):] in live_sessions
    )
    budget = max(cap - live_active, 0)

    for card in cards:
        if budget <= 0:
            break
        if not _recoverable(card, live_sessions):
            continue
        session_name = (card.claimed_by or "")[len(CLAIMANT_PREFIX):]
        target = resolve(project_path, session_name)
        if target is None:
            logger.info(
                "no resumable transcript for card %s (session %s); leaving for reaper",
                card.id, session_name,
            )
            continue
        session_id, project_folder = target
        await apply_operation(
            session, op_type="update", entity_type="card", project_key=project_key,
            entity_id=card.id,
            payload={"resume_session_id": session_id,
                     "resume_project_folder": project_folder},
        )
        await session.flush()
        try:
            result = await redispatch(session, card_id=card.id, project_path=project_path)
        except Exception:
            logger.exception("resume redispatch failed for card %s", card.id)
            continue
        if result is not None:
            recovered.append(result)
            budget -= 1
            logger.info(
                "resumed interrupted session for card %s (session %s -> %s)",
                card.id, session_id, result.get("session_name"),
            )
    return recovered


async def recover_interrupted_sessions() -> int:
    """Resume interrupted agent sessions across every enabled project on this device.

    Called once at startup. Returns the number of sessions resumed. Never raises:
    a recovery failure must not block application startup.
    """
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dispatch import (
        _live_sessions,
        _registered_project_paths,
        list_autodispatch_projects,
        match_project_paths,
    )

    live = _live_sessions()
    if live is None:
        # Ambiguous tmux failure: same caution as the reaper — never assume every
        # session is dead, or we'd resume live work into a duplicate session.
        logger.warning("session recovery skipped: tmux liveness unavailable")
        return 0

    async with KanbanSessionLocal() as ks:
        enabled = set(await list_autodispatch_projects(ks))
    if not enabled:
        return 0

    paths = await _registered_project_paths()
    mapping = match_project_paths(enabled, paths)

    total = 0
    for project_key, project_path in mapping.items():
        async with KanbanSessionLocal() as ks:
            try:
                recovered = await recover_project(
                    ks, project_key=project_key, project_path=project_path,
                    live_sessions=live,
                )
                await ks.commit()
                total += len(recovered)
            except Exception:
                logger.exception("session recovery failed for %s", project_key)

    if total:
        logger.info("resumed %d interrupted agent session(s) after restart", total)
    return total
