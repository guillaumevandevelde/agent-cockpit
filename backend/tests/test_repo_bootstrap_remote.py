"""TDD for RepoBootstrapService.create_remote (the optional gh-remote step).

subprocess is mocked throughout — no real ``gh`` or network is touched. The
KanbanMeta key-migration is exercised as an injected async callable (the real
``migrate_project_keys`` lives in a sibling card); tests assert it is *called*
with the right (old_slug_key, new_git_key), not that it mutates the DB.
"""
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.repo_bootstrap_service import (
    BootstrapRemoteCreationError,
    CreateRemoteResult,
    RepoBootstrapService,
)


def _make_run(*, auth_ok=True, create_ok=True, origin_url="https://github.com/me/my-app.git"):
    """Return a fake ``subprocess.run`` that dispatches on argv."""
    calls = []

    def _run(cmd, *args, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["gh", "auth"]:
            return SimpleNamespace(returncode=0 if auth_ok else 1, stdout="", stderr="")
        if cmd[:3] == ["gh", "repo", "create"]:
            if not create_ok:
                raise subprocess.CalledProcessError(1, cmd, stderr="HTTP 422 name already exists")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[0] == "git" and "get-url" in cmd:
            return SimpleNamespace(returncode=0, stdout=origin_url + "\n", stderr="")
        raise AssertionError(f"unexpected command {cmd}")

    _run.calls = calls
    return _run


@pytest.fixture
def gh_present(monkeypatch):
    monkeypatch.setattr(
        "app.services.repo_bootstrap_service.shutil.which",
        lambda name: "/usr/bin/gh" if name == "gh" else None,
    )


async def test_create_remote_happy_path_migrates_keys(monkeypatch, tmp_path, gh_present):
    fake_run = _make_run(origin_url="https://github.com/me/my-app.git")
    monkeypatch.setattr("app.services.repo_bootstrap_service.subprocess.run", fake_run)
    migrate = AsyncMock()
    svc = RepoBootstrapService(migrate_keys=migrate)

    local_path = str(tmp_path / "my-app")
    result = await svc.create_remote(local_path, repo_name="my-app", visibility="private")

    assert isinstance(result, CreateRemoteResult)
    assert result.created is True
    assert result.remote_url == "https://github.com/me/my-app.git"
    assert result.new_key == "git:github.com/me/my-app"
    migrate.assert_awaited_once_with("slug:my-app", "git:github.com/me/my-app")

    create_cmd = next(c for c in fake_run.calls if c[:3] == ["gh", "repo", "create"])
    assert create_cmd == [
        "gh", "repo", "create", "my-app", "--private",
        f"--source={local_path}", "--remote=origin", "--push",
    ]


async def test_create_remote_missing_gh_is_graceful(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.services.repo_bootstrap_service.shutil.which", lambda name: None
    )
    migrate = AsyncMock()
    svc = RepoBootstrapService(migrate_keys=migrate)

    result = await svc.create_remote(str(tmp_path / "my-app"), repo_name="my-app")

    assert result == CreateRemoteResult(created=False, reason="gh_missing_or_unauthed")
    migrate.assert_not_awaited()


async def test_create_remote_missing_auth_is_graceful(monkeypatch, tmp_path, gh_present):
    monkeypatch.setattr(
        "app.services.repo_bootstrap_service.subprocess.run",
        _make_run(auth_ok=False),
    )
    migrate = AsyncMock()
    svc = RepoBootstrapService(migrate_keys=migrate)

    result = await svc.create_remote(str(tmp_path / "my-app"), repo_name="my-app")

    assert result == CreateRemoteResult(created=False, reason="gh_missing_or_unauthed")
    migrate.assert_not_awaited()


async def test_create_remote_subprocess_error_raises(monkeypatch, tmp_path, gh_present):
    monkeypatch.setattr(
        "app.services.repo_bootstrap_service.subprocess.run",
        _make_run(create_ok=False),
    )
    migrate = AsyncMock()
    svc = RepoBootstrapService(migrate_keys=migrate)

    with pytest.raises(BootstrapRemoteCreationError):
        await svc.create_remote(str(tmp_path / "my-app"), repo_name="my-app")
    migrate.assert_not_awaited()


async def test_create_remote_identical_key_skips_migration(monkeypatch, tmp_path, gh_present):
    monkeypatch.setattr(
        "app.services.repo_bootstrap_service.subprocess.run", _make_run()
    )
    migrate = AsyncMock()
    svc = RepoBootstrapService(migrate_keys=migrate)
    # Force old_key == new_key (e.g. a repo that already carried the remote):
    monkeypatch.setattr(svc, "_derive_keys", lambda local_path, url: ("git:same", "git:same"))

    result = await svc.create_remote(str(tmp_path / "my-app"), repo_name="my-app")

    assert result.created is True
    assert result.new_key == "git:same"
    migrate.assert_not_awaited()


async def test_create_remote_without_migrate_callable_errors_when_needed(
    monkeypatch, tmp_path, gh_present
):
    monkeypatch.setattr(
        "app.services.repo_bootstrap_service.subprocess.run", _make_run()
    )
    svc = RepoBootstrapService()  # no migrate_keys wired

    with pytest.raises(RuntimeError, match="migrate_keys"):
        await svc.create_remote(str(tmp_path / "my-app"), repo_name="my-app")
