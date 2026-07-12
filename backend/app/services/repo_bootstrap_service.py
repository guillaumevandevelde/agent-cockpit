"""Optional GitHub-remote creation step in the repo-bootstrap chain.

Takes a locally initialised repo (from the atomic-init step) and, when GitHub
auth is available, creates the matching remote via ``gh repo create``. On
success the project's ``KanbanMeta`` keys are migrated from the pre-remote
``slug:<name>`` key to the post-remote ``git:<host>/<path>`` key so autodispatch
(and the other per-project flags: shipmode / skip_permissions / transport) keep
pointing at the same project.

This service only orchestrates the ``gh`` call and delegates the KanbanMeta
rename to an injected ``migrate_keys`` callable (the real
``migrate_project_keys`` helper lives in a sibling card) — it never writes
``KanbanMeta`` directly. A failed remote creation is non-fatal: the local repo
stays usable, it just has no remote yet.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.kanban.project_key import _slug, normalize_remote

logger = logging.getLogger(__name__)

MigrateKeysFn = Callable[[str, str], Awaitable[object]]


class BootstrapRemoteCreationError(RuntimeError):
    """``gh repo create`` failed (network/auth error or repo-name collision).

    The local repo is intentionally left intact — a failed remote creation only
    means "no remote right now", not "roll back the local repo".
    """


@dataclass(frozen=True)
class CreateRemoteResult:
    created: bool
    reason: str | None = None
    remote_url: str | None = None
    new_key: str | None = None


class RepoBootstrapService:
    def __init__(self, *, migrate_keys: MigrateKeysFn | None = None) -> None:
        self._migrate_keys = migrate_keys

    async def create_remote(
        self, local_path: str, *, repo_name: str, visibility: str = "private"
    ) -> CreateRemoteResult:
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
