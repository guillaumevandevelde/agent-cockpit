# backend/tests/test_pane_resume.py
"""Pane-resume path for rate-limited sessions (kanban card e2116332...).

The CLI doesn't exit on a rate limit — it prints the notice and returns to its
prompt — so a detected limit usually means the tmux pane is still alive. The
existing reaction (kill + move to "To Resume" + scheduled `claude --resume`)
loses the entire session context. The pane-resume path tries the cheaper
alternative first: schedule a continuation nudge via the existing tmux_inject
machinery at the parsed reset time + margin, keep the card claimed in place,
and only fall back to the kill+To Resume reaction when (a) the pane is gone,
(b) the nudge re-hits the limit after max attempts, or (c) the pane never
becomes ready within the existing timeout. See
docs/cockpit/sessie-limiet-auto-dispatch-analyse.md §5 (R2).
"""
import json
import re
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

from app.kanban import dispatch
from app.kanban.operations import apply_operation
from app.kanban.service import get_card, list_cards
from app.utils.path_utils import convert_path_to_folder_name
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()

PK = "git:example.com/me/repo"


def _stub_parse_reset_time(message):
    """Parse-reset-time stand-in for tests — returns (datetime, tz) for
    limit-form messages so handle_rate_limit_signal can unpack parsed.
    Matches the same "look for an explicit reset time" semantic as the real
    ``auto_resume_service.parse_reset_time``."""
    m = re.search(
        r"resets\s+(\d{1,2}(?::\d{2})?(?:am|pm)?)\s*\(([^)]+)\)",
        message or "",
        re.IGNORECASE,
    )
    if not m:
        return None
    time_str, tz_name = m.group(1), m.group(2)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        return None
    now = datetime.now(tz)
    fmt = "%I:%M%p" if ":" in time_str else "%I%p"
    if "am" not in time_str.lower() and "pm" not in time_str.lower():
        fmt = "%H:%M"
    try:
        parsed = datetime.strptime(time_str.lower(), fmt)
    except ValueError:
        return None
    reset = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
    if reset <= now:
        reset += timedelta(days=1)
    return reset, tz_name


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _make_card(s, title="Task", column="engineer"):
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None, payload={"title": title, "column": column},
    )
    await s.flush()
    return cid


def _assistant_error(text):
    return {
        "type": "assistant",
        "isApiErrorMessage": True,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def _assistant(text):
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def _user(text):
    return {
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def _write_transcript(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def _build_worktree_transcript(tmp_path, session_name, entries):
    """Lay out <repo>/.claude/worktrees/<session_name> plus its transcript."""
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / session_name
    worktree.mkdir(parents=True)
    folder = convert_path_to_folder_name(str(worktree))
    projects_dir = tmp_path / "projects"
    folder_dir = projects_dir / folder
    folder_dir.mkdir(parents=True)
    transcript = folder_dir / "sess1.jsonl"
    _write_transcript(transcript, entries)
    return repo, projects_dir, transcript


def _patch_auto_resume(monkeypatch):
    """Stub the auto_resume_service at the source module so handle_rate_limit_signal
    picks up a parse_reset_time that returns a real tuple — patching the
    whole instance with a MagicMock would make parse_reset_time return a
    MagicMock instead of a (datetime, str) tuple and the unpack would fail."""
    import app.services.scheduling.auto_resume as ar_module

    class _Stub:
        parse_reset_time = staticmethod(_stub_parse_reset_time)

        def schedule_resume(self, *a, **kw):
            return "stub-job"

        def cancel(self, *a, **kw):
            return True

    monkeypatch.setattr(ar_module, "auto_resume_service", _Stub())


def _add_job_mock(monkeypatch):
    """Mock scheduler_service._sched.add_job so try_pane_resume's direct
    scheduler access is observable without firing real APScheduler jobs."""
    from app.services.scheduling import scheduler as sched_module
    add_job = patch.object(sched_module.scheduler_service._sched, "add_job")
    remove_job = patch.object(
        sched_module.scheduler_service._sched, "remove_job",
        side_effect=Exception("no prior job"),
    )
    return add_job.start(), remove_job.start()


# ---- try_pane_resume --------------------------------------------------------


@pytest.mark.asyncio
async def test_try_pane_resume_schedules_nudge_when_pane_alive(tmp_path, monkeypatch):
    """Pane still alive → nudge scheduled on the scheduler, card metadata
    tracks the pending nudge, activity comment explains the deferral."""
    session_name = "k-panealive-0001"
    repo, _projects_dir, _transcript = _build_worktree_transcript(tmp_path, session_name, [])
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)
    _patch_auto_resume(monkeypatch)
    add_job_mock, _remove_job_mock = _add_job_mock(monkeypatch)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="pane-alive")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()

    reset_time = datetime.now(UTC) + timedelta(hours=5)

    with patch(
        "app.services.scheduling.session_resolver.resolve_target",
        return_value=f"{session_name}:0.0",
    ) as resolve_mock:
        ok = await dispatch.try_pane_resume(
            cwd=str(repo / ".claude" / "worktrees" / session_name),
            reset_time=reset_time,
            message="Continue where you left off.",
        )
    assert ok is True
    resolve_mock.assert_called_once()
    add_job_mock.assert_called_once()
    # First nudge fires at reset_time + PANE_RESUME_MARGIN_S
    trigger = add_job_mock.call_args.kwargs["trigger"]
    fire_at = trigger.run_date
    assert fire_at >= reset_time + timedelta(seconds=dispatch.PANE_RESUME_MARGIN_S - 5)
    assert fire_at <= reset_time + timedelta(seconds=dispatch.PANE_RESUME_MARGIN_S + 5)
    # Card metadata reflects the pending nudge
    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.meta["pane_resume_pending"] is True
    assert card.meta["pane_resume_attempts"] == 1
    # Card stays on its agent column, claim stays put
    assert card.column == "engineer"
    assert card.claimed_by == f"agent:{session_name}"


@pytest.mark.asyncio
async def test_try_pane_resume_returns_false_when_pane_gone(tmp_path, monkeypatch):
    """Pane gone → try_pane_resume is a clean no-op so the caller can fall
    back to the existing kill+To Resume path. No metadata, no scheduled nudge."""
    session_name = "k-panegone-0001"
    repo, _projects_dir, _transcript = _build_worktree_transcript(tmp_path, session_name, [])
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)
    _patch_auto_resume(monkeypatch)
    add_job_mock, _remove_job_mock = _add_job_mock(monkeypatch)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="pane-gone")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()

    with patch(
        "app.services.scheduling.session_resolver.resolve_target", return_value=None,
    ):
        ok = await dispatch.try_pane_resume(
            cwd=str(repo / ".claude" / "worktrees" / session_name),
            reset_time=datetime.now(UTC) + timedelta(hours=5),
            message="Continue where you left off.",
        )
    assert ok is False
    add_job_mock.assert_not_called()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert not (card.meta or {}).get("pane_resume_pending")
    assert card.column == "engineer"
    assert card.claimed_by == f"agent:{session_name}"


# ---- handle_rate_limit_signal: pane-resume vs fallback ----------------------


@pytest.mark.asyncio
async def test_handle_rate_limit_signal_uses_pane_resume_when_pane_alive(
    tmp_path, monkeypatch,
):
    """When the pane is alive, handle_rate_limit_signal must NOT call
    move_limited_session_to_resume: the card stays in place, the nudge is
    scheduled, the existing reaction is fully deferred."""
    session_name = "k-handlealive-0001"
    repo, _projects_dir, _transcript = _build_worktree_transcript(tmp_path, session_name, [])
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)
    _patch_auto_resume(monkeypatch)
    add_job_mock, _remove_job_mock = _add_job_mock(monkeypatch)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="handle-alive")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()

    with patch(
        "app.services.scheduling.session_resolver.resolve_target",
        return_value=f"{session_name}:0.0",
    ), patch.object(
        dispatch, "move_limited_session_to_resume", return_value=True,
    ) as move_mock:
        moved = await dispatch.handle_rate_limit_signal(
            cwd=str(repo / ".claude" / "worktrees" / session_name),
            message="You've hit your session limit · resets 11:10pm (Europe/Brussels)",
            source="transcript",
        )

    add_job_mock.assert_called_once()
    move_mock.assert_not_called()  # pane-resume took over
    # moved reflects "did the standard kill+To Resume happen", which is no
    assert moved is False

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.column == "engineer"
    assert card.claimed_by == f"agent:{session_name}"
    assert card.meta["pane_resume_pending"] is True


@pytest.mark.asyncio
async def test_handle_rate_limit_signal_falls_back_when_pane_gone(
    tmp_path, monkeypatch,
):
    """When the pane is gone, handle_rate_limit_signal must hit the existing
    kill+To Resume path unchanged. This is the vangnet-route described in
    acceptance criteria #3."""
    session_name = "k-handlegone-0001"
    repo, _projects_dir, _transcript = _build_worktree_transcript(tmp_path, session_name, [])
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)
    _patch_auto_resume(monkeypatch)
    _add_job_mock(monkeypatch)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="handle-gone")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()

    with patch(
        "app.services.scheduling.session_resolver.resolve_target", return_value=None,
    ), patch.object(
        dispatch, "move_limited_session_to_resume", return_value=True,
    ) as move_mock:
        moved = await dispatch.handle_rate_limit_signal(
            cwd=str(repo / ".claude" / "worktrees" / session_name),
            message="You've hit your session limit · resets 11:10pm (Europe/Brussels)",
            source="transcript",
        )

    move_mock.assert_awaited_once()
    assert moved is True


# ---- backoff / max attempts -------------------------------------------------


@pytest.mark.asyncio
async def test_handle_rate_limit_signal_reschedules_with_backoff_on_relimit(
    tmp_path, monkeypatch,
):
    """Card already has pane_resume_pending (a previous nudge ran). Another
    transcript-tail sweep sees another limit hit → bump attempts and
    reschedule the nudge at a later fire time, do NOT fall back yet."""
    session_name = "k-backoff-0001"
    repo, _projects_dir, _transcript = _build_worktree_transcript(tmp_path, session_name, [])
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)
    _patch_auto_resume(monkeypatch)
    add_job_mock, _remove_job_mock = _add_job_mock(monkeypatch)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="backoff")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        # Seed metadata as if a previous nudge was scheduled
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid,
            payload={"metadata": {
                "pane_resume_pending": True,
                "pane_resume_attempts": 1,
                "pane_resume_reset_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
            }},
        )
        await s.commit()

    with patch(
        "app.services.scheduling.session_resolver.resolve_target",
        return_value=f"{session_name}:0.0",
    ), patch.object(
        dispatch, "move_limited_session_to_resume", return_value=True,
    ) as move_mock:
        moved = await dispatch.handle_rate_limit_signal(
            cwd=str(repo / ".claude" / "worktrees" / session_name),
            message="You've hit your session limit · resets 11:10pm (Europe/Brussels)",
            source="transcript",
        )

    add_job_mock.assert_called_once()
    move_mock.assert_not_called()  # backoff path is still pane-resume, not fallback
    assert moved is False

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.meta["pane_resume_pending"] is True
    assert card.meta["pane_resume_attempts"] == 2
    # Fire time strictly later than first nudge's fire time (first was at
    # reset+margin, second at reset+margin + backoff)
    first_fire = datetime.fromisoformat(card.meta["pane_resume_reset_at"]) + timedelta(
        seconds=dispatch.PANE_RESUME_MARGIN_S
    )
    second_fire = add_job_mock.call_args.kwargs["trigger"].run_date
    assert second_fire > first_fire


@pytest.mark.asyncio
async def test_handle_rate_limit_signal_falls_back_after_max_attempts(
    tmp_path, monkeypatch,
):
    """Card has pane_resume_pending with attempts == MAX. Next limit hit must
    trigger the existing kill+To Resume reaction (acceptance criteria #4)."""
    session_name = "k-maxattempts-0001"
    repo, _projects_dir, _transcript = _build_worktree_transcript(tmp_path, session_name, [])
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)
    _patch_auto_resume(monkeypatch)
    add_job_mock, _remove_job_mock = _add_job_mock(monkeypatch)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="max-attempts")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid,
            payload={"metadata": {
                "pane_resume_pending": True,
                "pane_resume_attempts": dispatch.PANE_RESUME_MAX_ATTEMPTS,
                "pane_resume_reset_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
            }},
        )
        await s.commit()

    with patch.object(
        dispatch, "move_limited_session_to_resume", return_value=True,
    ) as move_mock:
        moved = await dispatch.handle_rate_limit_signal(
            cwd=str(repo / ".claude" / "worktrees" / session_name),
            message="You've hit your session limit · resets 11:10pm (Europe/Brussels)",
            source="transcript",
        )

    move_mock.assert_awaited_once()
    add_job_mock.assert_not_called()
    assert moved is True

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    # Pending state cleared on fallback so the next dispatch tick doesn't try
    # to re-attempt pane-resume on this card.
    assert not (card.meta or {}).get("pane_resume_pending")


# ---- recovery ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_detect_transcript_clears_pane_resume_pending_on_recovered_transcript(
    tmp_path, monkeypatch,
):
    """Card has pane_resume_pending; the next transcript-tail sweep shows the
    session recovered (ordinary assistant activity after the previous limit).
    Pending state clears so future sweeps don't try to nudge a healthy session."""
    session_name = "k-recovered-0001"
    repo, projects_dir, _transcript = _build_worktree_transcript(
        tmp_path, session_name,
        [
            _assistant_error("You've hit your session limit · resets 9pm (Europe/Brussels)"),
            # Waited for reset, then nudged successfully
            _user("continue"),
            _assistant("All good, picking up where we left off."),
        ],
    )

    import app.kanban.session_recovery as session_recovery
    monkeypatch.setattr(session_recovery, "get_claude_projects_dir", lambda: projects_dir)
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="recovered")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid,
            payload={"metadata": {
                "pane_resume_pending": True,
                "pane_resume_attempts": 1,
                "pane_resume_reset_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
            }},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    with patch.object(dispatch, "handle_rate_limit_signal") as handle_mock:
        handled = await dispatch.detect_transcript_rate_limits(
            cards=cards, project_path=str(repo),
        )

    # Recovered transcript → no rate-limit handling triggered
    assert handled == 0
    handle_mock.assert_not_called()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert not (card.meta or {}).get("pane_resume_pending")