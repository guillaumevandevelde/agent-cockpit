"""Tests for per-project env-injection in spawn_session.

The acceptance contract (kanban card `[security][D] Per-project env-injectie
in spawn_session`, follow-up #5 in `docs/cockpit/veilig-bouwen-en-uitleveren.md`):

1. spawn_session does NOT inherit the backend's `os.environ` into the spawned
   tmux session. Today the leak is via tmux itself (a bare `tmux new-session`
   passes through the parent's env); this test monkeypatches a host env-var
   into `os.environ` and asserts it never reaches the tmux argv.
2. spawn_session injects an explicit env dict supplied by the caller
   (``extra_env``), where ``SecretStore.get(project_key, …)`` results land
   once follow-up #4 lands. Spawns in project A see only A's keys; spawns
   in project B that don't supply A's keys never see them — even when
   they're sitting in another project's SecretStore mock.
3. ``COCKPIT_PROJECT_KEY`` and ``COCKPIT_RUNTIME`` are auto-injected when
   the caller supplies ``project_key``/``runtime``.
4. Control characters (``\n``/``\r``/``\x00``) in any injected value raise
   ``ValueError`` — same rule ``build_provider_env`` already enforces in
   ``agentic_cli/provider_env.py``.
5. The audit hook is called with the *names* of the injected vars (no values).

The tests here are pin-tests for the security fix; the sibling
``test_runs_spawn.py`` covers provider-specific env injection and stays green.
"""
import pytest


def _capture_spawn(monkeypatch, monkeypatch_session_name="repo-abcd"):
    """Replace ``spawn.subprocess.run`` and ``spawn._session_name_for``.

    Returns ``(calls, cleanup)``. ``calls`` is a list of argv lists. Cleanup
    resets the in-memory session registry so successive tests stay isolated.
    """
    from app.services.runs import spawn

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(spawn, "_session_name_for",
                        lambda directory, preferred=None: monkeypatch_session_name)
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()

    return calls


def _env_dict_argv(argv):
    """Return ``{KEY: VALUE}`` reconstructed from a tmux argv.

    Walks pairs of ``("-e", "KEY=VALUE")`` and ignores everything else.
    """
    env = {}
    for i, item in enumerate(argv):
        if item == "-e" and i + 1 < len(argv):
            pair = argv[i + 1]
            if "=" in pair:
                k, _, v = pair.partition("=")
                env[k] = v
    return env


def test_spawn_session_does_not_inherit_os_environ(monkeypatch, tmp_path):
    """A host-env var (``STRIPE_KEY`` from the test process) must NOT reach tmux.

    This is the core security fix: today ``tmux new-session`` inherits the
    full parent env; ``spawn_session`` builds its own explicit dict and only
    forwards that to tmux via ``-e``. We assert by setting a uniquely-named
    host var and asserting it never appears in the tmux argv.
    """
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    monkeypatch.setenv("HOST_LEAKED_VAR", "supersecret-bad")
    calls = _capture_spawn(monkeypatch)

    spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="plain"),
        project_key="git:example.com/repo-a",
        runtime="worktree",
    )

    argv = calls[0]
    assert "-e" in argv  # sanity: at least the runtime/project-key vars injected
    env = _env_dict_argv(argv)
    assert "HOST_LEAKED_VAR" not in env, (
        f"spawn_session leaked a host env-var into the tmux session: {env}"
    )


def test_spawn_session_injects_cockpit_project_key_and_runtime(monkeypatch, tmp_path):
    """``COCKPIT_PROJECT_KEY`` and ``COCKPIT_RUNTIME`` are auto-injected when set."""
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    calls = _capture_spawn(monkeypatch)

    spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="plain"),
        project_key="git:example.com/repo-a",
        runtime="worktree",
    )

    env = _env_dict_argv(calls[0])
    assert env["COCKPIT_PROJECT_KEY"] == "git:example.com/repo-a"
    assert env["COCKPIT_RUNTIME"] == "worktree"


def test_spawn_session_runtime_omitted_when_not_supplied(monkeypatch, tmp_path):
    """``COCKPIT_RUNTIME`` only appears when the caller passes ``runtime``."""
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    calls = _capture_spawn(monkeypatch)

    spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="plain"),
        project_key="git:example.com/repo-a",
    )

    env = _env_dict_argv(calls[0])
    assert env["COCKPIT_PROJECT_KEY"] == "git:example.com/repo-a"
    assert "COCKPIT_RUNTIME" not in env


def test_spawn_session_extra_env_keys_reach_tmux(monkeypatch, tmp_path):
    """``extra_env`` is forwarded as ``-e KEY=VALUE`` (caller-resolved secrets)."""
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    calls = _capture_spawn(monkeypatch)

    spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="plain"),
        project_key="git:example.com/repo-a",
        runtime="worktree",
        extra_env={"STRIPE_KEY_A": "sk_live_a", "GH_TOKEN_A": "ghp_a"},
    )

    env = _env_dict_argv(calls[0])
    assert env["STRIPE_KEY_A"] == "sk_live_a"
    assert env["GH_TOKEN_A"] == "ghp_a"


def test_spawn_session_isolates_projects_via_caller_supplied_env(monkeypatch, tmp_path):
    """Cross-project isolation: A's spawn sees only A's keys, B's sees only B's.

    This mirrors the test in the card description:
    - spawn in project A with A's secrets set → STRIPE_KEY_A appears
    - spawn in project A (without A's secrets supplied) → STRIPE_KEY_B is absent,
      even though B's SecretStore might still have it on disk.

    The caller-resolved contract (``extra_env``) keeps spawn_session pure:
    it never reads SecretStore itself — that's follow-up #4's job.
    """
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    # Simulate SecretStore holding both projects' keys; the caller filters
    # down to project A's entries before passing to spawn_session.
    secret_store = {
        "git:example.com/repo-a": {"STRIPE_KEY_A": "sk_live_a"},
        "git:example.com/repo-b": {"STRIPE_KEY_B": "sk_live_b"},
    }

    def spawn_for(project_key, secrets):
        calls_local = _capture_spawn(monkeypatch)
        spawn.spawn_session(
            "claude-code",
            SpawnCommandOptions(directory=str(tmp_path), mode="plain"),
            project_key=project_key,
            runtime="worktree",
            extra_env=secrets,
        )
        return _env_dict_argv(calls_local[0])

    # Spawn A with its own secrets → STRIPE_KEY_A present, STRIPE_KEY_B absent.
    env_a = spawn_for("git:example.com/repo-a",
                      secret_store["git:example.com/repo-a"])
    assert env_a.get("STRIPE_KEY_A") == "sk_live_a"
    assert "STRIPE_KEY_B" not in env_a

    # Spawn A with NO secrets supplied → neither key present.
    env_a_empty = spawn_for("git:example.com/repo-a", {})
    assert "STRIPE_KEY_A" not in env_a_empty
    assert "STRIPE_KEY_B" not in env_a_empty


def test_spawn_session_extra_env_rejects_control_chars(monkeypatch, tmp_path):
    """Newline/null-byte values raise ValueError (mirrors ``_clean`` in provider_env)."""
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    _capture_spawn(monkeypatch)

    with pytest.raises(ValueError, match="Environment value must not contain"):
        spawn.spawn_session(
            "claude-code",
            SpawnCommandOptions(directory=str(tmp_path), mode="plain"),
            project_key="git:example.com/repo-a",
            runtime="worktree",
            extra_env={"BAD_KEY": "sk_live\nFOO=bar"},
        )


def test_spawn_session_extra_env_rejects_null_bytes(monkeypatch, tmp_path):
    """NUL bytes also rejected (the same control-char rule)."""
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    _capture_spawn(monkeypatch)

    with pytest.raises(ValueError, match="Environment value must not contain"):
        spawn.spawn_session(
            "claude-code",
            SpawnCommandOptions(directory=str(tmp_path), mode="plain"),
            project_key="git:example.com/repo-a",
            extra_env={"BAD_KEY": "sk_live\x00FOO"},
        )


def test_spawn_session_audit_log_records_names_without_values(monkeypatch, tmp_path):
    """The audit hook gets the names of injected vars, not the values.

    Once follow-up #10 lands (security_audit table), this hook will write
    one row per spawn with the var names. Until then, the hook is a
    logger.info call that gets asserted via monkeypatch — same surface,
    safer migration path.
    """
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    audit_calls = []

    def fake_audit(project_key, runtime, session_name, env_var_names, **kw):
        audit_calls.append({
            "project_key": project_key,
            "runtime": runtime,
            "session_name": session_name,
            "env_var_names": sorted(env_var_names),
            "kind": kw.get("kind"),
        })

    # Direct attribute swap; the module reads `spawn._record_audit` lazily
    # so this hook intercepts every env-inject from this point on.
    monkeypatch.setattr(spawn, "_record_audit", fake_audit, raising=False)

    _capture_spawn(monkeypatch)

    spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="plain"),
        project_key="git:example.com/repo-a",
        runtime="worktree",
        extra_env={"STRIPE_KEY_A": "sk_live_a", "GH_TOKEN_A": "ghp_a"},
    )

    assert len(audit_calls) == 1
    record = audit_calls[0]
    assert record["project_key"] == "git:example.com/repo-a"
    assert record["runtime"] == "worktree"
    assert record["session_name"] == "repo-abcd"
    # Names only — values must never appear in the audit log.
    assert "STRIPE_KEY_A" in record["env_var_names"]
    assert "GH_TOKEN_A" in record["env_var_names"]
    assert "COCKPIT_PROJECT_KEY" in record["env_var_names"]
    assert "COCKPIT_RUNTIME" in record["env_var_names"]
    assert all("sk_live" not in n for n in record["env_var_names"])


def test_spawn_session_default_runtime_is_worktree(monkeypatch, tmp_path):
    """When ``runtime`` isn't supplied, default to ``worktree`` for backward compat.

    Existing callers (test_runs_spawn.py, dispatch.py) don't pass ``runtime``;
    they implicitly use the worktree transport. The default keeps their
    behaviour unchanged — same tmux argv shape, same provider env.
    """
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    calls = _capture_spawn(monkeypatch)

    spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="plain"),
        project_key="git:example.com/repo-a",
    )

    env = _env_dict_argv(calls[0])
    assert env.get("COCKPIT_RUNTIME") == "worktree"


def test_cc_spawn_session_does_not_inherit_os_environ(monkeypatch):
    """Legacy Claude Code bridge: same isolation property."""
    import app.services.runs.cc_spawn as cc_spawn

    monkeypatch.setenv("HOST_LEAKED_VAR", "supersecret-bad")

    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(cc_spawn.subprocess, "run", fake_run)
    cc_spawn.get_spawned_sessions().clear()

    cc_spawn.spawn_session(
        directory="/tmp",
        mode="plain",
        project_key="git:example.com/repo-a",
        runtime="worktree",
    )

    argv = captured["cmd"]
    env = _env_dict_argv(argv)
    assert "HOST_LEAKED_VAR" not in env, (
        f"cc_spawn leaked a host env-var: {env}"
    )
    assert env.get("COCKPIT_PROJECT_KEY") == "git:example.com/repo-a"
    assert env.get("COCKPIT_RUNTIME") == "worktree"


def test_cc_spawn_session_extra_env_isolates_projects(monkeypatch):
    """Legacy bridge mirrors the new contract: caller-resolved secrets only."""
    import app.services.runs.cc_spawn as cc_spawn

    captured = []

    def fake_run(cmd, **kw):
        captured.append(cmd)
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(cc_spawn.subprocess, "run", fake_run)
    cc_spawn.get_spawned_sessions().clear()

    # Project A's spawn — A's secrets only.
    cc_spawn.spawn_session(
        directory="/tmp",
        mode="plain",
        project_key="git:example.com/repo-a",
        runtime="worktree",
        extra_env={"STRIPE_KEY_A": "sk_live_a"},
    )
    env_a = _env_dict_argv(captured[-1])
    assert env_a.get("STRIPE_KEY_A") == "sk_live_a"
    assert "STRIPE_KEY_B" not in env_a

    # Project A's spawn with NO secrets — neither B's key (would-be in store)
    # nor A's own key leaks through. Tests the cross-project isolation rule.
    cc_spawn.spawn_session(
        directory="/tmp",
        mode="plain",
        project_key="git:example.com/repo-a",
        runtime="worktree",
    )
    env_a_empty = _env_dict_argv(captured[-1])
    assert "STRIPE_KEY_A" not in env_a_empty
    assert "STRIPE_KEY_B" not in env_a_empty