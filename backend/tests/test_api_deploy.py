"""REST API tests for ``/api/v1/deploy``.

We mock the deploy service so the test is hermetic (no docker, no
git, no subprocess). The API contract tested here is what the
frontend relies on: status field shape, error mapping, and the 404
path for unknown targets.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.deploy import (
    DeployResult,
    DeployStatus,
    GHCRDeployTarget,
)

# -- fixtures ---------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def fake_deploy_target(monkeypatch: pytest.MonkeyPatch):
    """Replace ``GHCRDeployTarget.deploy`` with an AsyncMock + preset return.

    Tests set ``fake_deploy_target.return_value`` to control what the
    handler sees; ``fake_deploy_target.call_args_list`` lets assertions
    inspect how the handler invoked the target (project_path, tag,
    credentials).
    """
    mock = AsyncMock()
    # ``app.services.deploy.get_target`` returns a registry instance;
    # patching ``deploy`` on the *class* makes the lookup pick up the
    # patched method because Python looks it up at call time.
    monkeypatch.setattr(GHCRDeployTarget, "deploy", mock)
    return mock


# -- list endpoint ----------------------------------------------------------


def test_list_targets_returns_ghcr(client: TestClient) -> None:
    r = client.get("/api/v1/deploy/targets")
    assert r.status_code == 200, r.text
    body = r.json()
    ids = [t["id"] for t in body["targets"]]
    assert "ghcr" in ids
    # Sorted alphabetically so the UI doesn't flicker on registration order.
    assert ids == sorted(ids)


# -- invoke endpoint: happy path --------------------------------------------


def test_invoke_happy_path_returns_completed(
    client: TestClient, fake_deploy_target: AsyncMock, tmp_path: Path
) -> None:
    project = tmp_path / "app"
    project.mkdir()

    started = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)
    completed = datetime(2026, 7, 14, 12, 5, 0, tzinfo=UTC)
    fake_deploy_target.return_value = DeployResult(
        status=DeployStatus.COMPLETED,
        image_ref="ghcr.io/acme/widgets:v1.0.0",
        logs="#1 pushing\n#1 done\n",
        started_at=started,
        completed_at=completed,
    )

    r = client.post(
        "/api/v1/deploy/targets/ghcr/invoke",
        json={
            "project_path": str(project),
            "tag": "v1.0.0",
            "credentials": {"ghcr_token": "t"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert body["image_ref"] == "ghcr.io/acme/widgets:v1.0.0"
    assert body["error"] is None
    assert body["logs"] == "#1 pushing\n#1 done\n"
    assert body["started_at"].startswith("2026-07-14T12:00:00")
    assert body["completed_at"].startswith("2026-07-14T12:05:00")


def test_invoke_passes_credentials_through(
    client: TestClient, fake_deploy_target: AsyncMock, tmp_path: Path
) -> None:
    """The handler must forward ``credentials`` verbatim to the target."""
    project = tmp_path / "app"
    project.mkdir()

    fake_deploy_target.return_value = DeployResult(
        status=DeployStatus.COMPLETED,
        image_ref="ghcr.io/acme/widgets:v0.1.0",
        logs="",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )

    r = client.post(
        "/api/v1/deploy/targets/ghcr/invoke",
        json={
            "project_path": str(project),
            "tag": "v0.1.0",
            "credentials": {"ghcr_token": "ghp_xyz", "extra": "ignored"},
        },
    )
    assert r.status_code == 200, r.text

    call = fake_deploy_target.call_args
    assert call.args[0] == str(project)
    assert call.args[1] == "v0.1.0"
    assert call.kwargs["credentials"] == {
        "ghcr_token": "ghp_xyz",
        "extra": "ignored",
    }


def test_invoke_omitted_credentials_is_none(
    client: TestClient, fake_deploy_target: AsyncMock, tmp_path: Path
) -> None:
    """No ``credentials`` field → handler passes ``None`` (so the target can fall back)."""
    project = tmp_path / "app"
    project.mkdir()

    fake_deploy_target.return_value = DeployResult(
        status=DeployStatus.COMPLETED,
        image_ref="ghcr.io/acme/widgets:v0.2.0",
        logs="",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )

    r = client.post(
        "/api/v1/deploy/targets/ghcr/invoke",
        json={"project_path": str(project), "tag": "v0.2.0"},
    )
    assert r.status_code == 200, r.text

    call = fake_deploy_target.call_args
    # ``credentials=None`` is the documented "use gh auth fallback" signal.
    assert call.kwargs["credentials"] is None


# -- invoke endpoint: failure paths -----------------------------------------


def test_invoke_failed_deploy_returns_200_with_failed_status(
    client: TestClient, fake_deploy_target: AsyncMock, tmp_path: Path
) -> None:
    """A *failed deploy* is not an HTTP error — it's a 200 with status=failed.

    Distinguishing "endpoint crashed" (HTTP 500) from "deploy ran and
    the image push failed" (HTTP 200, body.status=failed) is the whole
    reason the body has a ``status`` field rather than HTTP semantics.
    """
    project = tmp_path / "app"
    project.mkdir()

    fake_deploy_target.return_value = DeployResult(
        status=DeployStatus.FAILED,
        image_ref="ghcr.io/acme/widgets:v9.9.9",
        logs="ERROR: build failed\n",
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        error="docker buildx build --push failed (exit 1): build failed",
    )

    r = client.post(
        "/api/v1/deploy/targets/ghcr/invoke",
        json={"project_path": str(project), "tag": "v9.9.9"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "failed"
    assert "build failed" in body["error"]


def test_invoke_unknown_target_returns_404(client: TestClient) -> None:
    r = client.post(
        "/api/v1/deploy/targets/does-not-exist/invoke",
        json={"project_path": "/tmp", "tag": "v1.0.0"},
    )
    assert r.status_code == 404, r.text
    assert "does-not-exist" in r.json()["detail"]


def test_invoke_unexpected_crash_returns_500(
    client: TestClient, fake_deploy_target: AsyncMock, tmp_path: Path
) -> None:
    """If the target itself raises (a bug, not a build failure), the endpoint returns 500.

    The contract on ``DeployTarget.deploy`` is "always return a result,
    never raise" — but the handler still defends against a breach of
    that contract so a buggy third-party target can't crash the API.
    """
    project = tmp_path / "app"
    project.mkdir()

    fake_deploy_target.side_effect = RuntimeError("subprocess blew up")

    r = client.post(
        "/api/v1/deploy/targets/ghcr/invoke",
        json={"project_path": str(project), "tag": "v1.0.0"},
    )
    assert r.status_code == 500, r.text
    assert "crashed" in r.json()["detail"]


# -- input validation -------------------------------------------------------


def test_invoke_missing_project_path_returns_422(client: TestClient) -> None:
    r = client.post(
        "/api/v1/deploy/targets/ghcr/invoke",
        json={"tag": "v1.0.0"},
    )
    assert r.status_code == 422


def test_invoke_missing_tag_returns_422(client: TestClient) -> None:
    r = client.post(
        "/api/v1/deploy/targets/ghcr/invoke",
        json={"project_path": "/tmp"},
    )
    assert r.status_code == 422


def test_invoke_empty_tag_returns_422(client: TestClient) -> None:
    r = client.post(
        "/api/v1/deploy/targets/ghcr/invoke",
        json={"project_path": "/tmp", "tag": ""},
    )
    assert r.status_code == 422