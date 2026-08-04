# backend/tests/test_kanban_takeover.py
"""Promote a headless-dispatched card's session to an attachable tmux pane.

Implements `docs/cockpit/human-takeover-headless-decision.md` §7: takeover is
a promotion (kill the headless process, `--resume` the same session_id in
tmux under the *same* session_name), not a second takeover-UX. Reusing the
session_name is what keeps the `agent:<session_name>` claim, branch, and
worktree untouched, and what shifts the liveness source from the headless
registry to tmux for free (`reap_stale_claims` already unions both).
"""
import pytest
import pytest_asyncio

from app.kanban import takeover
from app.kanban.operations import apply_operation
from app.kanban.service import get_card
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()

PK = "git:example.com/me/repo"


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


async def _claim(s, cid, claimant):
    await apply_operation(
        s, op_type="claim", entity_type="card", project_key=PK,
        entity_id=cid, payload={"claimed_by": claimant},
    )


@pytest.mark.asyncio
async def test_promote_spawns_resume_with_same_session_name_and_persists_resume_fields():
    kill_calls = []
    spawn_calls = []

    def fake_kill(name):
        kill_calls.append(name)
        return True

    def fake_spawn(**kwargs):
        spawn_calls.append(kwargs)
        return {"tmux_target": "k-hl-0001:0.0", "session_name": "k-hl-0001"}

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await _claim(s, cid, "agent:k-hl-0001")
        await s.commit()

        result = await takeover.promote_to_tmux(
            s, card_id=cid, project_key=PK, project_path="/repo",
            resolve=lambda p, n, **kwargs: ("sess-abc", "-repo--claude-worktrees-k-hl-0001"),
            live_sessions=lambda: set(),
            kill_headless=fake_kill,
            spawn=fake_spawn,
        )
        await s.commit()
        card = await get_card(s, cid)

    assert result == {"tmux_target": "k-hl-0001:0.0", "session_name": "k-hl-0001"}
    assert kill_calls == ["k-hl-0001"]
    assert len(spawn_calls) == 1
    call = spawn_calls[0]
    # Same session_name reused — the claim/branch/worktree stay untouched.
    assert call["session_name"] == "k-hl-0001"
    assert call["options"].mode == "resume"
    assert call["options"].session_id == "sess-abc"
    assert call["options"].project_folder == "-repo--claude-worktrees-k-hl-0001"
    # No prompt: an idle REPL waiting for the human, not an injected message.
    assert call["options"].prompt is None

    assert card.resume_session_id == "sess-abc"
    assert card.resume_project_folder == "-repo--claude-worktrees-k-hl-0001"


@pytest.mark.asyncio
async def test_promote_routes_resolution_and_spawn_to_original_cli():
    resolved_with = []
    spawn_calls = []

    def fake_resolve(project_path, session_name, **kwargs):
        resolved_with.append(kwargs.get("cli_id"))
        return "codex-session", "/repo/.claude/worktrees/k-hl-codex"

    def fake_spawn(**kwargs):
        spawn_calls.append(kwargs)
        return {"tmux_target": "k-hl-codex:0.0", "session_name": "k-hl-codex"}

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={
                "analyst_agent_id": "codex-cli",
                "executor_agent_id": "claude-code",
            },
        )
        await _claim(s, cid, "agent:k-hl-codex")
        await s.commit()

        await takeover.promote_to_tmux(
            s,
            card_id=cid,
            project_key=PK,
            project_path="/repo",
            resolve=fake_resolve,
            live_sessions=lambda: set(),
            kill_headless=lambda name: True,
            spawn=fake_spawn,
        )

    assert resolved_with == ["codex-cli"]
    assert spawn_calls[0]["cli_id"] == "codex-cli"


@pytest.mark.asyncio
async def test_promote_threads_repo_path_for_mcp_fallback():
    """Kaart ``bc123e2d…``: ``promote_to_tmux`` builds the same ``SpawnCommandOptions``
    as ``make_resume_transport`` and must therefore also thread ``repo_path``
    (= the repo-root passed in via ``project_path``) so the resume spawn into
    the worktree still falls back to ``<repo>/.mcp.json`` when the worktree
    itself has none. Without this the takeover path silently loses
    ``cockpit-kanban`` MCP on the first promotion of any external product-project
    card — same bug class as the resume transport.
    """
    spawn_calls = []

    def fake_spawn(**kwargs):
        spawn_calls.append(kwargs)
        return {"tmux_target": "k-hl-mcp:0.0", "session_name": "k-hl-mcp"}

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await _claim(s, cid, "agent:k-hl-mcp")
        await s.commit()

        project_root = "/scratch/scratchpad/product-x"
        await takeover.promote_to_tmux(
            s, card_id=cid, project_key=PK, project_path=project_root,
            resolve=lambda p, n, **kwargs: ("sess-abc", "-repo--claude-worktrees-k-hl-mcp"),
            live_sessions=lambda: set(),
            kill_headless=lambda n: True,
            spawn=fake_spawn,
        )
        await s.commit()

    assert len(spawn_calls) == 1
    call = spawn_calls[0]
    assert call["options"].repo_path == project_root, (
        f"takeover must thread repo_path so the worktree's missing "
        f".mcp.json falls back to the repo-root copy; got "
        f"opts.repo_path={call['options'].repo_path!r}"
    )


@pytest.mark.asyncio
async def test_promote_raises_when_card_not_claimed_by_agent():
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await _claim(s, cid, "me@ui")
        await s.commit()

        with pytest.raises(takeover.TakeoverError):
            await takeover.promote_to_tmux(
                s, card_id=cid, project_key=PK, project_path="/repo",
                resolve=lambda p, n, **kwargs: ("sess-abc", "folder"),
                live_sessions=lambda: set(),
                kill_headless=lambda n: False,
                spawn=lambda **kw: {},
            )


@pytest.mark.asyncio
async def test_promote_raises_when_session_already_live_in_tmux():
    spawn_calls = []

    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await _claim(s, cid, "agent:k-hl-0002")
        await s.commit()

        with pytest.raises(takeover.TakeoverError):
            await takeover.promote_to_tmux(
                s, card_id=cid, project_key=PK, project_path="/repo",
                resolve=lambda p, n, **kwargs: ("sess-abc", "folder"),
                live_sessions=lambda: {"k-hl-0002"},
                kill_headless=lambda n: False,
                spawn=lambda **kw: spawn_calls.append(kw) or {},
            )
    assert spawn_calls == []


@pytest.mark.asyncio
async def test_promote_raises_when_tmux_liveness_ambiguous():
    # `_live_sessions()` returns None on an ambiguous tmux failure — never
    # assume "not live" and spawn a duplicate pane on top of a real one.
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await _claim(s, cid, "agent:k-hl-0003")
        await s.commit()

        with pytest.raises(takeover.TakeoverError):
            await takeover.promote_to_tmux(
                s, card_id=cid, project_key=PK, project_path="/repo",
                resolve=lambda p, n, **kwargs: ("sess-abc", "folder"),
                live_sessions=lambda: None,
                kill_headless=lambda n: False,
                spawn=lambda **kw: {},
            )


@pytest.mark.asyncio
async def test_promote_raises_when_no_resumable_transcript():
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await _claim(s, cid, "agent:k-hl-0004")
        await s.commit()

        with pytest.raises(takeover.TakeoverError):
            await takeover.promote_to_tmux(
                s, card_id=cid, project_key=PK, project_path="/repo",
                resolve=lambda p, n, **kwargs: None,
                live_sessions=lambda: set(),
                kill_headless=lambda n: False,
                spawn=lambda **kw: {},
            )


@pytest.mark.asyncio
async def test_promote_raises_when_card_not_found():
    async with KanbanSessionLocal() as s:
        with pytest.raises(takeover.TakeoverError):
            await takeover.promote_to_tmux(
                s, card_id="does-not-exist", project_key=PK, project_path="/repo",
                resolve=lambda p, n, **kwargs: ("sess-abc", "folder"),
                live_sessions=lambda: set(),
                kill_headless=lambda n: False,
                spawn=lambda **kw: {},
            )
