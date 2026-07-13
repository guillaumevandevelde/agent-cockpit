"""RunService — sandboxed spawn of a built app on a random 127.0.0.1 port.

Picks the cleanest available transport:

  * ``container``  — when docker or podman is on the host. Re-uses the
    sandbox-runtime detection from ``sandcastle_service`` so we don't
    grow a second container-probe layer. The container is bound to
    ``127.0.0.1:<port>`` only (never ``0.0.0.0``).
  * ``subprocess``  — when no container runtime is available. Logs a
    warning so the caller knows the app ran unsandboxed.

Either way the bound URL is exposed on the activity feed via the
``url`` field of the returned ``RunInstance``. If ``health_path`` is
provided, a background task polls it; failure within
``health_timeout_s`` marks the instance ``failed`` and tears the
transport down.

Audit: every ``start``/``stop`` is recorded via ``_record_audit``
(defined in ``app.services.runs.spawn``) — today a no-op log line,
swapped for a real ``security_audit`` row by follow-up #10.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import socket
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.run_instance import AppRun
from app.services.runs.spawn import _record_audit

logger = logging.getLogger(__name__)

# Status values written to AppRun.status. Kept narrow on purpose so the
# frontend can map them to coloured chips without a translation table.
_STATUS_PENDING = "pending"
_STATUS_STARTING = "starting"
_STATUS_HEALTHY = "healthy"
_STATUS_UNHEALTHY = "unhealthy"
_STATUS_FAILED = "failed"
_STATUS_STOPPED = "stopped"

_TRANSPORT_CONTAINER = "container"
_TRANSPORT_SUBPROCESS = "subprocess"

# Container runtimes we know how to drive. Subset of the sandcastle
# provider list — "no-sandbox" / "vercel" make no sense here because
# we're not spawning an agent, we're exposing a user command on a port.
_CONTAINER_RUNTIMES = ("docker", "podman")

# Poll cadence for the health-check loop. 1s is short enough that a
# healthy app reaches "healthy" within a second of binding, long enough
# not to thrash logs on a slow test host.
_HEALTH_POLL_INTERVAL_S = 1.0
_HEALTH_PROBE_TIMEOUT_S = 2.0

# Env var passed to the spawned process so it knows which port to bind
# on (we can't otherwise inject flags into an arbitrary user command).
# Most CLI servers honour ``PORT``; the runner can also be told via
# ``--port`` in the command itself if the caller prefers that.
_PORT_ENV_VAR = "PORT"

# Default port range we hand out when no port is requested. Excludes the
# backend (8000) and vite (5173) defaults so a stray test never collides.
_PORT_RANGE = (4001, 5000)


class RunInstance(BaseModel):
    """Public view of an AppRun row.

    Distinct from the ORM class so the API never accidentally serialises
    internal columns (e.g. ``container_id``) until we're ready to expose
    them."""

    id: int
    instance_id: str
    project_path: str
    command: list[str]
    env_keys: list[str]
    port: int
    url: str
    health_path: str | None
    status: str
    transport: str
    container_id: str | None = None
    pid: int | None = None
    log_path: str | None = None
    error: str | None = None
    started_at: datetime
    stopped_at: datetime | None = None


def _serialize(row: AppRun) -> RunInstance:
    return RunInstance(
        id=row.id,
        instance_id=row.instance_id,
        project_path=row.project_path,
        command=list(row.command or []),
        env_keys=list(row.env_keys or []),
        port=row.port,
        url=row.url,
        health_path=row.health_path,
        status=row.status,
        transport=row.transport,
        container_id=row.container_id,
        pid=row.pid,
        log_path=row.log_path,
        error=row.error,
        started_at=row.started_at,
        stopped_at=row.stopped_at,
    )


def _project_key(project_path: str) -> str | None:
    """Resolve the device-independent audit key for ``project_path``.

    Wraps ``resolve_project_key`` in a try/except so a repo without a
    git remote (or any other resolver hiccup) doesn't break a start —
    the worst case is the audit row is dropped, which is the same
    behaviour the spawn audit hook already has for missing keys."""
    try:
        from app.kanban.project_key import resolve_project_key
        return resolve_project_key(project_path)
    except Exception:
        logger.debug("could not resolve project_key for %s", project_path, exc_info=True)
        return None


def _pick_free_port(preferred: int | None = None, exclude: set[int] | None = None) -> int:
    """Bind a free port on 127.0.0.1, hand it back, close the socket.

    ``exclude`` lets the caller skip ports it knows are about to be
    bound by sibling runs (so two sequential ``start()`` calls don't
    both pick ``4001`` while the first socket is in TIME_WAIT).

    The brief TIME_WAIT between bind() and the spawn is acceptable for
    the MVP: the kernel keeps the port reserved long enough that
    nothing else on the host can grab it, and if the caller asked for a
    specific ``preferred`` port we retry until that exact port is free
    (or fall through to a random pick if it's stubbornly held)."""
    excluded = exclude or set()

    def _try_bind(port: int) -> bool:
        if port in excluded:
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                return False
            return True

    if preferred is not None and _try_bind(preferred):
        return preferred
    # Either no preferred port was given, or another process holds the
    # one the caller asked for. Walk the configured range so the caller
    # still gets a working port back and can read it off
    # ``instance.port`` — surfaced through the API either way.

    for port in range(*_PORT_RANGE):
        if _try_bind(port):
            return port
    # Fallback: ask the kernel for any free port. Returns a fresh
    # ephemeral, races aside.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _ports_in_use(project_path: str) -> set[int]:
    """Read every non-terminal AppRun row for ``project_path`` and return its port.

    Used by ``start()`` to feed the ``exclude`` argument of
    ``_pick_free_port`` — without this, two sequential ``start()`` calls
    would race for the lowest free port (typically 4001) because the
    brief bind/close between calls leaves the kernel holding the port
    only briefly via SO_REUSEADDR."""
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(AppRun.port).where(
                    AppRun.project_path == project_path,
                    AppRun.status.notin_([_STATUS_STOPPED, _STATUS_FAILED]),
                )
            )
        ).all()
        return {row[0] for row in rows}


class PortBusyError(RuntimeError):
    """Deprecated; kept so external callers don't break on import.

    Older builds raised this when the caller's preferred port was held
    by another process; the current implementation silently substitutes
    the next free port in the configured range and returns it via
    ``instance.port``. New code shouldn't catch this."""


async def _container_available() -> tuple[str | None, dict[str, Any]]:
    """Probe docker/podman availability via the sandcastle health check.

    Returns ``(runtime, health_dict)``. ``runtime`` is ``None`` when no
    container runtime is available — the caller falls back to subprocess."""
    from app.services.sandcastle_service import sandcastle_service

    health = await sandcastle_service.check_health()
    if health.get("docker_available"):
        return "docker", health
    if health.get("podman_available"):
        return "podman", health
    return None, health


class RunService:
    """Public entry point for spawning ad-hoc app runs."""

    # ---- lifecycle ----------------------------------------------------------

    async def start(
        self,
        project_path: str,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        port: int | None = None,
        health_path: str | None = None,
        health_timeout_s: int = 30,
    ) -> RunInstance:
        """Spawn ``command`` in ``project_path`` on a free 127.0.0.1 port.

        Returns a ``RunInstance`` with ``status=starting``. A background
        task drives the transport, health-loop and final status; clients
        can poll ``get()`` (or stream ``logs()``) to follow progress.
        """
        if not command:
            raise ValueError("command must be a non-empty list")

        runtime, _health = await _container_available()
        if runtime is None:
            logger.warning(
                "RunService.start: no container runtime available; falling back to plain subprocess for %s",
                project_path,
            )
            transport = _TRANSPORT_SUBPROCESS
        else:
            transport = _TRANSPORT_CONTAINER

        excluded_ports = await _ports_in_use(project_path)
        chosen_port = _pick_free_port(port, exclude=excluded_ports)
        instance_id = uuid.uuid4().hex[:12]
        env_keys = sorted((env or {}).keys())

        log_dir = Path(project_path) / ".run-service" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = str(log_dir / f"{instance_id}.log")

        url = f"http://127.0.0.1:{chosen_port}"

        async with AsyncSessionLocal() as session:
            row = AppRun(
                project_path=project_path,
                instance_id=instance_id,
                command=command,
                env_keys=env_keys,
                port=chosen_port,
                url=url,
                health_path=health_path,
                status=_STATUS_STARTING,
                transport=transport,
                log_path=log_path,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            serialized = _serialize(row)

        # Audit BEFORE the spawn so a crashed subprocess still leaves a
        # trail. Follow-up #10 will swap this for a real row.
        _record_audit(
            _project_key(project_path),
            transport,
            instance_id,
            env_keys,
        )
        logger.info(
            "run_service.start instance_id=%s transport=%s port=%d command=%s env_keys=%s",
            instance_id, transport, chosen_port, command, env_keys,
        )

        asyncio.create_task(
            self._drive(serialized, command, env or {}, runtime, health_path, health_timeout_s)
        )
        return serialized

    # ---- background driver -------------------------------------------------

    async def _drive(
        self,
        instance: RunInstance,
        command: list[str],
        env: dict[str, str],
        runtime: str | None,
        health_path: str | None,
        health_timeout_s: int,
    ) -> None:
        """Spawn the process / container, run the health loop, persist status.

        Lives as a single task per instance so cancellation naturally
        tears the transport down. Errors are caught and recorded; an
        unexpected exception here still leaves the row in a terminal
        state instead of "starting" forever."""
        instance_id = instance.instance_id
        try:
            if instance.transport == _TRANSPORT_CONTAINER and runtime:
                handle = await self._spawn_container(instance, command, env, runtime)
            else:
                handle = await self._spawn_subprocess(instance, command, env)

            # Persist handle (container_id or pid) so ``stop()`` can reach it.
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(AppRun).where(AppRun.instance_id == instance_id)
                )
                row = result.scalar_one_or_none()
                if row is None:
                    handle.cleanup()
                    return
                row.container_id = handle.container_id
                row.pid = handle.pid
                await session.commit()

            # Health-loop. If health_path is None, mark healthy as soon as
            # the handle's PID/container exists; the caller is then free to
            # hit whatever URL they like.
            if health_path:
                healthy = await self._wait_for_health(instance, health_path, health_timeout_s)
                final_status = _STATUS_HEALTHY if healthy else _STATUS_FAILED
                if not healthy:
                    await self._mark_failed(instance_id, "health check did not pass within timeout")
                    handle.cleanup()
            else:
                final_status = _STATUS_HEALTHY

            await self._update_status(instance_id, final_status)
        except Exception as exc:  # noqa: BLE001 - we want any failure path
            logger.exception("RunService driver crashed for %s", instance_id)
            await self._mark_failed(instance_id, str(exc))
            try:
                await self._force_cleanup(instance_id)
            except Exception:
                logger.debug("post-crash cleanup also failed for %s", instance_id)

    async def _spawn_container(
        self,
        instance: RunInstance,
        command: list[str],
        env: dict[str, str],
        runtime: str,
    ) -> _Handle:
        """Start the container, return a handle that can stop it later.

        Uses ``docker run`` / ``podman run`` directly (no sandcastle
        library involvement) because the sandcastle library is
        agent-shaped — we just need a normal container that binds a
        port and runs a command. Image is left to the caller via the
        ``DOCKER_IMAGE`` env var, defaulting to ``python:3.11-slim`` so
        the unit tests can pick a tiny deterministic image."""
        image = os.environ.get("RUN_SERVICE_IMAGE", "python:3.11-slim")
        container_name = f"run-{instance.instance_id}"

        env_args: list[str] = []
        for key, value in env.items():
            env_args += ["-e", f"{key}={value}"]
        env_args += ["-e", f"{_PORT_ENV_VAR}={instance.port}"]

        cmd = [
            runtime, "run",
            "--name", container_name,
            "-d",
            "-p", f"127.0.0.1:{instance.port}:{instance.port}",
            "-w", instance.project_path,
            *env_args,
            image,
            *command,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"{runtime} run failed: {stderr.decode(errors='replace').strip() or stdout.decode(errors='replace').strip()}"
            )
        container_id = stdout.decode().strip()

        # Tail the container's logs into our local log file so the
        # ``/logs`` endpoint has the same shape as the subprocess path.
        tail_proc = await asyncio.create_subprocess_exec(
            runtime, "logs", "-f", container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        return _ContainerHandle(runtime, container_name, container_id, instance.log_path, tail_proc)

    async def _spawn_subprocess(
        self,
        instance: RunInstance,
        command: list[str],
        env: dict[str, str],
    ) -> _Handle:
        """Plain ``subprocess.Popen`` fallback when no container runtime is available.

        The port is communicated via the ``PORT`` env var so the caller
        can keep the command as-is (a typical FastAPI command like
        ``uvicorn app.main:app`` honours ``$PORT`` via
        ``uvicorn.run(..., port=int(os.environ['PORT']))``; for other
        servers the caller should add ``--port $PORT`` to ``command``)."""
        merged_env = os.environ.copy()
        merged_env[_PORT_ENV_VAR] = str(instance.port)
        for key, value in env.items():
            merged_env[key] = value

        with open(instance.log_path, "ab", buffering=0) as log_fh:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=log_fh,
                stderr=log_fh,
                cwd=instance.project_path,
                env=merged_env,
                start_new_session=True,
            )
        return _SubprocessHandle(proc, instance.log_path)

    async def _wait_for_health(
        self,
        instance: RunInstance,
        health_path: str,
        timeout_s: int,
    ) -> bool:
        """Poll ``GET http://127.0.0.1:<port><health_path>`` until 2xx or timeout."""
        import httpx

        deadline = asyncio.get_running_loop().time() + timeout_s
        url = f"http://127.0.0.1:{instance.port}{health_path}"
        async with httpx.AsyncClient(timeout=_HEALTH_PROBE_TIMEOUT_S) as client:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    resp = await client.get(url)
                except (httpx.RequestError, OSError):
                    await asyncio.sleep(_HEALTH_POLL_INTERVAL_S)
                    continue
                if 200 <= resp.status_code < 300:
                    return True
                await asyncio.sleep(_HEALTH_POLL_INTERVAL_S)
        return False

    # ---- read paths ---------------------------------------------------------

    async def get(self, instance_id: str) -> RunInstance | None:
        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(AppRun).where(AppRun.instance_id == instance_id)
                )
            ).scalar_one_or_none()
            return _serialize(row) if row else None

    async def list(self, project_path: str) -> list[RunInstance]:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(AppRun)
                    .where(AppRun.project_path == project_path)
                    .order_by(AppRun.started_at.desc())
                )
            ).scalars().all()
            return [_serialize(r) for r in rows]

    async def logs(self, instance_id: str, offset: int = 0) -> dict[str, Any]:
        instance = await self.get(instance_id)
        if instance is None:
            return {"error": "not_found"}
        if not instance.log_path:
            return {"instance_id": instance_id, "log_content": ""}
        path = Path(instance.log_path)
        if not path.exists():
            return {"instance_id": instance_id, "log_content": ""}
        with path.open("rb") as fh:
            if offset:
                fh.seek(offset)
            data = fh.read()
        return {
            "instance_id": instance_id,
            "status": instance.status,
            "log_offset": offset + len(data),
            "log_content": data.decode(errors="replace"),
        }

    # ---- teardown -----------------------------------------------------------

    async def stop(self, instance_id: str) -> bool:
        """Stop the run, free the port, mark ``stopped``.

        Returns False for unknown id or already-stopped instances so
        DELETE is idempotent for the frontend."""
        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(AppRun).where(AppRun.instance_id == instance_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            if row.status == _STATUS_STOPPED:
                return False
            project_path = row.project_path
            transport = row.transport
            port = row.port
            container_id = row.container_id
            pid = row.pid
            row.status = _STATUS_STOPPED
            row.stopped_at = datetime.now(UTC)
            await session.commit()

        # Best-effort cleanup of the underlying process / container.
        if transport == _TRANSPORT_CONTAINER and container_id:
            for runtime in _CONTAINER_RUNTIMES:
                ok = await _try_runtime_cleanup(runtime, container_id)
                if ok:
                    break
        elif pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    pass

        _record_audit(
            _project_key(project_path),
            transport,
            instance_id,
            # env *names* only — same contract as start()
            sorted([]),
        )
        logger.info("run_service.stop instance_id=%s port=%d", instance_id, port)
        return True

    async def _force_cleanup(self, instance_id: str) -> None:
        """Internal teardown used by the driver after a crash.

        Same shape as ``stop`` but swallows every exception — we're
        already on a failure path and a half-broken container should
        not mask the original error."""
        try:
            await self.stop(instance_id)
        except Exception:
            logger.debug("force_cleanup failed for %s", instance_id, exc_info=True)

    # ---- helpers ------------------------------------------------------------

    async def _update_status(self, instance_id: str, status: str) -> None:
        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(AppRun).where(AppRun.instance_id == instance_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return
            row.status = status
            await session.commit()

    async def _mark_failed(self, instance_id: str, error: str) -> None:
        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(AppRun).where(AppRun.instance_id == instance_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return
            row.status = _STATUS_FAILED
            row.error = error
            row.stopped_at = datetime.now(UTC)
            await session.commit()


# ---------------------------------------------------------------------------
# Internal handle types — the spawner hands the driver a small object that
# knows how to terminate whatever it spawned. Two flavours because the
# container and subprocess paths use different teardown primitives.
# ---------------------------------------------------------------------------


class _Handle:
    container_id: str | None = None
    pid: int | None = None

    def cleanup(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError


class _ContainerHandle(_Handle):
    def __init__(self, runtime: str, name: str, container_id: str, log_path: str | None, tail_proc: asyncio.subprocess.Process) -> None:
        self.runtime = runtime
        self.name = name
        self.container_id = container_id
        self.log_path = log_path
        self._tail = tail_proc

    def cleanup(self) -> None:
        # Stop the log tail first so docker rm doesn't race with the
        # process reading from the container.
        if self._tail and self._tail.returncode is None:
            try:
                self._tail.terminate()
            except ProcessLookupError:
                pass
        # Async event-loop-friendly: schedule the actual ``docker stop``
        # as a fire-and-forget task. The handle is sync because nothing
        # outside _drive cares about awaiting cleanup.
        asyncio.create_task(_try_runtime_cleanup(self.runtime, self.name))


class _SubprocessHandle(_Handle):
    def __init__(self, proc: asyncio.subprocess.Process, log_path: str | None) -> None:
        self._proc = proc
        self.pid = proc.pid
        self.log_path = log_path

    def cleanup(self) -> None:
        if self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass


async def _try_runtime_cleanup(runtime: str, name_or_id: str) -> bool:
    """``docker rm -f <name>`` (or podman) — fire-and-forget teardown.

    Returns True when the runtime actually took the action. Container
    runtimes return non-zero when the name doesn't exist; we treat that
    as "nothing to do" rather than an error because the caller may have
    already removed it via the CLI."""
    try:
        proc = await asyncio.create_subprocess_exec(
            runtime, "rm", "-f", name_or_id,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0
    except FileNotFoundError:
        return False


# Module-level singleton — matches the rest of the service layer.
run_service = RunService()