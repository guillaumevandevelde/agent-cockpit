"""DeployTarget abstraction + GHCR MVP.

A ``DeployTarget`` knows how to take a project tree on disk and turn it
into something an operator can pull + run. The MVP implements exactly
one target: ``GHCRDeployTarget``, which builds an OCI image with
``docker buildx build --push`` and pushes it to ``ghcr.io``.

Out of scope for this MVP (per kanban card):

* runtime provisioning — "deploy" here means "the image exists in
  the registry", NOT "a container is running somewhere";
* cloud-provider deploys (Vercel / Fly / AWS / GCP);
* DNS / domain wiring;
* cost governance.

The interface is intentionally narrow — ``deploy(project_path, tag,
*, credentials) -> DeployResult`` — so future targets (EcrDeployTarget,
FlyDeployTarget, …) plug in without breaking the API surface.

Credentials flow: callers pass a ``credentials`` dict (``{"ghcr_token":
"…"}`` for the GHCR target). When no explicit token is given, the
target falls back to ``gh auth token`` so a developer who has already
run ``gh auth login`` doesn't need to repeat the gesture.

Audit: every ``deploy_start`` / ``deploy_complete`` event is recorded
through ``_record_audit``. Today that hook is a structured log line,
mirroring the stub in ``app.services.runs.spawn._record_audit``; the
real ``security_audit`` row write lands with follow-up #10.

See ``docs/features/deploy.md`` for the threat model + recovery
procedure (rollback = push an older tag).
"""
from __future__ import annotations

import abc
import asyncio
import logging
import re
import shlex
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


# -- status & result --------------------------------------------------------


class DeployStatus(StrEnum):
    """Lifecycle states for a single deploy.

    ``str`` mixin so the values serialise straight to JSON without a
    custom encoder. The ordering matches the call order: a deploy is
    created (``pending``), starts ``building``, transitions to
    ``pushing`` (for the GHCR target, these two overlap inside the
    single ``docker buildx`` call), then lands on ``completed`` or
    ``failed``.
    """

    PENDING = "pending"
    BUILDING = "building"
    PUSHING = "pushing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class DeployResult:
    """Outcome of a single ``deploy()`` invocation.

    The dataclass is mutable on purpose — the deploy service flips
    ``status`` as it moves through the lifecycle, so a future
    streaming API (e.g. WebSocket log tail) can subscribe to the same
    object without a re-implementation.
    """

    status: DeployStatus
    image_ref: str | None
    logs: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise for the REST response.

        ``started_at`` / ``completed_at`` are returned as ISO-8601 so
        the frontend can ``new Date(...)`` them without a custom
        parser.
        """
        return {
            "status": self.status.value,
            "image_ref": self.image_ref,
            "logs": self.logs,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "error": self.error,
        }


# -- abstract interface -----------------------------------------------------


class DeployTarget(abc.ABC):
    """A deploy target takes a project tree + image tag and produces an
    image_ref at some registry / runtime.

    Implementations must be safe to call concurrently for *distinct*
    ``project_path`` values; concurrency on a single path is the
    implementation's problem (the MVP doesn't enforce this — push
    conflicts land on the operator).
    """

    id: str  # subclasses set this to the registry key, e.g. "ghcr"

    @abc.abstractmethod
    async def deploy(
        self,
        project_path: str,
        tag: str,
        *,
        credentials: dict[str, str] | None = None,
    ) -> DeployResult:
        """Build (and push / upload) ``project_path`` under ``tag``.

        ``credentials`` carries provider-specific secrets. The MVP
        recognises ``"ghcr_token"`` for ``GHCRDeployTarget``; other
        targets will define their own keys. A ``None`` or empty dict
        triggers the target's environment-based fallback (e.g.
        ``gh auth token``).

        Must always return a ``DeployResult``; failures land on
        ``status=FAILED`` with ``error`` populated rather than
        raising, so the REST handler can serialise uniformly.
        """


# -- audit log --------------------------------------------------------------
#
# Mirrors ``app.services.runs.spawn._record_audit``: today a structured
# log line, replaced by a real ``security_audit`` row write with
# follow-up #10. The contract is the same: callable, never raises,
# var *names* are the only stable identifier (no secret values).

_AUDIT_LOG = logging.getLogger(f"{__name__}.audit")


def _record_audit(
    event: str,
    *,
    target_id: str,
    project_path: str,
    image_ref: str | None,
    status: str | None,
    error: str | None = None,
) -> None:
    """Emit a structured audit line for a deploy lifecycle event.

    ``image_ref`` is included because it's the resource identifier
    (not a secret); ``error`` is the human-readable failure message
    but never includes credentials. The auth token is never logged.
    """
    _AUDIT_LOG.info(
        "deploy_audit event=%s target=%s project=%s image_ref=%s status=%s error=%s",
        event,
        target_id,
        project_path,
        image_ref or "-",
        status or "-",
        error or "-",
    )


# -- GHCR target (MVP) ------------------------------------------------------


# Match ``github.com:<owner>/<repo>`` (ssh) and ``https://github.com/<owner>/<repo>``
# (https). The trailing ``.git`` is optional. We deliberately don't
# accept arbitrary git hosts — ghcr.io is GitHub-only — so we *fail*
# on non-GitHub remotes rather than guessing.
_GH_HOSTS = ("github.com",)
_GH_REMOTE_RE = re.compile(
    r"^(?:https://github\.com/|git@github\.com:)(?P<owner>[A-Za-z0-9._-]+)/"
    r"(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?$"
)


class GHCRDeployTarget(DeployTarget):
    """Build + push an OCI image to ``ghcr.io``.

    The MVP invokes ``docker buildx build --push --tag <image_ref>
    <project_path>``. Authentication comes from an explicit
    ``ghcr_token`` in ``credentials`` (e.g. read from
    ``app.services.secrets_store``), with a fallback to ``gh auth
    token`` for developers who already have the gh CLI logged in.

    ``project_path`` is the *context* for ``docker buildx`` — the
    directory is passed verbatim; we do not inspect it for a
    Dockerfile (buildx does, with its default name lookup).

    Owner / repo are inferred from ``git remote get-url origin``; a
    project without a GitHub remote is rejected with a clear error
    rather than pushed to the wrong place.
    """

    id = "ghcr"

    # Tag constraints are deliberately permissive — docker accepts a
    # much wider set than this regex, but we enforce *some* shape so
    # a typo can't accidentally push an empty tag.
    _TAG_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

    async def _run(
        self, cmd: list[str], *, cwd: str | None = None
    ) -> tuple[int, str, str]:
        """Run ``cmd`` to completion and return ``(returncode, stdout, stderr)``.

        Extracted so tests can patch it with an ``AsyncMock`` without
        having to fake ``asyncio.create_subprocess_exec``. The real
        implementation captures both streams so logs land on the
        ``DeployResult`` regardless of outcome.
        """
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout_b, stderr_b = await proc.communicate()
        return (
            proc.returncode if proc.returncode is not None else -1,
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
        )

    async def _docker_login(
        self, *, username: str, token: str
    ) -> tuple[int, str, str]:
        """Run ``docker login ghcr.io`` with the token on stdin.

        Split into its own method so tests don't have to fake stdin
        pipelining. The token is fed via ``stdin`` (``--password-stdin``)
        so it never appears in the process argv / ``ps`` output.
        Returns ``(returncode, stdout, stderr)`` — the same shape as
        ``_run`` so the caller can log uniformly.
        """
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "login",
            "ghcr.io",
            "-u",
            username,
            "--password-stdin",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await proc.communicate(input=token.encode("utf-8"))
        return (
            proc.returncode if proc.returncode is not None else -1,
            stdout_b.decode("utf-8", errors="replace"),
            stderr_b.decode("utf-8", errors="replace"),
        )

    async def deploy(
        self,
        project_path: str,
        tag: str,
        *,
        credentials: dict[str, str] | None = None,
    ) -> DeployResult:
        if not self._TAG_RE.match(tag):
            return DeployResult(
                status=DeployStatus.FAILED,
                image_ref=None,
                error=f"invalid tag {tag!r}: must match {self._TAG_RE.pattern}",
            )

        creds = credentials or {}

        # Probe the git remote first — without it we can't build the
        # image_ref and there's nothing useful to push.
        rc, stdout, stderr = await self._run(
            ["git", "remote", "get-url", "origin"],
            cwd=project_path,
        )
        probe_logs = (
            f"[probe] git remote get-url origin\n[probe-stdout]\n{stdout}\n"
            f"[probe-stderr]\n{stderr}\n"
        )
        if rc != 0:
            result = DeployResult(
                status=DeployStatus.FAILED,
                image_ref=None,
                logs=probe_logs,
                error=(
                    f"no git remote found at {project_path!r}: "
                    f"`git remote get-url origin` exited {rc}"
                ),
            )
            result.completed_at = datetime.now(UTC)
            _record_audit(
                "deploy_complete",
                target_id=self.id,
                project_path=project_path,
                image_ref=None,
                status="failed",
                error=result.error,
            )
            return result

        remote_url = stdout.strip()
        match = _GH_REMOTE_RE.match(remote_url)
        if not match:
            # Detect host explicitly so the error message can name it
            # ("gitlab", "bitbucket", "file://", etc).
            host = _extract_host(remote_url) or "unknown"
            result = DeployResult(
                status=DeployStatus.FAILED,
                image_ref=None,
                logs=probe_logs,
                error=(
                    f"remote {remote_url!r} is not a GitHub origin (host={host!r}); "
                    f"GHCR is GitHub-only"
                ),
            )
            result.completed_at = datetime.now(UTC)
            _record_audit(
                "deploy_complete",
                target_id=self.id,
                project_path=project_path,
                image_ref=None,
                status="failed",
                error=result.error,
            )
            return result

        owner = match.group("owner")
        repo = match.group("repo")
        image_ref = f"ghcr.io/{owner}/{repo}:{tag}"

        # Resolve credentials: explicit ``ghcr_token`` wins; else
        # fall back to ``gh auth token``; else fail with a clear
        # message that points the operator at the two options.
        token = (creds.get("ghcr_token") or "").strip()
        token_source = "credentials"
        if not token:
            rc, stdout, stderr = await self._run(["gh", "auth", "token"])
            probe_logs += (
                f"[probe] gh auth token\n[probe-stdout]\n{stdout}\n"
                f"[probe-stderr]\n{stderr}\n"
            )
            if rc == 0 and stdout.strip():
                token = stdout.strip()
                token_source = "gh_cli"

        if not token:
            result = DeployResult(
                status=DeployStatus.FAILED,
                image_ref=image_ref,
                logs=probe_logs,
                error=(
                    "no GHCR credentials: provide credentials.ghcr_token "
                    "or run `gh auth login`"
                ),
            )
            result.completed_at = datetime.now(UTC)
            _record_audit(
                "deploy_complete",
                target_id=self.id,
                project_path=project_path,
                image_ref=image_ref,
                status="failed",
                error=result.error,
            )
            return result

        _record_audit(
            "deploy_start",
            target_id=self.id,
            project_path=project_path,
            image_ref=image_ref,
            status="building",
        )

        # docker buildx: build + push in one invocation. We log in
        # first via ``docker login ghcr.io`` using the resolved
        # token so the push is authenticated without depending on
        # the developer's local docker config.
        login_rc, login_out, login_err = await self._docker_login(
            username=owner, token=token
        )
        # Scrub the token from any echoed login output — never leave
        # a secret in the deploy log.
        login_out = login_out.replace(token, "[REDACTED]")
        login_err = login_err.replace(token, "[REDACTED]")

        login_logs = (
            f"[docker-login] ghcr.io (via {token_source})\n"
            f"[docker-login-stdout]\n{login_out}\n"
            f"[docker-login-stderr]\n{login_err}\n"
        )
        if login_rc != 0:
            result = DeployResult(
                status=DeployStatus.FAILED,
                image_ref=image_ref,
                logs=probe_logs + login_logs,
                error=f"docker login ghcr.io failed (exit {login_rc}): {login_err.strip()}",
            )
            result.completed_at = datetime.now(UTC)
            _record_audit(
                "deploy_complete",
                target_id=self.id,
                project_path=project_path,
                image_ref=image_ref,
                status="failed",
                error=result.error,
            )
            return result

        # ``docker buildx build --push`` combines the build + push in
        # one command; for the MVP we don't separate them in the
        # ``status`` field (BUILDING / PUSHING are both emitted as
        # one continuous log) — the API exposes BUILDING as the
        # "in-flight" state and COMPLETED / FAILED as the terminal.
        build_cmd = [
            "docker",
            "buildx",
            "build",
            "--push",
            "--tag",
            image_ref,
            project_path,
        ]
        rc, stdout, stderr = await self._run(build_cmd)
        build_logs = (
            f"[docker-buildx] {shlex.join(build_cmd)}\n"
            f"[docker-buildx-stdout]\n{stdout}\n"
            f"[docker-buildx-stderr]\n{stderr}\n"
        )
        # Defensive scrub: docker echoes the image tag and image_ref
        # don't include secrets, but the registry might re-echo the
        # token in some failure modes — strip it just in case.
        build_logs = build_logs.replace(token, "[REDACTED]")

        result = DeployResult(
            status=DeployStatus.COMPLETED if rc == 0 else DeployStatus.FAILED,
            image_ref=image_ref,
            logs=probe_logs + login_logs + build_logs,
        )
        if rc != 0:
            tail = (stderr or stdout).strip().splitlines()[-5:]
            tail_text = "\n".join(tail) if tail else "(no output captured)"
            result.error = (
                f"docker buildx build --push failed (exit {rc}): {tail_text}"
            )
        result.completed_at = datetime.now(UTC)
        _record_audit(
            "deploy_complete",
            target_id=self.id,
            project_path=project_path,
            image_ref=image_ref,
            status=result.status.value,
            error=result.error,
        )
        return result


def _extract_host(url: str) -> str | None:
    """Best-effort host extraction for error messages.

    Handles ssh-style (``git@host:path``) and https-style
    (``scheme://host/...``) URLs. Returns ``None`` if neither
    matches — the caller treats that as "unrecognised".
    """
    if url.startswith(("git@", "ssh://")):
        # ``git@host:path`` → split on ``@`` then on ``:``.
        after_at = url.split("@", 1)[-1]
        return after_at.split(":", 1)[0] or None
    if "://" in url:
        after_scheme = url.split("://", 1)[-1]
        return after_scheme.split("/", 1)[0] or None
    return None


# -- registry ---------------------------------------------------------------


@dataclass(frozen=True)
class TargetInfo:
    """Metadata returned by ``list_targets()``.

    Lightweight pydantic-free view-model so the API layer can serialise
    it without a custom encoder. ``target_type`` is the class name so
    the UI can colour-code targets without an enum.
    """

    id: str
    target_type: str


_TARGET_REGISTRY: dict[str, DeployTarget] = {
    GHCRDeployTarget().id: GHCRDeployTarget(),
}


def list_targets() -> list[TargetInfo]:
    """Return the sorted registry of available deploy targets.

    Sorted by id so the UI doesn't flicker on registration order
    changes.
    """
    return [
        TargetInfo(id=t.id, target_type=type(t).__name__)
        for t in sorted(_TARGET_REGISTRY.values(), key=lambda x: x.id)
    ]


def get_target(target_id: str) -> DeployTarget:
    """Look up a target by id.

    Raises ``KeyError`` for unknown ids — the API layer translates
    that to a 404.
    """
    try:
        return _TARGET_REGISTRY[target_id]
    except KeyError as e:
        raise KeyError(f"unknown deploy target {target_id!r}") from e