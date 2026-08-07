"""Integration tests for GET /api/v1/kanban/cards/{cid}/run-ledger.

Covers kanban card aa8158e3's acceptance criteria: the full spine
(task/context/files/tests/outcome+model), a card without a branch
deliverable, and a card with a branch but no iteration-loop progress file.
"""
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.database import AsyncSessionLocal
from app.main import app
from app.models.database import Project
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo_with_branch_diff(root: Path, branch: str) -> None:
    """Repo with an `origin/master` ref and `branch` one commit ahead
    (two changed files) — no real remote needed, `update-ref` fakes it."""
    _git(root, "init", "-q", "-b", "master")
    _git(root, "config", "user.email", "t@t.test")
    _git(root, "config", "user.name", "Tester")
    (root / "a.txt").write_text("hello\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    _git(root, "update-ref", "refs/remotes/origin/master", "master")

    _git(root, "checkout", "-qb", branch)
    (root / "a.txt").write_text("hello\nworld\n")
    (root / "b.txt").write_text("new file\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "feature change")


async def _register_project(path: Path) -> str:
    """Insert a Project row for `path` (main app DB, separate from the
    kanban DB) and return the project_key that `resolve_project_key` will
    compute for it (no git remote in these test repos -> slug: form)."""
    from app.kanban.project_key import resolve_project_key

    async with AsyncSessionLocal() as db:
        db.add(Project(name=path.name, path=str(path)))
        await db.commit()
    return resolve_project_key(str(path))


@pytest.mark.asyncio
async def test_run_ledger_returns_404_for_missing_card():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get("/api/v1/kanban/cards/does-not-exist/run-ledger")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_run_ledger_card_without_branch_deliverable(tmp_path):
    """Freshly-created Backlog card: task/context are always available,
    files/tests are best-effort empty (no branch deliverable yet), outcome
    is empty (card never moved to Done/Impediment)."""
    project_key = await _register_project(tmp_path)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
                          json={"project_key": project_key, "title": "no branch yet",
                                "description": "do the thing",
                                "confirm_new_project": True})
        cid = r.json()["id"]

        r = await ac.get(f"/api/v1/kanban/cards/{cid}/run-ledger")
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["card_id"] == cid
        assert body["task"] == {"title": "no branch yet", "description": "do the thing"}

        assert body["context"]["available"] is True
        assert "no branch yet" in body["context"]["prompt"]
        assert body["context"]["phase"] == "executor"

        assert body["files"]["available"] is False
        assert body["files"]["branch"] is None
        assert body["files"]["note"]

        assert body["tests"]["available"] is False
        assert body["tests"]["note"]

        assert body["outcome"]["column"] == "Backlog"
        assert body["outcome"]["outcome_text"] is None
        assert body["outcome"]["model"] is None

        assert body["usage_url"] == f"/api/v1/kanban/cards/{cid}/usage"


@pytest.mark.asyncio
async def test_run_ledger_card_with_branch_but_no_iteration_file(tmp_path):
    """Card with a branch deliverable and a real diff, but the
    iteration-loop skill never ran in this worktree — files must be
    available, tests must not."""
    project_key = await _register_project(tmp_path)
    branch = "k-test-branch"
    _init_repo_with_branch_diff(tmp_path, branch)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
                          json={"project_key": project_key, "title": "has branch",
                                "confirm_new_project": True})
        cid = r.json()["id"]

        r = await ac.post(f"/api/v1/kanban/cards/{cid}/deliverables",
                          json={"kind": "branch", "ref": branch})
        assert r.status_code == 200, r.text

        r = await ac.get(f"/api/v1/kanban/cards/{cid}/run-ledger")
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["files"]["available"] is True
        assert body["files"]["branch"] == branch
        paths = {f["path"] for f in body["files"]["files"]}
        assert paths == {"a.txt", "b.txt"}
        assert body["files"]["files_changed"] == 2
        assert body["files"]["insertions_total"] > 0

        assert body["tests"]["available"] is False
        assert "iteration" in body["tests"]["note"]


@pytest.mark.asyncio
async def test_run_ledger_full_chain(tmp_path):
    """End to end: branch deliverable with a real diff, an iteration-loop
    progress file in the worktree, a pr deliverable, and a Done card with a
    `**Summary:**` comment — every step must be populated."""
    project_key = await _register_project(tmp_path)
    branch = "k-full-chain"
    _init_repo_with_branch_diff(tmp_path, branch)

    worktree_state_dir = tmp_path / ".claude" / "worktrees" / branch / ".claude" / "state"
    worktree_state_dir.mkdir(parents=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
                          json={"project_key": project_key, "title": "full chain",
                                "description": "ship it", "model": "claude-sonnet-4-5",
                                "confirm_new_project": True})
        cid = r.json()["id"]

        # Iteration-loop progress file, written after we know the card id.
        iteration_file = worktree_state_dir / f"iteration-{cid}.txt"
        iteration_file.write_text(
            "2026-07-18T09:00:00Z | preset=verify | iter=1 | ran lint+build | blocked\n"
            "2026-07-18T09:05:00Z | preset=verify | iter=2 | ran lint+build | clean\n",
        )

        r = await ac.post(f"/api/v1/kanban/cards/{cid}/deliverables",
                          json={"kind": "branch", "ref": branch})
        assert r.status_code == 200, r.text
        r = await ac.post(f"/api/v1/kanban/cards/{cid}/deliverables",
                          json={"kind": "pr", "ref": "https://github.com/o/r/pull/1"})
        assert r.status_code == 200, r.text

        # Kaart efbb82e6… — the REST /move shares the gate with MCP;
        # a Done move needs `summary` or it's refused with 422. Pass
        # the summary inline so the gate accepts the move.
        r = await ac.post(f"/api/v1/kanban/cards/{cid}/move",
                          json={"column": "Done",
                                "summary": "shipped the thing"})
        assert r.status_code == 200, r.text

        r = await ac.get(f"/api/v1/kanban/cards/{cid}/run-ledger")
        assert r.status_code == 200, r.text
        body = r.json()

        assert body["task"]["title"] == "full chain"
        assert body["task"]["description"] == "ship it"

        assert body["context"]["available"] is True
        assert "full chain" in body["context"]["prompt"]

        assert body["files"]["available"] is True
        assert body["files"]["branch"] == branch
        assert body["files"]["files_changed"] == 2

        assert body["tests"]["available"] is True
        assert body["tests"]["iteration_count"] == 2
        assert body["tests"]["status"] == "clean"
        assert body["tests"]["ci_url"] == "https://github.com/o/r/pull/1"

        assert body["outcome"]["column"] == "Done"
        assert body["outcome"]["outcome_text"] == "shipped the thing"
        assert body["outcome"]["outcome_source"] == "summary"
        assert body["outcome"]["model"] == "claude-sonnet-4-5"
        assert body["outcome"]["completed_at"] is not None

        assert body["usage_url"] == f"/api/v1/kanban/cards/{cid}/usage"
