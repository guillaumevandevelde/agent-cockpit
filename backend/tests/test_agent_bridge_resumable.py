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
