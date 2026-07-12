"""RepoBootstrapService — atomic mkdir + git init + first commit + .gitignore + README-stub.

The atomic ground-stone of facet B (see `docs/cockpit/repo-provisioning-bootstrap.md`
§3.1). The service materialises a *local* git repo at a target path:
``.git/``, ``.gitignore``, ``README.md``, and one bootstrap commit on
``main``. Higher-level siblings (``InceptionService``, future
``BlueprintApply`` orchestration) compose this service to assemble full
projects.

Atomicity is implemented with a **staging directory** under
``<target>.staging-<uuid>``: every write happens inside the staging
directory first, and only when every step succeeded do we ``mv`` the
staging tree onto ``<target>``. A failure in any step ``rm -rf``'s the
staging tree so the caller's filesystem is never left half-written.

Public surface (MVP)::

    RepoBootstrapService().init_local(
        path, *, project_name, gitignore_profile="default",
    ) -> InitResult

``InitResult`` carries ``path`` (resolved), ``first_commit_sha`` (full
hex SHA-1), and ``gitignore_profile_used`` (the resolved profile — handy
when the caller passes an unknown profile name and we silently fall back).

Out of scope for this module:
- ``gh repo create`` + key-migration (kanban card sibling #2)
- ``.claude/``-seeding (kanban card sibling #4)
- template scaffolding (kanban card sibling #3)
- ``ProjectService`` registry-row creation (kanban card sibling #5)
- CI-bootstrap (facet D)
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# A dummy identity used only when the caller hasn't configured one at
# repo-scope (and there's no inherited global config). The first commit
# needs *some* name + email; we use a neutral placeholder so the bootstrap
# commit lands cleanly without us touching the user's global git config.
DUMMY_GIT_USER_NAME = "Repo Bootstrap"
DUMMY_GIT_USER_EMAIL = "repo-bootstrap@localhost"


class RepoBootstrapError(Exception):
    """Base class for RepoBootstrapService errors."""


class RepoAlreadyInitializedError(RepoBootstrapError):
    """Raised when the target path already exists or already has ``.git/``.

    The card calls out idempotency as a design choice: pick the variant
    with the least surprise. Silent overwrite of an in-flight repo would
    be the worst of the options, so we refuse loudly and the caller can
    ``remove`` the target themselves if they really want a clean slate.
    """


# Stock .gitignore profiles. The default profile is intentionally broad:
# it covers Python and Node, plus IDE/OS noise. Tighter profiles exist
# for projects that don't want the Node half (e.g. a pure-Python service).
_GITIGNORE_PROFILES: dict[str, str] = {
    "default": (
        "# Python\n"
        "__pycache__/\n"
        "*.py[cod]\n"
        "*$py.class\n"
        "*.so\n"
        ".Python/\n"
        "venv/\n"
        ".venv/\n"
        "env/\n"
        ".env\n"
        ".pytest_cache/\n"
        ".mypy_cache/\n"
        ".ruff_cache/\n"
        "*.egg-info/\n"
        "\n"
        "# Node\n"
        "node_modules/\n"
        "npm-debug.log*\n"
        "yarn-debug.log*\n"
        "yarn-error.log*\n"
        ".pnpm-debug.log*\n"
        "\n"
        "# IDEs / editors\n"
        ".idea/\n"
        ".vscode/\n"
        "*.swp\n"
        "*.swo\n"
        "*~\n"
        ".DS_Store\n"
        "\n"
        "# Build / dist\n"
        "build/\n"
        "dist/\n"
        "\n"
    ),
    "python": (
        "# Python\n"
        "__pycache__/\n"
        "*.py[cod]\n"
        "*$py.class\n"
        "*.so\n"
        ".Python/\n"
        "venv/\n"
        ".venv/\n"
        "env/\n"
        ".env\n"
        ".pytest_cache/\n"
        ".mypy_cache/\n"
        ".ruff_cache/\n"
        "*.egg-info/\n"
        "\n"
        "# IDEs / editors\n"
        ".idea/\n"
        ".vscode/\n"
        "*.swp\n"
        "*.swo\n"
        "*~\n"
        ".DS_Store\n"
        "\n"
    ),
    "minimal": (
        "__pycache__/\n"
        "node_modules/\n"
        ".DS_Store\n"
        "\n"
    ),
}


@dataclass
class InitResult:
    """Return value of `RepoBootstrapService.init_local`."""

    path: str
    first_commit_sha: str
    gitignore_profile_used: str


class RepoBootstrapService:
    """Stateless service — every method takes a path string and returns a
    fresh result. Cross-call state would only matter for a registry of
    known profiles, and the profile catalog is a module-level constant
    for now (sibling card sibling #5 will deliver a typed catalog).

    The service is safe to construct once and call repeatedly from
    different code paths; there's no shared mutable state.
    """

    def __init__(self, git_executable: str = "git"):
        # The `git_executable` knob exists primarily so tests can verify
        # the missing-git branch without monkey-patching subprocess. In
        # production we always use the literal "git" on PATH.
        self.git_executable = git_executable

    def init_local(
        self,
        path: str,
        *,
        project_name: str,
        gitignore_profile: str = "default",
    ) -> InitResult:
        """Atomically initialise a local git repo at ``path``.

        Steps (each guarded by the staging-dir pattern — see module docstring):
            1. ``mkdir -p <path>.staging-<uuid>``
            2. ``git init -b main`` inside the staging dir
            3. Write ``.gitignore`` from the resolved profile (fallback to
               ``default`` for unknown profile names)
            4. Write ``README.md`` with a stub body carrying the project name
            5. Configure a per-repo dummy identity (no global config touch)
            6. ``git add . && git commit -m "chore: bootstrap <name>"``
            7. ``mv`` the staging tree onto the final target path

        Returns `InitResult` with the resolved path + first commit SHA.

        Raises:
            RepoAlreadyInitializedError: target already exists or already
                has ``.git/``. Refuses loudly rather than silently
                overwriting — a silent skip could clobber the user's
                in-flight branch.
            RepoBootstrapError: any other bootstrap failure (parent
                missing, ``git`` missing, ``git init``/``commit`` exited
                non-zero, …). The staging dir is removed before raising.
        """
        target = _resolve_target(path)
        parent = target.parent

        # Refuse if the parent doesn't exist. The card calls this out
        # explicitly: "faalt als parent niet bestaat". Avoids creating a
        # deep nested chain as a side-effect — the caller is expected to
        # make sure their parent path is real first.
        if not parent.exists():
            raise RepoBootstrapError(
                f"parent directory {parent} does not exist; refusing to create "
                f"{target} (caller must ensure the parent path exists)"
            )
        if not os.access(str(parent), os.W_OK | os.X_OK):
            raise RepoBootstrapError(
                f"parent directory {parent} is not writable"
            )

        # Refuse loud-and-fast if the target already exists or is already
        # a git repo. The card's idempotency clause explicitly leaves
        # the choice to the implementer; we pick explicit refusal because
        # silent skip could overwrite user content.
        if target.exists():
            if (target / ".git").is_dir():
                raise RepoAlreadyInitializedError(
                    f"{target} already has a .git/ directory; refusing to "
                    f"re-initialise. Remove the directory if you really want "
                    f"a clean bootstrap."
                )
            raise RepoAlreadyInitializedError(
                f"{target} already exists; refusing to clobber. Remove the "
                f"directory if you want a clean bootstrap."
            )

        staging = _new_staging_dir(parent, target.name)

        try:
            self._git_init(staging)
            self._write_gitignore(staging, gitignore_profile)
            self._write_readme(staging, project_name)
            self._configure_local_identity(staging)
            sha = self._initial_commit(staging, project_name)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            logger.exception("repo bootstrap: staging failed at %s", staging)
            raise

        # Promote staging → final target. ``os.rename`` is atomic on POSIX
        # when source and dest are on the same filesystem, which they are
        # by construction (staging lives next to target).
        try:
            os.rename(staging, target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            logger.exception("repo bootstrap: rename %s → %s failed", staging, target)
            raise RepoBootstrapError(
                f"failed to promote staging dir {staging} → {target}"
            ) from None

        return InitResult(
            path=str(target),
            first_commit_sha=sha,
            gitignore_profile_used=(
                gitignore_profile
                if gitignore_profile in _GITIGNORE_PROFILES
                else "default"
            ),
        )

    # -- step helpers -------------------------------------------------------

    def _git_init(self, staging) -> None:  # type: ignore[no-untyped-def]
        """``git init -b main`` inside the staging dir."""
        try:
            subprocess.run(
                [self.git_executable, "init", "--initial-branch=main", str(staging)],
                capture_output=True, text=True, timeout=15, check=True,
            )
        except FileNotFoundError as exc:
            raise RepoBootstrapError(
                f"{self.git_executable!r} executable not found on PATH; cannot "
                f"bootstrap a git repo without git"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RepoBootstrapError(
                f"git init failed in {staging}: {exc.stderr.strip() or exc}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RepoBootstrapError(
                f"git init timed out in {staging}"
            ) from exc

    def _write_gitignore(self, staging, profile: str) -> None:  # type: ignore[no-untyped-def]
        """Write the resolved profile's gitignore body.

        Unknown profile names fall back to ``default`` rather than raising
        — we still want a successful bootstrap, and the caller can read
        the resolved profile off ``InitResult.gitignore_profile_used``.
        """
        body = _GITIGNORE_PROFILES.get(profile) or _GITIGNORE_PROFILES["default"]
        (staging / ".gitignore").write_text(body)

    def _write_readme(self, staging, project_name: str) -> None:  # type: ignore[no-untyped-def]
        """Write ``README.md`` with a stub body carrying the project name."""
        body = (
            f"# {project_name}\n"
            "\n"
            f"Bootstrap scaffold for `{project_name}`. Edit this file to describe\n"
            "the project.\n"
        )
        (staging / "README.md").write_text(body)

    def _configure_local_identity(self, staging) -> None:  # type: ignore[no-untyped-def]
        """Set ``user.name`` + ``user.email`` *inside* the new repo only.

        We deliberately do NOT touch ``--global`` config so the user's
        machine-wide identity stays theirs. If the caller has a real
        identity they want to use, they can ``git -C <path> config
        user.email`` after we return; for the bootstrap commit itself we
        just need *some* valid (name, email) pair.
        """
        try:
            subprocess.run(
                [self.git_executable, "-C", str(staging), "config", "user.name",
                 DUMMY_GIT_USER_NAME],
                capture_output=True, text=True, timeout=5, check=True,
            )
            subprocess.run(
                [self.git_executable, "-C", str(staging), "config", "user.email",
                 DUMMY_GIT_USER_EMAIL],
                capture_output=True, text=True, timeout=5, check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RepoBootstrapError(
                f"failed to set local git identity in {staging}: "
                f"{exc.stderr.strip() or exc}"
            ) from exc

    def _initial_commit(self, staging, project_name: str) -> str:  # type: ignore[no-untyped-def]
        """Stage everything in the staging dir, commit it, return the new HEAD sha."""
        try:
            subprocess.run(
                [self.git_executable, "-C", str(staging), "add", "."],
                capture_output=True, text=True, timeout=10, check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RepoBootstrapError(
                f"git add failed in {staging}: {exc.stderr.strip() or exc}"
            ) from exc

        message = f"chore: bootstrap {project_name}"
        # ``git -C <staging> commit`` (NOT passing ``--git-dir``/``--work-tree``)
        # — keeps cwd-resolution consistent and avoids tripping over inherited
        # GIT_DIR / GIT_WORK_TREE env vars from the host environment.
        try:
            subprocess.run(
                [self.git_executable, "-C", str(staging), "commit", "-m", message],
                capture_output=True, text=True, timeout=15, check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RepoBootstrapError(
                f"git commit failed in {staging}: {exc.stderr.strip() or exc}"
            ) from exc

        try:
            result = subprocess.run(
                [self.git_executable, "-C", str(staging), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5, check=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RepoBootstrapError(
                f"git rev-parse HEAD failed in {staging}: "
                f"{exc.stderr.strip() or exc}"
            ) from exc

        sha = result.stdout.strip()
        if len(sha) != 40 or not all(c in "0123456789abcdef" for c in sha):
            raise RepoBootstrapError(
                f"unexpected rev-parse output: {sha!r}"
            )
        return sha


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _resolve_target(path: str):
    """Resolve ``path`` to an absolute Path.

    Mirrors the existing ``ProjectService.add_project`` shape (use
    ``Path(...).resolve()`` so ``~`` is expanded and relative paths are
    absolutised). We do NOT call ``os.path.realpath`` here — we want the
    *intended* path, not the post-symlink-following one, so the staging
    dir lives next to where the caller expects the final target.
    """
    return Path(path).expanduser().resolve()


def _new_staging_dir(parent, target_name: str):
    """Return a fresh ``<parent>/<target_name>.staging-<uuid>`` path.

    The uuid suffix ensures two concurrent bootstrap calls against the
    same parent don't collide on the staging path; a sibling card
    (single-user MVP, but cheap insurance) will need to consider what to
    do if the caller *deliberately* wants to recover a half-finished
    staging — for now we just ``rm -rf`` on failure so a stale one is
    always safe to delete.
    """

    return parent / f"{target_name}.staging-{uuid.uuid4().hex}"