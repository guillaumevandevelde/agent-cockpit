# backend/tests/test_kanban_project_key.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.kanban.project_key import (
    normalize_remote,
    resolve_project_key,
    resolve_project_path,
    safe_resolve_project_key,
)


def test_normalize_strips_git_suffix_and_scheme():
    assert normalize_remote("https://github.com/u/repo.git") == "github.com/u/repo"
    assert normalize_remote("git@github.com:u/repo.git") == "github.com/u/repo"
    assert normalize_remote("ssh://git@host.com/u/repo") == "host.com/u/repo"


def test_normalize_converts_all_colons_to_slashes():
    # scp-style host:path and ssh-with-port both collapse to slash-separated.
    assert normalize_remote("git@host.com:22/u/repo.git") == "host.com/22/u/repo"


def test_resolve_uses_git_remote_when_present():
    key = resolve_project_key("/any/path", _remote_getter=lambda p: "git@github.com:u/repo.git")
    assert key == "git:github.com/u/repo"


def test_resolve_falls_back_to_slug_when_no_remote():
    key = resolve_project_key("/home/me/My Project", _remote_getter=lambda p: None)
    assert key == "slug:my-project"


def test_safe_returns_none_when_remote_getter_raises():
    # _remote_getter raising mirrors the real `_git_remote` path under
    # adversarial subprocess conditions (e.g. PermissionError on a missing
    # .git). The safe wrapper must absorb that and return None rather than
    # letting the bare exception escape to call sites that must not fail-open
    # (env-injection / audit rows in spawn_session).
    def _boom(_: str) -> str | None:
        raise PermissionError("no .git access")

    assert safe_resolve_project_key("/any/path", _remote_getter=_boom) is None


def test_safe_returns_key_when_remote_resolves():
    # The safe wrapper is a no-op on the happy path: same return value as
    # resolve_project_key when the underlying call succeeds.
    key = safe_resolve_project_key(
        "/any/path",
        _remote_getter=lambda p: "git@github.com:u/repo.git",
    )
    assert key == "git:github.com/u/repo"


def _mock_session_local(paths: list[str]):
    """Build an AsyncSessionLocal double whose one query yields `paths`."""
    mock_sl = MagicMock()
    mock_session = AsyncMock()
    mock_execute = MagicMock()
    mock_execute.scalars.return_value.all.return_value = paths
    mock_session.execute = AsyncMock(return_value=mock_execute)
    mock_sl.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_sl.return_value.__aexit__ = AsyncMock(return_value=None)
    return mock_sl


class TestResolveProjectPath:
    """The public `project_key -> path` reverse resolver. Same match
    semantics and None-on-not-found contract as the private
    `session_cleanup._get_project_path` it replaced (self-improve card)."""

    @pytest.mark.asyncio
    async def test_returns_matching_path(self):
        with patch("app.database.AsyncSessionLocal", _mock_session_local(["/home/me/repo"])), \
             patch("app.kanban.project_key.resolve_project_key",
                   return_value="git:example.com/me/repo"):
            path = await resolve_project_path("git:example.com/me/repo")
            assert path == "/home/me/repo"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_match(self):
        with patch("app.database.AsyncSessionLocal", _mock_session_local(["/home/me/repo"])), \
             patch("app.kanban.project_key.resolve_project_key",
                   return_value="git:other.com/repo"):
            path = await resolve_project_path("git:example.com/me/repo")
            assert path is None

    @pytest.mark.asyncio
    async def test_returns_none_on_db_error(self):
        mock_sl = MagicMock()
        mock_sl.return_value.__aenter__ = AsyncMock(side_effect=Exception("db error"))
        mock_sl.return_value.__aexit__ = AsyncMock(return_value=None)
        with patch("app.database.AsyncSessionLocal", mock_sl):
            path = await resolve_project_path("git:example.com/me/repo")
            assert path is None

    @pytest.mark.asyncio
    async def test_skips_paths_where_key_lookup_fails(self):
        with patch("app.database.AsyncSessionLocal",
                   _mock_session_local(["/bad/path", "/home/me/repo"])), \
             patch("app.kanban.project_key.resolve_project_key",
                   side_effect=[Exception("not a git repo"), "git:example.com/me/repo"]):
            path = await resolve_project_path("git:example.com/me/repo")
            assert path == "/home/me/repo"
