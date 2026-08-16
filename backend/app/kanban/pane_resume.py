"""Pane-resume voor rate-limited sessies (uitgelicht uit dispatch.py).

Wanneer de tmux-pane van een rate-limited sessie nog leeft, plannen we een
nudge op het geparseerde reset-moment in plaats van de sessie te killen en de
kaart naar "To Resume" te schuiven. Zie
docs/cockpit/sessie-limiet-auto-dispatch-analyse.md §5 (R2).

`dispatch.py` importeert deze namen terug, zodat bestaande call-sites en
patch-punten (`dispatch.try_pane_resume`, gebruikt door de reconciler) blijven
werken. Andersom haalt deze module zijn dispatch-helpers lazy op via
module-attribuut-toegang, zodat er geen import-cyclus ontstaat.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.kanban.operations import apply_operation
from app.kanban.project_key import safe_resolve_project_key
from app.kanban.service import list_cards

logger = logging.getLogger(__name__)


def _dispatch():
    """Lazy handle op dispatch.py — voorkomt een import-cyclus, want dispatch
    importeert deze module op modulen-niveau terug."""
    from app.kanban import dispatch

    return dispatch

# Pane-resume: when a rate-limited session's tmux pane is still alive, defer
# the kill+To Resume reaction and try to nudge the same session back to life
# at the parsed reset time via the existing tmux_inject helpers. See
# docs/cockpit/sessie-limiet-auto-dispatch-analyse.md §5 (R2) for the
# motivation; this constant block sets the timing knobs.
#
# Margin: how long after the parsed reset time the *first* nudge fires, in
# seconds. Tiny on purpose — we want to resume the moment the limit resets,
# not minutes later. 60 s absorbs clock skew + the time the Claude CLI takes
# to render the post-reset prompt frame.
PANE_RESUME_MARGIN_S = 60
# Backoff: how much *additional* delay each subsequent nudge adds when a
# previous nudge re-hit the limit. Linear in `attempts` — `reset + margin*attempts`
# — so attempts 1/2/3 fire at reset+60s/+120s/+180s. Small enough that even
# the third try lands inside the same wall-clock window, large enough that
# we don't burn scheduler slots on a stuck limit.
PANE_RESUME_BACKOFF_S = 60
# Max attempts before falling back to the existing kill+To Resume reaction.
# 3 follows the standard retry-with-fallback shape (initial + 2 retries) and
# keeps total wall-clock under ~3 minutes so the operator doesn't sit on a
# stalled session for long.
PANE_RESUME_MAX_ATTEMPTS = 3


def _pane_resume_job_id(cwd: str) -> str:
    """Stable apscheduler job id for the pane-resume nudge of a given cwd.

    Exposed so the fallback path can remove the job deterministically —
    without that, the previously-scheduled nudge still fires at the parsed
    reset time even after the card has been moved to "To Resume", and ends
    up injecting keystrokes into a worktree that's been reused for a
    different session (kaart e2116332, gemeten op 2026-07-24).
    """
    return f"pane-resume-{hash(cwd) % 100000}"


async def _read_pane_resume_state(cwd: str) -> dict | None:
    """Read the pane-resume metadata for the card claimed by `cwd`'s session.

    Returns ``{"attempts": int, "reset_at": iso, "fired": bool}`` when a
    previous nudge is pending, ``None`` otherwise. Reads via
    `_resume_target_from_cwd` + `list_cards` so it follows the same "kanban
    card claimed by this session on a non-fixed column" predicate as
    `move_limited_session_to_resume`.

    ``fired`` distinguishes "nudge scheduled, waiting for the apscheduler
    job to fire it" (False) from "nudge already fired, monitoring for
    recovery/re-limit on subsequent ticks" (True). Without this flag the
    dispatch tick would treat every re-detection of the same in-transcript
    limit as a fresh re-hit and burn the attempt budget in ~30 s, well
    before the scheduled nudge ever got a chance to fire — see kanban card
    e2116332 for the production measurement that surfaced this race.
    """
    target = _dispatch()._resume_target_from_cwd(cwd)
    if target is None:
        return None
    project_path, session_name = target
    project_key = safe_resolve_project_key(project_path)
    if project_key is None:
        return None

    claimant = _dispatch().CLAIMANT_PREFIX + session_name
    from app.kanban.db import KanbanSessionLocal
    async with KanbanSessionLocal() as ks:
        cards = await list_cards(ks, project_key)
    card = next(
        (c for c in cards
         if c.column not in ("Done", "To Resume")
         and c.claimed_by == claimant),
        None,
    )
    if card is None:
        return None
    meta = card.meta or {}
    if not meta.get("pane_resume_pending"):
        return None
    return {
        "attempts": int(meta.get("pane_resume_attempts", 0)),
        "reset_at": meta.get("pane_resume_reset_at"),
        "fired": bool(meta.get("pane_resume_fired", False)),
    }


async def _clear_pane_resume_state(cwd: str) -> None:
    """Strip the pane-resume metadata keys from the card claimed by `cwd`'s
    session. Idempotent — a no-op when there's no card or no pending state.
    Used at fallback time so a later sweep doesn't try to nudge a card that's
    already been moved to "To Resume"."""
    target = _dispatch()._resume_target_from_cwd(cwd)
    if target is None:
        return
    project_path, session_name = target
    project_key = safe_resolve_project_key(project_path)
    if project_key is None:
        return
    claimant = _dispatch().CLAIMANT_PREFIX + session_name
    from app.kanban.db import KanbanSessionLocal
    async with KanbanSessionLocal() as ks:
        cards = await list_cards(ks, project_key)
        card = next(
            (c for c in cards
             if c.column not in ("Done", "To Resume")
             and c.claimed_by == claimant),
            None,
        )
        if card is None:
            return
        meta = dict(card.meta or {})
        if not meta:
            return
        meta.pop("pane_resume_pending", None)
        meta.pop("pane_resume_attempts", None)
        meta.pop("pane_resume_reset_at", None)
        meta.pop("pane_resume_fired", None)
        await apply_operation(
            ks, op_type="update", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={"metadata": meta},
        )
        await ks.commit()


async def _clear_pane_resume_metadata_for_card(card, project_path: str) -> None:
    """Strip pane-resume metadata from a card we already have in hand.

    Used by the detection sweep when the transcript no longer shows an active
    limit (i.e. the previous nudge did land and the session recovered) — keeps
    the attempt counter fresh for the next genuine limit cycle. No-op when
    the card has no pending state. Card stays in place; only the metadata
    keys change.
    """
    meta = dict(card.meta or {})
    if not meta.get("pane_resume_pending"):
        return
    meta.pop("pane_resume_pending", None)
    meta.pop("pane_resume_attempts", None)
    meta.pop("pane_resume_reset_at", None)
    meta.pop("pane_resume_fired", None)
    project_key = safe_resolve_project_key(project_path)
    if project_key is None:
        return
    from app.kanban.db import KanbanSessionLocal
    async with KanbanSessionLocal() as ks:
        await apply_operation(
            ks, op_type="update", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={"metadata": meta},
        )
        await ks.commit()
    logger.info(
        "pane-resume pending cleared on card %s (transcript shows recovery)",
        card.id,
    )


async def _do_move_to_resume(cwd: str, pause_until) -> bool:
    """The existing kill+To Resume reaction (sibling helper so `handle_rate_limit_signal`
    stays a single composition). Mirrors the original exception-swallow so the
    caller doesn't have to."""
    try:
        return await _dispatch().move_limited_session_to_resume(
            cwd, scheduled_at=pause_until.isoformat(),
        )
    except Exception:
        logger.exception("failed to move kanban card to To Resume for %s", cwd)
        return False


async def try_pane_resume(
    cwd: str, reset_time, message: str, *, attempts: int = 1,
) -> bool:
    """Try to keep a rate-limited session alive by injecting a continuation
    nudge into its still-alive tmux pane at reset_time + margin*attempts.

    The Claude Code CLI doesn't exit on a rate limit — it prints the notice
    and returns to its prompt — so the tmux pane is usually still alive at
    detection time. Killing it loses the entire session context; nudging the
    same pane at the reset time recovers without losing anything. Reuses the
    existing `tmux_inject` + `auto_resume_service.schedule_resume` machinery
    so the keystroke delivery path is identical to the manual scheduled-
    message flow that's been in production.

    Returns True when a nudge is scheduled and the card metadata reflects the
    pending state; False when the pane is gone (the caller should fall back to
    `move_limited_session_to_resume`). A no-card-found case is also False —
    for non-kanban sessions (human-started, sandcastle) there's nothing to
    update on the board anyway.
    """
    from apscheduler.triggers.date import DateTrigger

    from app.kanban.db import KanbanSessionLocal
    from app.services.scheduling.scheduler import scheduler_service
    from app.services.scheduling.session_resolver import resolve_target

    target = resolve_target(cwd)
    if target is None:
        return False  # pane gone — caller falls back

    fire_at = reset_time + timedelta(
        seconds=PANE_RESUME_MARGIN_S + PANE_RESUME_BACKOFF_S * (attempts - 1),
    )
    # A reset time can be in the past: the dated weekly-limit wording ("resets
    # Aug 3, 7pm") parses to a real date, so a limit noticed after its reset
    # (idle session, backend restart replaying an old transcript tail) yields a
    # fire_at that APScheduler would silently drop as a misfire -- leaving the
    # card pinned on `pane_resume_pending=True` with nobody left to nudge it.
    # Deliver those immediately instead; the limit has demonstrably lifted.
    fire_at = max(fire_at, datetime.now(UTC) + timedelta(seconds=1))

    # Schedule the nudge via the scheduler directly so we can call our own
    # `_execute_pane_resume` (the standard auto_resume_service._execute_resume
    # doesn't include the wait_for_pane_ready guard the acceptance criteria
    # ask for).
    job_id = _pane_resume_job_id(cwd)
    try:
        scheduler_service._sched.remove_job(job_id)
    except Exception:
        pass
    scheduler_service._sched.add_job(
        _execute_pane_resume,
        trigger=DateTrigger(run_date=fire_at),
        args=[cwd, message],
        id=job_id,
        replace_existing=True,
        misfire_grace_time=300,
        coalesce=True,
    )

    # Persist the pending state on the card so the next dispatch tick knows
    # this nudge is in flight and can back off / fall back when (if) another
    # limit is detected. `pane_resume_fired` starts False and flips to True
    # once `_execute_pane_resume` actually delivers the keystroke — that's
    # the signal that a *new* limit detection is a real re-hit rather than
    # the same in-transcript message being re-scanned every tick.
    resume_target = _dispatch()._resume_target_from_cwd(cwd)
    if resume_target is None:
        return False
    project_path, session_name = resume_target
    project_key = safe_resolve_project_key(project_path)
    if project_key is None:
        return False
    claimant = _dispatch().CLAIMANT_PREFIX + session_name

    async with KanbanSessionLocal() as ks:
        cards = await list_cards(ks, project_key)
        card = next(
            (c for c in cards
             if c.column not in ("Done", "To Resume")
             and c.claimed_by == claimant),
            None,
        )
        if card is None:
            return False
        meta = dict(card.meta or {})
        meta.update({"pane_resume_pending": True, "pane_resume_attempts": attempts,
                     "pane_resume_reset_at": reset_time.isoformat(), "pane_resume_fired": False,
                     "pane_resume_cwd": cwd,  # cwd+message: reconciler.py herbouwt de job hieruit
                     "pane_resume_message": message})
        await apply_operation(
            ks, op_type="update", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={"metadata": meta},
        )
        await _dispatch()._post_rate_limit_activity_comment(
            ks, card=card, project_key=project_key,
            text=(
                f"⏰ Rate-limit gedetecteerd op live pane — nudge #{attempts} "
                f"gepland om {fire_at.isoformat()} (reset {reset_time.isoformat()}). "
                f"Kaart blijft geclaimd; pane blijft leven."
            ),
        )
        await ks.commit()

    logger.info(
        "pane-resume scheduled (cwd=%s attempts=%s fire_at=%s)",
        cwd, attempts, fire_at.isoformat(),
    )
    return True


async def _execute_pane_resume(cwd: str, message: str) -> bool:
    """Scheduler-fired executor for a pending pane-resume.

    Resolves the live tmux pane, waits for it to be ready (so a stuck or
    unrendered pane doesn't silently swallow the keystroke), and sends the
    continuation message via the existing `tmux_inject.send_text` helper.
    Any failure (pane gone, never ready, send_text returned False) falls
    back to the existing kill+To Resume reaction so the card doesn't sit
    claimed indefinitely while the session is actually dead.

    On a successful delivery the card's `pane_resume_fired` metadata flips
    to True — that's the signal that flips the bookkeeping from "nudge
    scheduled, don't reschedule" to "nudge landed, watch the transcript for
    recovery or a real re-hit" (kaart e2116332).
    """
    from app.services.scheduling.session_resolver import resolve_target
    from app.services.scheduling.tmux_inject import send_text, wait_for_pane_ready

    target = resolve_target(cwd)
    if target is None:
        logger.warning("pane-resume: pane gone for %s; falling back", cwd)
        await _pane_resume_fallback_to_kill(cwd)
        return False

    ready = await wait_for_pane_ready(target, timeout_s=30.0)
    if not ready:
        logger.warning(
            "pane-resume: pane %s never became ready; falling back", target,
        )
        await _pane_resume_fallback_to_kill(cwd)
        return False

    ok = send_text(target, message)
    if not ok:
        logger.warning(
            "pane-resume: send_text failed for %s; falling back", target,
        )
        await _pane_resume_fallback_to_kill(cwd)
        return False

    await _mark_pane_resume_fired(cwd)
    logger.info("pane-resume: nudge sent to %s", target)
    return True


async def _mark_pane_resume_fired(cwd: str) -> None:
    """Flip the card's `pane_resume_fired` flag to True after the nudge is
    actually delivered. Idempotent — a no-op when there's no card in the
    expected claimed/pending state. Called from `_execute_pane_resume` after
    a successful send so the next dispatch tick knows that subsequent
    re-detection of an in-transcript limit is a real re-hit rather than the
    same scheduled nudge still waiting in the scheduler queue."""
    target = _dispatch()._resume_target_from_cwd(cwd)
    if target is None:
        return
    project_path, session_name = target
    project_key = safe_resolve_project_key(project_path)
    if project_key is None:
        return
    claimant = _dispatch().CLAIMANT_PREFIX + session_name
    from app.kanban.db import KanbanSessionLocal
    async with KanbanSessionLocal() as ks:
        cards = await list_cards(ks, project_key)
        card = next(
            (c for c in cards
             if c.column not in ("Done", "To Resume")
             and c.claimed_by == claimant),
            None,
        )
        if card is None:
            return
        meta = dict(card.meta or {})
        if not meta.get("pane_resume_pending"):
            return
        if meta.get("pane_resume_fired"):
            return  # already marked — no need to rewrite
        meta["pane_resume_fired"] = True
        await apply_operation(
            ks, op_type="update", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={"metadata": meta},
        )
        await ks.commit()


async def _pane_resume_fallback_to_kill(cwd: str) -> None:
    """When the scheduled nudge can't be delivered (pane gone / not ready /
    send_keys failed) or the max-attempts cap has been hit, strip the
    pending metadata, cancel the still-scheduled apscheduler job, and run
    the standard kill+To Resume reaction so the card moves into the
    existing auto-resume-rebuild path. Reads the parsed reset time back
    from the card metadata so the reaction schedules a resume at the same
    wall-clock moment the (failed) nudge was aiming at.

    The apscheduler-job cancellation is load-bearing: without it, the
    already-scheduled `_execute_pane_resume` still fires at its original
    reset-time + margin and ends up injecting a "Continue where you left
    off." keystroke into whatever tmux pane happens to be hosting the
    worktree by then — which, on a long reset window, is very likely a
    *different* session that claimed the same worktree in the meantime
    (kaart e2116332: 2 lost-injection events gemeten op 2026-07-24).
    """
    from app.services.scheduling.scheduler import scheduler_service

    resume_target = _dispatch()._resume_target_from_cwd(cwd)
    if resume_target is None:
        return
    project_path, session_name = resume_target
    project_key = safe_resolve_project_key(project_path)
    if project_key is None:
        return
    claimant = _dispatch().CLAIMANT_PREFIX + session_name

    # Cancel the still-scheduled apscheduler job up front — idempotent, a
    # missing job (the scheduler was restarted, the nudge already ran, …)
    # is not an error condition here.
    job_id = _pane_resume_job_id(cwd)
    try:
        scheduler_service._sched.remove_job(job_id)
    except Exception:
        pass

    from app.kanban.db import KanbanSessionLocal
    async with KanbanSessionLocal() as ks:
        cards = await list_cards(ks, project_key)
        card = next(
            (c for c in cards
             if c.column not in ("Done", "To Resume")
             and c.claimed_by == claimant),
            None,
        )
        if card is not None:
            meta = dict(card.meta or {})
            reset_at = meta.get("pane_resume_reset_at")
            meta.pop("pane_resume_pending", None)
            meta.pop("pane_resume_attempts", None)
            meta.pop("pane_resume_reset_at", None)
            meta.pop("pane_resume_fired", None)
            await apply_operation(
                ks, op_type="update", entity_type="card", project_key=project_key,
                entity_id=card.id, payload={"metadata": meta},
            )
            await ks.commit()
        else:
            reset_at = None

    scheduled_at = None
    if reset_at:
        try:
            scheduled_at = datetime.fromisoformat(reset_at)
        except ValueError:
            scheduled_at = None

    try:
        await _dispatch().move_limited_session_to_resume(cwd, scheduled_at=scheduled_at)
    except Exception:
        logger.exception(
            "pane-resume fallback: move_limited_session_to_resume failed for %s", cwd,
        )
