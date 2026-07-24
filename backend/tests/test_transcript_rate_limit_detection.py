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


# ---- _tail_rate_limit_message ----------------------------------------------


def test_tail_rate_limit_message_detects_anthropic_session_limit(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [
        _user("keep going"),
        _assistant_error("You've hit your session limit · resets 11:10pm (Europe/Brussels)"),
    ])
    msg = dispatch._tail_rate_limit_message(path)
    assert msg is not None
    assert "session limit" in msg.lower()


def test_tail_rate_limit_message_detects_minimax_token_plan(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [
        _user("keep going"),
        _assistant_error(
            "API Error: Request rejected (429) · Token Plan usage limit reached: "
            "Upgrade your Token Plan or purchase Credits for more usage. (2056)"
        ),
    ])
    msg = dispatch._tail_rate_limit_message(path)
    assert msg is not None
    assert "token plan" in msg.lower()


def test_tail_rate_limit_message_none_when_activity_resumed(tmp_path):
    """An api-error followed by ordinary assistant/user activity means the
    session recovered on its own -- nothing should happen."""
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [
        _assistant_error("You've hit your session limit · resets 11:10pm (Europe/Brussels)"),
        _user("continue"),
        _assistant("Sure, continuing where I left off."),
    ])
    assert dispatch._tail_rate_limit_message(path) is None


def test_tail_rate_limit_message_none_without_api_error(tmp_path):
    path = tmp_path / "t.jsonl"
    _write_transcript(path, [
        _user("hi"),
        _assistant("hello, how can I help?"),
    ])
    assert dispatch._tail_rate_limit_message(path) is None


def test_tail_rate_limit_message_ignores_bookkeeping_entries_after_error(tmp_path):
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
    msg = dispatch._tail_rate_limit_message(path)
    assert msg is not None
    assert "session limit" in msg.lower()


def test_tail_rate_limit_message_none_for_empty_transcript(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text("")
    assert dispatch._tail_rate_limit_message(path) is None


def test_tail_rate_limit_message_none_for_missing_file(tmp_path):
    assert dispatch._tail_rate_limit_message(tmp_path / "missing.jsonl") is None


def test_tail_rate_limit_message_reads_only_the_tail(tmp_path, monkeypatch):
    """A transcript far larger than the tail window must still resolve from
    just the last chunk -- no full-file parse."""
    monkeypatch.setattr(dispatch, "_TRANSCRIPT_TAIL_BYTES", 512)
    path = tmp_path / "t.jsonl"
    entries = [_user("x" * 200) for _ in range(50)]
    entries.append(_assistant_error("You've hit your session limit · resets 9pm (Europe/Brussels)"))
    _write_transcript(path, entries)
    assert path.stat().st_size > 512
    msg = dispatch._tail_rate_limit_message(path)
    assert msg is not None


# ---- detect_transcript_rate_limits -----------------------------------------


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
    monkeypatch.setattr(session_recovery, "get_claude_projects_dir", lambda: projects_dir)
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
    monkeypatch.setattr(session_recovery, "get_claude_projects_dir", lambda: projects_dir)
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
    monkeypatch.setattr(session_recovery, "get_claude_projects_dir", lambda: projects_dir)
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
    monkeypatch.setattr(session_recovery, "get_claude_projects_dir", lambda: projects_dir)
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
