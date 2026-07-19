"""RepoBootstrapService — atomic mkdir + git init + first commit + .gitignore + README-stub,
plus the optional gh-remote + key-migration step.

The atomic ground-stone of facet B (see `docs/cockpit/repo-provisioning-bootstrap.md`
§3.1). The service materialises a *local* git repo at a target path —
``.git/``, ``.gitignore``, ``README.md``, and one bootstrap commit on
``main`` — and then optionally promotes it to a GitHub-hosted remote via
``gh repo create``, migrating the project's ``KanbanMeta`` keys from
``slug:<basename>`` to ``git:<host>/<path>`` so autodispatch / shipmode /
skip_permissions / transport meta keep pointing at the same project.

Higher-level siblings (``InceptionService``, ``BlueprintApply``
orchestration) compose this service to assemble full projects.

Atomicity is implemented with a **staging directory** under
``<target>.staging-<uuid>``: every write happens inside the staging
directory first, and only when every step succeeded do we ``mv`` the
staging tree onto ``<target>``. A failure in any step ``rm -rf``'s the
staging tree so the caller's filesystem is never left half-written.

Public surface (MVP)::

    svc = RepoBootstrapService(migrate_keys=...)     # both methods now share a class
    svc.init_local(path, *, project_name, gitignore_profile="default") -> InitResult
    await svc.create_remote(local_path, *, repo_name, visibility="private") -> CreateRemoteResult

``InitResult`` carries ``path`` (resolved), ``first_commit_sha`` (full
hex SHA-1), and ``gitignore_profile_used`` (the resolved profile — handy
when the caller passes an unknown profile name and we silently fall
back).

``CreateRemoteResult`` carries ``created`` (bool), ``reason`` (None on
success, ``"gh_missing_or_unauthed"`` on graceful no-op), ``remote_url``,
and ``new_key``.

Out of scope for this module:
- ``.claude/``-seeding (kanban card sibling #4 — BlueprintApplyEngine)
- template scaffolding (sibling #3 — TemplateService)
- ``ProjectService`` registry-row creation
- CI-bootstrap (facet D)
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.kanban.project_key import _slug, normalize_remote
from app.services.bootstrap_policy import BootstrapPolicy, render_license

logger = logging.getLogger(__name__)

MigrateKeysFn = Callable[[str, str], Awaitable[object]]


# ---------------------------------------------------------------------------
# Dummy identity + .gitignore profile catalog
# ---------------------------------------------------------------------------


# A dummy identity used only when the caller hasn't configured one at
# repo-scope (and there's no inherited global config). The first commit
# needs *some* name + email; we use a neutral placeholder so the bootstrap
# commit lands cleanly without us touching the user's global git config.
DUMMY_GIT_USER_NAME = "Repo Bootstrap"
DUMMY_GIT_USER_EMAIL = "repo-bootstrap@localhost"


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


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RepoBootstrapError(Exception):
    """Base class for RepoBootstrapService errors."""


class RepoAlreadyInitializedError(RepoBootstrapError):
    """Raised when the target path already exists or already has ``.git/``.

    The card calls out idempotency as a design choice: pick the variant
    with the least surprise. Silent overwrite of an in-flight repo would
    be the worst of the options, so we refuse loudly and the caller can
    ``remove`` the target themselves if they really want a clean slate.
    """


class BootstrapRemoteCreationError(RuntimeError):
    """``gh repo create`` failed (network/auth error or repo-name collision).

    The local repo is intentionally left intact — a failed remote creation only
    means "no remote right now", not "roll back the local repo".
    """


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class InitResult:
    """Return value of `RepoBootstrapService.init_local`."""

    path: str
    first_commit_sha: str
    gitignore_profile_used: str


@dataclass(frozen=True)
class CreateRemoteResult:
    created: bool
    reason: str | None = None
    remote_url: str | None = None
    new_key: str | None = None


# ---------------------------------------------------------------------------
# Unified service
# ---------------------------------------------------------------------------


class RepoBootstrapService:
    """Single-class facade over the local + remote bootstrap steps.

    Previously the local-init step (kanban card §6 #1) and the
    gh-remote-step (§6 #2) lived in two separate ``RepoBootstrapService``
    classes in two different files — the "atomic-init" half in
    ``repo_bootstrap.py`` and the "remote-creation" half in
    ``repo_bootstrap_service.py``. Unifying them lets a single
    orchestrator (e.g. ``InceptionService``) call ``init_local`` and
    ``create_remote`` on the same instance, sharing any future
    policy- or cache-level state if we add it.

    Stateless today — every method takes a path string and returns a
    fresh result. The service is safe to construct once and call
    repeatedly from different code paths.

    Constructor knobs:
      - ``migrate_keys``: optional awaitable ``(old, new) -> …`` for the
        post-``gh repo create`` key rename. Sibling §6 #7's
        ``migrate_project_keys`` is the canonical caller.
      - ``git_executable``: ``"git"`` by default; override only in tests
        that want to exercise the missing-git branch without
        monkey-patching ``subprocess``.
    """

    def __init__(
        self,
        *,
        migrate_keys: MigrateKeysFn | None = None,
        git_executable: str = "git",
    ) -> None:
        self._migrate_keys = migrate_keys
        self.git_executable = git_executable

    # ------------------------------------------------------------------
    # init_local — atomic mkdir + git init + first commit + .gitignore + README
    # ------------------------------------------------------------------

    def init_local(
        self,
        path: str,
        *,
        project_name: str,
        gitignore_profile: str = "default",
        policy: BootstrapPolicy | None = None,
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
            self._write_license(staging, policy, project_name)
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

    # ------------------------------------------------------------------
    # create_remote — gh repo create + KanbanMeta key-migration
    # ------------------------------------------------------------------

    async def create_remote(
        self, local_path: str, *, repo_name: str, visibility: str = "private"
    ) -> CreateRemoteResult:
        """Add a GitHub remote to a locally-init'd repo and migrate meta keys.

        Steps:
            1. Check ``gh auth status`` — not logged in? Return
               ``CreateRemoteResult(created=False, reason="gh_missing_or_unauthed")``
               without raising.
            2. ``subprocess.run(["gh", "repo", "create", repo_name, f"--{visibility}",
               f"--source={local_path}", "--remote=origin", "--push"], check=True)``.
            3. Read ``git -C <local_path> remote get-url origin`` → new key
               ``git:<host>/<path>``.
            4. If ``slug:<basename(local_path)>`` ≠ ``git:<host>/<path>``,
               call the injected ``migrate_keys(old, new)`` (sibling #7's
               ``migrate_project_keys`` is the canonical implementation).
            5. Return ``CreateRemoteResult(created=True, remote_url=…, new_key=…)``.

        Failure-rollback: ``gh repo create`` exit ≠ 0 →
        ``BootstrapRemoteCreationError``; the local repo is left intact
        (atomic-init already succeeded in step 1 with its own staging-dir
        rollback). "No remote" ≠ "rollback"; the user can retry once
        ``gh auth login`` is done.
        """
        if not self._gh_available():
            logger.info(
                "gh unavailable or unauthed; user must `gh auth login` to add a remote"
            )
            return CreateRemoteResult(created=False, reason="gh_missing_or_unauthed")

        try:
            subprocess.run(
                [
                    "gh", "repo", "create", repo_name, f"--{visibility}",
                    f"--source={local_path}", "--remote=origin", "--push",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise BootstrapRemoteCreationError(
                f"gh repo create failed for {repo_name!r}: {exc.stderr or exc}"
            ) from exc

        remote_url = self._origin_url(local_path)
        old_key, new_key = self._derive_keys(local_path, remote_url)
        if old_key != new_key:
            await self._migrate(old_key, new_key)

        return CreateRemoteResult(created=True, remote_url=remote_url, new_key=new_key)

    # ------------------------------------------------------------------
    # init_local step helpers
    # ------------------------------------------------------------------

    def _git_init(self, staging: Path) -> None:
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

    def _write_gitignore(self, staging: Path, profile: str) -> None:
        """Write the resolved profile's gitignore body.

        Unknown profile names fall back to ``default`` rather than raising
        — we still want a successful bootstrap, and the caller can read
        the resolved profile off ``InitResult.gitignore_profile_used``.
        """
        body = _GITIGNORE_PROFILES.get(profile) or _GITIGNORE_PROFILES["default"]
        (staging / ".gitignore").write_text(body)

    def _write_readme(self, staging: Path, project_name: str) -> None:
        """Write ``README.md`` with a stub body carrying the project name."""
        body = (
            f"# {project_name}\n"
            "\n"
            f"Bootstrap scaffold for `{project_name}`. Edit this file to describe\n"
            "the project.\n"
        )
        (staging / "README.md").write_text(body)

    def _write_license(
        self, staging: Path, policy: BootstrapPolicy | None, project_name: str
    ) -> None:
        """Write ``LICENSE`` from ``policy`` (§1.6), or nothing when no policy.

        No policy means the caller opts out of policy-driven content entirely —
        we keep the historical behaviour of not shipping a LICENSE. A policy with
        ``license=None`` (proprietary) also writes no file.
        """
        if policy is None:
            return
        holder = policy.copyright_holder or DUMMY_GIT_USER_NAME
        body = render_license(
            policy, holder=holder, year=datetime.now(UTC).year
        )
        if body is not None:
            (staging / "LICENSE").write_text(body)

    def _configure_local_identity(self, staging: Path) -> None:
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

    def _initial_commit(self, staging: Path, project_name: str) -> str:
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

    # ------------------------------------------------------------------
    # create_remote helpers
    # ------------------------------------------------------------------

    def _derive_keys(self, local_path: str, remote_url: str) -> tuple[str, str]:
        old_key = f"slug:{_slug(os.path.basename(os.path.normpath(local_path)))}"
        new_key = f"git:{normalize_remote(remote_url)}"
        return old_key, new_key

    async def _migrate(self, old_key: str, new_key: str) -> None:
        if self._migrate_keys is None:
            raise RuntimeError(
                "RepoBootstrapService.create_remote needs a migrate_keys callable "
                "(migrate_project_keys) to migrate KanbanMeta keys after remote creation"
            )
        await self._migrate_keys(old_key, new_key)

    @staticmethod
    def _gh_available() -> bool:
        if shutil.which("gh") is None:
            return False
        try:
            result = subprocess.run(
                ["gh", "auth", "status"], capture_output=True, text=True
            )
        except OSError:
            return False
        return result.returncode == 0

    @staticmethod
    def _origin_url(local_path: str) -> str:
        result = subprocess.run(
            ["git", "-C", local_path, "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _resolve_target(path: str) -> Path:
    """Resolve ``path`` to an absolute Path.

    Mirrors the existing ``ProjectService.add_project`` shape (use
    ``Path(...).resolve()`` so ``~`` is expanded and relative paths are
    absolutised). We do NOT call ``os.path.realpath`` here — we want the
    *intended* path, not the post-symlink-following one, so the staging
    dir lives next to where the caller expects the final target.
    """
    return Path(path).expanduser().resolve()


def _new_staging_dir(parent: Path, target_name: str) -> Path:
    """Return a fresh ``<parent>/<target_name>.staging-<uuid>`` path.

    The uuid suffix ensures two concurrent bootstrap calls against the
    same parent don't collide on the staging path; a sibling card
    (single-user MVP, but cheap insurance) will need to consider what to
    do if the caller *deliberately* wants to recover a half-finished
    staging — for now we just ``rm -rf`` on failure so a stale one is
    always safe to delete.
    """

    return parent / f"{target_name}.staging-{uuid.uuid4().hex}"