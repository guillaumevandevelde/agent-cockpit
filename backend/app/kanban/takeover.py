"""Promote a headless-dispatched card's session to an attachable tmux pane.

Implements `docs/cockpit/human-takeover-headless-decision.md` §7: a human
"take over" is a *promotion* of the existing session, not a second
takeover-UX. The machinery already exists (§5 of the doc) — this module is
mostly wiring:

1. Best-effort end the headless subprocess (`headless_runner.kill_headless_session`).
2. Resolve the resumable transcript for the card's claimed session
   (`session_recovery._resolve_resume_target` — transport-agnostic, globs
   the worktree's transcript dir, never touches tmux).
3. Persist `resume_session_id`/`resume_project_folder` on the card — the
   same fields crash-recovery uses, not a parallel mechanism.
4. Spawn `claude --resume <session_id>` in tmux under the **same**
   session_name the headless run used. Reusing the name is what keeps the
   `agent:<session_name>` claim, branch, and worktree untouched, and what
   shifts the card's liveness source from the headless registry to tmux for
   free — `reap_stale_claims` already unions both sources by name, so once a
   tmux session with that name exists, the reaper stops needing the headless
   registry to consider the claim alive.

No prompt is passed to the resume spawn: an injected user message would
defeat the point of a takeover (a human wants an idle REPL waiting on them,
not the agent immediately continuing on its own — see decision §4.2).

Promotion is one-way by construction: once `resume_session_id` is set,
`dispatch.get_transport_for_card` prioritizes it over `card.transport`, so
any future redispatch of this card resumes over tmux and never falls back to
headless — no separate "promoted" flag needed.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from app.kanban.dispatch import CLAIMANT_PREFIX, _live_sessions
from app.kanban.session_recovery import _resolve_resume_target

logger = logging.getLogger(__name__)

ResolveFn = Callable[[str, str], tuple[str, str] | None]
LiveSessionsFn = Callable[[], set[str] | None]
KillFn = Callable[[str], bool]
SpawnFn = Callable[..., dict]


class TakeoverError(ValueError):
    """Raised when a card's session cannot be promoted to an attachable pane."""


def _default_spawn(**kwargs) -> dict:
    from app.services.runs.spawn import spawn_session

    return spawn_session("claude-code", **kwargs)


async def promote_to_tmux(
    session,
    *,
    card_id: str,
    project_key: str,
    project_path: str,
    resolve: ResolveFn = _resolve_resume_target,
    live_sessions: LiveSessionsFn = _live_sessions,
    kill_headless: KillFn | None = None,
    spawn: SpawnFn | None = None,
) -> dict:
    """Promote the card's headless session to a tmux pane. Returns the spawn result.

    Raises :class:`TakeoverError` for every case where promotion cannot
    proceed — never partially promotes (kill only happens once every other
    precondition already passed... except the kill itself, which is
    deliberately attempted before resolving the transcript so a still-running
    process can't keep appending to the transcript while it's being read).
    """
    from app.kanban.headless_runner import kill_headless_session
    from app.kanban.operations import apply_operation
    from app.kanban.service import get_card
    from app.services.agentic_cli.base import SpawnCommandOptions

    kill_headless = kill_headless or kill_headless_session
    spawn = spawn or _default_spawn

    card = await get_card(session, card_id)
    if card is None:
        raise TakeoverError("card not found")

    claimant = card.claimed_by or ""
    if not claimant.startswith(CLAIMANT_PREFIX):
        raise TakeoverError("card has no active agent session to take over")
    session_name = claimant[len(CLAIMANT_PREFIX):]

    live = live_sessions()
    if live is None:
        raise TakeoverError("tmux status is unavailable right now — try again")
    if session_name in live:
        raise TakeoverError("session is already an attachable tmux pane")

    kill_headless(session_name)

    target = resolve(project_path, session_name)
    if target is None:
        raise TakeoverError("no resumable transcript found for this session yet")
    resume_session_id, resume_project_folder = target

    await apply_operation(
        session, op_type="update", entity_type="card", project_key=project_key,
        entity_id=card_id,
        payload={"resume_session_id": resume_session_id,
                 "resume_project_folder": resume_project_folder},
    )
    await session.flush()

    options = SpawnCommandOptions(
        directory=project_path,
        mode="resume",
        session_id=resume_session_id,
        project_folder=resume_project_folder,
        skip_permissions=True,
    )
    result = spawn(
        options=options,
        session_name=session_name,
        project_key=project_key,
        runtime="worktree",
    )
    logger.info(
        "promoted headless card %s (session %s) to tmux %s",
        card_id, session_name, result.get("tmux_target"),
    )
    return result
