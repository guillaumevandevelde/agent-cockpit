"""Sandcastle service: config CRUD and run orchestration."""
import asyncio
import json
import logging
import os
import signal
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.sandcastle import SandcastleConfig, SandcastleRun

logger = logging.getLogger(__name__)

# Path to the Node.js wrapper script
SCRIPT_DIR = Path(__file__).parent.parent.parent / "scripts"
RUNNER_SCRIPT = SCRIPT_DIR / "sandcastle_runner.mjs"


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

            config.updated_at = datetime.now(timezone.utc)
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
            config.updated_at = datetime.now(timezone.utc)
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

            # Create run record
            run = SandcastleRun(
                project_path=project_path,
                config_id=config.id,
                prompt=prompt,
                status="pending",
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
            run.started_at = datetime.now(timezone.utc)
            await session.commit()

            try:
                # Validate prerequisites
                if not RUNNER_SCRIPT.exists():
                    raise FileNotFoundError(f"Runner script not found: {RUNNER_SCRIPT}")

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
                    )
                    run.pid = process.pid
                    await session.commit()

                    # Wait for completion with timeout
                    timeout = config.idle_timeout_seconds or 600
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=timeout
                    )
                except asyncio.TimeoutError:
                    # Kill the process on timeout
                    if process and process.returncode is None:
                        process.kill()
                        await process.communicate()
                    raise TimeoutError(f"Run timed out after {timeout} seconds")

                # Update run with results
                run.stdout = stdout.decode() if stdout else None
                run.stderr = stderr.decode() if stderr else None
                run.completed_at = datetime.now(timezone.utc)

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
                run.completed_at = datetime.now(timezone.utc)
                run.error = "Run was cancelled by user"
                await session.commit()
            except TimeoutError as e:
                run.status = "failed"
                run.error = str(e)
                run.completed_at = datetime.now(timezone.utc)
                await session.commit()
            except FileNotFoundError as e:
                run.status = "failed"
                run.error = f"Prerequisite missing: {e}"
                run.completed_at = datetime.now(timezone.utc)
                await session.commit()
            except Exception as e:
                logger.exception("Sandcastle run %s failed", run_id)
                run.status = "failed"
                run.error = str(e)
                run.completed_at = datetime.now(timezone.utc)
                await session.commit()

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
            "sandbox_provider": config.sandbox_provider,
            "agent_provider": config.agent_provider,
            "model": config.model,
            "branch_strategy": config.branch_strategy,
            "docker_image": config.docker_image,
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
                run.started_at = datetime.now(timezone.utc)
            await session.commit()

            try:
                # Build the parallel command
                cmd = self._build_parallel_run_command(config, runs)

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
                    run.completed_at = datetime.now(timezone.utc)

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
                    run.completed_at = datetime.now(timezone.utc)
                await session.commit()
            except Exception as e:
                logger.exception("Sandcastle parallel runs failed")
                for run in runs:
                    run.status = "failed"
                    run.error = str(e)
                    run.completed_at = datetime.now(timezone.utc)
                await session.commit()

    def _build_parallel_run_command(
        self,
        config: SandcastleConfig,
        runs: list[SandcastleRun],
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
            "docker_image": config.docker_image,
            "max_iterations": config.max_iterations,
            "idle_timeout_seconds": config.idle_timeout_seconds,
            "project_path": config.project_path,
            "runs": runs_config,
            "use_shared_sandbox": False,
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

            # Kill the process if PID is available
            if run.pid:
                try:
                    os.kill(run.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

            run.status = "cancelled"
            run.completed_at = datetime.now(timezone.utc)
            await session.commit()
            return True

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
                        with open(log_path, "r") as f:
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

        # Check if sandcastle Docker image exists
        if health["docker_available"]:
            try:
                process = await asyncio.create_subprocess_exec(
                    "docker", "image", "inspect", "sandcastle:local",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await process.communicate()
                health["docker_image_exists"] = process.returncode == 0
            except FileNotFoundError:
                pass

        # Check npm dependencies
        node_modules_path = Path(__file__).parent.parent.parent / "node_modules" / "@ai-hero" / "sandcastle"
        health["npm_dependencies_installed"] = node_modules_path.exists()

        return health

    async def build_docker_image(self, image_name: str = "sandcastle:local") -> dict[str, Any]:
        """Build the sandcastle Docker image."""
        dockerfile_path = Path(__file__).parent.parent.parent.parent / ".sandcastle" / "Dockerfile"
        if not dockerfile_path.exists():
            return {"success": False, "error": f"Dockerfile not found at {dockerfile_path}"}

        try:
            process = await asyncio.create_subprocess_exec(
                "docker", "build", "-t", image_name, "-f", str(dockerfile_path), str(dockerfile_path.parent),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode == 0:
                return {"success": True, "message": f"Image {image_name} built successfully"}
            else:
                return {"success": False, "error": stderr.decode() if stderr else "Build failed"}
        except FileNotFoundError:
            return {"success": False, "error": "Docker not found"}


# Module-level singleton
sandcastle_service = SandcastleService()