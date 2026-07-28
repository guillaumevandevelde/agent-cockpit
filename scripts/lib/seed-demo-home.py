#!/usr/bin/env python3
"""Seed a throwaway $HOME with sanitized demo data for screenshot capture.

Companion to scripts/capture-screenshots.sh — produces the file-system
half of the demo state. Server-side state (kanban cards, presence
events, scheduled messages, agent mail) is POSTed by capture-screenshots.sh
once the throwaway backend is healthy; this script only writes files.

Why this script exists
----------------------
The previous ad-hoc capture flow (commits f2b2153 + kaart 35d372a0) ran
~2 hours of throwaway setup each time, seeded a throwaway $HOME from
scratch, and threw the rig away. Card beabca63… folded that dance into
this single command. The shell wrapper owns the lifecycle; this script
owns just the file-system seed.

Sanitization guarantees
-----------------------
Output under --target contains NONE of:
  * the host $HOME, the repo root, or any path inside it
  * the host username
  * the host git remote
  * the host tmux socket
Every project path, transcript, and config file uses fictional
identifiers (`example-project`, `demo-api`) rooted under a fake
`/srv/projects/` parent. Verified by
scripts/test_capture_screenshots.sh Task 9.

Usage
-----
    seed-demo-home.py --target <dir>
    seed-demo-home.py --target <dir> --jsonl-count 6   # more transcripts
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from textwrap import dedent

# Fictional demo identifiers — never the host's actual paths.
DEMO_PROJECTS = [
    {
        "id": "example-project",
        "display": "Example Web App",
        "path": "/srv/projects/example-project",
        "git_remote": "https://github.com/example-co/example-project.git",
        "transcripts": 3,
    },
    {
        "id": "demo-api",
        "display": "Demo REST API",
        "path": "/srv/projects/demo-api",
        "git_remote": "https://github.com/example-co/demo-api.git",
        "transcripts": 2,
    },
]

DEMO_AGENTS = [
    {
        "filename": "engineer.md",
        "frontmatter": {"name": "Engineer", "description": "End-to-end feature implementation."},
    },
    {
        "filename": "analyst.md",
        "frontmatter": {"name": "Analyst", "description": "Multi-agent decomposition into child cards."},
    },
    {
        "filename": "reviewer.md",
        "frontmatter": {"name": "Reviewer", "description": "Pre-Done gate: feature-compliance check."},
    },
]

DEMO_COMMANDS = [
    {
        "filename": "ship.md",
        "body": "Ship the current branch via the git-ship recipe.\n",
    },
    {
        "filename": "flag-problem.md",
        "body": "File a [problem] card against the current state.\n",
    },
]

DEMO_SKILLS = [
    {
        "dirname": "example-skill",
        "manifest": dedent(
            """\
            ---
            name: example-skill
            description: A demonstration skill shipped with the cockpit installer.
            ---
            # Example skill

            Loads sample data into the throwaway $HOME so the Skills page
            has entries to render.
            """
        ),
    },
]

DEMO_BLUEPRINT = {
    "filename": "demo-blueprint.yaml",
    "body": dedent(
        """\
        apiVersion: blueprint/v1
        kind: ProjectBlueprint
        metadata:
          name: demo-blueprint
          description: Seeds a new project with example commands and skills.
        spec:
          pin: 1.0.0
          files:
            - path: .claude/commands/ship.md
              template: ship.md
            - path: .claude/skills/example-skill/SKILL.md
              template: example-skill/SKILL.md
        """
    ),
}

# Two example scheduled messages — the Scheduled Messages page renders
# these on first load. Shape matches `ScheduledMessageCreate` in
# `backend/app/models/scheduled_message_schemas.py` so the wrapper can
# POST them verbatim.
DEMO_SCHEDULED_MESSAGES = [
    {
        "kind": "scheduled_message",
        "target_project": "/srv/projects/example-project",
        "message": "Sweep the kanban for stale Doing cards.",
        "trigger_type": "cron",
        "cron_expr": "17 9 * * 1-5",
        "timezone": "UTC",
        "permission_mode": "acceptEdits",
        "on_missing_session": "spawn",
        "when_busy": "wait_until_idle",
        "target_kind": "project",
        "session_preview": "Daily kanban sweep",
    },
    {
        "kind": "scheduled_message",
        "target_project": "/srv/projects/demo-api",
        "message": "Poll provider usage so the Usage page stays fresh.",
        "trigger_type": "cron",
        "cron_expr": "7 * * * *",
        "timezone": "UTC",
        "permission_mode": "acceptEdits",
        "on_missing_session": "spawn",
        "when_busy": "wait_until_idle",
        "target_kind": "project",
        "session_preview": "Hourly usage poll",
    },
]

# A handful of presence events so the Presence page is not empty. Shape
# matches `PresenceEventIn` in `backend/app/models/schemas.py:1830`.
# Each event triggers `process_event` which is the same path real
# Claude Code HTTP hooks take — no separate code path.
DEMO_PRESENCE_EVENTS = [
    {
        "kind": "presence_event",
        "session_id": "demo-engineer-001",
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "cwd": "/srv/projects/example-project",
        "user_prompt": "Refining seed-demo-home.py — sanitization tightening",
    },
    {
        "kind": "presence_event",
        "session_id": "demo-analyst-002",
        "hook_event_name": "UserPromptSubmit",
        "cwd": "/srv/projects/example-project",
        "user_prompt": "Awaiting parent card review",
    },
    {
        "kind": "presence_event",
        "session_id": "demo-reviewer-003",
        "hook_event_name": "PermissionRequest",
        "cwd": "/srv/projects/demo-api",
        "user_prompt": "Awaiting human decision on shape of FCR prompt",
    },
]

# A small backlog + doing + done mix so the kanban screenshot has rows on
# every column a default project shows. Shape matches `CardCreate` in
# `backend/app/kanban/schemas.py:341`. `confirm_new_project=True` lets the
# first POST seed the project bucket on the demo backend.
DEMO_KANBAN_CARDS = [
    {
        "kind": "kanban_card",
        "project_key": "example-project",
        "title": "Refresh demo landing page",
        "description": "Recapture the landing-page hero against the new branding.",
        "column": "Backlog",
        "work_type": "feature",
        "labels": ["frontend"],
    },
    {
        "kind": "kanban_card",
        "project_key": "example-project",
        "title": "Add OpenAPI schema lint to CI",
        "description": "Run spectral on every PR to catch breaking changes early.",
        "column": "Backlog",
        "work_type": "chore",
        "labels": ["backend", "ci"],
    },
    {
        "kind": "kanban_card",
        "project_key": "example-project",
        "title": "Wire presence hook into demo project",
        "description": "Hook the demo project's settings.json to the presence webhook.",
        "column": "Doing",
        "work_type": "feature",
        "labels": ["frontend"],
    },
    {
        "kind": "kanban_card",
        "project_key": "demo-api",
        "title": "Replace sandbox shim with v2 contract",
        "description": "Migrate the sandcastle bridge to the v2 manifest.",
        "column": "Doing",
        "work_type": "chore",
        "labels": ["backend"],
    },
    {
        "kind": "kanban_card",
        "project_key": "example-project",
        "title": "Seed example transcripts",
        "description": "Add 5 transcript files under .claude/projects/example-project/.",
        "column": "Done",
        "work_type": "chore",
        "labels": ["chore"],
    },
    {
        "kind": "kanban_card",
        "project_key": "demo-api",
        "title": "Pin agent config defaults",
        "description": "Lock provider + model defaults to keep the demo screenshots stable.",
        "column": "Done",
        "work_type": "chore",
        "labels": ["chore"],
    },
]


def encode_path(abs_path: str) -> str:
    """Match Claude Code's path-folder encoding.

    Replaces '/' and '.' with '-', matching
    `backend/app/utils/path_utils.py:convert_path_to_folder_name` so the
    demo sessions land under the same parent dir the real Claude
    backend expects.
    """
    return abs_path.rstrip("/").replace("/", "-").replace(".", "-")


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def seed_claude_dir(claude_dir: Path) -> None:
    """Seed `~/.claude/` — settings, commands, agents, skills, projects."""
    claude_dir.mkdir(parents=True, exist_ok=True)

    # settings.json — minimal hooks config so the Presence page can
    # subscribe to events without a real Claude CLI.
    write_json(
        claude_dir / "settings.json",
        {
            "hooks": {
                "SessionStart": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo demo-session-start"}]}],
                "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "echo demo-post-tool"}]}],
            },
            "permissions": {"allow": ["Read", "Grep"], "deny": ["Bash(rm:*)"]},
            "model": "sonnet",
            "env": {"DEMO_MODE": "1"},
        },
    )

    # commands/<name>.md
    for cmd in DEMO_COMMANDS:
        write_text(claude_dir / "commands" / cmd["filename"], cmd["body"])

    # agents/<name>.md
    for agent in DEMO_AGENTS:
        body = "---\n"
        for k, v in agent["frontmatter"].items():
            body += f"{k}: {v}\n"
        body += "---\n\n# " + agent["frontmatter"]["name"] + "\n\nDemo agent.\n"
        write_text(claude_dir / "agents" / agent["filename"], body)

    # skills/<name>/SKILL.md
    for skill in DEMO_SKILLS:
        write_text(claude_dir / "skills" / skill["dirname"] / "SKILL.md", skill["manifest"])

    # projects/<encoded>/<encoded>/<session-id>.jsonl
    now = datetime.now(timezone.utc)
    for project in DEMO_PROJECTS:
        encoded = encode_path(project["path"])
        session_dir = claude_dir / "projects" / encoded / encoded
        session_dir.mkdir(parents=True, exist_ok=True)
        for i in range(project["transcripts"]):
            ts = (now - timedelta(hours=i * 3 + 1)).isoformat()
            session_id = f"demo-{project['id']}-{i:03d}"
            lines = [
                {
                    "type": "user",
                    "timestamp": ts,
                    "sessionId": session_id,
                    "message": {"role": "user", "content": f"Demo user message #{i} for {project['display']}."},
                },
                {
                    "type": "assistant",
                    "timestamp": ts,
                    "sessionId": session_id,
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": f"Demo assistant reply #{i}."},
                        ],
                    },
                },
                {
                    "type": "user",
                    "timestamp": ts,
                    "sessionId": session_id,
                    "message": {"role": "user", "content": "Follow-up question."},
                },
            ]
            transcript = "\n".join(json.dumps(line) for line in lines) + "\n"
            write_text(session_dir / f"{session_id}.jsonl", transcript)


def seed_registry_dir(registry_dir: Path) -> None:
    """Seed `~/.claude-registry/` — projects, blueprints."""
    registry_dir.mkdir(parents=True, exist_ok=True)

    # blueprints/<file>.yaml
    write_text(registry_dir / "blueprints" / DEMO_BLUEPRINT["filename"], DEMO_BLUEPRINT["body"])

    # projects/<id>/<metadata>
    for project in DEMO_PROJECTS:
        meta_dir = registry_dir / "projects" / project["id"]
        write_json(
            meta_dir / "project.json",
            {
                "id": project["id"],
                "name": project["display"],
                "path": project["path"],
                "git_remote": project["git_remote"],
                "registered_at": datetime.now(timezone.utc).isoformat(),
            },
        )


def write_demo_state_jsonl(target_dir: Path) -> None:
    """Write a sidecar JSONL the wrapper POSTs to the backend.

    The backend creates its kanban DB schema on first launch but starts
    empty; capturing the kanban page from an empty board would yield a
    blank screenshot. The shell wrapper POSTs the entries below via the
    REST API once the throwaway backend is healthy, so the kanban /
    presence / scheduled-messages pages render with a believable amount
    of demo content.

    Shape: one JSON object per line, each carries a `kind` discriminator
    that selects the POST endpoint:
      * `kanban_card`       → POST /api/v1/kanban/cards
      * `presence_event`    → POST /api/v1/presence/events
      * `scheduled_message` → POST /api/v1/scheduled-messages
    Other fields on each line match the matching Pydantic Create
    schema, so the wrapper forwards the payload verbatim.
    """
    state_file = target_dir / "demo-state.jsonl"
    lines: list[dict] = list(DEMO_KANBAN_CARDS) + list(DEMO_PRESENCE_EVENTS) + list(DEMO_SCHEDULED_MESSAGES)
    write_text(state_file, "\n".join(json.dumps(line) for line in lines) + "\n")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--target",
        required=True,
        type=Path,
        help="Throwaway HOME directory to seed (will be created if missing).",
    )
    parser.add_argument(
        "--jsonl-count",
        type=int,
        default=None,
        help="Override transcript count per project (default: use DEMO_PROJECTS value).",
    )
    args = parser.parse_args(argv)

    target: Path = args.target.resolve()
    target.mkdir(parents=True, exist_ok=True)

    if args.jsonl_count is not None:
        for project in DEMO_PROJECTS:
            project["transcripts"] = max(0, args.jsonl_count)

    seed_claude_dir(target / ".claude")
    seed_registry_dir(target / ".claude-registry")
    write_demo_state_jsonl(target)

    print(f"seed-demo-home: populated {target} with {len(DEMO_PROJECTS)} projects, "
          f"{len(DEMO_AGENTS)} agents, {len(DEMO_COMMANDS)} commands, "
          f"{len(DEMO_SKILLS)} skills, {len(DEMO_BLUEPRINT)} blueprint, "
          f"{sum(p['transcripts'] for p in DEMO_PROJECTS)} transcripts")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))