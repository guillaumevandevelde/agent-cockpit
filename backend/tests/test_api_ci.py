"""REST API tests for /api/v1/ci/templates (CI-template engine)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# -- list -------------------------------------------------------------------


def test_list_ci_templates_returns_three_profiles(client):
    r = client.get("/api/v1/ci/templates")
    assert r.status_code == 200, r.text
    body = r.json()
    names = {entry["name"] for entry in body["templates"]}
    assert names == {"python-strict", "node-strict", "minimal"}


def test_list_ci_templates_exposes_parameters(client):
    r = client.get("/api/v1/ci/templates")
    assert r.status_code == 200, r.text
    body = r.json()
    by_name = {entry["name"]: entry for entry in body["templates"]}
    py_params = {p["name"] for p in by_name["python-strict"]["parameters"]}
    assert {"python_version", "requirements_dev_path"} <= py_params
    node_params = {p["name"] for p in by_name["node-strict"]["parameters"]}
    assert "node_version" in node_params
    assert by_name["minimal"]["parameters"] == []


# -- apply ------------------------------------------------------------------


def test_apply_ci_template_writes_workflow(client, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    r = client.post(
        "/api/v1/ci/templates/python-strict/apply",
        json={
            "project_path": str(project),
            "parameters": {"python_version": "3.11"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile"] == "python-strict"
    assert body["written_file"] == ".github/workflows/python-strict.yml"
    assert body["skipped_existing"] is False
    assert body["force"] is False

    wf = project / ".github" / "workflows" / "python-strict.yml"
    assert wf.is_file()
    assert "3.11" in wf.read_text()


def test_apply_ci_template_is_idempotent(client, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    r1 = client.post(
        "/api/v1/ci/templates/minimal/apply",
        json={"project_path": str(project)},
    )
    assert r1.status_code == 200, r1.text
    assert r1.json()["skipped_existing"] is False

    r2 = client.post(
        "/api/v1/ci/templates/minimal/apply",
        json={"project_path": str(project)},
    )
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["skipped_existing"] is True
    assert body["written_file"] is None


def test_apply_ci_template_force_overwrites(client, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    # Prime the workflow.
    client.post(
        "/api/v1/ci/templates/minimal/apply",
        json={"project_path": str(project)},
    )
    wf = project / ".github" / "workflows" / "minimal.yml"
    wf.write_text("tampered: yes\n")

    r = client.post(
        "/api/v1/ci/templates/minimal/apply",
        json={"project_path": str(project), "force": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["skipped_existing"] is False
    assert body["force"] is True
    assert body["written_file"] == ".github/workflows/minimal.yml"
    assert "tampered" not in wf.read_text()


def test_apply_ci_template_unknown_profile_returns_404(client, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()

    r = client.post(
        "/api/v1/ci/templates/nope/apply",
        json={"project_path": str(project)},
    )
    assert r.status_code == 404, r.text
    assert "unknown CI profile" in r.json()["detail"]