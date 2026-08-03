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
    _RESTRICTED_NETWORK_NAME,
    SandcastleService,
    _container_security_flags,
    _network_option,
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
        memory_limit_mb=None,
        cpu_quota=None,
        pids_limit=None,
        read_only_rootfs=False,
        network_mode="bridge",
        egress_allowlist=None,
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


# ---- container security flags ----------------------------------------------
#
# The sandcastle library only exposes `cpus` and `network` natively, so memory /
# pids / read-only rootfs are enforced via docker/podman `run` flags spliced in
# by the runner's PATH shim. These tests lock down exactly which flags each
# config field emits — the contract the shim (and thus the kernel) relies on.

def test_security_flags_empty_when_nothing_configured():
    assert _container_security_flags(_config_ns()) == []


def test_security_flags_memory_limit_disables_swap_for_predictable_oom():
    flags = _container_security_flags(_config_ns(memory_limit_mb=256))
    # --memory-swap == --memory means the run OOM-kills at the cap instead of
    # silently swapping past it.
    assert "--memory" in flags and "256m" in flags
    assert "--memory-swap" in flags
    i = flags.index("--memory-swap")
    assert flags[i + 1] == "256m"


def test_security_flags_pids_limit():
    flags = _container_security_flags(_config_ns(pids_limit=128))
    assert flags[flags.index("--pids-limit") + 1] == "128"


def test_security_flags_read_only_rootfs_adds_writable_tmpfs():
    flags = _container_security_flags(_config_ns(read_only_rootfs=True))
    assert "--read-only" in flags
    # /tmp and /home/agent must stay writable or the agent + credentials mount break.
    assert flags.count("--tmpfs") == 2
    assert "/tmp" in flags and "/home/agent" in flags


def test_security_flags_combined():
    flags = _container_security_flags(
        _config_ns(memory_limit_mb=512, pids_limit=64, read_only_rootfs=True)
    )
    assert "--memory" in flags and "--pids-limit" in flags and "--read-only" in flags


def test_network_option_maps_modes():
    assert _network_option("none") == "none"
    assert _network_option("restricted") == _RESTRICTED_NETWORK_NAME
    assert _network_option("bridge") is None
    assert _network_option(None) is None


def _config_ns(**overrides):
    base = dict(memory_limit_mb=None, pids_limit=None, read_only_rootfs=False)
    base.update(overrides)
    return SimpleNamespace(**base)


# ---- API boundary validation -----------------------------------------------

def test_config_update_rejects_invalid_network_mode():
    from app.api.v1.sandcastle.router import SandcastleConfigUpdate
    with pytest.raises(ValueError):
        SandcastleConfigUpdate(network_mode="nonee")


def test_config_update_accepts_valid_network_modes():
    from app.api.v1.sandcastle.router import SandcastleConfigUpdate
    for mode in ("none", "bridge", "restricted"):
        assert SandcastleConfigUpdate(network_mode=mode).network_mode == mode


def test_config_update_rejects_non_positive_caps():
    from app.api.v1.sandcastle.router import SandcastleConfigUpdate
    for bad in (dict(memory_limit_mb=0), dict(pids_limit=-1), dict(cpu_quota=0)):
        with pytest.raises(ValueError):
            SandcastleConfigUpdate(**bad)


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


def test_build_run_command_threads_resource_caps_into_config(tmp_path):
    svc = SandcastleService()
    config = _config(
        tmp_path,
        sandbox_provider="docker",
        memory_limit_mb=256,
        cpu_quota=1.5,
        pids_limit=100,
        read_only_rootfs=True,
        network_mode="none",
        egress_allowlist=["example.com"],
    )
    run = SimpleNamespace(id=3, prompt="x")
    svc._build_run_command(config, run, branch_name=None, max_iterations=None)
    data = json.loads((Path(tmp_path) / ".sandcastle" / "run-config-3.json").read_text())
    assert data["cpu_quota"] == 1.5
    assert data["network"] == "none"
    assert data["egress_allowlist"] == ["example.com"]
    assert "--memory" in data["container_run_flags"]
    assert "--read-only" in data["container_run_flags"]
    assert "--pids-limit" in data["container_run_flags"]


def test_build_run_command_no_caps_yields_empty_flags(tmp_path):
    svc = SandcastleService()
    config = _config(tmp_path, sandbox_provider="docker")  # all caps default/None
    run = SimpleNamespace(id=4, prompt="x")
    svc._build_run_command(config, run, branch_name=None, max_iterations=None)
    data = json.loads((Path(tmp_path) / ".sandcastle" / "run-config-4.json").read_text())
    assert data["container_run_flags"] == []
    assert data["network"] is None  # bridge => provider default


def test_build_run_command_threads_extra_env_into_config(tmp_path):
    svc = SandcastleService()
    # Pick a non-Claude-Code agent so the test isolates the "extras pass
    # through" contract from the per-CLI baseline injection exercised by
    # the dedicated tests below. ``CLAUDE_CODE_BASELINE_ENV`` is only
    # injected for ``cli_id == "claude-code"``, so the assertion below
    # reads the dict exactly without subtracting baseline vars. The
    # ``COCKPIT_*`` session-context vars are still added by
    # ``build_spawn_env`` for every transport and belong in the dict
    # (the worktree transport gets them today; kaart 1f8b4e99… unifies
    # the contract so sandcastle does too).
    config = _config(tmp_path, sandbox_provider="docker", agent_provider="open-code")
    run = SimpleNamespace(id=5, prompt="x")
    svc._build_run_command(
        config, run, branch_name=None, max_iterations=None,
        extra_env={"API_TOKEN": "t0k", "DB_URL": "sqlite://"},
    )
    data = json.loads((Path(tmp_path) / ".sandcastle" / "run-config-5.json").read_text())
    # Project-scoped secrets land in the run-config `env` the runner reads and
    # injects as the sandbox provider's env vars.
    assert data["env"] == {
        "API_TOKEN": "t0k",
        "DB_URL": "sqlite://",
        "COCKPIT_PROJECT_KEY": config.project_path,
        "COCKPIT_RUNTIME": "sandcastle",
    }


def test_build_run_command_no_extra_env_yields_only_cockpit_context(tmp_path):
    svc = SandcastleService()
    # Non-Claude-Code agent so the baseline-env injection path doesn't
    # muddy this isolated "no extras => only cockpit context" contract.
    # The baseline-injection tests below cover the Claude Code side.
    config = _config(tmp_path, sandbox_provider="docker", agent_provider="open-code")
    run = SimpleNamespace(id=6, prompt="x")
    svc._build_run_command(config, run, branch_name=None, max_iterations=None)
    data = json.loads((Path(tmp_path) / ".sandcastle" / "run-config-6.json").read_text())
    # No caller secrets and a non-Claude-Code agent => just the COCKPIT_*
    # session-context vars (added by build_spawn_env for every transport).
    assert data["env"] == {
        "COCKPIT_PROJECT_KEY": config.project_path,
        "COCKPIT_RUNTIME": "sandcastle",
    }


# ---- baseline env injection -----------------------------------------------
#
# Kaart 1f8b4e9963e24451a02eea03c5d1592a: ``CLAUDE_CODE_BASELINE_ENV`` from
# ``app.services.agentic_cli.provider_env`` (today just
# ``CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL=1``) must reach a sandbox Claude
# Code spawn — every other Claude Code transport (``worktree``,
# ``headless``, ``cc_spawn``) routes its explicit env through
# ``build_spawn_env`` and gets the baseline for free. ``sandcastle``
# historically passed ``extra_env`` straight through, so a sandboxed Claude
# Code agent never saw the baseline and the bundled ``claude-api`` skill
# could fire on turn one — the trigger hit prompts naming
# ``claude-*``/``anthropic``/``Opus``/``Sonnet``/``Haiku`` and the next
# request died with ``invalid_request: Prompt is too long``.
#
# These tests assert the contract the runner.mjs-side of the bridge relies
# on: every ``agent_provider="claude-code"`` run gets the baseline, every
# other agent CLI (``codex-cli``, ``open-code``) does not (their own
# basenames live next to theirs, not in ``CLAUDE_CODE_BASELINE_ENV``), and
# caller-supplied extras still win on collision so a stale project secret
# can't disable the safety toggle.

def test_build_run_command_claude_code_baseline_env_injected(tmp_path):
    """A sandbox Claude Code run gets ``CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL=1``.

    Without the baseline, the bundled ``claude-api`` skill fires on any
    prompt mentioning ``claude-*``/``anthropic``/``Opus``/``Sonnet``/``Haiku``
    and inlines ~212k tokens of skill body into a single tool result —
    every card on this repo matches that trigger, and a sandboxed session
    dies 8 seconds after dispatch with ``invalid_request: Prompt is too
    long`` (kaart 1f8b4e9963e24451a02eea03c5d1592a)."""
    from app.services.agentic_cli.provider_env import CLAUDE_CODE_BASELINE_ENV

    svc = SandcastleService()
    config = _config(tmp_path, sandbox_provider="docker", agent_provider="claude-code")
    run = SimpleNamespace(id=70, prompt="x")
    svc._build_run_command(config, run, branch_name=None, max_iterations=None)
    data = json.loads((Path(tmp_path) / ".sandcastle" / "run-config-70.json").read_text())
    # Baseline is Claude-Code-specific, so agent_provider="claude-code" must include it.
    for key, value in CLAUDE_CODE_BASELINE_ENV.items():
        assert data["env"].get(key) == value, (
            f"expected baseline var {key}={value!r} in run-config env, got {data['env']!r}"
        )


def test_build_run_command_baseline_merges_with_caller_extras(tmp_path):
    """Baseline vars merge into ``extra_env``; both survive the merge.

    A project secret (here ``API_TOKEN``) lives alongside the baseline;
    the runner.mjs injects both into the sandbox provider's env. The two
    layers are independent: missing extras do not strip the baseline and
    present extras do not strip absent ones either."""
    svc = SandcastleService()
    config = _config(tmp_path, sandbox_provider="docker", agent_provider="claude-code")
    run = SimpleNamespace(id=71, prompt="x")
    svc._build_run_command(
        config, run, branch_name=None, max_iterations=None,
        extra_env={"API_TOKEN": "t0k"},
    )
    data = json.loads((Path(tmp_path) / ".sandcastle" / "run-config-71.json").read_text())
    assert data["env"]["API_TOKEN"] == "t0k"
    assert data["env"]["CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL"] == "1"


def test_build_run_command_caller_extras_override_baseline(tmp_path):
    """Caller-supplied extras win on collision (the ``build_spawn_env`` contract).

    A project secret that explicitly sets ``CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL=0``
    is the operator opting the sandbox back in — the baseline must yield
    so the operator's choice is respected, mirroring the precedence
    chain documented in ``provider_env.build_spawn_env``."""
    svc = SandcastleService()
    config = _config(tmp_path, sandbox_provider="docker", agent_provider="claude-code")
    run = SimpleNamespace(id=72, prompt="x")
    svc._build_run_command(
        config, run, branch_name=None, max_iterations=None,
        extra_env={"CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL": "0"},
    )
    data = json.loads((Path(tmp_path) / ".sandcastle" / "run-config-72.json").read_text())
    assert data["env"]["CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL"] == "0"


def test_build_run_command_baseline_skipped_for_non_claude_code_agents(tmp_path):
    """Baseline is Claude-Code-specific — ``codex-cli``/``open-code`` runs must not see it.

    The var name starts with ``CLAUDE_CODE_`` and only Claude Code's CLI
    honours it. Injecting it into a Codex or OpenCode container is
    meaningless (silently ignored) but still misleading in a runner
    trace; the build-time branch in ``build_spawn_env`` keeps the var
    scoped to ``cli_id == "claude-code"`` and the sandcastle lane must
    honour that same scoping instead of broadening it."""
    svc = SandcastleService()
    for cli in ("codex-cli", "open-code"):
        config = _config(tmp_path, sandbox_provider="docker", agent_provider=cli)
        run = SimpleNamespace(id=80 + hash(cli) % 100, prompt="x")
        svc._build_run_command(config, run, branch_name=None, max_iterations=None)
        data = json.loads(
            (Path(tmp_path) / ".sandcastle" / f"run-config-{run.id}.json").read_text()
        )
        assert "CLAUDE_CODE_DISABLE_CLAUDE_API_SKILL" not in data["env"], (
            f"baseline var leaked into {cli} run-config env: {data['env']!r}"
        )


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


def test_build_parallel_command_threads_resource_caps(tmp_path):
    svc = SandcastleService()
    config = _config(
        tmp_path, sandbox_provider="docker", memory_limit_mb=256,
        cpu_quota=2.0, read_only_rootfs=True, network_mode="restricted",
    )
    runs = [SimpleNamespace(id=11, prompt="a")]
    svc._build_parallel_run_command(config, runs, use_shared_sandbox=False)
    data = json.loads((Path(tmp_path) / ".sandcastle" / "parallel-config-11.json").read_text())
    assert data["cpu_quota"] == 2.0
    assert data["network"] == _RESTRICTED_NETWORK_NAME
    assert "--memory" in data["container_run_flags"]


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
