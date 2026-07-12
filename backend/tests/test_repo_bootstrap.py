"""Tests for RepoBootstrapService — the atomic mkdir+git-init+first-commit
service that scaffolds a brand-new local repo.

Acceptance criteria from kanban card dceb60ab (facet B, follow-up #1):
- happy path: tmp_path, `.git/` exists, `.gitignore` written, `README.md`
  written, one commit on `main`, no exceptions, exit-code 0
- failure paths:
  - parent not writable → exception, no `<target>` created, no half-staging
  - `git` not in PATH → exception, staging cleaned up
- idempotent: a second call on an already-initialised path must *detect*
  it and either skip (returning existing commit) or refuse explicitly —
  we pick the variant that produces the least surprise: explicit refusal,
  because a silent skip on an existing repo could overwrite the user's
  in-flight branch without warning.

Out of scope for these tests: `gh` (card #2), `.claude/`-seeding (#4),
project-registration via ProjectService (#3 in the chain), and CI-bootstrap
(facet D). The service returns (path, first_commit_sha) and stops there.
"""
from __future__ import annotations

import subprocess

import pytest

from app.services.repo_bootstrap import (
    InitResult,
    RepoAlreadyInitializedError,
    RepoBootstrapError,
    RepoBootstrapService,
)

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_init_local_creates_gitignore_and_readme_and_first_commit(tmp_path):
    target = tmp_path / "my-project"

    result = RepoBootstrapService().init_local(
        str(target), project_name="my-project"
    )

    # Returns the right shape
    assert isinstance(result, InitResult)
    assert result.path == str(target.resolve())
    assert len(result.first_commit_sha) == 40
    assert result.first_commit_sha == result.first_commit_sha.lower()  # full sha, hex

    # Visible end-state on disk
    assert (target / ".git").is_dir()
    assert (target / ".gitignore").is_file()
    assert (target / "README.md").is_file()

    # Default profile ignores the usual suspects
    gitignore = (target / ".gitignore").read_text()
    assert "__pycache__/" in gitignore
    assert "node_modules/" in gitignore

    # README carries the project name
    assert "my-project" in (target / "README.md").read_text()

    # First commit is on `main` with the expected message
    head = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    assert head.stdout.strip() == "main"

    log = subprocess.run(
        ["git", "-C", str(target), "log", "--oneline", "--no-decorate"],
        capture_output=True, text=True, check=True,
    )
    assert "chore: bootstrap my-project" in log.stdout

    # The commit we returned is actually HEAD
    sha = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    assert sha.stdout.strip() == result.first_commit_sha

    # No leftover staging directory on disk
    assert not (target.with_name(target.name + ".staging-")).exists()
    # And no other stragglers at the parent level
    siblings = [p.name for p in tmp_path.iterdir()]
    assert all(not s.startswith(target.name + ".staging-") for s in siblings)


def test_init_local_fails_clean_when_parent_does_not_exist(tmp_path):
    """Parent not present → service must refuse; no half-created target, no
    staging dir."""
    target = tmp_path / "nope" / "deep" / "child"
    assert not target.parent.exists()

    with pytest.raises((RepoBootstrapError, FileNotFoundError, FileExistsError)):
        RepoBootstrapService().init_local(str(target), project_name="x")

    assert not target.exists()
    assert not target.parent.exists()


def test_init_local_refuses_when_target_already_exists(tmp_path):
    """The card calls out idempotency: pick the variant with the least
    surprise. We refuse explicitly (raise RepoAlreadyInitializedError)
    so a silent overwrite of the user's in-flight work is impossible."""
    target = tmp_path / "p"
    target.mkdir()
    (target / "preexisting.txt").write_text("user data")

    with pytest.raises(RepoAlreadyInitializedError):
        RepoBootstrapService().init_local(str(target), project_name="p")

    # User data must be untouched
    assert (target / "preexisting.txt").read_text() == "user data"
    # And we must NOT have created a .git/ inside the existing dir
    assert not (target / ".git").exists()


def test_init_local_refuses_when_target_already_has_git_dir(tmp_path):
    """Even when the directory is otherwise empty, an existing `.git/`
    is a strong signal "this is already a repo" — refuse rather than
    silently reinitialise."""
    target = tmp_path / "p"
    target.mkdir()
    subprocess.run(["git", "init"], cwd=str(target), check=True, capture_output=True)

    with pytest.raises(RepoAlreadyInitializedError):
        RepoBootstrapService().init_local(str(target), project_name="p")


def test_init_local_cleans_up_staging_when_git_not_in_path(tmp_path, monkeypatch):
    """When `git` is missing, the service raises and the staging directory
    must not be left behind."""
    target = tmp_path / "p"

    # Make `git` invisible to subprocess by patching PATH.
    monkeypatch.setenv("PATH", "")

    with pytest.raises(RepoBootstrapError):
        RepoBootstrapService().init_local(str(target), project_name="p")

    # No target, no staging, no leftovers at parent level
    assert not target.exists()
    siblings = [p.name for p in tmp_path.iterdir()]
    assert not any(s.startswith("p.staging-") for s in siblings), siblings


def test_init_local_returns_full_hex_sha_not_abbreviated(tmp_path):
    target = tmp_path / "p"
    result = RepoBootstrapService().init_local(str(target), project_name="p")
    # 40 hex chars for SHA-1
    assert len(result.first_commit_sha) == 40
    int(result.first_commit_sha, 16)  # parses as hex


def test_init_local_supports_python_gitignore_profile(tmp_path):
    target = tmp_path / "p"
    RepoBootstrapService().init_local(
        str(target), project_name="p", gitignore_profile="python"
    )
    text = (target / ".gitignore").read_text()
    assert "__pycache__/" in text
    assert "*.py[cod]" in text
    # node_modules is a default-profile artefact; the python profile is
    # tighter, so it should be absent.
    assert "node_modules/" not in text


def test_init_local_unknown_profile_falls_back_to_default(tmp_path):
    """Unknown profile is a non-fatal choice — we still want to succeed,
    we just use the default profile and record the fallback in the result."""
    target = tmp_path / "p"
    result = RepoBootstrapService().init_local(
        str(target), project_name="p", gitignore_profile="nonexistent-profile",
    )
    assert result.gitignore_profile_used == "default"
    assert (target / ".gitignore").is_file()


def test_init_local_uses_dummy_identity_when_repo_has_none(tmp_path, monkeypatch):
    """The first commit needs a user.email/user.name. We set a per-call
    dummy identity inside the staging repo so the commit never depends on
    the caller's global git config."""
    # Strip any inherited git identity from the test runner.
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
              "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    home_gitconfig = tmp_path / "home.gitconfig"
    monkeypatch.setenv("HOME", str(tmp_path))
    # Point git at an empty global config to neutralise any host-wide one.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home_gitconfig))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(home_gitconfig))

    target = tmp_path / "p"
    RepoBootstrapService().init_local(str(target), project_name="p")

    # The local repo's config is what got used; verify the dummy identity
    # is set on the new repo (not inherited from anywhere else).
    cfg = subprocess.run(
        ["git", "-C", str(target), "config", "--get", "user.email"],
        capture_output=True, text=True, check=True,
    )
    assert cfg.stdout.strip()  # non-empty dummy