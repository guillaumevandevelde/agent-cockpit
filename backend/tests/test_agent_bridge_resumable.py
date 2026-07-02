"""Tests for worktree-aware resume aggregation and directory resolution."""
import json
import os
import time
from pathlib import Path

import pytest


def test_resume_resolve_directory_prefers_transcript_cwd(monkeypatch, tmp_path):
    from app.services.cc_bridge import spawn as claude_spawn
    from app.services.providers.base import SpawnCommandOptions
    from app.services.providers.claude_code import ClaudeCodeProvider

    worktree_dir = tmp_path / "wt"
    worktree_dir.mkdir()
    project_folder = "-tmp-wt"
    session_id = "sess-1"
    tdir = tmp_path / ".claude" / "projects" / project_folder
    tdir.mkdir(parents=True)
    (tdir / f"{session_id}.jsonl").write_text(
        json.dumps({"cwd": str(worktree_dir)}) + "\n", encoding="utf-8"
    )

    monkeypatch.setattr(claude_spawn.Path, "home", classmethod(lambda cls: tmp_path))

    provider = ClaudeCodeProvider()
    resolved = provider.resolve_directory(
        SpawnCommandOptions(
            directory=str(tmp_path),  # non-empty, deliberately NOT the worktree
            mode="resume",
            session_id=session_id,
            project_folder=project_folder,
        )
    )

    assert resolved == str(worktree_dir)


def _write_session(folder: Path, session_id: str, text: str):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{session_id}.jsonl").write_text(
        json.dumps({"type": "user", "message": {"content": text}}) + "\n",
        encoding="utf-8",
    )


def test_encode_project_folder_matches_claude_layout():
    from app.services.agent_bridge.resumable import _encode_project_folder

    encoded = _encode_project_folder(
        "/home/guillaume/dev/claude-cockpit/.claude/worktrees/Kanban-plan"
    )
    assert encoded == "-home-guillaume-dev-claude-cockpit--claude-worktrees-Kanban-plan"


@pytest.mark.asyncio
async def test_aggregates_main_and_worktree_sessions(monkeypatch, tmp_path):
    from app.services.agent_bridge import resumable

    main_dir = tmp_path / "repo"
    main_dir.mkdir()
    wt_dir = tmp_path / "repo" / ".claude" / "worktrees" / "feat"
    wt_dir.mkdir(parents=True)

    projects_dir = tmp_path / "projects"
    main_folder = resumable._encode_project_folder(str(main_dir))
    wt_folder = resumable._encode_project_folder(str(wt_dir))
    _write_session(projects_dir / main_folder, "main-sess", "hello from main")
    _write_session(projects_dir / wt_folder, "wt-sess", "hello from worktree")

    # Make the worktree session newer so it sorts first.
    future = time.time() + 10
    os.utime(projects_dir / wt_folder / "wt-sess.jsonl", (future, future))

    monkeypatch.setattr(
        resumable,
        "_list_worktrees",
        lambda d: [(str(main_dir), True), (str(wt_dir), False)],
    )
    monkeypatch.setattr(
        "app.services.session_service.get_claude_projects_dir", lambda: projects_dir
    )

    sessions = await resumable.list_resumable_sessions(str(main_dir), limit=20, db=None)

    assert [s.id for s in sessions] == ["wt-sess", "main-sess"]
    assert {s.id: s.worktree_label for s in sessions} == {
        "wt-sess": "feat",
        "main-sess": "main",
    }


@pytest.mark.asyncio
async def test_non_git_directory_returns_only_its_own_sessions(monkeypatch, tmp_path):
    from app.services.agent_bridge import resumable

    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    projects_dir = tmp_path / "projects"
    folder = resumable._encode_project_folder(str(plain_dir))
    _write_session(projects_dir / folder, "only-sess", "hello")

    # Real git call on a non-repo returns non-zero -> fallback to [(dir, True)].
    monkeypatch.setattr(
        "app.services.session_service.get_claude_projects_dir", lambda: projects_dir
    )

    sessions = await resumable.list_resumable_sessions(str(plain_dir), limit=20, db=None)

    assert [s.id for s in sessions] == ["only-sess"]
    assert sessions[0].worktree_label == "main"


def test_list_worktrees_parses_porcelain_output(monkeypatch):
    from types import SimpleNamespace

    from app.services.agent_bridge import resumable

    porcelain = (
        "worktree /home/g/repo\n"
        "HEAD abc123\n"
        "branch refs/heads/master\n"
        "\n"
        "worktree /home/g/repo/.claude/worktrees/feat\n"
        "HEAD def456\n"
        "branch refs/heads/worktree-feat\n"
    )
    monkeypatch.setattr(
        resumable.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=porcelain, stderr=""),
    )

    result = resumable._list_worktrees("/home/g/repo")

    assert result == [
        ("/home/g/repo", True),
        ("/home/g/repo/.claude/worktrees/feat", False),
    ]


def test_list_worktrees_falls_back_when_git_missing(monkeypatch):
    from app.services.agent_bridge import resumable

    def boom(*a, **k):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(resumable.subprocess, "run", boom)

    assert resumable._list_worktrees("/some/dir") == [("/some/dir", True)]
