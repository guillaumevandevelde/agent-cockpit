"""Tests for provider-aware tmux spawning."""
import json
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
            platform="bedrock",
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
    assert spawn.get_spawned_sessions()["repo-abcd"]["platform"] == "bedrock"


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
            platform="bedrock",
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
        SpawnCommandOptions(directory=str(tmp_path), mode="plain", platform="minimax"),
    )

    argv = calls[0]
    assert "-e" in argv
    assert "ANTHROPIC_AUTH_TOKEN=sk-test-key" in argv
    assert "ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic" in argv
    assert spawn.get_spawned_sessions()["repo-abcd"]["platform"] == "minimax"


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
            platform="minimax",
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
        SpawnCommandOptions(directory=str(tmp_path), mode="plain", platform="minimax"),
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
    assert spawn.get_spawned_sessions()["repo-abcd"]["platform"] == "anthropic"


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
