"""Per-CLI discovery of resumable sessions from vendor-owned stores."""
import json
import os
import sqlite3
from pathlib import Path

import pytest

from app.services.agentic_cli.claude_code import ClaudeCodeCli
from app.services.agentic_cli.codex_cli import CodexCli
from app.services.agentic_cli.copilot_cli import CopilotCli
from app.services.agentic_cli.mimo_code import MiMoCodeCli
from app.services.agentic_cli.open_code import OpenCodeCli
from app.utils.path_utils import convert_path_to_folder_name


def _session_db(
    path: Path,
    rows: list[tuple[str, str, int, int | None, str | None]],
) -> None:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT NOT NULL, "
        "time_updated INTEGER NOT NULL, time_archived INTEGER, parent_id TEXT)"
    )
    connection.executemany(
        "INSERT INTO session(id, directory, time_updated, time_archived, parent_id) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    connection.commit()
    connection.close()


def test_claude_resolver_keeps_encoded_project_folder(tmp_path):
    worktree = tmp_path / "repo" / ".claude" / "worktrees" / "k-claude"
    worktree.mkdir(parents=True)
    folder = convert_path_to_folder_name(str(worktree))
    transcript_dir = tmp_path / "projects" / folder
    transcript_dir.mkdir(parents=True)
    old = transcript_dir / "1111.jsonl"
    new = transcript_dir / "2222.jsonl"
    old.write_text("{}", encoding="utf-8")
    new.write_text("{}", encoding="utf-8")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))

    assert ClaudeCodeCli().resolve_resume_target(
        worktree, data_dir=tmp_path / "projects"
    ) == ("2222", folder)


def test_codex_resolver_selects_newest_exact_worktree(tmp_path):
    worktree = tmp_path / "repo" / ".claude" / "worktrees" / "k-codex"
    worktree.mkdir(parents=True)
    sessions = tmp_path / "codex" / "sessions" / "2026" / "08" / "03"
    sessions.mkdir(parents=True)
    for session_id, cwd, mtime in [
        ("11111111-1111-1111-1111-111111111111", str(worktree), 1000),
        ("22222222-2222-2222-2222-222222222222", str(worktree), 2000),
        ("33333333-3333-3333-3333-333333333333", str(tmp_path / "other"), 3000),
    ]:
        path = sessions / f"rollout-2026-08-03T00-00-00-{session_id}.jsonl"
        path.write_text(
            json.dumps(
                {
                    "timestamp": "2026-08-03T00:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": session_id,
                        "session_id": session_id,
                        "cwd": cwd,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        os.utime(path, (mtime, mtime))

    child_id = "44444444-4444-4444-4444-444444444444"
    child = sessions / f"rollout-2026-08-03T00-00-01-{child_id}.jsonl"
    child.write_text(
        json.dumps(
            {
                "timestamp": "2026-08-03T00:00:01Z",
                "type": "session_meta",
                "payload": {
                    "id": child_id,
                    "session_id": child_id,
                    "parent_thread_id": "22222222-2222-2222-2222-222222222222",
                    "cwd": str(worktree),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(child, (2500, 2500))

    mismatched_id = "55555555-5555-5555-5555-555555555555"
    mismatched = sessions / f"rollout-2026-08-03T00-00-02-{mismatched_id}.jsonl"
    mismatched.write_text(
        json.dumps(
            {
                "timestamp": "2026-08-03T00:00:02Z",
                "type": "session_meta",
                "payload": {"id": "not-the-filename-id", "cwd": str(worktree)},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    os.utime(mismatched, (2600, 2600))

    assert CodexCli().resolve_resume_target(
        worktree, data_dir=tmp_path / "codex"
    ) == (
        "22222222-2222-2222-2222-222222222222",
        str(worktree.resolve()),
    )


def test_codex_resolver_ignores_malformed_rollouts(tmp_path):
    worktree = tmp_path / "repo" / ".claude" / "worktrees" / "k-codex"
    worktree.mkdir(parents=True)
    sessions = tmp_path / "codex" / "sessions" / "2026" / "08" / "03"
    sessions.mkdir(parents=True)
    (sessions / "rollout-broken.jsonl").write_text("not-json\n", encoding="utf-8")

    assert CodexCli().resolve_resume_target(
        worktree, data_dir=tmp_path / "codex"
    ) is None


@pytest.mark.parametrize(
    ("cli", "database_name"),
    [(OpenCodeCli(), "opencode.db"), (MiMoCodeCli(), "mimocode.db")],
)
def test_sqlite_resolvers_select_newest_unarchived_exact_directory(
    tmp_path, cli, database_name
):
    worktree = tmp_path / "repo" / ".claude" / "worktrees" / "k-sqlite"
    worktree.mkdir(parents=True)
    data_dir = tmp_path / cli.id
    data_dir.mkdir()
    _session_db(
        data_dir / database_name,
        [
            ("ses_old", str(worktree.resolve()), 1000, None, None),
            ("ses_new", str(worktree.resolve()), 2000, None, None),
            ("ses_child", str(worktree.resolve()), 2500, None, "ses_new"),
            ("ses_archived", str(worktree.resolve()), 3000, 3001, None),
            ("ses_other", str((tmp_path / "other").resolve()), 4000, None, None),
        ],
    )

    assert cli.resolve_resume_target(worktree, data_dir=data_dir) == (
        "ses_new",
        str(worktree.resolve()),
    )


def test_sqlite_resolver_reads_committed_wal_while_writer_is_open(tmp_path):
    worktree = tmp_path / "repo" / ".claude" / "worktrees" / "k-wal"
    worktree.mkdir(parents=True)
    data_dir = tmp_path / "open-code"
    data_dir.mkdir()
    database = data_dir / "opencode.db"
    writer = sqlite3.connect(database)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute(
            "CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT NOT NULL, "
            "time_updated INTEGER NOT NULL, time_archived INTEGER, parent_id TEXT)"
        )
        writer.execute(
            "INSERT INTO session(id, directory, time_updated) VALUES (?, ?, ?)",
            ("ses_wal", str(worktree.resolve()), 1000),
        )
        writer.commit()

        assert OpenCodeCli().resolve_resume_target(
            worktree, data_dir=data_dir,
        ) == ("ses_wal", str(worktree.resolve()))
    finally:
        writer.close()


def test_sqlite_resolver_returns_none_for_unreadable_schema(tmp_path):
    worktree = tmp_path / "repo" / ".claude" / "worktrees" / "k-open"
    worktree.mkdir(parents=True)
    data_dir = tmp_path / "open-code"
    data_dir.mkdir()
    sqlite3.connect(data_dir / "opencode.db").close()

    assert OpenCodeCli().resolve_resume_target(worktree, data_dir=data_dir) is None


def test_copilot_resume_discovery_is_explicitly_unsupported(tmp_path):
    cli = CopilotCli()

    assert cli.supports_resume_resolution is False
    assert cli.resolve_resume_target(tmp_path) is None
