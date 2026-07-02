"""Unit tests for sandcastle service helpers that don't touch the database.

Covers run-config building (run_id wiring, docker-image defaulting, shared-sandbox
threading) and the overall-timeout computation. DB-backed methods (start_run etc.)
are not exercised here because the test harness only patches the kanban DB, not the
main app DB.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from app.services.sandcastle_service import SandcastleService, _overall_timeout


def _config(tmp_path, **overrides):
    base = dict(
        id=7,
        project_path=str(tmp_path),
        sandbox_provider="podman",
        agent_provider="claude-code",
        model="sonnet",
        branch_strategy="merge-to-head",
        docker_image=None,
        max_iterations=1,
        idle_timeout_seconds=600,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---- overall timeout -------------------------------------------------------

def test_overall_timeout_scales_with_iterations_not_just_idle():
    # The absolute wall-clock ceiling must be comfortably larger than the idle
    # timeout, otherwise an actively-working run is killed at the idle boundary.
    single = _overall_timeout(idle_timeout_seconds=600, max_iterations=1)
    assert single > 600
    multi = _overall_timeout(idle_timeout_seconds=600, max_iterations=5)
    assert multi > single


def test_overall_timeout_has_a_sane_floor():
    # Tiny idle timeouts must not produce an absurdly small absolute ceiling.
    assert _overall_timeout(idle_timeout_seconds=10, max_iterations=1) >= 1800


def test_overall_timeout_handles_zero_iterations():
    assert _overall_timeout(idle_timeout_seconds=600, max_iterations=0) >= 1800


# ---- single-run command building -------------------------------------------

def test_build_run_command_includes_run_id(tmp_path):
    svc = SandcastleService()
    config = _config(tmp_path)
    run = SimpleNamespace(id=42, prompt="do the thing")
    cmd = svc._build_run_command(config, run, branch_name=None, max_iterations=None)

    # run-id is on the CLI...
    assert "--run-id" in cmd
    assert "42" in cmd
    # ...and inside the config file the runner reads, so its log filename matches
    # the path Python records.
    cfg_path = Path(tmp_path) / ".sandcastle" / "run-config-42.json"
    data = json.loads(cfg_path.read_text())
    assert data["run_id"] == 42


def test_build_run_command_defaults_docker_image_for_container_providers(tmp_path):
    svc = SandcastleService()
    config = _config(tmp_path, sandbox_provider="podman", docker_image=None)
    run = SimpleNamespace(id=1, prompt="x")
    svc._build_run_command(config, run, branch_name=None, max_iterations=None)
    data = json.loads((Path(tmp_path) / ".sandcastle" / "run-config-1.json").read_text())
    assert data["docker_image"] == "sandcastle:local"


def test_build_run_command_leaves_image_none_for_no_sandbox(tmp_path):
    svc = SandcastleService()
    config = _config(tmp_path, sandbox_provider="no-sandbox", docker_image=None)
    run = SimpleNamespace(id=2, prompt="x")
    svc._build_run_command(config, run, branch_name=None, max_iterations=None)
    data = json.loads((Path(tmp_path) / ".sandcastle" / "run-config-2.json").read_text())
    assert data["docker_image"] is None


# ---- parallel-run command building -----------------------------------------

def test_build_parallel_command_threads_shared_sandbox_flag(tmp_path):
    svc = SandcastleService()
    config = _config(tmp_path)
    runs = [SimpleNamespace(id=5, prompt="a"), SimpleNamespace(id=6, prompt="b")]
    svc._build_parallel_run_command(config, runs, use_shared_sandbox=True)
    data = json.loads((Path(tmp_path) / ".sandcastle" / "parallel-config-5.json").read_text())
    assert data["use_shared_sandbox"] is True


def test_build_parallel_command_defaults_docker_image(tmp_path):
    svc = SandcastleService()
    config = _config(tmp_path, sandbox_provider="docker", docker_image=None)
    runs = [SimpleNamespace(id=9, prompt="a")]
    svc._build_parallel_run_command(config, runs, use_shared_sandbox=False)
    data = json.loads((Path(tmp_path) / ".sandcastle" / "parallel-config-9.json").read_text())
    assert data["docker_image"] == "sandcastle:local"
