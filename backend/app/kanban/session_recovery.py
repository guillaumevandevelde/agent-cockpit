"""Resume interrupted agent sessions after a host/backend restart.

When the machine reboots, tmux dies but every card that an agent session was
working on stays in its agent column, still claimed by ``agent:<session>``. The
dispatch reaper would release those claims and orphan the cards; a subsequent
redispatch then builds a *fresh* worktree, throwing away the work-in-progress and
the prior CLI conversation.

Instead, at startup (before the dispatch scheduler runs, so the reaper never gets
to release the claim first) we detect cards whose agent session is gone but whose
worktree still has a vendor-owned resumable session record, tag them with that
session id, and re-dispatch them in *resume* mode through the original CLI adapter.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.kanban.dispatch import CLAIMANT_PREFIX, _effective_resume_cli_id
from app.services.agentic_cli import get_agentic_cli

logger = logging.getLogger(__name__)

# (project_path, session_name) -> (session_id, project_folder) | None
ResolveFn = Callable[..., tuple[str, str | None] | None]
# (session, *, card_id, project_path) -> result dict | None
RedispatchFn = Callable[..., Awaitable[dict | None]]


def _recoverable(card, live_sessions: set[str]) -> bool:
    """True when a card's agent session is dead and its work is worth resuming.

    Fixed columns (Backlog/Impediment/Done), human (`me@ui`) claims, unclaimed
    cards, and cards whose session is still live are all left alone. Sandcastle
    cards have no local worktree or host-side session store, so they cannot be
    resumed this way.
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


def _resolve_transcript_file(
    project_path: str, session_name: str, *, projects_dir: Path | None = None,
) -> Path | None:
    """Find the most recently modified Claude transcript for an agent session."""
    worktree = Path(project_path) / ".claude" / "worktrees" / session_name
    cli = get_agentic_cli("claude-code")
    return cli.resolve_transcript_file(worktree, data_dir=projects_dir)


def _resolve_resume_target(
    project_path: str,
    session_name: str,
    *,
    cli_id: str = "claude-code",
    projects_dir: Path | None = None,
) -> tuple[str, str | None] | None:
    """Delegate resumable-session discovery to the selected CLI adapter."""
    worktree = Path(project_path) / ".claude" / "worktrees" / session_name
    try:
        cli = get_agentic_cli(cli_id)
    except ValueError:
        logger.warning("resume detection requested for unknown cli=%s", cli_id)
        return None
    if not cli.supports_resume_resolution:
        logger.info("resume detection unsupported for cli=%s", cli_id)
        return None
    return cli.resolve_resume_target(worktree, data_dir=projects_dir)


async def recover_project(
    session, *, project_key: str, project_path: str, live_sessions: set[str],
    resolve: ResolveFn = _resolve_resume_target,
    redispatch: RedispatchFn | None = None,
) -> list[dict]:
    """Resume every recoverable interrupted session in one project, bounded by
    the shared hardware-aware session budget.

    For each dead-session card that still has a vendor-resolvable session: persist
    the resume session id/folder, then re-dispatch (which selects the resume transport).
    Per-card failures are logged and skipped so one bad card can't block the rest.

    Commits per recovered card rather than leaving it all to the caller — each
    spawn is a non-transactional tmux side effect, so its claim must be durable
    before the next spawn begins (see the commit site below). The caller still
    commits at the end to cover the nothing-recovered path.

    ``redispatch_card`` deliberately bypasses per-project limits for its normal
    (single-card, human-triggered) use, so this loop must enforce a safety bound
    itself. Without it, a project that accumulated many dead claims (e.g. via
    repeated dev-server restarts) would burst-resume all of them at startup,
    exhausting the global session budget regardless of per-column caps.
    Cards left over once the budget is exhausted are untouched here -- the
    reaper picks them up (via the same ``_move_to_resume``) on the next dispatch
    tick.
    """
    from app.kanban.operations import apply_operation
    from app.kanban.service import list_cards
    from app.services.scheduling.session_registry import session_registry

    if redispatch is None:
        from app.kanban.dispatch import redispatch_card as redispatch

    recovered: list[dict] = []
    cards = await list_cards(session, project_key)

    # Bound by the shared hardware-aware session budget, not a per-project cap.
    # Cards already claimed by a session that's still live count toward that
    # global count, so we subtract them from the budget for resuming dead ones.
    from app.kanban.schemas import COLUMNS
    live_active = sum(
        1 for c in cards
        if c.column not in COLUMNS
        and (c.claimed_by or "").startswith(CLAIMANT_PREFIX)
        and (c.claimed_by or "")[len(CLAIMANT_PREFIX):] in live_sessions
    )
    budget = max(session_registry.effective_max_sessions - live_active, 0)

    for card in cards:
        if budget <= 0:
            break
        if not _recoverable(card, live_sessions):
            continue
        session_name = (card.claimed_by or "")[len(CLAIMANT_PREFIX):]
        cli_id = _effective_resume_cli_id(card)
        target = resolve(
            project_path,
            session_name,
            cli_id=cli_id,
        )
        if target is None:
            logger.info(
                "no resumable session for card %s (session %s, cli=%s); "
                "leaving for reaper",
                card.id,
                session_name,
                cli_id,
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
            result = await redispatch(session, card_id=card.id, project_path=project_path,
                                       caller_source="recover_interrupted_sessions")
        except Exception:
            logger.exception("resume redispatch failed for card %s", card.id)
            continue
        if result is not None:
            recovered.append(result)
            budget -= 1
            # Make this resume durable BEFORE spawning the next one. Spawning
            # is a tmux side effect that no rollback can undo, so a death
            # anywhere later in this loop would otherwise leave the session
            # running while its claim disappears -- and the next boot, reading
            # the identical stale claim, would spawn a *second* session for the
            # same card, forever. That is the 2026-08-07 restart storm: the
            # health watchdog SIGKILLed the backend at 51s while recovery
            # (~37s per resume) was still on its second card, 14 times, leaking
            # 9 live OpenCode sessions into one worktree. Committing per card
            # makes recovery monotonic: work already done is never repeated.
            # Safe to commit mid-loop because both the production and test
            # session factories set ``expire_on_commit=False``, so the `cards`
            # list stays usable (an expiring session would trigger a lazy
            # refresh here and raise MissingGreenlet under asyncio).
            await session.commit()
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
