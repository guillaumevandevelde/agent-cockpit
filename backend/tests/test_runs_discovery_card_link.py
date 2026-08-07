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
    title: str = "T",
) -> str:
    """Create a kanban card and optionally stamp the dispatch breadcrumbs."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s,
            op_type="create",
            entity_type="card",
            project_key=project_key,
            entity_id=None,
            payload={"title": title, "description": "", "column": column},
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
        title="",
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


async def test_enrich_attaches_card_title_for_body_affordance():
    """The frontend surfaces the card title in the tile body so the
    operator can answer 'which board ticket is this' without clicking.
    Without this field the body affordance falls back to a generic icon
    (kanban card 215cd8ea…)."""
    await _reset()
    cwd = "/home/dev/projects/foo/.claude/worktrees/k-foo-1234"
    folder = convert_path_to_folder_name(cwd)
    card_id = await _seed_card(
        project_key="git:github.com/example/repo",
        column="engineer",
        dispatch_project_folder=folder,
        title="Vindbaar maken van de kaart-knop op Agent-Bridge",
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

    assert sessions[0]["card_title"] == "Vindbaar maken van de kaart-knop op Agent-Bridge"
    # Sanity: the link fields are still there.
    assert sessions[0]["card_id"] == card_id


async def test_enrich_card_title_falls_back_to_empty_string_when_unset():
    """A card whose `title` is the empty string (the ORM default) still
    gets a string in the dict — never `None` — so the React tile can
    safely read `session.card_title` without a nullish check."""
    await _reset()
    cwd = "/home/dev/projects/foo/.claude/worktrees/k-foo-1234"
    folder = convert_path_to_folder_name(cwd)
    await _seed_card(
        project_key="git:github.com/example/repo",
        column="engineer",
        dispatch_project_folder=folder,
        title="",
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

    assert sessions[0]["card_title"] == ""
    assert isinstance(sessions[0]["card_title"], str)


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


async def test_agent_bridge_teams_response_includes_matching_card_on_ungrouped(monkeypatch):
    """The ``/api/v1/agent-bridge/teams`` endpoint exposes the card navigation
    fields on ungrouped sessions so the SessionCard render-guard can show the
    "view kanban card" affordance.

    The ``/teams`` handler is the corner the Agent Bridge page actually reads
    (``useTeams`` → ``teams`` + ``ungrouped``); the ``/sessions`` enrichment is
    only consumed for the page-level count. Without this, the affordance
    never renders — kanban card 4cdf1fc6… (the bug this test exists to pin).
    """
    await _reset()
    cwd = "/home/dev/projects/foo/.claude/worktrees/k-foo-1234"
    card_id = await _seed_card(
        project_key="git:github.com/example/repo",
        column="engineer",
        dispatch_project_folder=convert_path_to_folder_name(cwd),
    )

    from app.api.v1.runs import router as agent_bridge_api
    from app.services.runs import groups as groups_service

    discovered = [
        {
            "cli": "claude-code",
            "cli_display_name": "Claude Code",
            "session_name": "k-foo-1234",
            "cwd": cwd,
            "tmux_target": "k-foo-1234:0.0",
        },
        {
            "cli": "claude-code",
            "cli_display_name": "Claude Code",
            "session_name": "k-foo-other",
            "cwd": "/home/dev/projects/foo/.claude/worktrees/k-foo-other",
            "tmux_target": "k-foo-other:0.0",
        },
    ]

    monkeypatch.setattr(agent_bridge_api, "discover_agent_sessions", lambda: discovered)

    async def fake_get_manual_groups(db):
        return []

    monkeypatch.setattr(groups_service, "get_manual_groups", fake_get_manual_groups)

    response = await agent_bridge_api.list_teams(db=None)
    wire = response.model_dump()

    assert wire["ungrouped"][0]["card_id"] == card_id
    assert wire["ungrouped"][0]["card_project_key"] == "git:github.com/example/repo"
    # The other session has no matching card — it must stay un-enriched.
    assert "card_id" not in wire["ungrouped"][1]


async def test_agent_bridge_teams_response_includes_matching_card_on_team_member(monkeypatch):
    """The team-member dicts in the ``teams`` array also get enriched, since
    the lead/member render path goes through the same dict objects that
    ``discover_groups`` emits (``get_ungrouped_runs`` shares the dicts with
    the input list).

    The frontend's TeamCard reads the same ``card_id`` field, so a
    dispatched lead or member needs the enrichment on its dict just as
    much as an ungrouped session does.
    """
    await _reset()
    cwd = "/home/dev/projects/foo/.claude/worktrees/k-foo-1234"
    card_id = await _seed_card(
        project_key="git:github.com/example/repo",
        column="engineer",
        dispatch_project_folder=convert_path_to_folder_name(cwd),
    )

    from app.api.v1.runs import router as agent_bridge_api
    from app.services.runs import groups as groups_service

    discovered = [
        {
            "cli": "claude-code",
            "cli_display_name": "Claude Code",
            "session_name": "lead-aaaa",
            "cwd": cwd,
            "tmux_target": "lead-aaaa:0.0",
        },
        {
            "cli": "claude-code",
            "cli_display_name": "Claude Code",
            "session_name": "member-bbbb",
            "cwd": cwd,
            "tmux_target": "member-bbbb:0.0",
        },
    ]

    monkeypatch.setattr(agent_bridge_api, "discover_agent_sessions", lambda: discovered)

    async def fake_get_manual_groups(db):
        return []

    monkeypatch.setattr(groups_service, "get_manual_groups", fake_get_manual_groups)

    response = await agent_bridge_api.list_teams(db=None)
    wire = response.model_dump()

    assert len(wire["teams"]) == 1
    team = wire["teams"][0]
    assert team["lead"]["card_id"] == card_id
    assert team["lead"]["card_project_key"] == "git:github.com/example/repo"
    # Both members share the same cwd → both get the same card_id.
    member_cards = [m["card_id"] for m in team["members"]]
    assert member_cards == [card_id, card_id]


async def test_agent_bridge_teams_enrichment_fails_open_when_kanban_db_unavailable(monkeypatch):
    """An unreachable KanbanSessionLocal must NOT blow up the /teams route —
    the page must still see the session list, just without the card link.
    Mirrors the fail-open contract on /sessions (router.py:107-111)."""
    await _reset()
    cwd = "/home/dev/projects/foo/.claude/worktrees/k-foo-1234"
    # Seed a card so enrichment would normally succeed — we want to verify
    # the failure path is the one being taken, not the "no match" path.
    await _seed_card(
        project_key="git:github.com/example/repo",
        column="engineer",
        dispatch_project_folder=convert_path_to_folder_name(cwd),
    )

    from app.api.v1.runs import router as agent_bridge_api
    from app.services.runs import groups as groups_service

    discovered = [
        {
            "cli": "claude-code",
            "cli_display_name": "Claude Code",
            "session_name": "k-foo-1234",
            "cwd": cwd,
            "tmux_target": "k-foo-1234:0.0",
        },
    ]

    monkeypatch.setattr(agent_bridge_api, "discover_agent_sessions", lambda: discovered)

    async def fake_get_manual_groups(db):
        return []

    monkeypatch.setattr(groups_service, "get_manual_groups", fake_get_manual_groups)

    real_kanban_session = agent_bridge_api.KanbanSessionLocal

    class _BrokenSession:
        def __aenter__(self):
            raise RuntimeError("kanban DB unavailable")

        def __aexit__(self, *args):
            return False

    monkeypatch.setattr(agent_bridge_api, "KanbanSessionLocal", lambda: _BrokenSession())

    try:
        response = await agent_bridge_api.list_teams(db=None)
    finally:
        monkeypatch.setattr(agent_bridge_api, "KanbanSessionLocal", real_kanban_session)

    # The session list MUST still come back — fail-open, not 500.
    wire = response.model_dump()
    assert len(wire["ungrouped"]) == 1
    assert wire["ungrouped"][0]["session_name"] == "k-foo-1234"
    # …and the card fields must be absent because enrichment failed.
    assert "card_id" not in wire["ungrouped"][0]


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


async def test_legacy_cc_bridge_sessions_route_does_not_enrich(monkeypatch):
    """The legacy ``/api/v1/cc-bridge/sessions`` route MUST NOT enrich sessions.

    The Agent Bridge frontend reads from ``/api/v1/agent-bridge/sessions``
    only — the ``cc-bridge`` URL prefix is a dead wire scheduled for removal
    (see ``cc_bridge/router.py`` module docstring). Enrichment at both sites
    is duplicate work that risks drift from the canonical implementation
    (kanban card cade1e9b919944258c442d273c1dcfd7, human decision on the
    impediment: single enrichment site).

    This test pins that invariant. If a future PR re-adds the enrichment to
    the legacy route, ``card_id`` will appear on the response and this test
    fails — a loud signal to revert rather than ship the duplicate.
    """
    await _reset()
    cwd = "/home/dev/projects/foo/.claude/worktrees/k-foo-1234"
    # Seed a card whose dispatch_project_folder would match if the legacy
    # route still ran enrichment — the test passes only when enrichment is
    # absent, so we get a positive failure signal the moment it's restored.
    await _seed_card(
        project_key="git:github.com/example/repo",
        column="engineer",
        dispatch_project_folder=convert_path_to_folder_name(cwd),
    )

    from app.api.v1.cc_bridge import router as cc_bridge_api

    discovered = [{"cwd": cwd, "session_name": "k-foo-1234"}]

    def fake_discover_cc_sessions():
        return discovered

    monkeypatch.setattr(cc_bridge_api, "discover_cc_sessions", fake_discover_cc_sessions)

    response = await cc_bridge_api.list_sessions()

    session = response["sessions"][0]
    assert "card_id" not in session, (
        "legacy cc-bridge route re-enriched sessions — enrichment belongs "
        "on the live agent-bridge route only (kanban card "
        "cade1e9b919944258c442d273c1dcfd7)"
    )
    assert "card_project_key" not in session
    assert "card_title" not in session