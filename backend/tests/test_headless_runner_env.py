"""Headless runner ``_build_env`` provider-parity tests.

Kaart 88f3c990…: ``headless_runner._build_env`` builds the explicit env
for the spawned subprocess. Compared to ``services/runs/spawn.py`` it
must reach provider-parity: MiniMax credentials + base URL, and the
``anthropic-compatible`` endpoint's ``base_url`` / ``auth_token`` must
all be threaded through, AND the ``cli_id`` must be threaded (not
hard-coded) so Claude-Code-specific env vars don't leak to a non-Claude
CLI.

These tests pin that contract at two levels:

1. **Unit tests on ``_build_env``** — fast feedback that the dict shape
   matches the worktree transport's output (provider-parity). Each test
   pins one provider × one CLI combination.

2. **Integration test on ``run_headless`` subprocess env** — the fake
   CLI writes ``os.environ`` to a file before exiting, so the test
   proves the env actually reaches the spawned process (the AC's
   "bewijs op de env van het gespawnde subprocess, niet op een
   unit-test van de dict alleen" requirement).
"""
from __future__ import annotations

import json
import sys as stdlib_sys

import pytest

import app.kanban.headless_runner as hr

# ---------------------------------------------------------------------------
# Unit tests on _build_env: provider-parity with services/runs/spawn.py
# ---------------------------------------------------------------------------


def test_build_env_threads_cli_id_not_hardcoded_for_opencode_minimax(monkeypatch):
    """The OpenCode MiniMax path is the second provider beyond Claude-Code
    that successfully routes through ``build_provider_env``. With the
    pre-fix hard-coded ``cli_id="claude-code"`` the subprocess would
    inherit Claude-Code-specific ``CLAUDE_CODE_AUTO_COMPACT_WINDOW``
    plus ``ANTHROPIC_*`` keys it cannot honour. With the fix, the env
    is exactly the OpenCode ``OPENCODE_CONFIG_CONTENT``-based contract.
    """
    from app.config import settings
    from app.services.agentic_cli.provider_env import (
        MINIMAX_BASE_URL_INTERNATIONAL,
        PROVIDER_MINIMAX,
    )

    monkeypatch.setattr(settings, "minimax_api_key", "sk-minimax-test")
    monkeypatch.setattr(settings, "minimax_base_url", MINIMAX_BASE_URL_INTERNATIONAL)

    env = hr._build_env(
        cli_id="open-code",
        provider=PROVIDER_MINIMAX,
        model=None,
        project_key=None,
    )
    # OpenCode's MiniMax contract: OPENCODE_CONFIG_CONTENT, NO
    # ANTHROPIC_* keys, NO CLAUDE_CODE_AUTO_COMPACT_WINDOW.
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in env
    assert "OPENCODE_CONFIG_CONTENT" in env


def test_build_env_threads_cli_id_for_codex_anthropic_compatible(monkeypatch):
    """Codex CLI rejects anthropic-compatible routing (no ANTHROPIC_BASE_URL
    mechanism wired). With ``cli_id`` threaded through, ``_build_env``
    must surface the same explicit ValueError the worktree path
    produces, instead of silently using the default Anthropic endpoint
    (the bug the original card description called out: ``anthropic-
    compatible`` going through headless used to throw a generic
    ``ValueError`` with no actionable message).
    """
    from app.services.agentic_cli.provider_env import PROVIDER_COMPATIBLE

    with pytest.raises(ValueError, match="codex-cli"):
        hr._build_env(
            cli_id="codex-cli",
            provider=PROVIDER_COMPATIBLE,
            model="claude-sonnet-4-5",
            project_key=None,
            endpoint_base_url="https://router.example.com/anthropic",
            endpoint_auth_token="sk-test",
        )


def test_build_env_passes_minimax_api_key_to_subprocess_env(monkeypatch):
    """The pre-fix ``_build_env`` did NOT pass ``minimax_api_key`` from
    settings, so the MiniMax subprocess authenticated with whatever
    ambient ``ANTHROPIC_AUTH_TOKEN`` happened to be in the host env
    (or failed silently with no token at all). After the fix, the
    Claude-Code MiniMax branch receives its credential via
    ``ANTHROPIC_AUTH_TOKEN`` exactly like the worktree transport
    produces.
    """
    from app.config import settings
    from app.services.agentic_cli.provider_env import (
        MINIMAX_BASE_URL_INTERNATIONAL,
        PROVIDER_MINIMAX,
    )

    monkeypatch.setattr(settings, "minimax_api_key", "sk-minimax-secret")
    monkeypatch.setattr(settings, "minimax_base_url", MINIMAX_BASE_URL_INTERNATIONAL)

    env = hr._build_env(
        cli_id="claude-code",
        provider=PROVIDER_MINIMAX,
        model=None,
        project_key=None,
    )
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-minimax-secret"
    assert env["ANTHROPIC_BASE_URL"] == MINIMAX_BASE_URL_INTERNATIONAL


def test_build_env_passes_minimax_base_url_override(monkeypatch):
    """The MiniMax provider has two base URLs (international + China) —
    ``settings.minimax_base_url`` overrides the international default
    when configured. The worktree transport honours it via
    ``options.minimax_base_url or settings.minimax_base_url``. Headless
    has no ``SpawnCommandOptions`` carrier, so it consumes settings
    directly. Pin that the override reaches the subprocess env."""
    from app.config import settings
    from app.services.agentic_cli.provider_env import (
        MINIMAX_BASE_URL_CHINA,
        PROVIDER_MINIMAX,
    )

    monkeypatch.setattr(settings, "minimax_api_key", "sk-minimax-secret")
    monkeypatch.setattr(settings, "minimax_base_url", MINIMAX_BASE_URL_CHINA)

    env = hr._build_env(
        cli_id="claude-code",
        provider=PROVIDER_MINIMAX,
        model=None,
        project_key=None,
    )
    assert env["ANTHROPIC_BASE_URL"] == MINIMAX_BASE_URL_CHINA


def test_build_env_passes_anthropic_compatible_endpoint_through(monkeypatch):
    """Regression guard for the kaart 27317b4871… fix: the dispatcher's
    ``endpoint_base_url`` / ``endpoint_auth_token`` must reach the
    subprocess env. The kwargs already worked after FCR gap 7; pin that
    provider-parity here so a future refactor that drops them again
    fails this test."""
    from app.services.agentic_cli.provider_env import PROVIDER_COMPATIBLE

    env = hr._build_env(
        cli_id="claude-code",
        provider=PROVIDER_COMPATIBLE,
        model="claude-sonnet-4-6",
        project_key=None,
        endpoint_base_url="https://router.example.com/anthropic",
        endpoint_auth_token="sk-compatible-secret",
    )
    assert env["ANTHROPIC_BASE_URL"] == "https://router.example.com/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-compatible-secret"
    assert env["ANTHROPIC_MODEL"] == "claude-sonnet-4-6"


def test_build_env_does_not_merge_os_environ(monkeypatch, tmp_path):
    """Contract: ``_build_env`` only injects explicit vars — never
    ``os.environ``. A leak here would silently route a MiniMax dispatch
    through whatever ambient ANTHROPIC_* vars the backend process
    happened to carry. Pin that the dict contains ONLY what we asked
    for."""
    from app.services.agentic_cli.provider_env import PROVIDER_ANTHROPIC

    sentinel = "leaked-from-host-env"
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", sentinel)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://should-not-leak.example.com")

    env = hr._build_env(
        cli_id="claude-code",
        provider=PROVIDER_ANTHROPIC,
        model=None,
        project_key=None,
    )
    assert sentinel not in env.values()
    assert "https://should-not-leak.example.com" not in env.values()


def test_build_env_default_anthropic_returns_minimal_env():
    """The default Anthropic provider needs no env override — the
    subprocess inherits the host's ANTHROPIC_* env (which is correct,
    since the credential is meant to be ambient for this provider).
    ``_build_env`` returns an empty dict so COCKPIT_* bookkeeping
    keys are added by ``build_spawn_env`` without any provider vars."""
    from app.services.agentic_cli.provider_env import PROVIDER_ANTHROPIC

    env = hr._build_env(
        cli_id="claude-code",
        provider=PROVIDER_ANTHROPIC,
        model=None,
        project_key=None,
    )
    # No provider-env keys — only the COCKPIT_* bookkeeping (or none).
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "CLAUDE_CODE_USE_BEDROCK" not in env


def test_build_env_sets_cockpit_runtime_to_headless():
    """``build_spawn_env`` injects ``COCKPIT_RUNTIME=headless`` — this
    pins the contract that headless's env-merging step runs through
    the shared ``build_spawn_env`` helper (provider-parity with
    ``services/runs/spawn.py``). A regression that bypasses
    ``build_spawn_env`` would skip the COCKPIT_* bookkeeping and
    the runtime-aware audit log, and would fail this test."""
    from app.services.agentic_cli.provider_env import PROVIDER_ANTHROPIC

    env = hr._build_env(
        cli_id="claude-code",
        provider=PROVIDER_ANTHROPIC,
        model=None,
        project_key="proj-test",
    )
    assert env["COCKPIT_PROJECT_KEY"] == "proj-test"
    assert env["COCKPIT_RUNTIME"] == "headless"


# ---------------------------------------------------------------------------
# Integration: the env actually reaches the spawned subprocess.
#
# The card's AC: "Een headless run met provider=minimax authenticeert
# daadwerkelijk — bewijs op de env van het gespawnde subprocess, niet
# op een unit-test van de dict alleen." We run ``run_headless`` with a
# fake_cli that dumps ``os.environ`` to a JSON file before exiting, then
# assert that the JSON contains the resolved MiniMax credential.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_headless_minimax_subprocess_sees_auth_token(
    monkeypatch, tmp_path,
):
    """End-to-end: a MiniMax headless run's subprocess actually receives
    the resolved ``ANTHROPIC_AUTH_TOKEN`` from settings. The fake CLI
    writes ``os.environ`` to a JSON file before exiting; the test reads
    it back and asserts the credential is present.
    """
    from app.config import settings
    from app.services.agentic_cli.provider_env import MINIMAX_BASE_URL_INTERNATIONAL

    monkeypatch.setattr(settings, "minimax_api_key", "sk-minimax-e2e-secret")
    monkeypatch.setattr(settings, "minimax_base_url", MINIMAX_BASE_URL_INTERNATIONAL)

    env_dump = tmp_path / "subprocess_env.json"
    pidfile = tmp_path / "fake_cli.pid"
    fake_cli = tmp_path / "fake_cli.py"
    fake_cli.write_text(
        "import json, sys, os\n"
        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        # Dump the env BEFORE emitting the events so the dump is on
        # disk even if the parser kills the process.
        f"open({str(env_dump)!r}, 'w').write(json.dumps(dict(os.environ)))\n"
        "def emit(p): sys.stdout.write(json.dumps(p) + '\\n'); sys.stdout.flush()\n"
        "emit({'type':'system','subtype':'init','session_id':'sess-e2e',"
        "     'cwd':'.','model':'claude-opus-4-8','permissionMode':'acceptEdits'})\n"
        "emit({'type':'result','subtype':'success','is_error':False,"
        "     'duration_ms':1,'total_cost_usd':0.0,'num_turns':1,"
        "     'usage':{'input_tokens':1,'output_tokens':1}})\n"
        "sys.exit(0)\n"
    )
    wrapper = tmp_path / "fake_cli.sh"
    wrapper.write_text(
        f"#!/bin/sh\nexec {stdlib_sys.executable} '{fake_cli}' \"$@\"\n"
    )
    wrapper.chmod(0o755)
    monkeypatch.setattr(hr, "resolve_cli_executable", lambda cli_id: str(wrapper))

    result = await hr.run_headless(
        cli_id="claude-code", directory=str(tmp_path), prompt="hi",
        session_name="k-fixture-minimax", skip_permissions=True,
        provider="minimax", model=None,
    )
    assert result["exit_code"] == 0

    # The subprocess actually saw the credential.
    assert env_dump.exists(), "fake_cli did not write subprocess env"
    sub_env = json.loads(env_dump.read_text())
    assert sub_env.get("ANTHROPIC_AUTH_TOKEN") == "sk-minimax-e2e-secret"
    assert sub_env.get("ANTHROPIC_BASE_URL") == MINIMAX_BASE_URL_INTERNATIONAL
