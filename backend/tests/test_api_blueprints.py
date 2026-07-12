"""REST API tests for the /api/v1/blueprints CRUD endpoints.

The store uses ``~/.claude-registry/blueprints/`` by default; tests patch
`BlueprintStore` to point at a tmp_path so they never touch the real store.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.blueprint.store import BlueprintStore


@pytest.fixture
def store_dir(tmp_path, monkeypatch) -> Path:
    """Patch the store's root to a tmp_path for the duration of this test."""
    root = tmp_path / "blueprints-store"
    root.mkdir()

    def _factory(root=root):
        return BlueprintStore(root=root)

    # Patch the module-level factory used by the API router.
    from app.api.v1 import blueprints as blueprints_api
    monkeypatch.setattr(blueprints_api, "_store", _factory)
    return root


@pytest.fixture
def client(store_dir) -> TestClient:
    return TestClient(app)


# -- list -------------------------------------------------------------------


def test_list_blueprints_empty(client):
    r = client.get("/api/v1/blueprints")
    assert r.status_code == 200
    assert r.json() == {"blueprints": []}


def test_list_blueprints_returns_all(client, store_dir):
    from app.services.blueprint.store import BlueprintStore
    store = BlueprintStore(root=store_dir)
    store.save(__import__("app.services.blueprint", fromlist=["Blueprint"]).Blueprint(
        name="alpha",
    ))
    store.save(__import__("app.services.blueprint", fromlist=["Blueprint"]).Blueprint(
        name="beta",
    ))

    r = client.get("/api/v1/blueprints")
    assert r.status_code == 200
    names = [bp["name"] for bp in r.json()["blueprints"]]
    assert names == ["alpha", "beta"]


# -- create -----------------------------------------------------------------


def test_create_blueprint_minimal(client):
    payload = {"name": "minimal", "description": "the smallest valid blueprint"}
    r = client.post("/api/v1/blueprints", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "minimal"
    assert body["description"] == "the smallest valid blueprint"
    assert body["version"] == 1
    assert body["skills"] == []
    assert body["agents"] == []


def test_create_blueprint_with_rich_fields(client):
    payload = {
        "name": "rich",
        "description": "all the fields",
        "settings": {"permission_mode": "plan", "model": "opus", "plansDirectory": ".plans"},
        "skills": [{"name": "frontend", "source": "project", "version_pin": "1.0"}],
        "agents": [{"name": "planner", "model_default": "opus", "tools": ["Read"]}],
        "statusline": "#!/bin/sh\necho hi\n",
        "output_style": "concise",
        "claudemd": "# context\n",
    }
    r = client.post("/api/v1/blueprints", json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["settings"] == {
        "permission_mode": "plan",
        "model": "opus",
        "plansDirectory": ".plans",
    }
    assert body["skills"] == [{"name": "frontend", "source": "project", "version_pin": "1.0"}]
    assert body["agents"][0]["tools"] == ["Read"]


def test_create_blueprint_invalid_name_returns_400(client):
    r = client.post("/api/v1/blueprints", json={"name": "../escape"})
    assert r.status_code == 400
    assert "name" in r.json()["detail"].lower()


def test_create_blueprint_duplicate_returns_409(client):
    payload = {"name": "duplicate"}
    r1 = client.post("/api/v1/blueprints", json=payload)
    assert r1.status_code == 201
    r2 = client.post("/api/v1/blueprints", json=payload)
    assert r2.status_code == 409


def test_create_blueprint_missing_name_returns_422(client):
    r = client.post("/api/v1/blueprints", json={"description": "no name"})
    assert r.status_code == 422


# -- read -------------------------------------------------------------------


def test_get_blueprint_returns_stored(client, store_dir):
    from app.services.blueprint import Blueprint
    from app.services.blueprint.store import BlueprintStore
    BlueprintStore(root=store_dir).save(Blueprint(name="hello", description="x"))

    r = client.get("/api/v1/blueprints/hello")
    assert r.status_code == 200
    assert r.json()["name"] == "hello"


def test_get_blueprint_not_found_returns_404(client):
    r = client.get("/api/v1/blueprints/__missing__")
    assert r.status_code == 404


def test_get_blueprint_invalid_name_returns_400(client):
    r = client.get("/api/v1/blueprints/with%2Fslash")
    assert r.status_code == 400


# -- update -----------------------------------------------------------------


def test_update_blueprint_partial(client, store_dir):
    from app.services.blueprint import Blueprint
    from app.services.blueprint.store import BlueprintStore
    BlueprintStore(root=store_dir).save(
        Blueprint(name="upd", description="v1", claudemd="# old"),
    )

    r = client.put("/api/v1/blueprints/upd", json={"description": "v2"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["description"] == "v2"
    # `claudemd` was not in the update body — must be preserved.
    assert body["claudemd"] == "# old"


def test_update_blueprint_clear_nullable_field(client, store_dir):
    from app.services.blueprint import Blueprint
    from app.services.blueprint.store import BlueprintStore
    BlueprintStore(root=store_dir).save(Blueprint(name="clr", claudemd="# bye"))

    r = client.put("/api/v1/blueprints/clr", json={"claudemd": None})
    assert r.status_code == 200, r.text
    assert r.json()["claudemd"] is None


def test_update_blueprint_clear_list_with_empty(client, store_dir):
    from app.services.blueprint import Blueprint, BlueprintSkill
    from app.services.blueprint.store import BlueprintStore
    BlueprintStore(root=store_dir).save(
        Blueprint(name="ls", skills=[BlueprintSkill(name="foo")]),
    )

    r = client.put("/api/v1/blueprints/ls", json={"skills": []})
    assert r.status_code == 200, r.text
    assert r.json()["skills"] == []


def test_update_blueprint_not_found_returns_404(client):
    r = client.put("/api/v1/blueprints/__missing__", json={"description": "x"})
    assert r.status_code == 404


# -- delete -----------------------------------------------------------------


def test_delete_blueprint(client, store_dir):
    from app.services.blueprint import Blueprint
    from app.services.blueprint.store import BlueprintStore
    BlueprintStore(root=store_dir).save(Blueprint(name="del"))

    r = client.delete("/api/v1/blueprints/del")
    assert r.status_code == 204
    assert not (store_dir / "del.json").exists()


def test_delete_blueprint_not_found_returns_404(client):
    r = client.delete("/api/v1/blueprints/__missing__")
    assert r.status_code == 404


# -- apply ------------------------------------------------------------------


def test_apply_blueprint_writes_to_project(client, store_dir, tmp_path):
    from app.services.blueprint import Blueprint, BlueprintAgent, BlueprintSkill
    from app.services.blueprint.store import BlueprintStore
    BlueprintStore(root=store_dir).save(
        Blueprint(
            name="deploy",
            skills=[BlueprintSkill(name="frontend", source="project", version_pin="1.0")],
            agents=[BlueprintAgent(name="planner", model_default="opus")],
            claudemd="# hello\n",
        ),
    )

    project = tmp_path / "deploy-target"
    project.mkdir()
    r = client.post(
        "/api/v1/blueprints/deploy/apply",
        json={"project_path": str(project)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["blueprint_name"] == "deploy"
    assert ".claude/skills/frontend/SKILL.md" in body["written_files"]
    assert ".claude/agents/planner.md" in body["written_files"]
    assert "frontend" in body["applied_skills"]
    assert "planner" in body["applied_agents"]

    # Verify the project was actually seeded.
    assert (project / ".claude" / "skills" / "frontend" / "SKILL.md").is_file()
    assert (project / ".claude" / "agents" / "planner.md").is_file()


def test_apply_blueprint_skips_populated_project(client, store_dir, tmp_path):
    from app.services.blueprint import Blueprint
    from app.services.blueprint.store import BlueprintStore
    BlueprintStore(root=store_dir).save(Blueprint(name="seed"))

    project = tmp_path / "already-seeded"
    project.mkdir()
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_text("user-owned")

    r = client.post(
        "/api/v1/blueprints/seed/apply",
        json={"project_path": str(project)},
    )
    assert r.status_code == 200
    assert r.json()["skipped_existing"] is True
    # User's settings untouched.
    assert (project / ".claude" / "settings.json").read_text() == "user-owned"


def test_apply_blueprint_force_overwrites(client, store_dir, tmp_path):
    from app.services.blueprint import Blueprint
    from app.services.blueprint.store import BlueprintStore
    BlueprintStore(root=store_dir).save(Blueprint(name="force"))

    project = tmp_path / "force-target"
    project.mkdir()
    (project / ".claude").mkdir()
    (project / ".claude" / "settings.json").write_text("user-owned")

    r = client.post(
        "/api/v1/blueprints/force/apply",
        json={"project_path": str(project), "force": True},
    )
    assert r.status_code == 200
    assert r.json()["skipped_existing"] is False
    assert (project / ".claude" / "settings.json").read_text() != "user-owned"


def test_apply_blueprint_unknown_name_returns_404(client):
    r = client.post(
        "/api/v1/blueprints/__missing__/apply",
        json={"project_path": "/tmp/does-not-matter"},
    )
    assert r.status_code == 404