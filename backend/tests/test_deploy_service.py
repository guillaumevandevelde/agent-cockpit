"""Unit tests for the DeployTarget abstraction.

Covers the two scenarios from the kanban card:

* happy path: ``GHCRDeployTarget.deploy(...)`` invokes ``docker
  buildx build --push`` with the right image_ref, succeeds → result
  has ``status=completed`` plus a non-empty ``image_ref`` and ``logs``.
* failure path: ``docker buildx`` exits non-zero → result has
  ``status=failed`` and an ``error`` string carrying the captured
  stderr.

Plus the abstract interface contract (``DeployTarget`` is abstract and
the registry has a ``ghcr`` entry).

Mocking strategy: the real ``GHCRDeployTarget.deploy`` delegates every
subprocess call to a single helper, ``_run(cmd, **kwargs)``, which
returns ``(returncode, stdout, stderr)``. We patch that helper
instance-method with an ``AsyncMock`` whose ``side_effect`` returns the
tuple directly — no need to fake ``asyncio.subprocess.Process``.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.deploy import (
    DeployResult,
    DeployStatus,
    DeployTarget,
    GHCRDeployTarget,
    get_target,
    list_targets,
)

# -- helpers ----------------------------------------------------------------


def _git_remote_stdout(url: str) -> str:
    """stdout payload the real ``git remote get-url origin`` would emit.

    ``_run`` decodes bytes to str before returning, so the test helper
    hands strings straight through.
    """
    return url + "\n"


def _ok(stdout: str = "", stderr: str = "") -> tuple[int, str, str]:
    """Tuple the real ``_run`` helper returns on success."""
    return (0, stdout, stderr)


def _fail(stdout: str = "", stderr: str = "") -> tuple[int, str, str]:
    """Tuple the real ``_run`` helper returns on failure (exit code 1)."""
    return (1, stdout, stderr)


# -- abstract contract ------------------------------------------------------


def test_deploy_target_is_abstract() -> None:
    """Cannot instantiate the bare ABC — subclass must implement ``deploy``."""
    with pytest.raises(TypeError):
        DeployTarget()  # type: ignore[abstract]


def test_registry_has_ghcr_entry() -> None:
    """The MVP ships a single registry entry, ``ghcr`` → ``GHCRDeployTarget``."""
    targets = list_targets()
    assert any(t.id == "ghcr" for t in targets)
    target = get_target("ghcr")
    assert isinstance(target, GHCRDeployTarget)


# -- happy path -------------------------------------------------------------


@pytest.mark.asyncio
async def test_ghcr_deploy_happy_path(tmp_path: Path) -> None:
    """Build + push succeeds → result is ``completed`` with image_ref & logs."""
    project = tmp_path / "app"
    project.mkdir()

    target = GHCRDeployTarget()
    with patch.object(
        target,
        "_run",
        new=AsyncMock(
            side_effect=[
                # First call: ``git remote get-url origin``.
                _ok(stdout=_git_remote_stdout("git@github.com:acme/widgets.git")),
                # Final: ``docker buildx build --push``.
                _ok(stdout="#1 building\n#1 pushing\n#1 done\n", stderr=""),
            ]
        ),
    ):
        # Patch ``_docker_login`` separately so the happy path doesn't
        # have to model the stdin pipe.
        with patch.object(
            target,
            "_docker_login",
            new=AsyncMock(return_value=(0, "Login Succeeded\n", "")),
        ):
            result = await target.deploy(
                project_path=str(project),
                tag="v1.2.3",
                credentials={"ghcr_token": "ghp_test_token"},
            )

    assert isinstance(result, DeployResult)
    assert result.status == DeployStatus.COMPLETED
    assert result.image_ref == "ghcr.io/acme/widgets:v1.2.3"
    assert result.error is None
    # Logs carry the remote probe and the docker invocation output.
    assert "pushing" in result.logs
    # Timestamps are populated and ``started_at <= completed_at``.
    assert isinstance(result.started_at, datetime)
    assert isinstance(result.completed_at, datetime)
    assert result.started_at <= result.completed_at


# -- failure path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_ghcr_deploy_build_failure_marks_result_failed(tmp_path: Path) -> None:
    """``docker buildx`` exits non-zero → result is ``failed`` with error."""
    project = tmp_path / "app"
    project.mkdir()

    target = GHCRDeployTarget()
    with patch.object(
        target,
        "_run",
        new=AsyncMock(
            side_effect=[
                _ok(stdout=_git_remote_stdout("https://github.com/acme/widgets.git")),
                _fail(
                    stdout="#1 building\n",
                    stderr=(
                        "ERROR: failed to solve: process "
                        '"/bin/sh -c npm ci" did not complete successfully: exit code: 1\n'
                    ),
                ),
            ]
        ),
    ):
        with patch.object(
            target,
            "_docker_login",
            new=AsyncMock(return_value=(0, "Login Succeeded\n", "")),
        ):
            result = await target.deploy(
                project_path=str(project),
                tag="v9.9.9-rc1",
                credentials={"ghcr_token": "ghp_test_token"},
            )

    assert result.status == DeployStatus.FAILED
    # image_ref is set even on failure so the operator knows what was attempted.
    assert result.image_ref == "ghcr.io/acme/widgets:v9.9.9-rc1"
    assert result.error is not None
    assert "npm ci" in result.error
    # Logs still capture both steps so post-mortem is possible.
    assert "failed to solve" in result.logs
    assert result.completed_at is not None


# -- input validation -------------------------------------------------------


@pytest.mark.asyncio
async def test_ghcr_deploy_no_git_remote_returns_failed(tmp_path: Path) -> None:
    """Project dir has no git remote → fail fast with a clear error."""
    project = tmp_path / "no-git"
    project.mkdir()

    target = GHCRDeployTarget()
    with patch.object(
        target, "_run", new=AsyncMock(return_value=_fail(stderr="fatal: no remote\n"))
    ):
        result = await target.deploy(
            project_path=str(project),
            tag="v1.0.0",
            credentials={"ghcr_token": "t"},
        )

    assert result.status == DeployStatus.FAILED
    assert result.error is not None
    assert "remote" in result.error.lower()


@pytest.mark.asyncio
async def test_ghcr_deploy_rejects_non_github_remote(tmp_path: Path) -> None:
    """A non-GitHub origin means we can't push to ``ghcr.io`` (it's GitHub-only)."""
    project = tmp_path / "gitlab-app"
    project.mkdir()

    target = GHCRDeployTarget()
    with patch.object(
        target,
        "_run",
        new=AsyncMock(
            return_value=_ok(stdout=_git_remote_stdout("git@gitlab.com:acme/widgets.git"))
        ),
    ):
        result = await target.deploy(
            project_path=str(project),
            tag="v1.0.0",
            credentials={"ghcr_token": "t"},
        )

    assert result.status == DeployStatus.FAILED
    assert result.error is not None
    assert "github" in result.error.lower()


@pytest.mark.asyncio
async def test_ghcr_deploy_invalid_tag_fails_before_subprocess(tmp_path: Path) -> None:
    """Tags with shell-hostile characters are rejected up-front (no subprocess run)."""
    project = tmp_path / "app"
    project.mkdir()

    target = GHCRDeployTarget()
    run_mock = AsyncMock()
    with patch.object(target, "_run", new=run_mock):
        result = await target.deploy(
            project_path=str(project),
            tag="bad tag with spaces",
            credentials={"ghcr_token": "t"},
        )

    assert result.status == DeployStatus.FAILED
    assert result.error is not None
    assert "invalid tag" in result.error.lower()
    # No subprocess was ever spawned.
    run_mock.assert_not_called()


# -- credential resolution --------------------------------------------------


@pytest.mark.asyncio
async def test_ghcr_deploy_without_explicit_token_falls_back_to_gh_cli(tmp_path: Path) -> None:
    """No ``ghcr_token`` in credentials → target falls back to ``gh auth token``."""
    project = tmp_path / "app"
    project.mkdir()

    captured_cmds: list[tuple[str, ...]] = []

    async def fake_run(cmd, **_kwargs):
        captured_cmds.append(tuple(cmd))
        if cmd and cmd[0] == "git":
            return _ok(stdout=_git_remote_stdout("git@github.com:acme/widgets.git"))
        if cmd and cmd[0] == "gh":
            return _ok(stdout="ghp_from_gh_cli\n", stderr="")
        if cmd and cmd[0] == "docker":
            return _ok(stdout="#1 pushing\n", stderr="")
        return _fail(stderr="unexpected command")

    target = GHCRDeployTarget()
    with patch.object(target, "_run", side_effect=fake_run):
        with patch.object(
            target,
            "_docker_login",
            new=AsyncMock(return_value=(0, "Login Succeeded\n", "")),
        ):
            result = await target.deploy(
                project_path=str(project),
                tag="v0.1.0",
                credentials={},  # no explicit token → fall back to gh CLI
            )

    assert result.status == DeployStatus.COMPLETED
    # The gh CLI was called (i.e. fallback path was taken).
    assert any(args[0] == "gh" and "auth" in args for args in captured_cmds)


@pytest.mark.asyncio
async def test_ghcr_deploy_no_credentials_and_no_gh_cli_returns_failed(tmp_path: Path) -> None:
    """No credentials and ``gh auth token`` fails → result is failed with clear error."""
    project = tmp_path / "app"
    project.mkdir()

    async def fake_run(cmd, **_kwargs):
        if cmd[0] == "git":
            return _ok(stdout=_git_remote_stdout("git@github.com:acme/widgets.git"))
        if cmd[0] == "gh":
            return _fail(stderr="gh: not authenticated\n")
        return _ok()

    target = GHCRDeployTarget()
    with patch.object(target, "_run", side_effect=fake_run):
        result = await target.deploy(
            project_path=str(project),
            tag="v1.0.0",
            credentials={},
        )

    assert result.status == DeployStatus.FAILED
    assert result.error is not None
    assert "credential" in result.error.lower() or "token" in result.error.lower()


# -- registry --------------------------------------------------------------


def test_get_target_unknown_id_raises() -> None:
    """Looking up an unknown target id raises a clear KeyError."""
    with pytest.raises(KeyError):
        get_target("does-not-exist")


# -- audit log --------------------------------------------------------------


@pytest.mark.asyncio
async def test_ghcr_deploy_emits_audit_events(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Every deploy emits both ``deploy_start`` and ``deploy_complete`` audit lines.

    The hook is a structured log today (real ``security_audit`` row
    lands with follow-up #10) — we assert the events landed on the
    dedicated ``deploy.audit`` logger and carry no secret values.
    """
    import logging

    project = tmp_path / "app"
    project.mkdir()

    target = GHCRDeployTarget()
    with caplog.at_level(logging.INFO, logger="app.services.deploy.audit"):
        with patch.object(
            target,
            "_run",
            new=AsyncMock(
                side_effect=[
                    _ok(stdout=_git_remote_stdout("git@github.com:acme/widgets.git")),
                    _ok(stdout="#1 pushing\n", stderr=""),
                ]
            ),
        ):
            with patch.object(
                target,
                "_docker_login",
                new=AsyncMock(return_value=(0, "Login Succeeded\n", "")),
            ):
                result = await target.deploy(
                    project_path=str(project),
                    tag="v1.0.0",
                    credentials={"ghcr_token": "ghp_should_never_appear_in_logs"},
                )

    assert result.status == DeployStatus.COMPLETED
    messages = [r.getMessage() for r in caplog.records]
    start = [m for m in messages if "deploy_start" in m]
    complete = [m for m in messages if "deploy_complete" in m]
    assert start, f"missing deploy_start audit line in {messages!r}"
    assert complete, f"missing deploy_complete audit line in {messages!r}"
    # No token anywhere — the audit hook never logs the secret.
    assert all("ghp_should_never_appear_in_logs" not in m for m in messages)