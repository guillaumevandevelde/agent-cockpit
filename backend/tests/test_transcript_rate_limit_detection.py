# backend/tests/test_transcript_rate_limit_detection.py
"""Transcript-tail rate-limit detection (kanban card c8ad1ea8...).

A subscription limit never arrives as a Notification hook -- it shows up in
the transcript as a plain assistant message with `isApiErrorMessage: true`.
The reaper's stuck-session pane scan only inspects sessions that never sent a
single hook event, so any mid-session limit is invisible to both existing
detectors. These tests cover the transcript-tail detector that closes that
gap: see docs/cockpit/sessie-limiet-auto-dispatch-analyse.md §1, §2.1, §2.2.
"""
import json
import logging
import unittest.mock as mock

import pytest
import pytest_asyncio

from app.kanban import dispatch, session_recovery
from app.kanban.operations import apply_operation
from app.kanban.service import get_card, list_cards
from app.utils.path_utils import convert_path_to_folder_name
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()

PK = "git:example.com/me/repo"


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _make_card(s, title="Task", column="Backlog"):
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


# ---- _tail_rate_limit_entry ------------------------------------------------


def test_tail_rate_limit_entry_detects_anthropic_session_limit(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [
        _user("keep going"),
        _assistant_error("You've hit your session limit · resets 11:10pm (Europe/Brussels)"),
    ])
    found = dispatch._tail_rate_limit_entry(path)
    assert found is not None
    msg = found[0]
    assert "session limit" in msg.lower()


def test_tail_rate_limit_entry_detects_minimax_token_plan(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [
        _user("keep going"),
        _assistant_error(
            "API Error: Request rejected (429) · Token Plan usage limit reached: "
            "Upgrade your Token Plan or purchase Credits for more usage. (2056)"
        ),
    ])
    found = dispatch._tail_rate_limit_entry(path)
    assert found is not None
    msg = found[0]
    assert "token plan" in msg.lower()


def test_tail_rate_limit_entry_none_when_activity_resumed(tmp_path):
    """An api-error followed by ordinary assistant/user activity means the
    session recovered on its own -- nothing should happen."""
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [
        _assistant_error("You've hit your session limit · resets 11:10pm (Europe/Brussels)"),
        _user("continue"),
        _assistant("Sure, continuing where I left off."),
    ])
    assert dispatch._tail_rate_limit_entry(path) is None


def test_tail_rate_limit_entry_none_without_api_error(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [
        _user("hi"),
        _assistant("hello, how can I help?"),
    ])
    assert dispatch._tail_rate_limit_entry(path) is None


def test_tail_rate_limit_entry_ignores_bookkeeping_entries_after_error(tmp_path):
    """system/last-prompt/file-history-snapshot/attachment entries are
    interleaved by Claude Code but carry no "the agent did something"
    signal -- they must not be mistaken for recovered activity."""
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [
        _assistant_error("You've hit your session limit · resets 9:00pm (Europe/Brussels)"),
        {"type": "system", "subtype": "compact", "durationMs": 10},
        {"type": "last-prompt", "lastPrompt": "do the thing", "sessionId": "s1"},
        {"type": "file-history-snapshot", "messageId": "m1", "snapshot": {}},
    ])
    found = dispatch._tail_rate_limit_entry(path)
    assert found is not None
    msg = found[0]
    assert "session limit" in msg.lower()


def test_tail_rate_limit_entry_none_for_empty_transcript(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("")
    assert dispatch._tail_rate_limit_entry(path) is None


def test_tail_rate_limit_entry_none_for_missing_file(tmp_path):
    assert dispatch._tail_rate_limit_entry(tmp_path / "missing.jsonl") is None


def test_tail_rate_limit_entry_reads_only_the_tail(tmp_path, monkeypatch):
    """A transcript far larger than the tail window must still resolve from
    just the last chunk -- no full-file parse."""
    monkeypatch.setattr(dispatch, "_TRANSCRIPT_TAIL_BYTES", 512)
    path = tmp_path / "t.jsonl"
    entries = [_user("x" * 200) for _ in range(50)]
    entries.append(_assistant_error("You've hit your session limit · resets 9pm (Europe/Brussels)"))
    _write_transcript(path, entries)
    assert path.stat().st_size > 512
    found = dispatch._tail_rate_limit_entry(path)
    assert found is not None


# ---- detect_transcript_rate_limits -----------------------------------------


def _redirect_projects_dir(monkeypatch, projects_dir):
    """Point transcript resolution at a fake projects dir.

    Patch the *consumer*, not the source module: `claude_code.py` does a
    `from app.utils.path_utils import get_claude_projects_dir` at import time,
    so the live binding sits on the adapter module. `session_recovery` used to
    own this call, but 2b195e59 moved resolution into the CLI adapter
    (`cli.resolve_transcript_file(worktree, data_dir=...)`) and dropped the
    import — patching `session_recovery` there now raises AttributeError.

    Redirecting only the base dir keeps the whole resolution chain real
    (worktree path -> convert_path_to_folder_name -> newest *.jsonl), which is
    what these tests exist to cover.
    """
    from app.services.agentic_cli import claude_code as claude_code_cli

    monkeypatch.setattr(
        claude_code_cli, "get_claude_projects_dir", lambda: projects_dir
    )


def _build_worktree_transcript(tmp_path, session_name, entries):
    """Lay out <repo>/.claude/worktrees/<session_name> plus its transcript
    under a fake projects dir, mirroring the real Claude Code layout."""
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


@pytest.mark.asyncio
async def test_detect_transcript_rate_limits_handles_mid_session_limit(tmp_path, monkeypatch):
    """The dominant gap from the analysis: a session that's been alive and
    productive for a while (so it's neither dead nor "stuck" in the reaper's
    sense) hits its limit mid-session. Only the transcript tail notices it."""
    session_name = "k-midlimit-0001"
    repo, projects_dir, _transcript = _build_worktree_transcript(
        tmp_path, session_name,
        [_user("go"), _assistant_error(
            "You've hit your session limit · resets 11:10pm (Europe/Brussels)"
        )],
    )
    _redirect_projects_dir(monkeypatch, projects_dir)
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)
    monkeypatch.setattr(dispatch, "_kill_agent_session", lambda name: None)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="mid-session", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    handled = await dispatch.detect_transcript_rate_limits(
        cards=cards, project_path=str(repo),
    )
    assert handled == 1

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.column == "To Resume"
    assert card.claimed_by is None
    assert card.scheduled_at is not None


@pytest.mark.asyncio
async def test_detect_transcript_rate_limits_is_idempotent(tmp_path, monkeypatch):
    """Re-running the sweep against the post-move card state must not act
    again -- the claim is already released, so there's nothing left to
    detect a signal *for*."""
    session_name = "k-midlimit-0002"
    repo, projects_dir, _transcript = _build_worktree_transcript(
        tmp_path, session_name,
        [_assistant_error("You've hit your session limit · resets 9pm (Europe/Brussels)")],
    )
    _redirect_projects_dir(monkeypatch, projects_dir)
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)
    monkeypatch.setattr(dispatch, "_kill_agent_session", lambda name: None)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="mid-session-2", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    handled_first = await dispatch.detect_transcript_rate_limits(
        cards=cards, project_path=str(repo),
    )
    assert handled_first == 1

    async with KanbanSessionLocal() as s:
        cards_after = await list_cards(s, PK)

    handled_second = await dispatch.detect_transcript_rate_limits(
        cards=cards_after, project_path=str(repo),
    )
    assert handled_second == 0


@pytest.mark.asyncio
async def test_detect_transcript_rate_limits_skips_recovered_session(tmp_path, monkeypatch):
    """A session whose transcript shows activity after the limit must be
    left alone -- it already recovered on its own."""
    session_name = "k-recovered-0001"
    repo, projects_dir, _transcript = _build_worktree_transcript(
        tmp_path, session_name,
        [
            _assistant_error("You've hit your session limit · resets 9pm (Europe/Brussels)"),
            _user("continue"),
            _assistant("Continuing now."),
        ],
    )
    _redirect_projects_dir(monkeypatch, projects_dir)
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="recovered", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    handled = await dispatch.detect_transcript_rate_limits(
        cards=cards, project_path=str(repo),
    )
    assert handled == 0

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.column == "engineer"
    assert card.claimed_by == f"agent:{session_name}"


@pytest.mark.asyncio
async def test_detect_transcript_rate_limits_skips_cards_without_agent_claim(tmp_path):
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="human wip", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "me@ui"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    handled = await dispatch.detect_transcript_rate_limits(
        cards=cards, project_path=str(tmp_path / "repo"),
    )
    assert handled == 0


@pytest.mark.asyncio
async def test_detect_transcript_rate_limits_skips_fixed_columns(tmp_path):
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="parked", column="Backlog")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-parked-0001"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    handled = await dispatch.detect_transcript_rate_limits(
        cards=cards, project_path=str(tmp_path / "repo"),
    )
    assert handled == 0


@pytest.mark.asyncio
async def test_detect_transcript_rate_limits_logs_info_with_session_and_transcript(
    tmp_path, monkeypatch, caplog,
):
    """§1.2 of the analysis doc greps backend logs for the rate-limit
    handling path; the transcript detector must log at INFO which
    transcript/session/classification drove the action, or that
    reproduction recipe stays blind to this new channel."""
    session_name = "k-midlimit-0003"
    repo, projects_dir, transcript = _build_worktree_transcript(
        tmp_path, session_name,
        [_assistant_error("You've hit your session limit · resets 9pm (Europe/Brussels)")],
    )
    _redirect_projects_dir(monkeypatch, projects_dir)
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)
    monkeypatch.setattr(dispatch, "_kill_agent_session", lambda name: None)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="mid-session-3", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    with caplog.at_level(logging.INFO, logger="app.kanban.dispatch"):
        handled = await dispatch.detect_transcript_rate_limits(
            cards=cards, project_path=str(repo),
        )
    assert handled == 1
    messages = [r.message for r in caplog.records]
    assert any(
        session_name in m and str(transcript) in m and "classification=limit" in m
        for m in messages
    ), messages


# ---- handle_rate_limit_signal ----------------------------------------------


@pytest.mark.asyncio
async def test_handle_rate_limit_signal_calls_move_with_parsed_reset_time(monkeypatch):
    from app.services.scheduling.auto_resume import auto_resume_service

    message = "You've hit your session limit · resets 11:10pm (Europe/Brussels)"
    expected_reset_time, _tz = auto_resume_service.parse_reset_time(message)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    with mock.patch.object(
        dispatch, "move_limited_session_to_resume", return_value=True,
    ) as move_mock:
        moved = await dispatch.handle_rate_limit_signal(
            "/p/.claude/worktrees/k-x-0001", message, source="transcript",
        )
    assert moved is True
    move_mock.assert_awaited_once_with(
        "/p/.claude/worktrees/k-x-0001",
        scheduled_at=expected_reset_time.isoformat(),
    )


@pytest.mark.asyncio
async def test_handle_rate_limit_signal_falls_back_when_reset_time_unparseable(monkeypatch):
    from datetime import UTC, datetime, timedelta

    import app.kanban.db as kdb
    from app.services.scheduling.auto_resume import FALLBACK_PAUSE_HOURS
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    before = datetime.now(UTC)
    with mock.patch.object(dispatch, "move_limited_session_to_resume", return_value=False) as move_mock:
        await dispatch.handle_rate_limit_signal(
            "/p/.claude/worktrees/k-x-0002",
            "You've hit your session limit",
            source="transcript",
        )
    after = datetime.now(UTC)

    move_mock.assert_awaited_once()
    scheduled_at = move_mock.call_args.kwargs["scheduled_at"]
    fire_at = datetime.fromisoformat(scheduled_at)
    assert before + timedelta(hours=FALLBACK_PAUSE_HOURS) <= fire_at
    assert fire_at <= after + timedelta(hours=FALLBACK_PAUSE_HOURS)


# ---- rate-limit signal idempotency (kanban card e279a52b…) -----------------
#
# The transcript tail carries the same limit message indefinitely (the
# limited session writes nothing new), so `detect_transcript_rate_limits`
# re-detects it on every dispatch tick. `handle_rate_limit_signal` must NOT
# re-parse / re-set the pause / re-attempt the move on a re-detection of the
# same message — otherwise two production bugs surface:
#
#   1. **Unparseable reset time** (MiniMax Token Plan, etc): fallback is
#      `now + FALLBACK_PAUSE_HOURS`, so each tick's `now` is later than the
#      last and `pause_until` slides forward by 10s per tick. The pause
#      never resolves. (8u36m onafgebroken her-armeren over 3 sessies,
#      logs/backend/*.log 2026-07-23→30.)
#
#   2. **Parseable reset time** (Anthropic "resets 05:20pm"): the original
#      parse produced a *future* reset_time. When the same message is
#      re-parsed at `now` past that original reset_time, `parse_reset_time`'s
#      `if reset_time <= now: reset_time += timedelta(days=1)` rolls the
#      deadline +24u — exactly when the limit was supposed to lift. Gemeten
#      op sessie k-update-readme-e85e om 2026-07-28T03:20:04Z (vier seconden
#      ná reset 05:20 +02:00): pause_until sprong van 05:20 day-1 naar
#      05:20 day-2.
#
# Acceptance: `handle_rate_limit_signal` is idempotent on the same message
# text — second-and-later calls do NOT call `set_paused_until` and do NOT
# re-parse / re-attempt the reaction.


@pytest.mark.asyncio
async def test_handle_rate_limit_signal_is_idempotent_on_redetected_message(monkeypatch):
    """Same message text re-detected at a later dispatch tick must NOT
    re-arm the pause. This is the regression for the +24u rollback on
    Anthropic-style "resets 05:20pm" messages (kaart e279a52b, gemeten op
    sessie k-update-readme-e85e om 2026-07-28T03:20:04Z)."""
    from app.services.scheduling import session_signals as ssignals

    message = "You've hit your session limit · resets 11:10pm (Europe/Brussels)"
    cwd = "/p/.claude/worktrees/k-bug-idempotent-0001"
    ssignals.session_signals.clear("k-bug-idempotent-0001")

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    captured = []
    real_set = None

    # Capture every call to set_paused_until — the load-bearing write we
    # want to assert stays a no-op on the second call.
    from app.kanban import dispatch_pause
    real_set = dispatch_pause.set_paused_until

    async def capture_set(s, when, *, provider=None):
        captured.append((when, provider))
        await real_set(s, when, provider=provider)

    monkeypatch.setattr(dispatch_pause, "set_paused_until", capture_set)

    # First call — fresh signal: full reaction runs.
    await dispatch.handle_rate_limit_signal(cwd, message, source="transcript")
    assert len(captured) == 1, "first call should set the pause"

    # Second call — same message re-detected: must NOT touch the pause.
    await dispatch.handle_rate_limit_signal(cwd, message, source="transcript")
    assert len(captured) == 1, (
        f"second call must not re-set pause; captured={captured!r}"
    )

    # Cleanup so subsequent tests start from a clean slate.
    ssignals.session_signals.clear("k-bug-idempotent-0001")


@pytest.mark.asyncio
async def test_handle_rate_limit_signal_idempotent_when_reset_unparseable(monkeypatch):
    """MiniMax-style unparseable message re-detected: must NOT slide the
    fallback `now + FALLBACK_PAUSE_HOURS` deadline forward. Each tick's `now`
    is later than the last, so without dedup the deadline keeps sliding and
    the pause never resolves."""
    from app.services.scheduling import session_signals as ssignals

    # Message with no parseable reset time (Token Plan wording variant).
    message = "API Error: Request rejected (429) · Token Plan usage limit reached"
    cwd = "/p/.claude/worktrees/k-bug-unparseable-0001"
    ssignals.session_signals.clear("k-bug-unparseable-0001")

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    captured = []
    from app.kanban import dispatch_pause
    real_set = dispatch_pause.set_paused_until

    async def capture_set(s, when, *, provider=None):
        captured.append((when, provider))
        await real_set(s, when, provider=provider)

    monkeypatch.setattr(dispatch_pause, "set_paused_until", capture_set)

    # Two ticks (simulated). The first sets the pause; the second must be a
    # no-op so the deadline stops sliding.
    await dispatch.handle_rate_limit_signal(cwd, message, source="transcript")
    assert len(captured) == 1

    # Sleep briefly so the two timestamps are different if both calls did
    # `now + 5h` — proves the second call didn't recompute.
    import asyncio
    await asyncio.sleep(0.01)

    await dispatch.handle_rate_limit_signal(cwd, message, source="transcript")
    assert len(captured) == 1, (
        f"second tick must NOT re-set pause; captured={captured!r}"
    )

    ssignals.session_signals.clear("k-bug-unparseable-0001")


@pytest.mark.asyncio
async def test_handle_rate_limit_signal_reprocesses_when_message_text_changes(monkeypatch):
    """After recovery the next genuine limit (different message text) must
    run the full reaction — the dedupe gate is per-message-text, not a
    permanent session-level block. Without this carve-out the first-write-wins
    `record_limit` would silently swallow a fresh limit after recovery."""
    from app.services.scheduling import session_signals as ssignals

    first_msg = "You've hit your session limit · resets 11:10pm (Europe/Brussels)"
    second_msg = "You've hit your weekly limit · resets 9pm (Europe/Brussels)"
    cwd = "/p/.claude/worktrees/k-bug-recovery-0001"
    ssignals.session_signals.clear("k-bug-recovery-0001")

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    captured = []
    from app.kanban import dispatch_pause
    real_set = dispatch_pause.set_paused_until

    async def capture_set(s, when, *, provider=None):
        captured.append((when, provider))
        await real_set(s, when, provider=provider)

    monkeypatch.setattr(dispatch_pause, "set_paused_until", capture_set)

    # First limit: full reaction.
    await dispatch.handle_rate_limit_signal(cwd, first_msg, source="transcript")
    assert len(captured) == 1
    first_pause = captured[-1][0]

    # Recovery simulated by clearing the structured signal — production path
    # is `detect_transcript_rate_limits` clearing it when _tail_rate_limit_entry
    # returns None (see test_detect_transcript_rate_limits_clears_signal_on_recovery).
    ssignals.session_signals.clear("k-bug-recovery-0001")

    # Fresh limit with DIFFERENT message text must run the full reaction.
    # The new reset time ("9pm") differs from the first one ("11:10pm"), so the
    # captured pause should be a new value, not the same as the first.
    await dispatch.handle_rate_limit_signal(cwd, second_msg, source="transcript")
    assert len(captured) == 2, (
        "fresh limit after recovery must re-arm the pause"
    )
    assert captured[-1][0] != first_pause, (
        "fresh limit with different message must set a different deadline"
    )

    ssignals.session_signals.clear("k-bug-recovery-0001")


@pytest.mark.asyncio
async def test_detect_transcript_rate_limits_clears_signal_on_recovery(monkeypatch, tmp_path):
    """When the session's transcript shows ordinary activity after the limit,
    the next sweep must clear the structured signal so a *new* limit (with
    a different message) starts from a clean slate — otherwise the
    first-write-wins `record_limit` silently swallows it (the new signal
    never lands in the registry and the dedupe gate built on top of it
    would also wrongly remember the old message)."""
    from app.services.scheduling import session_signals as ssignals

    session_name = "k-bug-recoverysweep-0001"
    # Transcript shows recovery only — no live limit at the tail. The sweep
    # must take the "no active limit" branch and clear the stale signal.
    repo, _projects_dir, transcript = _build_worktree_transcript(
        tmp_path, session_name,
        [
            _assistant_error("You've hit your session limit · resets 11:10pm (Europe/Brussels)"),
            _user("continue"),
            _assistant("Continuing where I left off."),
        ],
    )

    ssignals.session_signals.clear(session_name)

    # Pre-seed the signal with an old message: simulates a previous sweep
    # that recorded this session before recovery (the dominant real-world
    # scenario when a session hits its limit, then recovers on its own,
    # then hits a *different* limit later).
    ssignals.session_signals.record_limit(
        "/p/.claude/worktrees/" + session_name,
        "You've hit your session limit · resets 11:10pm (Europe/Brussels)",
    )
    assert ssignals.session_signals.limit_message(session_name) is not None

    # Patch _resolve_transcript_file on session_recovery — dispatch imports it
    # locally inside detect_transcript_rate_limits, so the symbol lives on
    # session_recovery, not dispatch.
    monkeypatch.setattr(
        session_recovery, "_resolve_transcript_file",
        lambda project_path, name, **kw: (
            transcript if name == session_name else None
        ),
    )
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)
    monkeypatch.setattr(dispatch, "_kill_agent_session", lambda name: None)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="recovery-sweep", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    # Sweep: tail shows ordinary assistant activity after the limit, so the
    # handler must NOT fire — but the stale signal MUST be cleared so the
    # next *genuine* limit (with a different message) can be recorded fresh.
    handled = await dispatch.detect_transcript_rate_limits(
        cards=cards, project_path=str(repo),
    )
    assert handled == 0
    assert ssignals.session_signals.limit_message(session_name) is None, (
        "structured signal still holds the OLD limit message after recovery; "
        "a fresh limit would be silently swallowed by record_limit's "
        "first-write-wins"
    )
    assert ssignals.session_signals.is_rate_limited(session_name) is False

    ssignals.session_signals.clear(session_name)


# ---- restart-survival + age guard (kanban card e279a52b…, revisit) ----------
#
# The first pass at this card deduped purely in memory. That removed the
# ~10 s/tick re-arming while the backend stayed up, but the in-memory registry
# is emptied by any restart (supervisor crash-restart, `cockpit.sh restart`,
# `uvicorn --reload`) and by `_kill_agent_session`. After such a reset the very
# same transcript tail read as a brand-new limit, and the measured +24 u
# rollover happened all over again — the subscription was re-locked at the
# exact moment it came free.
#
# The fix has two halves, both covered below:
#   1. a durable record in KanbanMeta, so the dedupe survives a restart;
#   2. an age guard, so a message whose reset moment has already passed can
#      never arm a pause at all — the backstop for when even the durable
#      record is gone (fresh DB, pruned row).


def _limit_entry(text, timestamp):
    entry = _assistant_error(text)
    entry["timestamp"] = timestamp
    return entry


@pytest.mark.asyncio
async def test_stored_signal_survives_in_memory_registry_reset(monkeypatch):
    """The measured production shape: same message, in-memory registry wiped
    by a restart. The durable record must still recognise it as handled."""
    from app.services.scheduling import session_signals as ssignals

    message = "You've hit your session limit · resets 11:10pm (Europe/Brussels)"
    session_name = "k-restart-0001"
    cwd = f"/p/.claude/worktrees/{session_name}"

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    captured = []
    from app.kanban import dispatch_pause
    real_set = dispatch_pause.set_paused_until

    async def capture_set(s, when, *, provider=None):
        captured.append(when)
        await real_set(s, when, provider=provider)

    monkeypatch.setattr(dispatch_pause, "set_paused_until", capture_set)

    await dispatch.handle_rate_limit_signal(cwd, message, source="transcript")
    assert len(captured) == 1, "first detection arms the pause"

    # Simulate the backend restart: the process-local registry is empty again,
    # exactly as it is after `cockpit.sh restart` or a supervisor crash-restart.
    ssignals.session_signals.clear(session_name)

    await dispatch.handle_rate_limit_signal(cwd, message, source="transcript")
    assert len(captured) == 1, (
        f"the persisted signal must survive the registry reset; captured={captured!r}"
    )

    ssignals.session_signals.clear(session_name)


@pytest.mark.asyncio
async def test_expired_limit_message_does_not_arm_a_pause(monkeypatch):
    """AC2 + the headline regression: the same limit message re-read *after*
    its own reset must not set a pause at all — not the original deadline, and
    certainly not one rolled a day forward.

    This is the exact production incident, replayed: session
    `k-update-readme-e85e` saw its deadline jump from 2026-07-28T05:20+02:00 to
    2026-07-29T05:20+02:00 at 03:20:04Z — four seconds after the reset it was
    waiting for."""
    from datetime import UTC, datetime, timedelta

    from app.services.scheduling import session_signals as ssignals

    message = "You've hit your session limit · resets 5:20am (Europe/Brussels)"
    session_name = "k-expired-0001"
    cwd = f"/p/.claude/worktrees/{session_name}"
    ssignals.session_signals.clear(session_name)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    captured = []
    from app.kanban import dispatch_pause
    real_set = dispatch_pause.set_paused_until

    async def capture_set(s, when, *, provider=None):
        captured.append(when)
        await real_set(s, when, provider=provider)

    monkeypatch.setattr(dispatch_pause, "set_paused_until", capture_set)

    # The message was written a full day ago, so its 05:20 reset is long past.
    observed_at = datetime.now(UTC) - timedelta(days=1)
    await dispatch.handle_rate_limit_signal(
        cwd, message, source="transcript", observed_at=observed_at,
    )

    assert captured == [], (
        "a limit whose reset already passed must not arm a pause; "
        f"captured={captured!r}"
    )

    ssignals.session_signals.clear(session_name)


@pytest.mark.asyncio
async def test_same_message_around_reset_does_not_push_the_deadline_forward(monkeypatch):
    """AC4, with the clock actually crossing the reset moment.

    This is the measured incident, replayed end to end. Detection 1 happens
    before the reset and arms a deadline. Detection 2 is the *same* message,
    read from the *same* unchanged transcript, at a wall-clock moment past
    that deadline — with both dedupe layers wiped, so only the age guard is
    left. The pause must not move forward. Before the fix this second call
    produced the +24 u jump seen on session k-update-readme-e85e.
    """
    from datetime import UTC, datetime, timedelta

    from app.kanban.rate_limit_signals import clear_handled_signal
    from app.services.scheduling import session_signals as ssignals

    session_name = "k-rollover-0001"
    cwd = f"/p/.claude/worktrees/{session_name}"
    ssignals.session_signals.clear(session_name)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    # The message announces a 05:20 UTC reset and was written at 03:20 UTC —
    # the shape of the production incident, two hours before its own reset.
    observed_at = datetime(2026, 7, 28, 3, 20, 0, tzinfo=UTC)
    reset_at = datetime(2026, 7, 28, 5, 20, 0, tzinfo=UTC)
    message = "You've hit your session limit \u00b7 resets 05:20 (UTC)"

    captured = []
    from app.kanban import dispatch_pause
    real_set = dispatch_pause.set_paused_until

    async def capture_set(s, when, *, provider=None):
        captured.append(when)
        await real_set(s, when, provider=provider)

    monkeypatch.setattr(dispatch_pause, "set_paused_until", capture_set)

    # Control the wall clock `handle_rate_limit_signal` reads. Patched on the
    # *consumer* module (dispatch), which is where the `datetime` name it calls
    # is actually bound — see the test-doubles convention in CLAUDE.md.
    class _Clock(datetime):
        current = observed_at

        @classmethod
        def now(cls, tz=None):
            return cls.current if tz is None else cls.current.astimezone(tz)

    monkeypatch.setattr(dispatch, "datetime", _Clock)

    # Tick 1: four seconds after the limit was announced, two hours before the
    # reset. The pause is armed at 05:20.
    _Clock.current = observed_at + timedelta(seconds=4)
    await dispatch.handle_rate_limit_signal(
        cwd, message, source="transcript", observed_at=observed_at,
    )
    assert len(captured) == 1, "first detection arms the pause"
    assert captured[0] == reset_at
    first_deadline = captured[0]

    # Wipe both dedupe layers: the restart-plus-pruned-row worst case.
    ssignals.session_signals.clear(session_name)
    async with KanbanSessionLocal() as s:
        await clear_handled_signal(s, session_name)
        await s.commit()

    # Tick 2: four seconds AFTER the reset — the exact moment the production
    # deadline jumped a day forward. Same message, same transcript timestamp.
    _Clock.current = reset_at + timedelta(seconds=4)
    await dispatch.handle_rate_limit_signal(
        cwd, message, source="transcript", observed_at=observed_at,
    )

    assert len(captured) == 1, (
        "re-detection past the reset must not arm another pause; "
        f"captured={captured!r}"
    )
    assert all(d <= first_deadline for d in captured), (
        f"the deadline must never move forward; captured={captured!r}"
    )

    ssignals.session_signals.clear(session_name)


@pytest.mark.asyncio
async def test_fresh_message_with_past_clock_time_still_rolls_over(monkeypatch):
    """AC3: the day-rollover stays correct for a *fresh* message. A limit
    reported at 23:00 saying "resets 1am" means 1am tomorrow, and that must
    still arm a pause."""
    from datetime import UTC, datetime, timedelta

    from app.services.scheduling import session_signals as ssignals

    session_name = "k-rollfresh-0001"
    cwd = f"/p/.claude/worktrees/{session_name}"
    ssignals.session_signals.clear(session_name)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    now = datetime.now(UTC)
    # One hour ago on the clock: without a rollover this would be in the past.
    past_clock = now - timedelta(hours=1)
    message = (
        "You've hit your session limit · resets "
        f"{past_clock.strftime('%H:%M')} (UTC)"
    )

    captured = []
    from app.kanban import dispatch_pause
    real_set = dispatch_pause.set_paused_until

    async def capture_set(s, when, *, provider=None):
        captured.append(when)
        await real_set(s, when, provider=provider)

    monkeypatch.setattr(dispatch_pause, "set_paused_until", capture_set)

    # No observed_at: a fresh detection, exactly like the Notification hook.
    await dispatch.handle_rate_limit_signal(cwd, message, source="hook")

    assert len(captured) == 1, "a fresh message must still arm a pause"
    assert captured[0] > now, (
        f"the rollover must land tomorrow, not in the past; got {captured[0]}"
    )

    ssignals.session_signals.clear(session_name)


@pytest.mark.asyncio
async def test_unparseable_message_deadline_is_anchored_to_the_message(monkeypatch):
    """The MiniMax half of the bug: an unparseable message re-read hours later
    must not push its `+ FALLBACK_PAUSE_HOURS` fallback hours further out. The
    fallback is anchored to when the message was written, so an old one lands
    in the past and the age guard drops it."""
    from datetime import UTC, datetime, timedelta

    from app.services.scheduling import session_signals as ssignals
    from app.services.scheduling.auto_resume import FALLBACK_PAUSE_HOURS

    session_name = "k-minimax-0001"
    cwd = f"/p/.claude/worktrees/{session_name}"
    ssignals.session_signals.clear(session_name)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    captured = []
    from app.kanban import dispatch_pause
    real_set = dispatch_pause.set_paused_until

    async def capture_set(s, when, *, provider=None):
        captured.append(when)
        await real_set(s, when, provider=provider)

    monkeypatch.setattr(dispatch_pause, "set_paused_until", capture_set)

    message = "API Error: Request rejected (429) · Token Plan usage limit reached"
    observed_at = datetime.now(UTC) - timedelta(hours=FALLBACK_PAUSE_HOURS + 1)

    await dispatch.handle_rate_limit_signal(
        cwd, message, source="transcript", observed_at=observed_at,
    )

    assert captured == [], (
        "an unparseable limit older than the fallback window must not arm a "
        f"pause; captured={captured!r}"
    )

    ssignals.session_signals.clear(session_name)


@pytest.mark.asyncio
async def test_tail_rate_limit_entry_returns_the_message_timestamp(tmp_path):
    """The age guard needs to know when the limit was announced; the transcript
    entry carries it."""
    from datetime import UTC, datetime

    path = tmp_path / "t.jsonl"
    _write_transcript(path, [
        _user("go"),
        _limit_entry(
            "You've hit your session limit · resets 11:10pm (Europe/Brussels)",
            "2026-07-28T03:20:04.955Z",
        ),
    ])

    found = dispatch._tail_rate_limit_entry(path)
    assert found is not None
    message, observed_at = found
    assert "session limit" in message
    assert observed_at == datetime(2026, 7, 28, 3, 20, 4, 955000, tzinfo=UTC)


def test_tail_rate_limit_entry_tolerates_a_missing_timestamp(tmp_path):
    """Older transcripts (and the tests above) carry no timestamp — that must
    degrade to "unknown", not to a crash."""
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [
        _assistant_error("You've hit your session limit · resets 11:10pm (Europe/Brussels)"),
    ])
    found = dispatch._tail_rate_limit_entry(path)
    assert found is not None
    assert found[1] is None


@pytest.mark.asyncio
async def test_sweep_clears_the_persisted_signal_on_recovery(monkeypatch, tmp_path):
    """The durable half of the recovery path: a session that recovered must
    also lose its stored record, so the next genuine limit under that name is
    handled as a fresh event even if it happens to carry identical wording."""
    from app.kanban.rate_limit_signals import (
        get_handled_signal,
        record_handled_signal,
    )
    from app.services.scheduling import session_signals as ssignals

    session_name = "k-persisted-recov"
    message = "You've hit your session limit · resets 11:10pm (Europe/Brussels)"
    repo, _projects_dir, transcript = _build_worktree_transcript(
        tmp_path, session_name,
        [
            _assistant_error(message),
            _user("continue"),
            _assistant("Continuing where I left off."),
        ],
    )

    ssignals.session_signals.clear(session_name)
    monkeypatch.setattr(
        session_recovery, "_resolve_transcript_file",
        lambda project_path, name, **kw: (
            transcript if name == session_name else None
        ),
    )
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)
    monkeypatch.setattr(dispatch, "_kill_agent_session", lambda name: None)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    from datetime import UTC, datetime

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="persisted-recovery", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await record_handled_signal(
            s, session_name, message,
            observed_at=datetime.now(UTC), pause_until=datetime.now(UTC),
        )
        await s.commit()
        cards = await list_cards(s, PK)
        assert await get_handled_signal(s, session_name) is not None

    handled = await dispatch.detect_transcript_rate_limits(
        cards=cards, project_path=str(repo),
    )
    assert handled == 0

    async with KanbanSessionLocal() as s:
        assert await get_handled_signal(s, session_name) is None, (
            "the stored signal must be cleared once the session recovered"
        )

    ssignals.session_signals.clear(session_name)
