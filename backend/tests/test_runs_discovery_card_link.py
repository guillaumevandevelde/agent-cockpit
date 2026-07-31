"""Tests for the session → kanban-card linkage in CC Bridge discovery.

The Agent Bridge page lists live tmux sessions. For sessions that the
kanban dispatcher spawned (a `KanbanCard.dispatch_project_folder` matching
the session's `cwd` after Claude's hyphen-encoding), the frontend renders a
"view kanban card" affordance. This test exercises the enrichment path the
cc-bridge route runs after `discover_agent_sessions()`.
"""
from __future__ import annotations

from app.kanban.models import KanbanCard
from app.kanban.operations import apply_operation
from app.utils.path_utils import convert_path_to_folder_name
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()


async def _seed_card(
    *,
    project_key: str = "git:github.com/example/repo",
    column: str = "Backlog",
    cwd: str | None = None,
    dispatch_project_folder: str | None = None,
    dispatch_session_id: str | None = None,
) -> str:
    """Create a kanban card and optionally stamp the dispatch breadcrumbs."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s,
            op_type="create",
            entity_type="card",
            project_key=project_key,
            entity_id=None,
            payload={"title": "T", "description": "", "column": column},
        )
        if dispatch_project_folder is not None or dispatch_session_id is not None:
            card = await s.get(KanbanCard, cid)
            assert card is not None
            if dispatch_project_folder is not None:
                card.dispatch_project_folder = dispatch_project_folder
            if dispatch_session_id is not None:
                card.dispatch_session_id = dispatch_session_id
        await s.commit()
        return cid


async def _reset():
    await reset_test_tables()


async def test_enrich_attaches_card_id_when_dispatch_project_folder_matches_cwd():
    """A session whose cwd hashes to a card's dispatch_project_folder gets
    the card_id + project_key attached so the frontend can render the
    navigate-to-card affordance."""
    await _reset()
    cwd = "/home/dev/projects/foo/.claude/worktrees/k-foo-1234"
    folder = convert_path_to_folder_name(cwd)
    card_id = await _seed_card(
        project_key="git:github.com/example/repo",
        column="engineer",
        dispatch_project_folder=folder,
        dispatch_session_id="session-uuid-1",
    )

    from app.services.runs.discovery import enrich_sessions_with_cards

    sessions = [
        {
            "cli": "claude-code",
            "cli_display_name": "Claude Code",
            "provider": "anthropic",
            "provider_display_name": "Anthropic",
            "tmux_target": "k-foo-1234:0.0",
            "session_name": "k-foo-1234",
            "window_name": "0",
            "pane_id": "%0",
            "cwd": cwd,
            "pid": "12345",
            "status": "active",
        }
    ]

    async with KanbanSessionLocal() as s:
        await enrich_sessions_with_cards(sessions, s)

    assert sessions[0]["card_id"] == card_id
    assert sessions[0]["card_project_key"] == "git:github.com/example/repo"


async def test_agent_bridge_sessions_response_includes_matching_card(monkeypatch):
    """The endpoint used by Agent Bridge exposes the card navigation fields."""
    await _reset()
    cwd = "/home/dev/projects/foo/.claude/worktrees/k-foo-1234"
    card_id = await _seed_card(
        project_key="git:github.com/example/repo",
        column="engineer",
        dispatch_project_folder=convert_path_to_folder_name(cwd),
    )

    from app.api.v1.runs import router as agent_bridge_api

    discovered = [{"cwd": cwd, "session_name": "k-foo-1234"}]
    discover_calls: list[str | None] = []

    def fake_discover(cli: str | None = None):
        discover_calls.append(cli)
        return discovered

    monkeypatch.setattr(agent_bridge_api, "discover_agent_sessions", fake_discover)

    response = await agent_bridge_api.list_sessions(cli="claude-code")

    assert discover_calls == ["claude-code"]
    assert response["sessions"][0]["card_id"] == card_id
    assert response["sessions"][0]["card_project_key"] == "git:github.com/example/repo"


async def test_enrich_leaves_session_untouched_when_no_match():
    """A session whose cwd doesn't match any card stays un-enriched —
    the SessionCard renders no link in that case."""
    await _reset()
    cwd = "/home/dev/projects/foo/.claude/worktrees/k-foo-1234"
    # Seed a card with a *different* dispatch_project_folder
    await _seed_card(
        project_key="git:github.com/example/repo",
        dispatch_project_folder="-some-other-folder",
    )

    from app.services.runs.discovery import enrich_sessions_with_cards

    sessions = [
        {
            "cli": "claude-code",
            "cli_display_name": "Claude Code",
            "provider": "anthropic",
            "provider_display_name": "Anthropic",
            "tmux_target": "k-foo-1234:0.0",
            "session_name": "k-foo-1234",
            "window_name": "0",
            "pane_id": "%0",
            "cwd": cwd,
            "pid": "12345",
            "status": "active",
        }
    ]

    async with KanbanSessionLocal() as s:
        await enrich_sessions_with_cards(sessions, s)

    assert "card_id" not in sessions[0]
    assert "card_project_key" not in sessions[0]


async def test_enrich_picks_most_recent_card_when_multiple_match():
    """Two cards dispatched the same worktree (e.g. a re-dispatch after
    re-dispatch) — the latest one wins, so the link points to the live
    work, not a stale Done card."""
    await _reset()
    cwd = "/home/dev/projects/foo/.claude/worktrees/k-foo-1234"
    folder = convert_path_to_folder_name(cwd)
    # Seed two cards with the same dispatch_project_folder
    older = await _seed_card(
        project_key="git:github.com/example/repo",
        column="Done",
        dispatch_project_folder=folder,
        dispatch_session_id="session-uuid-older",
    )
    newer = await _seed_card(
        project_key="git:github.com/example/repo",
        column="engineer",
        dispatch_project_folder=folder,
        dispatch_session_id="session-uuid-newer",
    )

    from app.services.runs.discovery import enrich_sessions_with_cards

    sessions = [
        {
            "cli": "claude-code",
            "cli_display_name": "Claude Code",
            "provider": "anthropic",
            "provider_display_name": "Anthropic",
            "tmux_target": "k-foo-1234:0.0",
            "session_name": "k-foo-1234",
            "window_name": "0",
            "pane_id": "%0",
            "cwd": cwd,
            "pid": "12345",
            "status": "active",
        }
    ]

    async with KanbanSessionLocal() as s:
        await enrich_sessions_with_cards(sessions, s)

    # The most-recently-created card wins.
    assert sessions[0]["card_id"] == newer
    assert sessions[0]["card_id"] != older


async def test_enrich_handles_empty_session_list():
    """Empty input → no DB query, no error. Defensive guard for the route."""
    await _reset()

    from app.services.runs.discovery import enrich_sessions_with_cards

    sessions: list[dict] = []
    async with KanbanSessionLocal() as s:
        # Must not raise; must leave the list untouched.
        await enrich_sessions_with_cards(sessions, s)
    assert sessions == []