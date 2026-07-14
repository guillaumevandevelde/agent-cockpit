# backend/tests/test_kanban_project_key.py
from app.kanban.project_key import (
    normalize_remote,
    resolve_project_key,
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
