"""Tests for per-session live git status (branch, dirty, ahead/behind)."""
import subprocess
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.services.agent_bridge import git_status as gs


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(path: Path) -> None:
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t.test")
    _git(path, "config", "user.name", "Tester")


# --- porcelain parser (pure) ---------------------------------------------------

def test_parse_clean_branch_with_upstream():
    output = "\n".join([
        "# branch.oid abc123",
        "# branch.head main",
        "# branch.upstream origin/main",
        "# branch.ab +2 -1",
    ])
    result = gs.parse_porcelain_v2(output)
    assert result == {
        "branch": "main",
        "detached": False,
        "upstream": "origin/main",
        "ahead": 2,
        "behind": 1,
        "dirty": False,
    }


def test_parse_dirty_when_changes_present():
    output = "\n".join([
        "# branch.head main",
        "# branch.ab +0 -0",
        "1 .M N... 100644 100644 100644 aaa bbb file.txt",
    ])
    result = gs.parse_porcelain_v2(output)
    assert result["dirty"] is True


def test_parse_detached_head():
    output = "\n".join([
        "# branch.head (detached)",
    ])
    result = gs.parse_porcelain_v2(output)
    assert result["detached"] is True
    assert result["branch"] is None


def test_parse_no_upstream_has_zero_ahead_behind():
    output = "\n".join([
        "# branch.head feature",
    ])
    result = gs.parse_porcelain_v2(output)
    assert result["upstream"] is None
    assert result["ahead"] == 0
    assert result["behind"] == 0
    assert result["branch"] == "feature"


# --- git_status against a real repo -------------------------------------------

@pytest.mark.asyncio
async def test_get_git_status_clean_repo(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("hello")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")

    result = await gs.get_git_status(str(tmp_path))
    assert result["is_git_repo"] is True
    assert result["branch"] == "main"
    assert result["dirty"] is False


@pytest.mark.asyncio
async def test_get_git_status_dirty_repo(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("hello")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    (tmp_path / "a.txt").write_text("changed")

    result = await gs.get_git_status(str(tmp_path))
    assert result["dirty"] is True


@pytest.mark.asyncio
async def test_get_git_status_non_git_dir(tmp_path):
    result = await gs.get_git_status(str(tmp_path))
    assert result["is_git_repo"] is False
    assert result["branch"] is None


# --- endpoint -----------------------------------------------------------------

def _client() -> AsyncClient:
    from app.main import app
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_git_status_endpoint_returns_status(monkeypatch, tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("hello")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")

    async def fake_resolve(target: str):
        return str(tmp_path)

    monkeypatch.setattr(gs, "resolve_pane_cwd", fake_resolve)

    async with _client() as ac:
        r = await ac.get("/api/v1/agent-bridge/sessions/proj:0.0/git-status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_git_repo"] is True
    assert body["branch"] == "main"
    assert body["dirty"] is False


@pytest.mark.asyncio
async def test_git_status_endpoint_404_when_pane_missing(monkeypatch):
    async def fake_resolve(target: str):
        return None

    monkeypatch.setattr(gs, "resolve_pane_cwd", fake_resolve)

    async with _client() as ac:
        r = await ac.get("/api/v1/agent-bridge/sessions/gone:0.0/git-status")
    assert r.status_code == 404
