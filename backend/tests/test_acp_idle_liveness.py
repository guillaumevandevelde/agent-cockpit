"""ACP idle-liveness detector + opencode resume-gate (kanban card 2fa8d501…).

Companion to ``test_progress_liveness``: that detector covers Claude Code
sessions (transcript mtime). ACP/opencode sessions were missing a parallel
detector — every existing liveness source (``live_sessions`` tmux,
``_structured_rate_limit_signal`` notification, ``_session_has_transcript``
pane scan) misses a session whose CLI process is alive but the agent itself
is hung on a subagent call that died with a defect (``prompt_async failed
cause=Die(ProviderModelNotFoundError)``, observed 2026-08-09 on the four
cards listed in the parent card).

``check_acp_idle_liveness`` reads ``MAX(time_updated)`` from the
session-row of the worktree in the opencode SQLite store. Silence past
the signal threshold posts a "stilstaand" comment; silence past the
action threshold moves the card to ``Impediment`` (NOT ``To Resume`` —
resuming would reproduce the hang, see the resume-gate tests below).
``OpenCodeCli.can_resume_safely`` blocks any resume whose last ``part``
is an unresolved tool call.

Failure modes the tests bound:

  - growing session (``time_updated`` advancing between ticks) must not
    trigger any action.
  - stalled past signal threshold: comment posted, card NOT released.
  - stalled past action threshold: card moved to Impediment with a
    structured comment, NO ``resume_session_id`` written.
  - session not in ``acp_live``: skipped — those transports own their own
    liveness check; the detector runs only when ACP says "alive in
    pidfile + process check", which is the exact signature of the bug
    class.
  - non-opencode CLI (Claude Code): skipped — that's ``check_progress_liveness``'s
    lane, this detector covers the ACP-only gap.
  - missing opencode DB / missing session row: fail-open, no action.
  - signal comment posted once per stall window.
  - resume-gate: last part ``type=tool`` with ``state.status not in
    {'completed', 'error'}`` blocks the resume; last part ``step-finish``
    or ``step-start`` allows it.
"""
from __future__ import annotations

import json
import sqlite3
import time

import pytest
import pytest_asyncio

from app.kanban import dispatch
from app.kanban.operations import apply_operation
from app.kanban.service import get_card, list_cards
from app.services.agentic_cli.open_code import OpenCodeCli
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()
PK = "git:example.com/me/repo"


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    dispatch._progress_liveness_state.clear()
    # The ACP-idle-liveness tracker uses a parallel state dict; clear it too
    # so a snapshot from a prior test doesn't leak.
    if hasattr(dispatch, "_acp_idle_state"):
        dispatch._acp_idle_state.clear()
    yield


async def _make_card(s, title="Task", column="engineer", executor_agent_id=None):
    payload = {"title": title, "column": column}
    if executor_agent_id is not None:
        payload["executor_agent_id"] = executor_agent_id
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None, payload=payload,
    )
    await s.flush()
    return cid


def _build_opencode_db(
    tmp_path,
    worktree: str,
    sessions: list[tuple[str, int, int | None]],
    parts: list[tuple[str, str, int, str]],
) -> str:
    """Lay out a fake opencode.db under ``<tmp_path>/open-code/opencode.db``.

    ``sessions``: list of ``(session_id, time_updated_ms, time_archived_ms_or_None)``.
    ``parts``: list of ``(part_id, session_id, time_updated_ms, data_json)``.

    The DB schema mirrors what ``backend/app/services/agentic_cli/open_code.py``
    reads (see the live-schema probe in the dispatch implementation).
    """
    data_dir = tmp_path / "open-code"
    data_dir.mkdir(exist_ok=True)
    db = data_dir / "opencode.db"
    # Idempotent: a single test calls ``_build_opencode_db`` multiple times
    # to advance ``time_updated`` between ticks, and we want each call to
    # re-create the schema cleanly instead of failing on duplicate tables.
    try:
        db.unlink()
    except FileNotFoundError:
        pass
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            directory TEXT NOT NULL,
            time_updated INTEGER NOT NULL,
            time_archived INTEGER,
            parent_id TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            time_created INTEGER NOT NULL,
            time_updated INTEGER NOT NULL,
            data TEXT NOT NULL
        );
        """
    )
    con.executemany(
        "INSERT INTO session(id, directory, time_updated, time_archived) "
        "VALUES (?, ?, ?, ?)",
        [(sid, worktree, tu, ta) for sid, tu, ta in sessions],
    )
    con.executemany(
        "INSERT INTO part(id, message_id, session_id, time_created, time_updated, data) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(pid, "msg-x", sid, tu, tu, data) for pid, sid, tu, data in parts],
    )
    con.commit()
    con.close()
    return str(data_dir)


def _redirect_opencode_data_dir(monkeypatch, data_dir):
    """Point the OpenCodeCli resolver at a fake data dir.

    Mirrors the patch-the-consumer rule from
    ``docs/cockpit/test-doubles-convention.md``: the adapter binds
    ``get_opencode_data_home`` into its own namespace at import time, so
    the patch has to land on the opencode module.
    """
    from app.services.agentic_cli import open_code
    monkeypatch.setattr(open_code, "get_opencode_data_home", lambda: data_dir)


# ============================================================================
# OpenCodeCli adapter helpers (last-write + can-resume-safely)
# ============================================================================

def test_opencode_cli_last_session_write_returns_max_time_updated(tmp_path):
    """``last_session_write`` returns the newest ``time_updated`` across all
    unarchived sessions for the given worktree directory. None when no
    session matches, or when the DB is unreadable."""
    worktree = str(tmp_path / "repo" / ".claude" / "worktrees" / "k-x-0001")
    other_worktree = str(tmp_path / "repo" / ".claude" / "worktrees" / "k-y-9999")
    data_dir_path = tmp_path / "open-code"
    data_dir_path.mkdir()
    db = data_dir_path / "opencode.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            directory TEXT NOT NULL,
            time_updated INTEGER NOT NULL,
            time_archived INTEGER,
            parent_id TEXT
        );
        """
    )
    con.executemany(
        "INSERT INTO session(id, directory, time_updated, time_archived) "
        "VALUES (?, ?, ?, ?)",
        [
            ("ses_old", worktree, 1000, None),
            ("ses_new", worktree, 2000, None),
            ("ses_archived", worktree, 3000, 3001),  # archived — excluded
            ("ses_other_dir", other_worktree, 2500, None),  # other worktree — excluded
        ],
    )
    con.commit()
    con.close()

    cli = OpenCodeCli()
    # Only the live rows in `worktree` count; max(time_updated) over
    # ses_old(1000), ses_new(2000) = 2000. ses_archived is excluded by
    # time_archived IS NULL; ses_other_dir is excluded by the directory
    # predicate.
    assert cli.last_session_write(worktree, data_dir=data_dir_path) == 2000


def test_opencode_cli_last_session_write_returns_none_for_missing_db(tmp_path):
    cli = OpenCodeCli()
    assert cli.last_session_write(
        str(tmp_path / "repo"),
        data_dir=tmp_path / "open-code",
    ) is None


def test_opencode_cli_can_resume_safely_blocks_unresolved_tool_call(tmp_path):
    """An assistant message whose last ``part`` is a tool-call still in
    flight (state.status == 'pending') must block the resume: replaying
    the conversation reproduces the hang (kaart 2fa8d501…)."""
    worktree = str(tmp_path / "repo" / ".claude" / "worktrees" / "k-x-0001")
    pending_part = json.dumps({
        "type": "tool",
        "tool": "bash",
        "callID": "call_abc",
        "state": {
            "status": "pending",
            "input": {"command": "long-running"},
            "output": "",
        },
    })
    _build_opencode_db(
        tmp_path, worktree,
        sessions=[("ses_pending", 2000, None)],
        parts=[("p1", "ses_pending", 2000, pending_part)],
    )
    cli = OpenCodeCli()
    assert cli.can_resume_safely(
        "ses_pending", data_dir=tmp_path / "open-code",
    ) is False


def test_opencode_cli_can_resume_safely_blocks_running_tool_call(tmp_path):
    """``running`` is the same class as ``pending`` for this gate — the
    result never arrives, the agent stays parked."""
    worktree = str(tmp_path / "repo" / ".claude" / "worktrees" / "k-x-0001")
    running_part = json.dumps({
        "type": "tool",
        "tool": "bash",
        "callID": "call_abc",
        "state": {"status": "running", "input": {}, "output": ""},
    })
    _build_opencode_db(
        tmp_path, worktree,
        sessions=[("ses_running", 2000, None)],
        parts=[("p1", "ses_running", 2000, running_part)],
    )
    cli = OpenCodeCli()
    assert cli.can_resume_safely(
        "ses_running", data_dir=tmp_path / "open-code",
    ) is False


def test_opencode_cli_can_resume_safely_allows_completed_tool_call(tmp_path):
    """A tool part whose state.status == 'completed' is safe to resume —
    the call already returned. We only block on unfinished calls."""
    worktree = str(tmp_path / "repo" / ".claude" / "worktrees" / "k-x-0001")
    finished_part = json.dumps({
        "type": "tool",
        "tool": "read",
        "callID": "call_abc",
        "state": {
            "status": "completed",
            "input": {"filePath": "/tmp/x"},
            "output": "ok",
        },
    })
    _build_opencode_db(
        tmp_path, worktree,
        sessions=[("ses_done", 2000, None)],
        parts=[("p1", "ses_done", 2000, finished_part)],
    )
    cli = OpenCodeCli()
    assert cli.can_resume_safely(
        "ses_done", data_dir=tmp_path / "open-code",
    ) is True


def test_opencode_cli_can_resume_safely_allows_step_finish(tmp_path):
    """``step-finish`` is the canonical "I'm done with this turn" part.
    Resume must be allowed."""
    worktree = str(tmp_path / "repo" / ".claude" / "worktrees" / "k-x-0001")
    finish_part = json.dumps({"type": "step-finish", "reason": "stop"})
    _build_opencode_db(
        tmp_path, worktree,
        sessions=[("ses_done", 2000, None)],
        parts=[("p1", "ses_done", 2000, finish_part)],
    )
    cli = OpenCodeCli()
    assert cli.can_resume_safely(
        "ses_done", data_dir=tmp_path / "open-code",
    ) is True


def test_opencode_cli_can_resume_safely_allows_error_tool_call(tmp_path):
    """``state.status == 'error'`` means the tool returned with an error
    result. The call IS resolved (it failed, but the agent saw the failure);
    resuming replays the same conversation and the agent handles the error
    on the next attempt. Not blocked."""
    worktree = str(tmp_path / "repo" / ".claude" / "worktrees" / "k-x-0001")
    error_part = json.dumps({
        "type": "tool",
        "tool": "bash",
        "callID": "call_abc",
        "state": {"status": "error", "input": {}, "output": "boom"},
    })
    _build_opencode_db(
        tmp_path, worktree,
        sessions=[("ses_err", 2000, None)],
        parts=[("p1", "ses_err", 2000, error_part)],
    )
    cli = OpenCodeCli()
    assert cli.can_resume_safely(
        "ses_err", data_dir=tmp_path / "open-code",
    ) is True


def test_opencode_cli_can_resume_safely_returns_true_when_db_unreadable(tmp_path):
    """Fail-open on DB error: a transient sqlite hiccup must not block a
    resume the operator explicitly asked for. Mirrors the rest of the
    detector family (``_live_sandcastle_sessions``, etc.)."""
    cli = OpenCodeCli()
    # No DB at all -> None from the resolver -> fail-open to True.
    assert cli.can_resume_safely(
        "ses_does_not_matter", data_dir=tmp_path / "open-code",
    ) is True


# ============================================================================
# check_acp_idle_liveness
# ============================================================================

def _redirect_opencode_data_dir_kwargs(monkeypatch, data_dir):
    """Variant that returns the data_dir so the test can re-use it."""
    _redirect_opencode_data_dir(monkeypatch, data_dir)
    return data_dir


@pytest.mark.asyncio
async def test_acp_idle_liveness_growing_session_no_action(tmp_path, monkeypatch):
    """An ACP-live session whose ``time_updated`` advances between ticks
    is productively working — never trigger any action."""
    session_name = "k-acp-growing-0001"
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / session_name
    worktree.mkdir(parents=True)
    worktree_str = str(worktree)

    # First tick: time_updated = t0 - 120 (baseline).
    t0 = time.time() - 120
    _build_opencode_db(
        tmp_path, worktree_str,
        sessions=[("ses_growing", int(t0 * 1000), None)],
        parts=[],
    )
    _redirect_opencode_data_dir(monkeypatch, tmp_path / "open-code")

    async with KanbanSessionLocal() as s:
        cid = await _make_card(
            s, title="acp-growing", column="engineer",
            executor_agent_id="open-code",
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    # First tick: record baseline, no action yet.
    actions_first = await dispatch.check_acp_idle_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        acp_live={session_name},
        now=t0, signal_seconds=30, action_seconds=60,
    )
    await s.commit()
    assert actions_first == set()

    # Advance the opencode session's time_updated — the agent wrote something.
    later = time.time()
    _build_opencode_db(
        tmp_path, worktree_str,
        sessions=[("ses_growing", int(later * 1000), None)],
        parts=[],
    )

    # Second tick: growth detected → reset, still no action.
    async with KanbanSessionLocal() as s:
        cards = await list_cards(s, PK)
    actions_second = await dispatch.check_acp_idle_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        acp_live={session_name},
        now=later, signal_seconds=30, action_seconds=60,
    )
    await s.commit()
    assert actions_second == set()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.claimed_by == f"agent:{session_name}"
    assert card.column == "engineer"


@pytest.mark.asyncio
async def test_acp_idle_liveness_signal_threshold_posts_comment_no_release(
    tmp_path, monkeypatch,
):
    """Past the signal threshold: post a 'stilstaand' comment but DO NOT
    release the claim — same shape as the Claude-Code detector."""
    session_name = "k-acp-signal-0001"
    now = 1_000_000.0
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / session_name
    worktree.mkdir(parents=True)
    _build_opencode_db(
        tmp_path, str(worktree),
        sessions=[("ses_signal", int((now - 60) * 1000), None)],
        parts=[],
    )
    _redirect_opencode_data_dir(monkeypatch, tmp_path / "open-code")

    async with KanbanSessionLocal() as s:
        cid = await _make_card(
            s, title="acp-signal", column="engineer",
            executor_agent_id="open-code",
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    # Baseline tick (mtime is now - 60).
    await dispatch.check_acp_idle_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        acp_live={session_name},
        now=now, signal_seconds=30, action_seconds=120,
    )
    await s.commit()

    # Second tick crosses signal (60s > 30s) but not action (< 120s).
    actions = await dispatch.check_acp_idle_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        acp_live={session_name},
        now=now + 60, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    assert actions == {session_name}

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        from app.kanban import service
        activity = await service.card_activity(s, cid)
    assert card.claimed_by == f"agent:{session_name}"
    assert card.column == "engineer"
    assert card.resume_session_id is None
    signal_comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment" and "stilstaand" in op.payload["text"].lower()
    ]
    assert signal_comments, "expected stilstaand comment"


@pytest.mark.asyncio
async def test_acp_idle_liveness_action_threshold_routes_to_impediment_no_resume(
    tmp_path, monkeypatch,
):
    """Past the action threshold: card moves to ``Impediment`` (NOT
    ``To Resume``) with a structured comment, and NO ``resume_session_id``
    is written. Resuming the same session would replay the conversation
    and hit the same pending subagent call — guaranteed hang, see the
    resume-gate tests below."""
    session_name = "k-acp-action-0001"
    now = 1_000_000.0
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / session_name
    worktree.mkdir(parents=True)
    _build_opencode_db(
        tmp_path, str(worktree),
        sessions=[("ses_action", int((now - 180) * 1000), None)],
        parts=[],
    )
    _redirect_opencode_data_dir(monkeypatch, tmp_path / "open-code")

    async with KanbanSessionLocal() as s:
        cid = await _make_card(
            s, title="acp-action", column="engineer",
            executor_agent_id="open-code",
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    # Baseline.
    await dispatch.check_acp_idle_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        acp_live={session_name},
        now=now, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    # Action threshold crossed (180s > 120s).
    actions = await dispatch.check_acp_idle_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        acp_live={session_name},
        now=now + 180, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    assert actions == {session_name}

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        from app.kanban import service
        activity = await service.card_activity(s, cid)
    assert card.column == "Impediment", card.column
    assert card.claimed_by is None
    # Crucially: NO resume pointer — resuming would reproduce the hang.
    assert card.resume_session_id is None
    impediment_comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment"
        and (
            "acp-idle" in op.payload.get("text", "").lower()
            or "resume-gate" in op.payload.get("text", "").lower()
        )
    ]
    assert impediment_comments, (
        "expected a structured comment explaining the Impediment routing"
    )


@pytest.mark.asyncio
async def test_acp_idle_liveness_skips_session_not_in_acp_live(
    tmp_path, monkeypatch,
):
    """ACP session that the ACP liveness check (pidfile + process) no
    longer reports as alive: ``reap_stale_claims`` handles it; this
    detector must NOT carve in."""
    session_name = "k-acp-dead-0001"
    now = 1_000_000.0
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / session_name
    worktree.mkdir(parents=True)
    _build_opencode_db(
        tmp_path, str(worktree),
        sessions=[("ses_dead", int((now - 600) * 1000), None)],
        parts=[],
    )
    _redirect_opencode_data_dir(monkeypatch, tmp_path / "open-code")

    async with KanbanSessionLocal() as s:
        cid = await _make_card(
            s, title="acp-dead", column="engineer",
            executor_agent_id="open-code",
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    actions = await dispatch.check_acp_idle_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        acp_live=set(),  # NOT in acp_live → reap_stale_claims owns it
        now=now, signal_seconds=30, action_seconds=60,
    )
    await s.commit()
    assert actions == set()


@pytest.mark.asyncio
async def test_acp_idle_liveness_skips_non_opencode_cli_session(
    tmp_path, monkeypatch,
):
    """Claude Code (or any non-opencode CLI) ACP session is the lane of
    ``check_progress_liveness``; this detector must NOT touch it — that
    would race the transcript-based detector and double-post comments."""
    session_name = "k-acp-claude-0001"
    now = 1_000_000.0
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / session_name
    worktree.mkdir(parents=True)
    # Empty opencode DB — no sessions for this worktree.
    _build_opencode_db(tmp_path, str(worktree), sessions=[], parts=[])
    _redirect_opencode_data_dir(monkeypatch, tmp_path / "open-code")

    async with KanbanSessionLocal() as s:
        cid = await _make_card(
            s, title="acp-claude", column="engineer",
            executor_agent_id="claude-code",  # NOT open-code
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    actions = await dispatch.check_acp_idle_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        acp_live={session_name},
        now=now, signal_seconds=30, action_seconds=60,
    )
    await s.commit()
    assert actions == set()


@pytest.mark.asyncio
async def test_acp_idle_liveness_skips_when_no_opencode_db(
    tmp_path, monkeypatch,
):
    """No opencode.db on disk: fail-open, no action. A transient DB
    absence must not be mistaken for "every opencode session is stale"."""
    session_name = "k-acp-nodb-0001"
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / session_name
    worktree.mkdir(parents=True)
    data_dir = tmp_path / "open-code"
    data_dir.mkdir(exist_ok=True)
    # Deliberately no opencode.db.
    _redirect_opencode_data_dir(monkeypatch, data_dir)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(
            s, title="acp-nodb", column="engineer",
            executor_agent_id="open-code",
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    actions = await dispatch.check_acp_idle_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        acp_live={session_name},
        now=time.time() + 999, signal_seconds=30, action_seconds=60,
    )
    await s.commit()
    assert actions == set()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.claimed_by == f"agent:{session_name}"
    assert card.column == "engineer"


@pytest.mark.asyncio
async def test_acp_idle_liveness_signal_posted_once_per_stall_window(
    tmp_path, monkeypatch,
):
    """Don't spam a comment every tick once the signal threshold is
    crossed — post exactly once per stall window, mirroring the
    transcript-based detector."""
    session_name = "k-acp-once-0001"
    now = 1_000_000.0
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / session_name
    worktree.mkdir(parents=True)
    _build_opencode_db(
        tmp_path, str(worktree),
        sessions=[("ses_once", int((now - 60) * 1000), None)],
        parts=[],
    )
    _redirect_opencode_data_dir(monkeypatch, tmp_path / "open-code")

    async with KanbanSessionLocal() as s:
        cid = await _make_card(
            s, title="acp-once", column="engineer",
            executor_agent_id="open-code",
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    # Baseline.
    await dispatch.check_acp_idle_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        acp_live={session_name},
        now=now, signal_seconds=30, action_seconds=240,
    )
    await s.commit()

    # Cross signal — first comment posted.
    actions_a = await dispatch.check_acp_idle_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        acp_live={session_name},
        now=now + 60, signal_seconds=30, action_seconds=240,
    )
    await s.commit()
    assert actions_a == {session_name}

    # Still stalled, no growth: must NOT post again.
    actions_b = await dispatch.check_acp_idle_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        acp_live={session_name},
        now=now + 90, signal_seconds=30, action_seconds=240,
    )
    await s.commit()
    assert actions_b == set()

    async with KanbanSessionLocal() as s:
        from app.kanban import service
        activity = await service.card_activity(s, cid)
    signal_comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment" and "stilstaand" in op.payload["text"].lower()
    ]
    assert len(signal_comments) == 1, signal_comments


@pytest.mark.asyncio
async def test_acp_idle_liveness_growth_after_signal_resets_counter(
    tmp_path, monkeypatch,
):
    """Growth after a signal resets the stall window so the next
    stall starts fresh — same shape as the transcript detector."""
    session_name = "k-acp-recover-0001"
    now = 1_000_000.0
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / session_name
    worktree.mkdir(parents=True)
    _build_opencode_db(
        tmp_path, str(worktree),
        sessions=[("ses_recover", int((now - 60) * 1000), None)],
        parts=[],
    )
    _redirect_opencode_data_dir(monkeypatch, tmp_path / "open-code")

    async with KanbanSessionLocal() as s:
        cid = await _make_card(
            s, title="acp-recover", column="engineer",
            executor_agent_id="open-code",
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    # Baseline.
    await dispatch.check_acp_idle_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        acp_live={session_name},
        now=now, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    # Cross signal threshold.
    await dispatch.check_acp_idle_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        acp_live={session_name},
        now=now + 60, signal_seconds=30, action_seconds=120,
    )
    await s.commit()

    # time_updated advances — agent wrote something.
    _build_opencode_db(
        tmp_path, str(worktree),
        sessions=[("ses_recover", int((now + 100) * 1000), None)],
        parts=[],
    )
    actions_growth = await dispatch.check_acp_idle_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        acp_live={session_name},
        now=now + 100, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    assert actions_growth == set()

    # Same time_updated as the growth tick, only 20s later: still under
    # signal threshold (needs 30s) — no new comment.
    actions_fresh_stall = await dispatch.check_acp_idle_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        acp_live={session_name},
        now=now + 120, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    assert actions_fresh_stall == set()

    async with KanbanSessionLocal() as s:
        from app.kanban import service
        activity = await service.card_activity(s, cid)
    signal_comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment" and "stilstaand" in op.payload["text"].lower()
    ]
    assert len(signal_comments) == 1, signal_comments

# ============================================================================
# _move_to_resume gate (resume-gate via OpenCodeCli.can_resume_safely)
# ============================================================================

@pytest.mark.asyncio
async def test_move_to_resume_opencode_unresolved_tool_call_blocks_to_impediment(
    tmp_path, monkeypatch,
):
    """When the candidate opencode session's last ``part`` is an
    unresolved tool call (``state.status NOT IN {completed, error}``),
    ``_move_to_resume`` must refuse to persist the resume pointer and
    instead route the card to ``Impediment`` with a structured comment.
    Resuming would replay the conversation and hit the same pending
    subagent call — exactly the second-round hang the card describes."""
    import unittest.mock as mock

    from app.kanban import session_recovery

    session_name = "k-resume-blocked-0001"
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / session_name
    worktree.mkdir(parents=True)
    # Last part is a pending tool call — the resume-gate must block.
    pending_part = json.dumps({
        "type": "tool",
        "tool": "bash",
        "callID": "call_xyz",
        "state": {"status": "pending", "input": {}, "output": ""},
    })
    _build_opencode_db(
        tmp_path, str(worktree),
        sessions=[("ses_resume_target", 2000, None)],
        parts=[("p_pending", "ses_resume_target", 2000, pending_part)],
    )
    _redirect_opencode_data_dir(monkeypatch, tmp_path / "open-code")

    async with KanbanSessionLocal() as s:
        cid = await _make_card(
            s, title="resume-blocked", column="engineer",
            executor_agent_id="open-code",
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target",
        return_value=("ses_resume_target", str(worktree.resolve())),
    ), mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        async with KanbanSessionLocal() as s:
            card = await get_card(s, cid)
            result = await dispatch._move_to_resume(
                s, card=card, project_key=PK, project_path=str(repo),
            )
            await s.commit()

    # Gate refused: result is False (no resume pointer written).
    assert result is False

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        from app.kanban import service
        activity = await service.card_activity(s, cid)
    assert card.column == "Impediment"
    assert card.claimed_by is None
    assert card.resume_session_id is None, (
        "resume-gate must NOT write the pointer — would reproduce hang"
    )
    gate_comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment"
        and "resume-gate" in op.payload.get("text", "").lower()
    ]
    assert gate_comments, (
        "expected a structured resume-gate comment on the card"
    )


@pytest.mark.asyncio
async def test_move_to_resume_opencode_completed_part_resumes_normally(
    tmp_path, monkeypatch,
):
    """A opencode session whose last ``part`` is ``step-finish`` or a
    completed tool call resumes normally — the gate doesn't carve out
    *every* opencode resume, only the ones with an unresolved tool call."""
    import unittest.mock as mock

    from app.kanban import session_recovery

    session_name = "k-resume-ok-0001"
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / session_name
    worktree.mkdir(parents=True)
    finish_part = json.dumps({"type": "step-finish", "reason": "stop"})
    _build_opencode_db(
        tmp_path, str(worktree),
        sessions=[("ses_resume_ok", 2000, None)],
        parts=[("p_done", "ses_resume_ok", 2000, finish_part)],
    )
    _redirect_opencode_data_dir(monkeypatch, tmp_path / "open-code")

    async with KanbanSessionLocal() as s:
        cid = await _make_card(
            s, title="resume-ok", column="engineer",
            executor_agent_id="open-code",
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target",
        return_value=("ses_resume_ok", str(worktree.resolve())),
    ), mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        async with KanbanSessionLocal() as s:
            card = await get_card(s, cid)
            result = await dispatch._move_to_resume(
                s, card=card, project_key=PK, project_path=str(repo),
            )
            await s.commit()

    assert result is True

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.column == "To Resume"
    assert card.resume_session_id == "ses_resume_ok"


@pytest.mark.asyncio
async def test_move_to_resume_non_opencode_cli_skips_gate(
    tmp_path, monkeypatch,
):
    """The resume-gate only fires for the opencode CLI; Claude Code and
    other CLIs use the existing ``_resolve_resume_target`` path unchanged.
    Verifies the gate isn't accidentally applied to the broader resume
    surface (which would change behavior for every Claude Code resume)."""
    import unittest.mock as mock

    from app.kanban import session_recovery

    session_name = "k-resume-claude-0001"
    repo = tmp_path / "repo"

    async with KanbanSessionLocal() as s:
        cid = await _make_card(
            s, title="resume-claude", column="engineer",
            executor_agent_id="claude-code",
        )
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()

    with mock.patch.object(
        session_recovery, "_resolve_resume_target",
        return_value=("claude-sess-abc", "proj-folder"),
    ), mock.patch.object(dispatch, "_kill_agent_session", return_value=None):
        async with KanbanSessionLocal() as s:
            card = await get_card(s, cid)
            result = await dispatch._move_to_resume(
                s, card=card, project_key=PK, project_path=str(repo),
            )
            await s.commit()

    assert result is True
    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.column == "To Resume"
    assert card.resume_session_id == "claude-sess-abc"
