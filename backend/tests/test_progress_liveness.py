"""Progress-liveness detector (kanban card f0953a11...).

The reaper today treats every session whose tmux pane still exists as alive.
A session that hit its subscription limit, crashed in a loop, is sitting on a
permission prompt, or is hung on a network timeout keeps its pane — and
therefore its claim — forever. ``check_progress_liveness`` adds a *second*
liveness signal: transcript mtime. When the transcript of an ``agent:``-claimed
session stops growing for the configured signal window, the card gets a
"stilstaand" activity comment so an operator can see the stall from the board.
When it stops growing for the action window, the existing ``_move_to_resume``
path is used to release the claim.

Failure modes the tests bound:

  - growing transcript must not trigger any action (the canonical "agent is
    still working" case).
  - stalled past signal threshold: comment posted, claim NOT released.
  - stalled past action threshold: claim released, card moved to To Resume.
  - missing transcript (worktree but no jsonl): fail-open, no action.
  - session in ``live_sessions``/``sandcastle_live``/``headless_live``: skipped
    — those transports own their own liveness sources.
  - transcript mtime advancing again resets both counters so the next stall
    starts fresh.

See ``docs/cockpit/sessie-limiet-auto-dispatch-analyse.md`` §5 R3 for the
underlying rationale.
"""
import os
import time

import pytest
import pytest_asyncio

from app.kanban import dispatch, service
from app.kanban.operations import apply_operation
from app.kanban.service import get_card, list_cards
from app.utils.path_utils import convert_path_to_folder_name
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()
PK = "git:example.com/me/repo"


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    # Module-level state is process-local; reset between tests so a snapshot
    # from a prior test doesn't leak into the next.
    dispatch._progress_liveness_state.clear()
    yield


async def _make_card(s, title="Task", column="engineer"):
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None, payload={"title": title, "column": column},
    )
    await s.flush()
    return cid


def _redirect_projects_dir(monkeypatch, projects_dir):
    """Point transcript resolution at a fake projects dir.

    Patch the *consumer* (`claude_code.py`), not the source module, per
    `docs/cockpit/test-doubles-convention.md` rule 1 — the adapter binds
    `get_claude_projects_dir` into its own namespace at import time.
    `session_recovery` used to own this call, but 2b195e59 moved resolution
    into the CLI adapter and dropped the import, so patching it there raises
    AttributeError. Redirecting only the base dir keeps the resolution chain
    real (worktree path -> convert_path_to_folder_name -> newest *.jsonl).
    """
    from app.services.agentic_cli import claude_code as claude_code_cli

    monkeypatch.setattr(
        claude_code_cli, "get_claude_projects_dir", lambda: projects_dir
    )


def _build_worktree_transcript(tmp_path, session_name, *, initial_mtime):
    """Lay out ``<repo>/.claude/worktrees/<session_name>`` plus its transcript
    under a fake projects dir, mirroring the real Claude Code layout."""
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / session_name
    worktree.mkdir(parents=True)
    folder = convert_path_to_folder_name(str(worktree))
    projects_dir = tmp_path / "projects"
    folder_dir = projects_dir / folder
    folder_dir.mkdir(parents=True)
    transcript = folder_dir / "sess1.jsonl"
    transcript.write_text(
        '{"type":"user","message":{"role":"user","content":[{"type":"text","text":"x"}]}}\n'
    )
    os.utime(transcript, (initial_mtime, initial_mtime))
    return repo, projects_dir, transcript


@pytest.mark.asyncio
async def test_progress_liveness_growing_transcript_no_action(tmp_path, monkeypatch):
    """A session whose transcript mtime advances between ticks is
    productively working — never trigger any action."""
    session_name = "k-growing-0001"
    t0 = time.time() - 120
    repo, projects_dir, transcript = _build_worktree_transcript(
        tmp_path, session_name, initial_mtime=t0,
    )
    _redirect_projects_dir(monkeypatch, projects_dir)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="growing", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    # First tick records the initial mtime — no action yet (just learning
    # the baseline).
    actions_first = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=t0, signal_seconds=30, action_seconds=60,
    )
    await s.commit()
    assert actions_first == set()

    # Advance the transcript's mtime — the session wrote something new.
    later = time.time()
    os.utime(transcript, (later, later))

    # Second tick: growth detected → reset, still no action.
    actions_second = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=later, signal_seconds=30, action_seconds=60,
    )
    await s.commit()
    assert actions_second == set()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.claimed_by == f"agent:{session_name}"
    assert card.column == "engineer"


@pytest.mark.asyncio
async def test_progress_liveness_signal_threshold_posts_comment_no_release(tmp_path, monkeypatch):
    """At the signal threshold (default 30min, test override 30s): post a
    'stilstaand' comment but DO NOT release the claim. The card stays on its
    agent column so an operator can intervene or the next stall can escalate."""
    session_name = "k-signal-0001"
    now = 1_000_000.0
    repo, projects_dir, _ = _build_worktree_transcript(
        tmp_path, session_name, initial_mtime=now - 60,
    )
    _redirect_projects_dir(monkeypatch, projects_dir)
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck-signal", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    # First tick: record the baseline (mtime was 'now - 60', no time has
    # passed yet relative to our tracking window).
    actions_first = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=now, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    assert actions_first == set()

    # Second tick: same mtime, now - last_seen > signal_seconds (30s) but
    # < action_seconds (120s) → signal threshold crossed.
    actions_second = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=now + 60, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    assert actions_second == {session_name}

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        activity = await service.card_activity(s, cid)
    assert card.claimed_by == f"agent:{session_name}"
    assert card.column == "engineer"
    signal_comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment" and "stilstaand" in op.payload["text"].lower()
    ]
    assert signal_comments, "no stilstaand comment posted"


@pytest.mark.asyncio
async def test_progress_liveness_signal_threshold_only_posted_once_per_stall(tmp_path, monkeypatch):
    """Don't spam a comment every tick once the signal threshold is crossed —
    post exactly once per stall window."""
    session_name = "k-once-0001"
    now = 1_000_000.0
    repo, projects_dir, _ = _build_worktree_transcript(
        tmp_path, session_name, initial_mtime=now - 60,
    )
    _redirect_projects_dir(monkeypatch, projects_dir)
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="once", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    # Baseline tick.
    await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=now, signal_seconds=30, action_seconds=120,
    )
    await s.commit()

    # Cross signal threshold — first comment.
    actions_a = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=now + 60, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    assert actions_a == {session_name}

    # Next tick still stalled, no growth: must NOT post again.
    actions_b = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=now + 90, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    assert actions_b == set()

    async with KanbanSessionLocal() as s:
        activity = await service.card_activity(s, cid)
    signal_comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment" and "stilstaand" in op.payload["text"].lower()
    ]
    assert len(signal_comments) == 1, signal_comments


@pytest.mark.asyncio
async def test_progress_liveness_action_threshold_releases_via_to_resume(tmp_path, monkeypatch):
    """At the action threshold: card moves to To Resume via the existing
    ``_move_to_resume`` path — claim released, tmux killed, resume pointer
    set on the card so a follow-up tick resumes the conversation."""
    session_name = "k-action-0001"
    now = 1_000_000.0
    repo, projects_dir, transcript = _build_worktree_transcript(
        tmp_path, session_name, initial_mtime=now - 180,  # stalled 3 min
    )
    _redirect_projects_dir(monkeypatch, projects_dir)
    monkeypatch.setattr(dispatch, "_kill_agent_session", lambda name: None)
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="stuck-action", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    # First tick records baseline.
    await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=now, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    # Second tick crosses action threshold (180s > 120s).
    actions = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=now + 180, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    assert actions == {session_name}

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    # Moved to To Resume, claim released.
    assert card.column == "To Resume"
    assert card.claimed_by is None
    # Resume pointer written so the next dispatch tick resumes in place.
    assert card.resume_session_id is not None
    assert card.resume_project_folder is not None


@pytest.mark.asyncio
async def test_progress_liveness_missing_transcript_no_action(tmp_path, monkeypatch):
    """A session whose worktree exists but has no transcript file is left
    alone — fail-open, never release on missing data."""
    session_name = "k-notranscript-0001"
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / session_name
    worktree.mkdir(parents=True)
    projects_dir = tmp_path / "projects"
    # Deliberately no transcript jsonl under projects_dir/<folder>/.

    _redirect_projects_dir(monkeypatch, projects_dir)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="notranscript", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    actions = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=time.time() + 999, signal_seconds=30, action_seconds=60,
    )
    await s.commit()
    assert actions == set()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
    assert card.claimed_by == f"agent:{session_name}"


@pytest.mark.asyncio
async def test_progress_liveness_missing_worktree_no_action(tmp_path, monkeypatch):
    """A session whose worktree doesn't exist has no transcript to read —
    also fail-open."""
    session_name = "k-noworktree-0001"
    repo = tmp_path / "repo"  # no worktree created
    projects_dir = tmp_path / "projects"

    _redirect_projects_dir(monkeypatch, projects_dir)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="noworktree", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    actions = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=time.time() + 999, signal_seconds=30, action_seconds=60,
    )
    await s.commit()
    assert actions == set()


@pytest.mark.asyncio
async def test_progress_liveness_skips_live_tmux_session(tmp_path, monkeypatch):
    """A session still in tmux is alive — progress-liveness must NOT trigger
    even if its transcript is quiet (agent may be reading, waiting for input,
    or thinking without writing)."""
    session_name = "k-live-0001"
    now = 1_000_000.0
    repo, projects_dir, _ = _build_worktree_transcript(
        tmp_path, session_name, initial_mtime=now - 600,
    )
    _redirect_projects_dir(monkeypatch, projects_dir)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="live", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    actions = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions={session_name}, sandcastle_live=set(), headless_live=set(),
        now=now, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    assert actions == set()


@pytest.mark.asyncio
async def test_progress_liveness_skips_sandcastle_live_session(tmp_path, monkeypatch):
    """Sandcastle has its own liveness source — never override it."""
    session_name = "k-sand-0001"
    now = 1_000_000.0
    repo, projects_dir, _ = _build_worktree_transcript(
        tmp_path, session_name, initial_mtime=now - 600,
    )
    _redirect_projects_dir(monkeypatch, projects_dir)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="sand", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    actions = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live={session_name}, headless_live=set(),
        now=now, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    assert actions == set()


@pytest.mark.asyncio
async def test_progress_liveness_skips_headless_live_session(tmp_path, monkeypatch):
    """Headless stream-json transport has its own liveness source — never
    override it."""
    session_name = "k-headless-0001"
    now = 1_000_000.0
    repo, projects_dir, _ = _build_worktree_transcript(
        tmp_path, session_name, initial_mtime=now - 600,
    )
    _redirect_projects_dir(monkeypatch, projects_dir)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="headless", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    actions = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live={session_name},
        now=now, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    assert actions == set()


@pytest.mark.asyncio
async def test_progress_liveness_transcript_growth_after_signal_resets_counter(tmp_path, monkeypatch):
    """Once a stall-window's signal comment is posted, growth must reset both
    counters so the next stall starts fresh (no re-posting the same comment,
    and a fresh signal threshold before any new action)."""
    session_name = "k-recover-0001"
    now = 1_000_000.0
    repo, projects_dir, transcript = _build_worktree_transcript(
        tmp_path, session_name, initial_mtime=now - 60,
    )
    _redirect_projects_dir(monkeypatch, projects_dir)
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="recover", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    # Baseline.
    await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=now, signal_seconds=30, action_seconds=120,
    )
    await s.commit()

    # Cross signal threshold — first comment posted.
    await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=now + 60, signal_seconds=30, action_seconds=120,
    )
    await s.commit()

    # Transcript grows.
    new_mtime = now + 100
    os.utime(transcript, (new_mtime, new_mtime))

    # Next tick: growth detected → counters reset.
    actions_growth = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=now + 100, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    assert actions_growth == set()

    # Another tick at the same mtime, only 20s later: still under signal
    # threshold (needs 30s) — no new comment, no action.
    actions_fresh_stall = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=now + 120, signal_seconds=30, action_seconds=120,
    )
    await s.commit()
    assert actions_fresh_stall == set()

    # Only ONE signal comment should exist on the activity feed (the first
    # stall's), not two.
    async with KanbanSessionLocal() as s:
        activity = await service.card_activity(s, cid)
    signal_comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment" and "stilstaand" in op.payload["text"].lower()
    ]
    assert len(signal_comments) == 1, signal_comments


@pytest.mark.asyncio
async def test_progress_liveness_skips_fixed_columns(tmp_path, monkeypatch):
    """Cards on fixed columns (Backlog / Impediment / Done / To Resume) are
    never the target of progress-liveness."""
    session_name = "k-fixed-0001"
    repo = tmp_path / "repo"

    _redirect_projects_dir(monkeypatch, tmp_path / "projects")

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="parked", column="Backlog")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    actions = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=time.time() + 999, signal_seconds=30, action_seconds=60,
    )
    await s.commit()
    assert actions == set()


@pytest.mark.asyncio
async def test_progress_liveness_skips_unclaimed_cards(tmp_path, monkeypatch):
    """A card with no ``agent:`` claim (e.g. human ``me@ui`` ownership) is
    never reaped by the dead-session logic, and neither by progress-liveness."""
    repo = tmp_path / "repo"

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="human wip", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "me@ui"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    actions = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=str(repo),
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=time.time() + 999, signal_seconds=30, action_seconds=60,
    )
    await s.commit()
    assert actions == set()


@pytest.mark.asyncio
async def test_progress_liveness_no_project_path_no_action(tmp_path):
    """A defensive default: when ``project_path`` is None, no card can have
    a transcript resolved, so progress-liveness is a no-op for the whole
    tick. Matches ``reap_stale_claims``'s ``project_path=None`` contract."""
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="no path", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": "agent:k-nopath-0001"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    actions = await dispatch.check_progress_liveness(
        s, project_key=PK, cards=cards, project_path=None,
        live_sessions=set(), sandcastle_live=set(), headless_live=set(),
        now=time.time() + 999, signal_seconds=30, action_seconds=60,
    )
    await s.commit()
    assert actions == set()


@pytest.mark.asyncio
async def test_progress_liveness_logs_warning_on_action(tmp_path, monkeypatch, caplog):
    """The action threshold must log at WARNING with session/card/stall — the
    same observability hook the existing stuck-session detector uses, so an
    operator can grep for it in backend logs."""
    import logging

    session_name = "k-action-log-0001"
    now = 1_000_000.0
    repo, projects_dir, transcript = _build_worktree_transcript(
        tmp_path, session_name, initial_mtime=now - 180,
    )
    _redirect_projects_dir(monkeypatch, projects_dir)
    monkeypatch.setattr(dispatch, "_kill_agent_session", lambda name: None)
    monkeypatch.setattr(dispatch, "safe_resolve_project_key", lambda path: PK)

    import app.kanban.db as kdb
    monkeypatch.setattr(kdb, "KanbanSessionLocal", KanbanSessionLocal)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="action-log", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=cid, payload={"claimed_by": f"agent:{session_name}"},
        )
        await s.commit()
        cards = await list_cards(s, PK)

    with caplog.at_level(logging.WARNING, logger="app.kanban.dispatch"):
        # First tick records baseline.
        await dispatch.check_progress_liveness(
            s, project_key=PK, cards=cards, project_path=str(repo),
            live_sessions=set(), sandcastle_live=set(), headless_live=set(),
            now=now, signal_seconds=30, action_seconds=120,
        )
        await s.commit()
        # Second tick crosses action threshold.
        await dispatch.check_progress_liveness(
            s, project_key=PK, cards=cards, project_path=str(repo),
            live_sessions=set(), sandcastle_live=set(), headless_live=set(),
            now=now + 180, signal_seconds=30, action_seconds=120,
        )
        await s.commit()

    messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        session_name in m and "progress-liveness" in m and "to resume" in m.lower()
        for m in messages
    ), messages