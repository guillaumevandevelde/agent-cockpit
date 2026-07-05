"""Tests for Plans API mutation endpoints (create/update/delete)."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.plan_service import PlanService


@pytest.fixture
def plans_dir(tmp_path, monkeypatch):
    """Point the Plans service at an isolated temp directory."""
    d = tmp_path / "plans"
    d.mkdir()
    monkeypatch.setattr(PlanService, "resolve_plans_dir", classmethod(lambda cls, project_path=None: d))
    return d


# --- Service-level tests ---------------------------------------------------


def test_create_plan_writes_file(plans_dir):
    plan = PlanService.create_plan(plans_dir, "my-plan", "# My Plan\n\nDo the thing.")
    assert plan["filename"] == "my-plan.md"
    assert plan["slug"] == "my-plan"
    assert plan["title"] == "My Plan"
    assert (plans_dir / "my-plan.md").read_text() == "# My Plan\n\nDo the thing."


def test_create_plan_appends_md_suffix(plans_dir):
    plan = PlanService.create_plan(plans_dir, "already.md", "# X")
    assert plan["filename"] == "already.md"


def test_create_plan_rejects_existing(plans_dir):
    PlanService.create_plan(plans_dir, "dup", "# A")
    with pytest.raises(ValueError):
        PlanService.create_plan(plans_dir, "dup", "# B")


def test_create_plan_creates_missing_dir(tmp_path, monkeypatch):
    d = tmp_path / "nope" / "plans"
    monkeypatch.setattr(PlanService, "resolve_plans_dir", classmethod(lambda cls, project_path=None: d))
    plan = PlanService.create_plan(d, "fresh", "# Fresh")
    assert (d / "fresh.md").exists()
    assert plan["title"] == "Fresh"


@pytest.mark.parametrize("bad", ["../escape", "sub/dir", "..", "", "   ", "/abs", "a/../b"])
def test_create_plan_rejects_traversal(plans_dir, bad):
    with pytest.raises(ValueError):
        PlanService.create_plan(plans_dir, bad, "# X")


def test_update_plan_overwrites_content(plans_dir):
    PlanService.create_plan(plans_dir, "edit-me", "# Old")
    plan = PlanService.update_plan(plans_dir, "edit-me", "# New\n\nUpdated.")
    assert plan["title"] == "New"
    assert (plans_dir / "edit-me.md").read_text() == "# New\n\nUpdated."


def test_update_plan_missing_returns_none(plans_dir):
    assert PlanService.update_plan(plans_dir, "ghost", "# X") is None


def test_update_plan_rejects_traversal(plans_dir):
    with pytest.raises(ValueError):
        PlanService.update_plan(plans_dir, "../escape", "# X")


def test_delete_plan_removes_file(plans_dir):
    PlanService.create_plan(plans_dir, "kill-me", "# Bye")
    assert PlanService.delete_plan(plans_dir, "kill-me") is True
    assert not (plans_dir / "kill-me.md").exists()


def test_delete_plan_missing_returns_false(plans_dir):
    assert PlanService.delete_plan(plans_dir, "ghost") is False


def test_delete_plan_rejects_traversal(plans_dir):
    with pytest.raises(ValueError):
        PlanService.delete_plan(plans_dir, "../../etc/passwd")


# --- API-level tests -------------------------------------------------------


@pytest.mark.asyncio
async def test_api_create_update_delete_roundtrip(plans_dir):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        # Create
        r = await ac.post("/api/v1/plans", json={"filename": "api-plan", "content": "# API Plan\n\nBody."})
        assert r.status_code == 201, r.text
        body = r.json()["plan"]
        assert body["filename"] == "api-plan.md"
        assert body["title"] == "API Plan"
        assert "linked_sessions" in body

        # Duplicate create -> 400
        r = await ac.post("/api/v1/plans", json={"filename": "api-plan", "content": "# Dup"})
        assert r.status_code == 400, r.text

        # Update
        r = await ac.put("/api/v1/plans/api-plan.md", json={"content": "# API Plan v2"})
        assert r.status_code == 200, r.text
        assert r.json()["plan"]["title"] == "API Plan v2"

        # Update missing -> 404
        r = await ac.put("/api/v1/plans/ghost.md", json={"content": "# X"})
        assert r.status_code == 404, r.text

        # Delete
        r = await ac.delete("/api/v1/plans/api-plan.md")
        assert r.status_code == 204, r.text
        assert not (plans_dir / "api-plan.md").exists()

        # Delete missing -> 404
        r = await ac.delete("/api/v1/plans/api-plan.md")
        assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_api_create_traversal_rejected(plans_dir):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/plans", json={"filename": "../escape", "content": "# X"})
        assert r.status_code == 400, r.text
