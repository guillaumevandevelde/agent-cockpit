"""Tests for provider-aware tmux spawning."""
import json
import re
from pathlib import Path
from types import SimpleNamespace


def test_claude_worktree_uses_generated_session_name_when_blank(monkeypatch, tmp_path):
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_session_name_for", lambda directory, preferred=None: "repo-abcd")
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()

    result = spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="worktree"),
    )

    assert result["session_name"] == "repo-abcd"
    assert calls[0][:7] == ["tmux", "new-session", "-d", "-s", "repo-abcd", "-c", str(tmp_path)]
    assert "--worktree repo-abcd" in calls[0][7]
    assert spawn.get_spawned_sessions()["repo-abcd"]["worktree_name"] == "repo-abcd"


def test_claude_resume_resolves_directory_from_transcript_cwd(monkeypatch, tmp_path):
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn
    from app.services.runs import spawn as claude_spawn

    project_dir = tmp_path / "claude-deck"
    project_dir.mkdir()
    project_folder = "-tmp-claude-deck"
    session_id = "session-123"
    transcript_dir = tmp_path / ".claude" / "projects" / project_folder
    transcript_dir.mkdir(parents=True)
    transcript = transcript_dir / f"{session_id}.jsonl"
    transcript.write_text(json.dumps({"cwd": str(project_dir)}) + "\n", encoding="utf-8")

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(claude_spawn.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(spawn, "_session_name_for", lambda directory, preferred=None: "claude-deck-abcd")
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()

    result = spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(
            directory="",
            mode="resume",
            session_id=session_id,
            project_folder=project_folder,
        ),
    )

    assert result["session_name"] == "claude-deck-abcd"
    assert calls[0][:7] == ["tmux", "new-session", "-d", "-s", "claude-deck-abcd", "-c", str(project_dir)]
    assert "--resume session-123" in calls[0][7]


def test_bedrock_platform_injects_env_flags(monkeypatch, tmp_path):
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_session_name_for", lambda directory, preferred=None: "repo-abcd")
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()

    spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(
            directory=str(tmp_path),
            mode="plain",
            provider="bedrock",
            aws_region="us-east-1",
            aws_profile="bedrock-prod",
        ),
    )

    argv = calls[0]
    # Fixed prefix stays identical to the no-env command.
    assert argv[:7] == ["tmux", "new-session", "-d", "-s", "repo-abcd", "-c", str(tmp_path)]
    # Env flags are injected as -e KEY=VALUE pairs before the shell command.
    assert "-e" in argv
    assert "CLAUDE_CODE_USE_BEDROCK=1" in argv
    assert "AWS_REGION=us-east-1" in argv
    assert "AWS_PROFILE=bedrock-prod" in argv
    assert spawn.get_spawned_sessions()["repo-abcd"]["provider"] == "bedrock"


def test_codex_bedrock_platform_omits_claude_specific_env(monkeypatch, tmp_path):
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_session_name_for", lambda directory, preferred=None: "repo-abcd")
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()

    spawn.spawn_session(
        "codex-cli",
        SpawnCommandOptions(
            directory=str(tmp_path),
            mode="plain",
            provider="bedrock",
            aws_region="us-east-2",
            aws_profile="codex-bedrock",
            bedrock_model="openai.gpt-5.5",
        ),
    )

    argv = calls[0]
    assert "AWS_REGION=us-east-2" in argv
    assert "AWS_PROFILE=codex-bedrock" in argv
    assert not any(flag.startswith("CLAUDE_CODE_USE_BEDROCK=") for flag in argv)
    assert not any(flag.startswith("ANTHROPIC_MODEL=") for flag in argv)
    assert 'model_provider="amazon-bedrock"' in argv[-1]
    assert "--model openai.gpt-5.5" in argv[-1]


def test_minimax_platform_injects_configured_key_and_default_base_url(monkeypatch, tmp_path):
    from app.config import settings
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_session_name_for", lambda directory, preferred=None: "repo-abcd")
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    monkeypatch.setattr(settings, "minimax_api_key", "sk-test-key")
    monkeypatch.setattr(settings, "minimax_base_url", None)
    spawn.get_spawned_sessions().clear()

    spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="plain", provider="minimax"),
    )

    argv = calls[0]
    assert "-e" in argv
    assert "ANTHROPIC_AUTH_TOKEN=sk-test-key" in argv
    assert "ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic" in argv
    assert spawn.get_spawned_sessions()["repo-abcd"]["provider"] == "minimax"


def test_minimax_platform_uses_configured_base_url_override(monkeypatch, tmp_path):
    from app.config import settings
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_session_name_for", lambda directory, preferred=None: "repo-abcd")
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    monkeypatch.setattr(settings, "minimax_api_key", "sk-test-key")
    monkeypatch.setattr(settings, "minimax_base_url", None)
    spawn.get_spawned_sessions().clear()

    spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(
            directory=str(tmp_path),
            mode="plain",
            provider="minimax",
            minimax_base_url="https://api.minimaxi.com/anthropic",
        ),
    )

    assert "ANTHROPIC_BASE_URL=https://api.minimaxi.com/anthropic" in calls[0]


def test_minimax_platform_without_configured_key_omits_auth_token(monkeypatch, tmp_path):
    from app.config import settings
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_session_name_for", lambda directory, preferred=None: "repo-abcd")
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    monkeypatch.setattr(settings, "minimax_api_key", None)
    spawn.get_spawned_sessions().clear()

    spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="plain", provider="minimax"),
    )

    argv = calls[0]
    assert not any(flag.startswith("ANTHROPIC_AUTH_TOKEN=") for flag in argv)


def test_anthropic_platform_adds_no_env_flags(monkeypatch, tmp_path):
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_session_name_for", lambda directory, preferred=None: "repo-abcd")
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()

    spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="plain"),
    )

    argv = calls[0]
    assert "-e" not in argv
    assert argv[:7] == ["tmux", "new-session", "-d", "-s", "repo-abcd", "-c", str(tmp_path)]
    assert len(argv) == 8
    assert spawn.get_spawned_sessions()["repo-abcd"]["provider"] == "anthropic"


def test_large_prompt_is_delivered_via_temp_file_not_inlined(monkeypatch, tmp_path):
    """A prompt too large for tmux's ~16KB command-line limit must NOT be inlined
    into `tmux new-session` — tmux rejects oversized commands with 'command too
    long', which made the spawn raise and the kanban card loop into Impediment.
    The prompt is delivered via a temp file the pane reads instead, keeping the
    tmux command line tiny while claude still receives the full prompt.
    """
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_session_name_for", lambda directory, preferred=None: "repo-abcd")
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()

    big_prompt = "PLAN CONTEXT line with unique marker\n" * 1000  # ~37KB, well over the limit

    spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="plain", prompt=big_prompt),
    )

    shell_command = calls[0][-1]
    # The raw prompt must not be inlined, and the whole tmux command stays small.
    assert big_prompt not in shell_command
    assert len(shell_command) < 16000
    # ...and it is actually delivered: the pane reads it back from a temp file.
    match = re.search(r"\$\(cat (.+?)\)", shell_command)
    assert match, shell_command
    prompt_path = Path(match.group(1).strip("'\""))
    try:
        assert prompt_path.read_text(encoding="utf-8") == big_prompt
    finally:
        prompt_path.unlink(missing_ok=True)


def test_sanitize_session_name_strips_invalid_chars():
    from app.services.runs import spawn

    assert spawn._sanitize_session_name("My Feature!") == "My-Feature"
    assert spawn._sanitize_session_name("---") == ""
    assert spawn._sanitize_session_name("a" * 40) == "a" * 20
    assert spawn._sanitize_session_name("a" * 19 + "!" + "aaaa") == "a" * 19


def test_session_name_for_uses_preferred_when_free(monkeypatch):
    from app.services.runs import spawn

    monkeypatch.setattr(spawn, "_running_session_names", lambda: set())

    assert spawn._session_name_for("/tmp/whatever", preferred="my-feature") == "my-feature"


def test_session_name_for_adds_suffix_on_collision(monkeypatch):
    from app.services.runs import spawn

    monkeypatch.setattr(spawn, "_running_session_names", lambda: {"my-feature"})
    monkeypatch.setattr(spawn.uuid, "uuid4", lambda: SimpleNamespace(hex="deadbeef"))

    assert spawn._session_name_for("/tmp/whatever", preferred="my-feature") == "my-feature-dead"


def test_spawn_session_uses_worktree_name_as_session_name(monkeypatch, tmp_path):
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_running_session_names", lambda: set())
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()

    result = spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="worktree", worktree_name="my-feature"),
    )

    assert result["session_name"] == "my-feature"
    assert calls[0][:5] == ["tmux", "new-session", "-d", "-s", "my-feature"]


def test_spawn_session_sanitizes_dirty_worktree_name(monkeypatch, tmp_path):
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_running_session_names", lambda: set())
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()

    result = spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="worktree",
                            worktree_name="feature/foo bar"),
    )

    assert result["worktree_name"] == "feature/foo-bar"
    assert result["worktree_name_adjusted"] is True
    # the sanitized branch is what reaches `claude --worktree`
    assert "--worktree feature/foo-bar" in calls[0][7]
    # and what is stored so cleanup removes the real worktree
    stored = spawn.get_spawned_sessions()[result["session_name"]]
    assert stored["worktree_name"] == "feature/foo-bar"


def test_spawn_session_keeps_clean_worktree_name_unflagged(monkeypatch, tmp_path):
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    def fake_run(args, capture_output=True, text=True, timeout=10):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_running_session_names", lambda: set())
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()

    result = spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="worktree",
                            worktree_name="my-feature"),
    )

    assert result["worktree_name"] == "my-feature"
    assert result["worktree_name_adjusted"] is False


def test_spawn_session_explicit_session_name_overrides(monkeypatch, tmp_path):
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    def fake_run(args, capture_output=True, text=True, timeout=10):
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_running_session_names", lambda: set())
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()

    result = spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(directory=str(tmp_path), mode="plain"),
        session_name="custom name",
    )

    assert result["session_name"] == "custom-name"


def test_anthropic_compatible_dispatch_sets_anthropic_model_on_process_env(monkeypatch, tmp_path):
    """AC1 (kaart 293d1faa…): a dispatched `anthropic-compatible` card must
    spawn a process whose environment contains ANTHROPIC_MODEL alongside
    ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN. The dispatch transport
    (kanban/dispatch.py) populates ``options.model`` and ``options.endpoint_*``;
    ``options.bedrock_model`` stays None. The spawn layer must forward
    ``options.model`` into ``build_provider_env`` for non-Bedrock providers
    so the CLI receives the model via its env. Asserts on the tmux argv
    (the env vars the spawned process actually sees), not on intermediate
    transport kwargs.
    """
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_session_name_for", lambda directory, preferred=None: "repo-abcd")
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()

    spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(
            directory=str(tmp_path),
            mode="plain",
            provider="anthropic-compatible",
            model="claude-opus-4-8",
            endpoint_base_url="https://example.com/anthropic",
            endpoint_auth_token="sk-test-token",
        ),
    )

    argv = calls[0]
    assert "ANTHROPIC_BASE_URL=https://example.com/anthropic" in argv
    assert "ANTHROPIC_AUTH_TOKEN=sk-test-token" in argv
    assert "ANTHROPIC_MODEL=claude-opus-4-8" in argv
    assert spawn.get_spawned_sessions()["repo-abcd"]["provider"] == "anthropic-compatible"


def test_bedrock_dispatch_uses_bedrock_model_not_options_model(monkeypatch, tmp_path):
    """Bedrock-codepath preservation: when provider=bedrock and a
    ``bedrock_model`` is set, that field (not ``model``) is forwarded
    into ``build_provider_env`` so the ANTHROPIC_MODEL env var reflects
    the Bedrock-specific model id. Without this branch, Bedrock dispatch
    would silently drop the model.
    """
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_session_name_for", lambda directory, preferred=None: "repo-abcd")
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.get_spawned_sessions().clear()

    spawn.spawn_session(
        "claude-code",
        SpawnCommandOptions(
            directory=str(tmp_path),
            mode="plain",
            provider="bedrock",
            aws_region="us-east-1",
            aws_profile="bedrock-prod",
            bedrock_model="openai.gpt-5.5",
        ),
    )

    argv = calls[0]
    assert "ANTHROPIC_MODEL=openai.gpt-5.5" in argv
    assert "CLAUDE_CODE_USE_BEDROCK=1" in argv
    assert "AWS_REGION=us-east-1" in argv
