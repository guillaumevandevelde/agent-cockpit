# backend/tests/test_kanban_session_recovery.py
"""Resume interrupted agent sessions after a host/backend restart."""
import os

import pytest
import pytest_asyncio

from app.kanban import session_recovery as recovery
from app.kanban.operations import apply_operation
from app.kanban.service import get_card
from app.utils.path_utils import convert_path_to_folder_name
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()

PK = "git:example.com/me/repo"


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _make_card(
    s, title="Task", column="Backlog", executor_agent_id=None,
):
    payload = {"title": title, "column": column}
    if executor_agent_id is not None:
        payload["executor_agent_id"] = executor_agent_id
    cid = await apply_operation(
        s, op_type="create", entity_type="card", project_key=PK,
        entity_id=None, payload=payload,
    )
    await s.flush()
    return cid


async def _claim(s, cid, claimant):
    await apply_operation(
        s, op_type="claim", entity_type="card", project_key=PK,
        entity_id=cid, payload={"claimed_by": claimant},
    )


# ---- _recoverable predicate ----------------------------------------------

class _Card:
    def __init__(self, column, claimed_by, transport=None):
        self.column = column
        self.claimed_by = claimed_by
        self.transport = transport


@pytest.mark.parametrize("card, expected", [
    (_Card("engineer", "agent:k-dead-1"), True),
    (_Card("developer", "agent:k-dead-2"), True),
    (_Card("engineer", "agent:k-alive-1"), False),          # live session
    (_Card("Backlog", "agent:k-dead-3"), False),            # fixed column
    (_Card("Done", "agent:k-dead-4"), False),               # fixed column
    (_Card("Impediment", "agent:k-dead-5"), False),         # fixed column
    (_Card("engineer", "me@ui"), False),                    # human claim
    (_Card("engineer", None), False),                       # unclaimed
    (_Card("engineer", "agent:k-dead-6", "sandcastle"), False),  # no local worktree
])
def test_recoverable_predicate(card, expected):
    assert recovery._recoverable(card, {"k-alive-1"}) is expected


# ---- _resolve_transcript_file ---------------------------------------------

def test_resolve_transcript_file_picks_most_recent_transcript(tmp_path):
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / "k-x-0001"
    worktree.mkdir(parents=True)
    folder = convert_path_to_folder_name(str(worktree))
    folder_dir = tmp_path / "projects" / folder
    folder_dir.mkdir(parents=True)
    old = folder_dir / "1111.jsonl"
    new = folder_dir / "2222.jsonl"
    old.write_text("{}")
    new.write_text("{}")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))

    result = recovery._resolve_transcript_file(
        str(repo), "k-x-0001", projects_dir=tmp_path / "projects")
    assert result == new


def test_resolve_transcript_file_none_without_worktree(tmp_path):
    result = recovery._resolve_transcript_file(
        str(tmp_path / "repo"), "k-missing", projects_dir=tmp_path / "projects")
    assert result is None


def test_resolve_transcript_file_none_without_transcript(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".claude" / "worktrees" / "k-y-0002").mkdir(parents=True)
    result = recovery._resolve_transcript_file(
        str(repo), "k-y-0002", projects_dir=tmp_path / "projects")
    assert result is None


def test_resolve_transcript_file_routes_to_requested_cli(monkeypatch, tmp_path):
    """Per-CLI routing (kaart 55fa66d1…): the resolver hands the worktree
    to the selected CLI's ``resolve_transcript_file`` rather than
    hard-coding Claude. Mirrors ``_resolve_resume_target``'s pattern."""
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / "k-codex"
    worktree.mkdir(parents=True)
    expected = tmp_path / "codex-transcript.jsonl"
    expected.write_text("{}")
    calls = []

    class FakeCli:
        supports_transcript_resolution = True

        def resolve_transcript_file(self, worktree_path, *, data_dir=None):
            calls.append((worktree_path, data_dir))
            return expected

    monkeypatch.setattr(
        recovery,
        "get_agentic_cli",
        lambda cli_id: FakeCli() if cli_id == "codex-cli" else None,
    )

    result = recovery._resolve_transcript_file(
        str(repo), "k-codex", cli_id="codex-cli",
    )

    assert result == expected
    assert calls == [(worktree, None)]


def test_resolve_transcript_file_unknown_cli_returns_none(caplog, tmp_path):
    """Unknown CLI is logged at warning and resolves to None — same shape
    as the unknown-CLI branch in ``_resolve_resume_target``. The reaper
    reads None as "no signal" and suppresses the pane scan rather than
    fire it (kaart 55fa66d1…)."""
    from unittest.mock import patch as _patch

    repo = tmp_path / "repo"
    (repo / ".claude" / "worktrees" / "k-mystery").mkdir(parents=True)

    with _patch.object(
        recovery, "get_agentic_cli",
        side_effect=ValueError("unknown cli"),
    ):
        with caplog.at_level("WARNING"):
            result = recovery._resolve_transcript_file(
                str(repo), "k-mystery", cli_id="mystery-cli",
            )

    assert result is None
    assert "transcript resolution requested for unknown cli=mystery-cli" in caplog.text


def test_resolve_transcript_file_logs_unsupported_cli(caplog, tmp_path):
    """A CLI that doesn't expose a transcript store (Copilot today) is
    logged at info and returns None. The reaper reads None as "no
    signal" and suppresses the pane scan — the bug class kaart
    55fa66d1… was about (a Codex/OpenCode/MiMo/Copilot session whose
    pane happened to mention '429' getting reaped mid-productivity)."""
    repo = tmp_path / "repo"
    (repo / ".claude" / "worktrees" / "k-copilot").mkdir(parents=True)

    with caplog.at_level("INFO"):
        result = recovery._resolve_transcript_file(
            str(repo), "k-copilot", cli_id="copilot-cli",
        )

    assert result is None
    assert "transcript resolution unsupported for cli=copilot-cli" in caplog.text


# ---- _resolve_resume_target ----------------------------------------------

def test_resolve_resume_target_picks_most_recent_transcript(tmp_path):
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / "k-x-0001"
    worktree.mkdir(parents=True)
    folder = convert_path_to_folder_name(str(worktree))
    folder_dir = tmp_path / "projects" / folder
    folder_dir.mkdir(parents=True)
    old = folder_dir / "1111.jsonl"
    new = folder_dir / "2222.jsonl"
    old.write_text("{}")
    new.write_text("{}")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))

    result = recovery._resolve_resume_target(
        str(repo), "k-x-0001", projects_dir=tmp_path / "projects")
    assert result == ("2222", folder)


def test_resolve_resume_target_none_without_worktree(tmp_path):
    result = recovery._resolve_resume_target(
        str(tmp_path / "repo"), "k-missing", projects_dir=tmp_path / "projects")
    assert result is None


def test_resolve_resume_target_none_without_transcript(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".claude" / "worktrees" / "k-y-0002").mkdir(parents=True)
    result = recovery._resolve_resume_target(
        str(repo), "k-y-0002", projects_dir=tmp_path / "projects")
    assert result is None


def test_resolve_resume_target_routes_to_requested_cli(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    worktree = repo / ".claude" / "worktrees" / "k-codex"
    worktree.mkdir(parents=True)
    calls = []

    class FakeCli:
        supports_resume_resolution = True

        def resolve_resume_target(self, worktree_path, *, data_dir=None):
            calls.append((worktree_path, data_dir))
            return "codex-session", str(worktree_path)

    monkeypatch.setattr(
        recovery,
        "get_agentic_cli",
        lambda cli_id: FakeCli() if cli_id == "codex-cli" else None,
    )

    result = recovery._resolve_resume_target(
        str(repo), "k-codex", cli_id="codex-cli",
    )

    assert result == ("codex-session", str(worktree))
    assert calls == [(worktree, None)]


def test_resolve_resume_target_logs_unsupported_cli(caplog, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".claude" / "worktrees" / "k-copilot").mkdir(parents=True)

    with caplog.at_level("INFO"):
        result = recovery._resolve_resume_target(
            str(repo), "k-copilot", cli_id="copilot-cli",
        )

    assert result is None
    assert "resume detection unsupported for cli=copilot-cli" in caplog.text


# ---- recover_project ------------------------------------------------------

@pytest.mark.asyncio
async def test_recover_project_resumes_dead_session():
    calls = []

    async def fake_redispatch(session, *, card_id, project_path, caller_source=None):
        card = await get_card(session, card_id)
        calls.append({
            "card_id": card_id,
            "project_path": project_path,
            "resume_session_id": card.resume_session_id,
            "resume_project_folder": card.resume_project_folder,
        })
        return {"card_id": card_id, "session_name": "k-new-9999"}

    def fake_resolve(project_path, session_name, *, cli_id):
        assert cli_id == "claude-code"
        return ("sess-abc", f"-p--claude-worktrees-{session_name}")

    async with KanbanSessionLocal() as s:
        dead = await _make_card(s, title="wip", column="engineer")
        await _claim(s, dead, "agent:k-dead-0001")
        await s.commit()
        recovered = await recovery.recover_project(
            s, project_key=PK, project_path="/p", live_sessions=set(),
            resolve=fake_resolve, redispatch=fake_redispatch,
        )
        await s.commit()

    assert len(recovered) == 1
    assert len(calls) == 1
    assert calls[0]["card_id"] == dead
    assert calls[0]["project_path"] == "/p"
    # Resume fields are persisted BEFORE redispatch reads the card, so the
    # resume transport is selected.
    assert calls[0]["resume_session_id"] == "sess-abc"
    assert calls[0]["resume_project_folder"] == "-p--claude-worktrees-k-dead-0001"


@pytest.mark.asyncio
async def test_recover_project_routes_non_claude_card_to_its_cli():
    calls = []

    async def fake_redispatch(session, *, card_id, project_path, caller_source=None):
        card = await get_card(session, card_id)
        calls.append((card.resume_session_id, card.resume_project_folder))
        return {"card_id": card_id, "session_name": "k-new-open"}

    def fake_resolve(project_path, session_name, *, cli_id):
        assert project_path == "/p"
        assert session_name == "k-dead-open"
        assert cli_id == "open-code"
        return "ses_open", "/p/.claude/worktrees/k-dead-open"

    async with KanbanSessionLocal() as s:
        dead = await _make_card(
            s,
            title="open-code wip",
            column="engineer",
            executor_agent_id="open-code",
        )
        await _claim(s, dead, "agent:k-dead-open")
        await s.commit()
        recovered = await recovery.recover_project(
            s,
            project_key=PK,
            project_path="/p",
            live_sessions=set(),
            resolve=fake_resolve,
            redispatch=fake_redispatch,
        )
        await s.commit()

    assert len(recovered) == 1
    assert calls == [("ses_open", "/p/.claude/worktrees/k-dead-open")]


@pytest.mark.asyncio
async def test_recover_project_skips_when_no_resumable_transcript():
    called = []

    async def fake_redispatch(session, *, card_id, project_path, caller_source=None):
        called.append(card_id)
        return {"card_id": card_id}

    async with KanbanSessionLocal() as s:
        dead = await _make_card(s, title="wip", column="engineer")
        await _claim(s, dead, "agent:k-dead-0002")
        await s.commit()
        recovered = await recovery.recover_project(
            s, project_key=PK, project_path="/p", live_sessions=set(),
            resolve=lambda p, n, **kwargs: None, redispatch=fake_redispatch,
        )
        await s.commit()
        card = await get_card(s, dead)

    assert recovered == []
    assert called == []
    # The claim is left untouched for the reaper's existing behaviour.
    assert card.claimed_by == "agent:k-dead-0002"
    assert card.resume_session_id is None


@pytest.mark.asyncio
async def test_recover_project_ignores_live_human_and_fixed_columns():
    called = []

    async def fake_redispatch(session, *, card_id, project_path, caller_source=None):
        called.append(card_id)
        return {"card_id": card_id}

    async with KanbanSessionLocal() as s:
        alive = await _make_card(s, title="alive", column="engineer")
        await _claim(s, alive, "agent:k-alive-0001")
        human = await _make_card(s, title="human", column="developer")
        await _claim(s, human, "me@ui")
        done = await _make_card(s, title="done", column="Done")
        await _claim(s, done, "agent:k-dead-done")
        await s.commit()
        recovered = await recovery.recover_project(
            s, project_key=PK, project_path="/p",
            live_sessions={"k-alive-0001"},
            resolve=lambda p, n, **kwargs: ("s", "f"), redispatch=fake_redispatch,
        )
        await s.commit()

    assert recovered == []
    assert called == []


@pytest.mark.asyncio
async def test_recover_project_respects_session_budget(monkeypatch):
    """Startup recovery must never resume more sessions than the shared
    hardware-aware session budget allows -- otherwise a project with more dead
    claims than that budget (e.g. via repeated dev-server restarts) would burst
    them all back to life at once. After dropping the per-project cap, the
    budget comes from session_registry.effective_max_sessions."""
    calls = []

    async def fake_redispatch(session, *, card_id, project_path, caller_source=None):
        calls.append(card_id)
        return {"card_id": card_id, "session_name": f"k-new-{card_id}"}

    class FakeRegistry:
        effective_max_sessions = 2

        @property
        def session_count(self):
            return 0

    fake_registry = FakeRegistry()
    from app.services.scheduling import session_registry as registry_mod
    monkeypatch.setattr(registry_mod, "session_registry", fake_registry)

    async with KanbanSessionLocal() as s:
        dead_cards = [
            await _make_card(s, title=f"wip-{i}", column="engineer")
            for i in range(3)
        ]
        for i, cid in enumerate(dead_cards):
            await _claim(s, cid, f"agent:k-dead-{i}")
        await s.commit()

        recovered = await recovery.recover_project(
            s, project_key=PK, project_path="/p", live_sessions=set(),
            resolve=lambda p, n, **kwargs: ("s", "f"), redispatch=fake_redispatch,
        )
        await s.commit()

    # Budget is 2 -> only 2 of the 3 dead sessions may be resumed this pass.
    assert len(recovered) == 2
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_recover_project_counts_live_sessions_against_session_budget(monkeypatch):
    """A card whose session is already live still occupies a session-budget
    slot, so recovery must leave that slot out of its budget for resuming dead
    ones."""
    calls = []

    async def fake_redispatch(session, *, card_id, project_path, caller_source=None):
        calls.append(card_id)
        return {"card_id": card_id, "session_name": f"k-new-{card_id}"}

    class FakeRegistry:
        effective_max_sessions = 2

        @property
        def session_count(self):
            return 0

    fake_registry = FakeRegistry()
    from app.services.scheduling import session_registry as registry_mod
    monkeypatch.setattr(registry_mod, "session_registry", fake_registry)

    async with KanbanSessionLocal() as s:
        alive = await _make_card(s, title="alive", column="engineer")
        await _claim(s, alive, "agent:k-alive-0001")
        dead_cards = [
            await _make_card(s, title=f"wip-{i}", column="engineer")
            for i in range(2)
        ]
        for i, cid in enumerate(dead_cards):
            await _claim(s, cid, f"agent:k-dead-{i}")
        await s.commit()

        recovered = await recovery.recover_project(
            s, project_key=PK, project_path="/p", live_sessions={"k-alive-0001"},
            resolve=lambda p, n, **kwargs: ("s", "f"), redispatch=fake_redispatch,
        )
        await s.commit()

    # Budget is 2, one slot already taken by the live session -> only 1 free.
    assert len(recovered) == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_recover_project_survives_redispatch_failure():
    async def boom_redispatch(session, *, card_id, project_path):
        raise RuntimeError("spawn exploded")

    async with KanbanSessionLocal() as s:
        a = await _make_card(s, title="a", column="engineer")
        await _claim(s, a, "agent:k-dead-a")
        b = await _make_card(s, title="b", column="engineer")
        await _claim(s, b, "agent:k-dead-b")
        await s.commit()
        # Must not raise even though every redispatch fails.
        recovered = await recovery.recover_project(
            s, project_key=PK, project_path="/p", live_sessions=set(),
            resolve=lambda p, n, **kwargs: ("s", "f"), redispatch=boom_redispatch,
        )
        await s.commit()

    assert recovered == []


@pytest.mark.asyncio
async def test_recover_project_commits_each_card_before_the_next_spawn():
    """Each resume must be durable before the next spawn starts.

    Spawning a session is a tmux side effect that no DB rollback can undo. So
    when the loop committed only after *every* card, a mid-loop death left the
    spawned sessions running while their claims vanished -- and the next boot
    re-read the identical stale claim and spawned yet another session for the
    same card.

    That is not hypothetical: on 2026-08-07 the health watchdog SIGKILLed the
    backend at 51s while recovery (~37s per resume) was on its second card, 14
    times in a row, leaking 9 live OpenCode sessions into one worktree. All 14
    boots logged the same stale session name -- proof no claim ever committed.

    Non-tautological: the assertion reads through a SECOND session, which sees
    committed rows only. That is exactly what the next boot's fresh process
    sees.
    """
    order: list[str] = []
    visible_to_a_fresh_process: dict[str, str | None] = {}

    async def fake_redispatch(session, *, card_id, project_path, caller_source=None):
        order.append(card_id)
        if len(order) == 2:
            async with KanbanSessionLocal() as other:
                prior = await get_card(other, order[0])
                visible_to_a_fresh_process["resume_session_id"] = (
                    prior.resume_session_id
                )
        return {"card_id": card_id, "session_name": f"k-new-{len(order)}"}

    async with KanbanSessionLocal() as s:
        a = await _make_card(s, title="wip a", column="engineer")
        await _claim(s, a, "agent:k-dead-000a")
        b = await _make_card(s, title="wip b", column="engineer")
        await _claim(s, b, "agent:k-dead-000b")
        await s.commit()
        recovered = await recovery.recover_project(
            s, project_key=PK, project_path="/p", live_sessions=set(),
            resolve=lambda p, n, **kwargs: (f"sess-{n}", None),
            redispatch=fake_redispatch,
        )
        await s.commit()

    assert len(recovered) == 2
    assert len(order) == 2, "both cards must be recovered for this test to mean anything"
    assert visible_to_a_fresh_process["resume_session_id"] == f"sess-{'k-dead-000a' if order[0] == a else 'k-dead-000b'}", (
        "the first card's resume must already be committed when the second "
        "card's session is spawned -- otherwise a kill in between leaks the "
        "first session and re-spawns it on the next boot"
    )


@pytest.mark.asyncio
async def test_recover_project_retains_claim_for_live_session_despite_mcp_disconnect():
    """AC4 regression for [self-improve] 4ed4edb9 (MCP-disconnect → claim-release).

    The b00f3705… incident's real trigger was startup session-recovery, not the
    MCP disconnect (see docs/cockpit/mcp-disconnect-claim-release-analyse.md).
    This locks the invariant that closes AC2/AC4 for the recovery path: a session
    still LIVE in the tmux snapshot must never be resumed, regardless of its
    MCP-server connection state — a transient MCP disconnect does not remove the
    session from `_live_sessions()`, so liveness hangs on the process (via its
    tmux session), never on the MCP link. The claim must be retained: no kill, no
    resume, no new claimant. A resolvable transcript is offered on purpose to
    prove the skip is driven by liveness, not by a missing transcript.
    """
    called = []

    async def fake_redispatch(session, *, card_id, project_path, caller_source=None):
        called.append(card_id)
        return {"card_id": card_id}

    async with KanbanSessionLocal() as s:
        live = await _make_card(s, title="mcp-disconnected but alive", column="engineer")
        await _claim(s, live, "agent:k-product-analy-312c")
        await s.commit()
        recovered = await recovery.recover_project(
            s, project_key=PK, project_path="/p",
            live_sessions={"k-product-analy-312c"},
            resolve=lambda p, n, **kwargs: ("sess", "folder"), redispatch=fake_redispatch,
        )
        await s.commit()
        card = await get_card(s, live)

    assert recovered == []
    assert called == [], "recovery must not redispatch a session that is live in tmux"
    assert card.claimed_by == "agent:k-product-analy-312c", "the live claim must be retained"
    assert card.resume_session_id is None


# ---- reap_pending_spawn_orphans (interrupt-window session leak) -----------

@pytest.mark.asyncio
async def test_reap_pending_spawn_orphans_kills_tmux_without_matching_claim(monkeypatch):
    """An interrupted spawn leaves a tmux session alive with no DB-side claim.

    Before this fix, a crash anywhere between ``tmux new-session`` and the
    post-spawn commit left the tmux session running with nobody able to
    identify or kill it. The new field ``pending_spawn_session`` is written
    BEFORE the spawn, so the boot scan can detect this exact state and kill
    the orphan.
    """
    killed = []

    def fake_kill_session(session_name):
        killed.append(session_name)

    monkeypatch.setattr(recovery, "_kill_tmux_session", fake_kill_session)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="interrupted", column="engineer")
        # No claim -- the spawn was interrupted before the claim op committed,
        # but tmux already created the session.
        card = await get_card(s, cid)
        card.pending_spawn_session = "k-orphan-aaaa"
        await s.commit()

        stats = await recovery.reap_pending_spawn_orphans(
            s, live_sessions={"k-orphan-aaaa"},
        )
        await s.commit()

        fresh = await get_card(s, cid)

    assert killed == ["k-orphan-aaaa"]
    assert stats == {"killed": 1, "stale_cleared": 0, "matched_cleared": 0}
    assert fresh.pending_spawn_session is None


@pytest.mark.asyncio
async def test_reap_pending_spawn_orphans_clears_stale_when_session_dead(monkeypatch):
    """The spawn never reached tmux (or died before this boot). Clear the
    leftover field but don't try to kill anything."""
    killed = []

    def fake_kill_session(session_name):
        killed.append(session_name)

    monkeypatch.setattr(recovery, "_kill_tmux_session", fake_kill_session)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="dead", column="engineer")
        card = await get_card(s, cid)
        card.pending_spawn_session = "k-dead-bbbb"
        await s.commit()

        stats = await recovery.reap_pending_spawn_orphans(
            s, live_sessions=set(),
        )
        await s.commit()

        fresh = await get_card(s, cid)

    assert killed == []
    assert stats == {"killed": 0, "stale_cleared": 1, "matched_cleared": 0}
    assert fresh.pending_spawn_session is None


@pytest.mark.asyncio
async def test_reap_pending_spawn_orphans_clears_matched_claim_bookkeeping(monkeypatch):
    """Normal late-finish bookkeeping: the spawn succeeded and the claim was
    set, but the post-spawn cleanup step that clears ``pending_spawn_session``
    never ran (process was SIGKILLed). Field must be cleared; tmux stays."""
    killed = []

    def fake_kill_session(session_name):
        killed.append(session_name)

    monkeypatch.setattr(recovery, "_kill_tmux_session", fake_kill_session)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="matched", column="engineer")
        await _claim(s, cid, "agent:k-live-cccc")
        card = await get_card(s, cid)
        card.pending_spawn_session = "k-live-cccc"
        await s.commit()

        stats = await recovery.reap_pending_spawn_orphans(
            s, live_sessions={"k-live-cccc"},
        )
        await s.commit()

        fresh = await get_card(s, cid)

    assert killed == [], "live session with a matching claim is not an orphan"
    assert stats == {"killed": 0, "stale_cleared": 0, "matched_cleared": 1}
    assert fresh.pending_spawn_session is None
    assert fresh.claimed_by == "agent:k-live-cccc"


@pytest.mark.asyncio
async def test_reap_pending_spawn_orphans_ignores_cards_without_pending_field(monkeypatch):
    """Cards that never went through the new pre-spawn path (legacy, or
    a normal successful spawn that already cleared the field) are not
    touched by this sweep."""
    killed = []

    def fake_kill_session(session_name):
        killed.append(session_name)

    monkeypatch.setattr(recovery, "_kill_tmux_session", fake_kill_session)

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="legacy", column="engineer")
        await _claim(s, cid, "agent:k-legacy-dddd")
        await s.commit()

        stats = await recovery.reap_pending_spawn_orphans(
            s, live_sessions={"k-legacy-dddd"},
        )

    assert killed == []
    assert stats == {"killed": 0, "stale_cleared": 0, "matched_cleared": 0}
