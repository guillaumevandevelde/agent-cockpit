"""Unit tests for sandcastle service helpers that don't touch the database.

Covers run-config building (run_id wiring, docker-image defaulting, shared-sandbox
threading) and the overall-timeout computation. DB-backed methods (start_run etc.)
are not exercised here because the test harness only patches the kanban DB, not the
main app DB.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.sandcastle_service import (
    SandcastleService,
    _overall_timeout,
    _pick_default_sandbox_provider,
)


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


# ---- default sandbox provider selection ------------------------------------
#
# Auto-creating a SandcastleConfig (e.g. when a project's kanban transport is
# switched to "sandcastle") must not silently fall back to "no-sandbox" just
# because that's the ORM column default — the entire point of the sandcastle
# transport is container isolation, so it should pick a real container runtime
# whenever one is actually available on the host.

def test_pick_default_sandbox_provider_prefers_docker():
    health = {"docker_available": True, "podman_available": True}
    assert _pick_default_sandbox_provider(health) == "docker"


def test_pick_default_sandbox_provider_falls_back_to_podman():
    health = {"docker_available": False, "podman_available": True}
    assert _pick_default_sandbox_provider(health) == "podman"


def test_pick_default_sandbox_provider_falls_back_to_no_sandbox_when_neither_available():
    health = {"docker_available": False, "podman_available": False}
    assert _pick_default_sandbox_provider(health) == "no-sandbox"


# ---- runtime-aware image build ---------------------------------------------
#
# build_docker_image() used to hardcode the "docker" binary, so it always
# failed with "Docker not found" on a podman-only host even though podman can
# build the identical image. These tests fake asyncio.create_subprocess_exec
# so no real container runtime is invoked.

class _FakeProcess:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_build_docker_image_auto_detects_podman_when_docker_unavailable(monkeypatch):
    svc = SandcastleService()

    async def fake_check_health():
        return {"docker_available": False, "podman_available": True}
    monkeypatch.setattr(svc, "check_health", fake_check_health)

    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return _FakeProcess(returncode=0)
    monkeypatch.setattr("app.services.sandcastle_service.asyncio.create_subprocess_exec", fake_exec)

    result = await svc.build_docker_image()

    assert result["success"] is True
    assert calls[0][0] == "podman"


@pytest.mark.asyncio
async def test_build_docker_image_honors_explicit_runtime(monkeypatch):
    svc = SandcastleService()

    # Image not present yet, so the idempotency short-circuit doesn't fire and a
    # real build is attempted with the explicitly requested runtime.
    async def fake_check_health():
        return {"docker_available": False, "podman_available": True}
    monkeypatch.setattr(svc, "check_health", fake_check_health)

    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return _FakeProcess(returncode=0)
    monkeypatch.setattr("app.services.sandcastle_service.asyncio.create_subprocess_exec", fake_exec)

    result = await svc.build_docker_image(runtime="podman")

    assert result["success"] is True
    assert calls[0][0] == "podman"


@pytest.mark.asyncio
async def test_build_docker_image_is_noop_when_image_already_present(monkeypatch):
    """Idempotent: an existing image is a no-op returning success — no build runs."""
    svc = SandcastleService()

    async def fake_check_health():
        return {"docker_available": True, "podman_available": False, "docker_image_exists": True}
    monkeypatch.setattr(svc, "check_health", fake_check_health)

    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return _FakeProcess(returncode=0)
    monkeypatch.setattr("app.services.sandcastle_service.asyncio.create_subprocess_exec", fake_exec)

    result = await svc.build_docker_image()

    assert result["success"] is True
    assert result["already_present"] is True
    assert calls == []  # no docker/podman build was invoked


@pytest.mark.asyncio
async def test_build_docker_image_force_rebuilds_existing_image(monkeypatch):
    """force=True builds even when the image already exists."""
    svc = SandcastleService()

    async def fake_check_health():
        return {"docker_available": True, "podman_available": False, "docker_image_exists": True}
    monkeypatch.setattr(svc, "check_health", fake_check_health)

    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return _FakeProcess(returncode=0)
    monkeypatch.setattr("app.services.sandcastle_service.asyncio.create_subprocess_exec", fake_exec)

    result = await svc.build_docker_image(force=True)

    assert result["success"] is True
    assert calls[0][0] == "docker"
    assert "build" in calls[0]


@pytest.mark.asyncio
async def test_build_docker_image_errors_when_no_runtime_available(monkeypatch):
    svc = SandcastleService()

    async def fake_check_health():
        return {"docker_available": False, "podman_available": False}
    monkeypatch.setattr(svc, "check_health", fake_check_health)

    result = await svc.build_docker_image()

    assert result["success"] is False
    assert "docker" in result["error"].lower() and "podman" in result["error"].lower()


# ---- podman image existence in health check --------------------------------

@pytest.mark.asyncio
async def test_check_health_reports_podman_image_exists(monkeypatch):
    svc = SandcastleService()

    async def fake_exec(*args, **kwargs):
        binary = args[0]
        if binary == "node":
            return _FakeProcess(0, b"v24.0.0")
        if binary == "docker":
            raise FileNotFoundError()
        if binary == "podman":
            return _FakeProcess(0, b"podman version 5.0")
        raise AssertionError(f"unexpected binary {binary}")
    monkeypatch.setattr("app.services.sandcastle_service.asyncio.create_subprocess_exec", fake_exec)

    health = await svc.check_health()

    assert health["podman_available"] is True
    assert health["docker_available"] is False
    assert health["podman_image_exists"] is True


# ---- image_ready signal + build bootstrap ----------------------------------

@pytest.mark.asyncio
async def test_check_health_image_ready_flips_false_to_true_after_build(monkeypatch):
    """image_ready reflects image existence: false on a fresh host, true once built."""
    svc = SandcastleService()
    state = {"image_built": False}

    async def fake_exec(*args, **kwargs):
        binary, sub = args[0], (args[1] if len(args) > 1 else "")
        if binary == "node":
            return _FakeProcess(0, b"v22.0.0")
        if binary == "docker":
            if sub == "--version":
                return _FakeProcess(0, b"Docker version 27.0.0")
            if sub == "image":  # image inspect
                return _FakeProcess(0 if state["image_built"] else 1)
        if binary == "podman":
            raise FileNotFoundError()
        raise AssertionError(f"unexpected binary {binary}")
    monkeypatch.setattr("app.services.sandcastle_service.asyncio.create_subprocess_exec", fake_exec)

    before = await svc.check_health()
    assert before["docker_available"] is True
    assert before["image_ready"] is False

    # Simulate a successful --build-image / build-image endpoint call.
    state["image_built"] = True

    after = await svc.check_health()
    assert after["image_ready"] is True


@pytest.mark.asyncio
async def test_ensure_sandbox_image_ready_raises_actionable_error_when_missing(monkeypatch):
    """A container run fails cleanly with a build-me hint when the image is absent."""
    svc = SandcastleService()

    async def fake_present(runtime, image_name):
        return False
    monkeypatch.setattr(svc, "_container_image_present", fake_present)

    config = SimpleNamespace(sandbox_provider="docker", docker_image=None)
    with pytest.raises(RuntimeError) as excinfo:
        await svc._ensure_sandbox_image_ready(config)

    message = str(excinfo.value)
    assert "sandcastle:local" in message
    assert "build-image" in message


@pytest.mark.asyncio
async def test_ensure_sandbox_image_ready_passes_when_image_present(monkeypatch):
    svc = SandcastleService()

    async def fake_present(runtime, image_name):
        return True
    monkeypatch.setattr(svc, "_container_image_present", fake_present)

    config = SimpleNamespace(sandbox_provider="podman", docker_image=None)
    await svc._ensure_sandbox_image_ready(config)  # must not raise


@pytest.mark.asyncio
async def test_ensure_sandbox_image_ready_skips_non_container_providers():
    """no-sandbox / vercel providers need no local image and are never blocked."""
    svc = SandcastleService()
    config = SimpleNamespace(sandbox_provider="no-sandbox", docker_image=None)
    await svc._ensure_sandbox_image_ready(config)  # must not raise (no image probe)


# ---- live container log streaming (visualization) --------------------------

class _FakeStdout:
    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""


class _FakeStreamingProcess:
    def __init__(self, lines: list[bytes]):
        self.stdout = _FakeStdout(lines)
        self.returncode = None
        self.terminated = False

    def terminate(self):
        self.terminated = True
        self.returncode = -15


@pytest.mark.asyncio
async def test_stream_container_logs_yields_decoded_lines(monkeypatch):
    svc = SandcastleService()
    fake_proc = _FakeStreamingProcess([b"line one\n", b"line two\n"])

    async def fake_exec(*args, **kwargs):
        assert args[:2] == ("docker", "logs")
        return fake_proc
    monkeypatch.setattr("app.services.sandcastle_service.asyncio.create_subprocess_exec", fake_exec)

    lines = [line async for line in svc.stream_container_logs("sandcastle-abc123", "docker")]

    assert lines == ["line one\n", "line two\n"]
    assert fake_proc.terminated is True  # generator tears the process down when exhausted


@pytest.mark.asyncio
async def test_stream_container_logs_rejects_container_without_sandcastle_prefix():
    svc = SandcastleService()
    with pytest.raises(ValueError):
        async for _ in svc.stream_container_logs("some-other-container", "docker"):
            pass


@pytest.mark.asyncio
async def test_stream_container_logs_rejects_unknown_runtime():
    svc = SandcastleService()
    with pytest.raises(ValueError):
        async for _ in svc.stream_container_logs("sandcastle-abc123", "vercel"):
            pass
