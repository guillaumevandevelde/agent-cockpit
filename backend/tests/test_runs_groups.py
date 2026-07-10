"""Tests for agent team discovery and grouping."""
from __future__ import annotations


def test_discover_groups_groups_by_cwd():
    """Sessions sharing cwd + provider are grouped into an auto-detected team."""
    from app.services.runs.groups import discover_groups

    sessions = [
        {
            "provider": "claude-code",
            "provider_display_name": "Claude Code",
            "session_name": "lead-aaaa",
            "cwd": "/repo/a",
            "tmux_target": "lead-aaaa:0.0",
        },
        {
            "provider": "claude-code",
            "provider_display_name": "Claude Code",
            "session_name": "member-bbbb",
            "cwd": "/repo/a",
            "tmux_target": "member-bbbb:0.0",
        },
        {
            "provider": "claude-code",
            "provider_display_name": "Claude Code",
            "session_name": "other-cccc",
            "cwd": "/repo/b",
            "tmux_target": "other-cccc:0.0",
        },
    ]

    teams = discover_groups(sessions)
    assert len(teams) == 1
    team = teams[0]
    assert team["is_auto_detected"] is True
    assert team["cwd"] == "/repo/a"
    assert len(team["runs"]) == 2
    assert team["runs"][0]["session_name"] == "lead-aaaa"
    assert team["runs"][1]["session_name"] == "member-bbbb"


def test_discover_groups_skips_single_session():
    """A single session in a directory is not grouped into a team."""
    from app.services.runs.groups import discover_groups

    sessions = [
        {
            "provider": "claude-code",
            "session_name": "solo",
            "cwd": "/repo/solo",
            "tmux_target": "solo:0.0",
        },
    ]
    teams = discover_groups(sessions)
    assert len(teams) == 0


def test_discover_groups_separates_by_provider():
    """Sessions with different providers in the same cwd form separate teams."""
    from app.services.runs.groups import discover_groups

    sessions = [
        {
            "provider": "claude-code",
            "session_name": "cc-lead",
            "cwd": "/repo/shared",
            "tmux_target": "cc-lead:0.0",
        },
        {
            "provider": "claude-code",
            "session_name": "cc-member",
            "cwd": "/repo/shared",
            "tmux_target": "cc-member:0.0",
        },
        {
            "provider": "codex-cli",
            "session_name": "codex-lead",
            "cwd": "/repo/shared",
            "tmux_target": "codex-lead:0.0",
        },
        {
            "provider": "codex-cli",
            "session_name": "codex-member",
            "cwd": "/repo/shared",
            "tmux_target": "codex-member:0.0",
        },
    ]

    teams = discover_groups(sessions)
    assert len(teams) == 2
    providers = {t["provider"] for t in teams}
    assert providers == {"claude-code", "codex-cli"}


def test_get_ungrouped_runs():
    """Sessions in teams are excluded from the ungrouped list."""
    from app.services.runs.groups import discover_groups, get_ungrouped_runs

    sessions = [
        {"provider": "claude-code", "session_name": "lead", "cwd": "/repo/a", "tmux_target": "lead:0.0"},
        {"provider": "claude-code", "session_name": "member", "cwd": "/repo/a", "tmux_target": "member:0.0"},
        {"provider": "claude-code", "session_name": "solo", "cwd": "/repo/b", "tmux_target": "solo:0.0"},
    ]

    teams = discover_groups(sessions)
    ungrouped = get_ungrouped_runs(sessions, teams)

    assert len(ungrouped) == 1
    assert ungrouped[0]["session_name"] == "solo"


def test_discover_groups_with_manual_teams():
    """Manual teams are merged into the team list and exclude auto-grouped sessions."""
    from app.services.runs.groups import discover_groups

    sessions = [
        {"provider": "claude-code", "session_name": "manual-lead", "cwd": "/repo/x", "tmux_target": "manual-lead:0.0"},
        {"provider": "claude-code", "session_name": "manual-follower", "cwd": "/repo/x", "tmux_target": "manual-follower:0.0"},
    ]

    manual_teams = [
        {
            "group_id": "manual-1",
            "name": "My Team",
            "provider": "claude-code",
            "cwd": "/repo/x",
            "is_auto_detected": False,
            "lead_run_name": "manual-lead",
            "memberships": [
                {"run_name": "manual-lead", "pane_id": None, "tmux_target": "manual-lead:0.0"},
                {"run_name": "manual-follower", "pane_id": None, "tmux_target": "manual-follower:0.0"},
            ],
        }
    ]

    teams = discover_groups(sessions, manual_teams)
    assert len(teams) == 1
    assert teams[0]["is_auto_detected"] is False
    assert teams[0]["name"] == "My Team"
    assert teams[0]["group_id"] == "manual-1"
