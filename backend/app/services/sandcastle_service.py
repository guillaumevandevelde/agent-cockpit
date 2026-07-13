"""Sandcastle service: config CRUD and run orchestration."""
import asyncio
import json
import logging
import os
import signal
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.sandcastle import SandcastleConfig, SandcastleRun

logger = logging.getLogger(__name__)

# Path to the Node.js wrapper script
SCRIPT_DIR = Path(__file__).parent.parent.parent / "scripts"
RUNNER_SCRIPT = SCRIPT_DIR / "sandcastle_runner.mjs"

# Container providers that need a concrete image name.
_CONTAINER_PROVIDERS = {"docker", "podman"}
DEFAULT_DOCKER_IMAGE = "sandcastle:local"
# Every sandcastle-spawned container is named with this prefix (see the docker/podman
# sandbox providers in @ai-hero/sandcastle) — used both to filter `ps` and to refuse
# to tail logs for a container this feature didn't spawn.
_CONTAINER_NAME_PREFIX = "sandcastle-"

# Absolute wall-clock ceiling for a run, regardless of idle activity.
_OVERALL_TIMEOUT_FLOOR = 1800  # 30 min


def _overall_timeout(idle_timeout_seconds: int, max_iterations: int) -> int:
    """Absolute wall-clock ceiling for a run.

    The *idle* timeout is enforced inside the sandbox by sandcastle itself; the
    Python subprocess only needs a generous absolute ceiling so a wedged run can
    never hang forever. Using the idle timeout directly (the old behaviour) killed
    actively-working runs at the idle boundary, so we scale it up by iterations and
    apply a sane floor."""
    idle = idle_timeout_seconds or 600
    iterations = max(max_iterations or 1, 1)
    return max(idle * iterations * 4, _OVERALL_TIMEOUT_FLOOR)


def _resolve_docker_image(sandbox_provider: str, docker_image: str | None) -> str | None:
    """Default the image for container providers; leave it unset otherwise."""
    if sandbox_provider in _CONTAINER_PROVIDERS and not docker_image:
        return DEFAULT_DOCKER_IMAGE
    return docker_image


def _pick_default_sandbox_provider(health: dict[str, Any]) -> str:
    """Pick a sandbox provider for a freshly auto-created SandcastleConfig.

    The ORM column defaults to "no-sandbox", but auto-creating a config only
    happens when a project explicitly opts into the sandcastle transport —
    the whole point of which is container isolation. Prefer a real container
    runtime whenever the host actually has one available."""
    if health.get("docker_available"):
        return "docker"
    if health.get("podman_available"):
        return "podman"
    return "no-sandbox"


# Grace window between asking the runner to shut down (SIGTERM, which it turns into
# an AbortSignal so sandcastle can dispose the container) and force-killing it.
_TERMINATE_GRACE_SECONDS = 20


def _signal_process_group(process, sig) -> None:
    """Send `sig` to the runner's whole process group.

    The runner spawns docker/podman CLI children; signalling only the node PID would
    leave those dangling. The subprocess is launched with start_new_session=True so
    its PID is also the process-group id."""
    if process is None or process.returncode is not None:
        return
    try:
        os.killpg(os.getpgid(process.pid), sig)
    except (ProcessLookupError, PermissionError):
        try:
            process.send_signal(sig)
        except ProcessLookupError:
            pass


async def _terminate_gracefully(process) -> None:
    """SIGTERM the runner so it can dispose its container, then SIGKILL if it hangs.

    The runner translates SIGTERM into an AbortSignal, letting sandcastle tear the
    sandbox container down cleanly. A hard SIGKILL is the fallback when the runner
    doesn't exit within the grace window (and can still orphan a dockerd-managed
    container — the unavoidable case)."""
    if process is None or process.returncode is not None:
        return
    _signal_process_group(process, signal.SIGTERM)
    try:
        await asyncio.wait_for(process.wait(), timeout=_TERMINATE_GRACE_SECONDS)
    except TimeoutError:
        _signal_process_group(process, signal.SIGKILL)
        try:
            await process.wait()
        except ProcessLookupError:
            pass


def _cleanup_run_config(project_path: str, filename: str) -> None:
    """Remove a temporary run-config-*.json file; ignore if already gone."""
    try:
        (Path(project_path) / ".sandcastle" / filename).unlink(missing_ok=True)
    except OSError:
        logger.debug("could not remove sandcastle config %s", filename)


def _kill_pid_group(pid: int | None, sig) -> None:
    """Send `sig` to the process group of `pid`, falling back to the bare pid."""
    if not pid:
        return
    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def _release_budget_slot(session_name: str) -> None:
    """Release the shared-session-budget slot reserved for a sandcastle run."""
    try:
        from app.services.scheduling.session_registry import session_registry
        session_registry.release_external(session_name)
    except Exception:
        logger.debug("could not release budget slot for %s", session_name)


class SandcastleService:
    """Service for managing sandcastle configurations and runs."""

    async def get_config(self, project_path: str) -> SandcastleConfig | None:
        """Get sandcastle config for a project."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SandcastleConfig).where(SandcastleConfig.project_path == project_path)
            )
            return result.scalar_one_or_none()

    async def get_or_create_config(self, project_path: str) -> SandcastleConfig:
        """Get or create sandcastle config for a project."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SandcastleConfig).where(SandcastleConfig.project_path == project_path)
            )
            config = result.scalar_one_or_none()
            if not config:
                config = SandcastleConfig(project_path=project_path)
                session.add(config)
                await session.commit()
                await session.refresh(config)
            return config

    async def update_config(self, project_path: str, updates: dict[str, Any]) -> SandcastleConfig:
        """Update sandcastle config for a project."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SandcastleConfig).where(SandcastleConfig.project_path == project_path)
            )
            config = result.scalar_one_or_none()
            if not config:
                config = SandcastleConfig(project_path=project_path)
                session.add(config)

            for key, value in updates.items():
                if hasattr(config, key):
                    setattr(config, key, value)

            config.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(config)
            return config

    async def toggle_config(self, config_id: int) -> SandcastleConfig:
        """Toggle enabled status for a config."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SandcastleConfig).where(SandcastleConfig.id == config_id)
            )
            config = result.scalar_one_or_none()
            if not config:
                raise ValueError(f"Config {config_id} not found")

            config.enabled = not config.enabled
            config.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(config)
            return config

    async def list_configs(self) -> list[SandcastleConfig]:
        """List all sandcastle configs."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(SandcastleConfig))
            return list(result.scalars().all())

    async def start_run(
        self,
        project_path: str,
        prompt: str,
        config_id: int | None = None,
        branch_name: str | None = None,
        max_iterations: int | None = None,
    ) -> SandcastleRun:
        """Start a new sandcastle run."""
        async with AsyncSessionLocal() as session:
            # Get config
            if config_id:
                result = await session.execute(
                    select(SandcastleConfig).where(SandcastleConfig.id == config_id)
                )
                config = result.scalar_one_or_none()
            else:
                result = await session.execute(
                    select(SandcastleConfig).where(SandcastleConfig.project_path == project_path)
                )
                config = result.scalar_one_or_none()

            if not config:
                raise ValueError(f"No sandcastle config found for {project_path}")

            if not config.enabled:
                raise ValueError("Sandcastle is disabled for this project")

            # Create run record. `branch` is seeded with the dispatcher's session
            # name so the kanban reaper can recognise this as a live sandcastle
            # session; `_parse_run_output` may later overwrite it with the real
            # branch the agent pushed to.
            run = SandcastleRun(
                project_path=project_path,
                config_id=config.id,
                prompt=prompt,
                status="pending",
                branch=branch_name,
            )
            session.add(run)
            await session.commit()
            await session.refresh(run)

            # Start the run in background
            asyncio.create_task(self._execute_run(run.id, config, branch_name, max_iterations))

            return run

    async def start_parallel_runs(
        self,
        project_path: str,
        prompts: list[dict[str, str]],
        config_id: int | None = None,
        use_shared_sandbox: bool = False,
    ) -> list[SandcastleRun]:
        """Start multiple sandcastle runs in parallel."""
        async with AsyncSessionLocal() as session:
            # Get config
            if config_id:
                result = await session.execute(
                    select(SandcastleConfig).where(SandcastleConfig.id == config_id)
                )
                config = result.scalar_one_or_none()
            else:
                result = await session.execute(
                    select(SandcastleConfig).where(SandcastleConfig.project_path == project_path)
                )
                config = result.scalar_one_or_none()

            if not config:
                raise ValueError(f"No sandcastle config found for {project_path}")

            if not config.enabled:
                raise ValueError("Sandcastle is disabled for this project")

            # Create run records
            runs = []
            for prompt_data in prompts:
                run = SandcastleRun(
                    project_path=project_path,
                    config_id=config.id,
                    prompt=prompt_data["prompt"],
                    status="pending",
                )
                session.add(run)
                runs.append(run)
            
            await session.commit()
            for run in runs:
                await session.refresh(run)

            # Start parallel runs in background
            asyncio.create_task(self._execute_parallel_runs(
                [r.id for r in runs], config, use_shared_sandbox
            ))

            return runs

    async def _execute_run(
        self,
        run_id: int,
        config: SandcastleConfig,
        branch_name: str | None = None,
        max_iterations: int | None = None,
    ) -> None:
        """Execute a sandcastle run via Node.js subprocess."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SandcastleRun).where(SandcastleRun.id == run_id)
            )
            run = result.scalar_one_or_none()
            if not run:
                return

            # Update status to running
            run.status = "running"
            run.started_at = datetime.now(UTC)
            await session.commit()

            try:
                # Validate prerequisites
                if not RUNNER_SCRIPT.exists():
                    raise FileNotFoundError(f"Runner script not found: {RUNNER_SCRIPT}")
                await self._ensure_sandbox_image_ready(config)

                # Build the command
                cmd = self._build_run_command(config, run, branch_name, max_iterations)

                # Create log file
                log_dir = Path(config.project_path) / ".sandcastle" / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                log_file = log_dir / f"run-{run_id}.log"
                run.log_file_path = str(log_file)
                await session.commit()

                # Execute the subprocess with timeout
                try:
                    env = os.environ.copy()
                    backend_nm = str(SCRIPT_DIR.parent / "node_modules")
                    env["NODE_PATH"] = backend_nm + os.pathsep + env.get("NODE_PATH", "")
                    process = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                        cwd=config.project_path,
                        env=env,
                        # Own process group so a timeout/cancel can take down the node
                        # process *and* the docker/podman CLI children it spawned.
                        start_new_session=True,
                    )
                    run.pid = process.pid
                    await session.commit()

                    # Absolute wall-clock ceiling. The idle timeout itself is enforced
                    # by sandcastle inside the sandbox; killing on the idle value here
                    # would abort actively-working runs.
                    timeout = _overall_timeout(config.idle_timeout_seconds, config.max_iterations)
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=timeout
                    )
                except TimeoutError:
                    await _terminate_gracefully(process)
                    raise TimeoutError(f"Run timed out after {timeout} seconds")

                # Update run with results
                run.stdout = stdout.decode() if stdout else None
                run.stderr = stderr.decode() if stderr else None
                run.completed_at = datetime.now(UTC)

                if process.returncode == 0:
                    run.status = "completed"
                    # Try to parse JSON output for commits, branch, etc.
                    self._parse_run_output(run, run.stdout)
                elif process.returncode == -9:  # SIGKILL
                    run.status = "failed"
                    run.error = "Process was killed (timeout or memory limit)"
                else:
                    run.status = "failed"
                    run.error = f"Process exited with code {process.returncode}"

                await session.commit()

            except asyncio.CancelledError:
                run.status = "cancelled"
                run.completed_at = datetime.now(UTC)
                run.error = "Run was cancelled by user"
                await session.commit()
            except TimeoutError as e:
                run.status = "failed"
                run.error = str(e)
                run.completed_at = datetime.now(UTC)
                await session.commit()
            except FileNotFoundError as e:
                run.status = "failed"
                run.error = f"Prerequisite missing: {e}"
                run.completed_at = datetime.now(UTC)
                await session.commit()
            except Exception as e:
                logger.exception("Sandcastle run %s failed", run_id)
                run.status = "failed"
                run.error = str(e)
                run.completed_at = datetime.now(UTC)
                await session.commit()
            finally:
                _cleanup_run_config(config.project_path, f"run-config-{run_id}.json")
                # Release the shared-budget slot the dispatcher reserved for this run.
                if branch_name:
                    from app.services.scheduling.session_registry import session_registry
                    session_registry.release_external(branch_name)

    def _build_run_command(
        self,
        config: SandcastleConfig,
        run: SandcastleRun,
        branch_name: str | None,
        max_iterations: int | None,
    ) -> list[str]:
        """Build the Node.js command to execute sandcastle."""
        # Create a temporary config file for this run
        run_config = {
            "run_id": run.id,
            "sandbox_provider": config.sandbox_provider,
            "agent_provider": config.agent_provider,
            "model": config.model,
            "branch_strategy": config.branch_strategy,
            "docker_image": _resolve_docker_image(config.sandbox_provider, config.docker_image),
            "max_iterations": max_iterations or config.max_iterations,
            "idle_timeout_seconds": config.idle_timeout_seconds,
            "prompt": run.prompt,
            "project_path": config.project_path,
            "branch_name": branch_name,
        }

        # Write config to temp file
        config_file = Path(config.project_path) / ".sandcastle" / f"run-config-{run.id}.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps(run_config))

        return [
            "node",
            str(RUNNER_SCRIPT),
            "--config", str(config_file),
            "--run-id", str(run.id),
        ]

    def _parse_run_output(self, run: SandcastleRun, stdout: str | None) -> None:
        """Parse JSON output from sandcastle runner."""
        if not stdout:
            return

        try:
            # Find JSON in output (may be mixed with logs)
            lines = stdout.strip().split("\n")
            for line in reversed(lines):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    output = json.loads(line)
                    run.commits = output.get("commits")
                    run.branch = output.get("branch")
                    run.output = output.get("output")
                    return
        except (json.JSONDecodeError, KeyError):
            pass

    async def _execute_parallel_runs(
        self,
        run_ids: list[int],
        config: SandcastleConfig,
        use_shared_sandbox: bool = False,
    ) -> None:
        """Execute multiple sandcastle runs in parallel via Node.js subprocess."""
        async with AsyncSessionLocal() as session:
            # Get all runs
            runs = []
            for run_id in run_ids:
                result = await session.execute(
                    select(SandcastleRun).where(SandcastleRun.id == run_id)
                )
                run = result.scalar_one_or_none()
                if run:
                    runs.append(run)

            if not runs:
                return

            # Update status to running
            for run in runs:
                run.status = "running"
                run.started_at = datetime.now(UTC)
            await session.commit()

            try:
                # Build the parallel command
                cmd = self._build_parallel_run_command(config, runs, use_shared_sandbox)

                # Create log file
                log_dir = Path(config.project_path) / ".sandcastle" / "logs"
                log_dir.mkdir(parents=True, exist_ok=True)
                log_file = log_dir / f"parallel-{runs[0].id}.log"
                for run in runs:
                    run.log_file_path = str(log_file)
                await session.commit()

                # Execute the subprocess
                env = os.environ.copy()
                backend_nm = str(SCRIPT_DIR.parent / "node_modules")
                env["NODE_PATH"] = backend_nm + os.pathsep + env.get("NODE_PATH", "")
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=config.project_path,
                    env=env,
                    start_new_session=True,
                )

                for run in runs:
                    run.pid = process.pid
                await session.commit()

                # Wait for completion
                stdout, stderr = await process.communicate()

                # Update runs with results
                stdout_text = stdout.decode() if stdout else None
                stderr_text = stderr.decode() if stderr else None

                # Parse parallel output
                parallel_results = self._parse_parallel_output(stdout_text)
                
                for i, run in enumerate(runs):
                    run.stdout = stdout_text
                    run.stderr = stderr_text
                    run.completed_at = datetime.now(UTC)

                    if i < len(parallel_results) and parallel_results[i].get("status") == "completed":
                        run.status = "completed"
                        run.commits = parallel_results[i].get("commits")
                        run.branch = parallel_results[i].get("branch")
                    elif process.returncode == 0:
                        run.status = "completed"
                    else:
                        run.status = "failed"
                        run.error = f"Process exited with code {process.returncode}"

                await session.commit()

            except asyncio.CancelledError:
                for run in runs:
                    run.status = "cancelled"
                    run.completed_at = datetime.now(UTC)
                await session.commit()
            except Exception as e:
                logger.exception("Sandcastle parallel runs failed")
                for run in runs:
                    run.status = "failed"
                    run.error = str(e)
                    run.completed_at = datetime.now(UTC)
                await session.commit()
            finally:
                _cleanup_run_config(config.project_path, f"parallel-config-{runs[0].id}.json")

    def _build_parallel_run_command(
        self,
        config: SandcastleConfig,
        runs: list[SandcastleRun],
        use_shared_sandbox: bool = False,
    ) -> list[str]:
        """Build the Node.js command to execute parallel sandcastle runs."""
        # Create a temporary config file for parallel runs
        runs_config = [
            {
                "run_id": run.id,
                "prompt": run.prompt,
                "branch_name": None,
            }
            for run in runs
        ]

        parallel_config = {
            "sandbox_provider": config.sandbox_provider,
            "agent_provider": config.agent_provider,
            "model": config.model,
            "branch_strategy": config.branch_strategy,
            "docker_image": _resolve_docker_image(config.sandbox_provider, config.docker_image),
            "max_iterations": config.max_iterations,
            "idle_timeout_seconds": config.idle_timeout_seconds,
            "project_path": config.project_path,
            "runs": runs_config,
            "use_shared_sandbox": use_shared_sandbox,
        }

        # Write config to temp file
        config_file = Path(config.project_path) / ".sandcastle" / f"parallel-config-{runs[0].id}.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        config_file.write_text(json.dumps(parallel_config))

        return [
            "node",
            str(RUNNER_SCRIPT),
            "--config", str(config_file),
            "--mode", "parallel",
        ]

    def _parse_parallel_output(self, stdout: str | None) -> list[dict]:
        """Parse JSON output from parallel sandcastle runner."""
        if not stdout:
            return []

        try:
            # Find JSON in output
            lines = stdout.strip().split("\n")
            for line in reversed(lines):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    output = json.loads(line)
                    return output.get("results", [])
        except (json.JSONDecodeError, KeyError):
            pass

        return []

    async def get_run(self, run_id: int) -> SandcastleRun | None:
        """Get a sandcastle run by ID."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SandcastleRun).where(SandcastleRun.id == run_id)
            )
            return result.scalar_one_or_none()

    async def get_run_graph(self, project_path: str, limit: int = 300) -> dict[str, Any]:
        """Build a lightweight run graph for a project from existing run data.

        SandcastleRun has no dependency/parent field, so batches are inferred
        from the log_file_path correlator that _execute_parallel_runs already
        stamps onto every run started via one /runs/parallel call: runs
        sharing a log file fan out from a synthetic batch node, everything
        else is a standalone node.
        """
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SandcastleRun)
                .where(SandcastleRun.project_path == project_path)
                .order_by(SandcastleRun.created_at.asc())
                .limit(limit)
            )
            runs = list(result.scalars().all())

        groups: dict[str, list[SandcastleRun]] = {}
        solo: list[SandcastleRun] = []
        for run in runs:
            if run.log_file_path:
                groups.setdefault(run.log_file_path, []).append(run)
            else:
                solo.append(run)

        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, str]] = []

        for log_path, group_runs in groups.items():
            if len(group_runs) > 1:
                batch_id = f"batch:{log_path}"
                nodes.append(self._batch_node(batch_id, group_runs))
                for run in group_runs:
                    run_node = self._run_node(run)
                    nodes.append(run_node)
                    edges.append({"source": batch_id, "target": run_node["id"]})
            else:
                nodes.append(self._run_node(group_runs[0]))

        for run in solo:
            nodes.append(self._run_node(run))

        return {"nodes": nodes, "edges": edges}

    def _run_node(self, run: SandcastleRun) -> dict[str, Any]:
        """Graph node for a single run."""
        duration = None
        if run.started_at:
            # SQLite round-trips DateTime(timezone=True) as naive even though it's
            # always written as UTC (see usage_service.py's cached_at handling for
            # the same idiom) -- reattach tzinfo before subtracting.
            started = run.started_at.replace(tzinfo=UTC)
            end = (
                run.completed_at.replace(tzinfo=UTC)
                if run.completed_at else datetime.now(UTC)
            )
            duration = (end - started).total_seconds()
        return {
            "id": f"run:{run.id}",
            "type": "run",
            "run_id": run.id,
            "label": run.prompt[:80],
            "prompt": run.prompt,
            "status": run.status,
            "branch": run.branch,
            "commits_count": len(run.commits) if run.commits else 0,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "duration_seconds": duration,
            "error": run.error,
        }

    def _batch_node(self, batch_id: str, group_runs: list[SandcastleRun]) -> dict[str, Any]:
        """Synthetic root node fanning out to a parallel batch's runs."""
        started = [r.started_at for r in group_runs if r.started_at]
        completed = [r.completed_at for r in group_runs if r.completed_at]
        return {
            "id": batch_id,
            "type": "batch",
            "run_id": None,
            "label": f"{len(group_runs)} parallel runs",
            "prompt": None,
            "status": self._aggregate_status(group_runs),
            "branch": None,
            "commits_count": None,
            "started_at": min(started).isoformat() if started else None,
            "completed_at": max(completed).isoformat() if len(completed) == len(group_runs) else None,
            "duration_seconds": None,
            "error": None,
        }

    @staticmethod
    def _aggregate_status(group_runs: list[SandcastleRun]) -> str:
        """Batch status: running while any child is active, else failed if any
        failed, else cancelled only if every child was cancelled, else completed."""
        statuses = {r.status for r in group_runs}
        if statuses & {"pending", "running"}:
            return "running"
        if "failed" in statuses:
            return "failed"
        if statuses == {"cancelled"}:
            return "cancelled"
        return "completed"

    async def list_runs(
        self,
        project_path: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[SandcastleRun]:
        """List sandcastle runs with optional filters."""
        async with AsyncSessionLocal() as session:
            query = select(SandcastleRun)
            if project_path:
                query = query.where(SandcastleRun.project_path == project_path)
            if status:
                query = query.where(SandcastleRun.status == status)
            query = query.order_by(SandcastleRun.created_at.desc()).limit(limit)
            result = await session.execute(query)
            return list(result.scalars().all())

    async def cancel_run(self, run_id: int) -> bool:
        """Cancel a running sandcastle run."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SandcastleRun).where(SandcastleRun.id == run_id)
            )
            run = result.scalar_one_or_none()
            if not run or run.status != "running":
                return False

            # Kill the whole process group, so the docker/podman CLI children the
            # runner spawned die with the node process — not just the node PID. The
            # runner turns SIGTERM into an AbortSignal and disposes the container.
            _kill_pid_group(run.pid, signal.SIGTERM)
            if run.branch:
                _release_budget_slot(run.branch)

            run.status = "cancelled"
            run.completed_at = datetime.now(UTC)
            await session.commit()
            return True

    async def delete_run(self, run_id: int) -> bool:
        """Delete a run record entirely. Cancels it first if still active.

        Returns False if no such run exists."""
        async with AsyncSessionLocal() as session:
            run = (
                await session.execute(
                    select(SandcastleRun).where(SandcastleRun.id == run_id)
                )
            ).scalar_one_or_none()
            if not run:
                return False
            self._teardown_run(run)
            await session.delete(run)
            await session.commit()
            return True

    async def clear_runs(
        self, project_path: str | None = None, include_running: bool = False
    ) -> int:
        """Bulk-delete run records, returning the number removed.

        By default only terminal runs (completed/failed/cancelled) are removed so an
        in-flight run is never yanked out from under the dispatcher. With
        include_running=True, pending/running runs are cancelled first and then
        removed too. An optional project_path scopes the cleanup to one project."""
        terminal = ("completed", "failed", "cancelled")
        async with AsyncSessionLocal() as session:
            query = select(SandcastleRun)
            if project_path:
                query = query.where(SandcastleRun.project_path == project_path)
            if not include_running:
                query = query.where(SandcastleRun.status.in_(terminal))
            runs = list((await session.execute(query)).scalars().all())
            for run in runs:
                self._teardown_run(run)
                await session.delete(run)
            await session.commit()
            return len(runs)

    def _teardown_run(self, run: SandcastleRun) -> None:
        """Best-effort side-effect cleanup before a run row is deleted: kill an
        active process group, free its budget slot, and remove its log file."""
        if run.status in ("pending", "running"):
            _kill_pid_group(run.pid, signal.SIGTERM)
            if run.branch:
                _release_budget_slot(run.branch)
        if run.log_file_path:
            try:
                Path(run.log_file_path).unlink(missing_ok=True)
            except OSError:
                logger.debug("could not remove run log %s", run.log_file_path)

    async def get_run_logs(self, run_id: int, offset: int = 0) -> dict[str, Any]:
        """Get logs for a sandcastle run with optional offset for streaming."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(SandcastleRun).where(SandcastleRun.id == run_id)
            )
            run = result.scalar_one_or_none()
            if not run:
                return {"error": "Run not found"}

            logs = {
                "run_id": run.id,
                "status": run.status,
                "stdout": run.stdout or "",
                "stderr": run.stderr or "",
                "error": run.error,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            }

            # If log file exists, read from offset
            if run.log_file_path:
                log_path = Path(run.log_file_path)
                if log_path.exists():
                    try:
                        with open(log_path) as f:
                            if offset > 0:
                                f.seek(offset)
                            content = f.read()
                            logs["log_content"] = content
                            logs["log_offset"] = f.tell()
                    except Exception as e:
                        logs["log_error"] = str(e)

            return logs

    async def check_health(self) -> dict[str, Any]:
        """Check sandcastle health: Docker/Podman availability, Node.js, etc."""
        health = {
            "node_available": False,
            "docker_available": False,
            "podman_available": False,
            "runner_script_exists": RUNNER_SCRIPT.exists(),
        }

        # Check Node.js
        try:
            process = await asyncio.create_subprocess_exec(
                "node", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            if process.returncode == 0:
                health["node_available"] = True
                health["node_version"] = stdout.decode().strip()
        except FileNotFoundError:
            pass

        # Check Docker
        try:
            process = await asyncio.create_subprocess_exec(
                "docker", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            if process.returncode == 0:
                health["docker_available"] = True
                health["docker_version"] = stdout.decode().strip()
        except FileNotFoundError:
            pass

        # Check Podman
        try:
            process = await asyncio.create_subprocess_exec(
                "podman", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await process.communicate()
            if process.returncode == 0:
                health["podman_available"] = True
                health["podman_version"] = stdout.decode().strip()
        except FileNotFoundError:
            pass

        # Check if the sandcastle image exists under each available runtime — a
        # podman-only host never has docker_available, so it must be checked
        # independently or the "not built yet" state can never be surfaced.
        for runtime, available_key, exists_key in (
            ("docker", "docker_available", "docker_image_exists"),
            ("podman", "podman_available", "podman_image_exists"),
        ):
            if not health[available_key]:
                continue
            try:
                process = await asyncio.create_subprocess_exec(
                    runtime, "image", "inspect", DEFAULT_DOCKER_IMAGE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await process.communicate()
                health[exists_key] = process.returncode == 0
            except FileNotFoundError:
                pass

        # Check npm dependencies
        node_modules_path = Path(__file__).parent.parent.parent / "node_modules" / "@ai-hero" / "sandcastle"
        health["npm_dependencies_installed"] = node_modules_path.exists()

        # Unified, explicit "is the sandbox image built?" signal. True iff the image
        # exists under at least one available container runtime; False when no runtime
        # is available at all (nothing can run it), so a fresh host reads as not-ready.
        health["image_ready"] = bool(
            health.get("docker_image_exists") or health.get("podman_image_exists")
        )

        return health

    async def _container_image_present(self, runtime: str, image_name: str) -> bool:
        """True if `image_name` exists locally under the given container runtime."""
        try:
            process = await asyncio.create_subprocess_exec(
                runtime, "image", "inspect", image_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await process.communicate()
            return process.returncode == 0
        except FileNotFoundError:
            return False

    async def _ensure_sandbox_image_ready(self, config: SandcastleConfig) -> None:
        """Fail fast with an actionable error if a container run's image is missing.

        A fresh host can enable sandcastle with a container provider before ever
        building `sandcastle:local`; without this guard the run would die deep inside
        the node/sandcastle stack with an opaque "no such image" error. Non-container
        providers (no-sandbox, vercel) need no local image and are skipped."""
        if config.sandbox_provider not in _CONTAINER_PROVIDERS:
            return
        image_name = _resolve_docker_image(config.sandbox_provider, config.docker_image)
        if not await self._container_image_present(config.sandbox_provider, image_name):
            raise RuntimeError(
                f"Sandbox image '{image_name}' is not built for "
                f"'{config.sandbox_provider}'. Build it first via "
                "POST /api/v1/sandcastle/build-image or "
                "`node backend/scripts/sandcastle_runner.mjs --build-image`."
            )

    async def list_running_containers(self) -> dict[str, Any]:
        """List running Docker and Podman containers whose name starts with 'sandcastle-'."""
        containers: list[dict[str, Any]] = []

        for runtime in ("docker", "podman"):
            try:
                process = await asyncio.create_subprocess_exec(
                    runtime, "ps",
                    "--filter", f"name={_CONTAINER_NAME_PREFIX}",
                    "--format", "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.CreatedAt}}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
                if process.returncode == 0:
                    for line in stdout.decode().strip().splitlines():
                        parts = line.split("\t")
                        if len(parts) >= 5:
                            containers.append({
                                "runtime": runtime,
                                "id": parts[0],
                                "name": parts[1],
                                "image": parts[2],
                                "status": parts[3],
                                "created_at": parts[4],
                            })
            except (TimeoutError, FileNotFoundError):
                pass

        return {"containers": containers}

    async def stream_container_logs(self, name: str, runtime: str):
        """Tail a running sandcastle container's own stdout/stderr via `logs -f`.

        This is a live view into the container itself — distinct from a run's SSE
        log stream, which tails the sandcastle library's own log *file* and only
        exists for runs started through this feature's `/runs` endpoints."""
        if runtime not in _CONTAINER_PROVIDERS:
            raise ValueError(f"unsupported runtime: {runtime}")
        if not name.startswith(_CONTAINER_NAME_PREFIX):
            raise ValueError("not a sandcastle container")

        process = await asyncio.create_subprocess_exec(
            runtime, "logs", "-f", "--tail", "200", name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                yield line.decode(errors="replace")
        finally:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass

    async def build_docker_image(
        self,
        image_name: str = DEFAULT_DOCKER_IMAGE,
        runtime: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Build the sandcastle image with the given (or auto-detected) container runtime.

        `docker build` and `podman build` take identical arguments for this Dockerfile,
        so a podman-only host (no `docker` binary at all) can build the same image —
        it just needs the right binary name on the command line instead of a
        hardcoded "docker".

        Idempotent: if the image already exists under the chosen runtime it's a no-op
        that returns success (exit 0), unless `force=True` requests a rebuild."""
        dockerfile_path = Path(__file__).parent.parent.parent.parent / ".sandcastle" / "Dockerfile"
        if not dockerfile_path.exists():
            return {"success": False, "error": f"Dockerfile not found at {dockerfile_path}"}

        health = await self.check_health()
        if runtime is None:
            runtime = _pick_default_sandbox_provider(health)
        if runtime not in _CONTAINER_PROVIDERS:
            return {"success": False, "error": "Neither Docker nor Podman is available"}

        if not force and health.get(f"{runtime}_image_exists"):
            return {
                "success": True,
                "already_present": True,
                "message": f"Image {image_name} already present — nothing to build",
            }

        try:
            process = await asyncio.create_subprocess_exec(
                runtime, "build", "-t", image_name, "-f", str(dockerfile_path), str(dockerfile_path.parent),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                return {"success": True, "message": f"Image {image_name} built successfully"}
            else:
                return {"success": False, "error": stderr.decode() if stderr else "Build failed"}
        except FileNotFoundError:
            return {"success": False, "error": f"{runtime} not found"}


# Module-level singleton
sandcastle_service = SandcastleService()