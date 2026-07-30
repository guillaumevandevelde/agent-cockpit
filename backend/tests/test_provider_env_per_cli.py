"""Per-CLI ``build_provider_env`` translation tests.

Card 2f3776dd…: ``build_provider_env`` used to be a single Claude-Code-
shaped function with one ``early-return`` for non-Claude CLIs, so
``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_AUTH_TOKEN`` / ``ANTHROPIC_MODEL``
never reached OpenCode / Codex / Copilot / MiMo. Each adapter now
declares its own env contract via ``_build_endpoint_env`` — the
per-CLI dispatch table in ``provider_env.py``. These tests pin the
per-CLI behaviour:

* Claude-Code keeps the existing Anthropic-env contract verbatim.
* OpenCode translates the generic endpoint config to ``OPENCODE_CONFIG_CONTENT``
  with an inline ``@ai-sdk/anthropic`` provider entry and a ``{env:VAR}``-
  resolved apiKey — the only zero-disk-IO mechanism OpenCode exposes for
  per-spawn custom endpoints (verified against
  https://opencode.ai/docs/config — "OPENCODE_CONFIG_CONTENT …
  allowing runtime overrides without modifying config files", plus
  ``OPENCODE_*`` precedence in the precedence table).
* Codex CLI accepts AWS env for Bedrock (it already routes Bedrock via
  its own ``--config model_provider=`` command-line flag — see
  ``codex_cli.py``) and rejects generic endpoints: Codex's native
  provider-config.toml path is out of scope for this card and the
  spawn layer cannot materialise a per-spawn ``config.toml`` without
  a dedicated helper. Carve-out: raise cleanly so the dispatcher
  surfaces the failure to the user instead of silently using the
  default Anthropic endpoint.
* Copilot and MiMo raise for every non-AWS provider — neither CLI
  exposes an endpoint-routing mechanism and we explicitly reject
  the request rather than pretending ``ANTHROPIC_BASE_URL`` is honoured.

Every test uses ``build_provider_env`` from ``provider_env.py`` (the same
public entry point spawn.py calls) so any regression in the dispatch
table breaks a test.
"""
from __future__ import annotations

import json

import pytest

# ---------------------------------------------------------------------------
# Claude-Code — regression: existing behaviour verbatim
# ---------------------------------------------------------------------------


def test_claude_code_anthropic_returns_empty():
    from app.services.agentic_cli.provider_env import (
        PROVIDER_ANTHROPIC,
        build_provider_env,
    )
    assert build_provider_env(PROVIDER_ANTHROPIC, cli_id="claude-code") == {}


def test_claude_code_unknown_provider_returns_empty():
    from app.services.agentic_cli.provider_env import build_provider_env
    assert build_provider_env("vertex", cli_id="claude-code") == {}


def test_claude_code_minimax_sets_anthropic_env_and_compact_window():
    """Claude-Code keeps the existing MiniMax contract — ANTHROPIC_* plus
    both context-window vars. This is the regression guard for
    "existing claude behavior stays exactly the same".
    """
    from app.services.agentic_cli.provider_env import (
        MINIMAX_AUTO_COMPACT_WINDOW,
        MINIMAX_BASE_URL_INTERNATIONAL,
        MINIMAX_DEFAULT_MODEL,
        MINIMAX_MAX_CONTEXT_TOKENS,
        PROVIDER_MINIMAX,
        build_provider_env,
    )
    env = build_provider_env(PROVIDER_MINIMAX, cli_id="claude-code")
    assert env == {
        "ANTHROPIC_BASE_URL": MINIMAX_BASE_URL_INTERNATIONAL,
        "ANTHROPIC_MODEL": MINIMAX_DEFAULT_MODEL,
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": MINIMAX_AUTO_COMPACT_WINDOW,
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": MINIMAX_MAX_CONTEXT_TOKENS,
    }


def test_claude_code_minimax_raises_the_ceiling_not_just_the_compact_point():
    """``AUTO_COMPACT_WINDOW`` alone cannot reach M3's 1M context.

    The CLI resolves the window as ``min(model_max, AUTO_COMPACT_WINDOW)``
    and falls back to a hardcoded 200_000 for a model it doesn't
    recognise, so the compact-window var can only ever *lower* the
    ceiling. ``CLAUDE_CODE_MAX_CONTEXT_TOKENS`` is the one that raises
    ``model_max`` (honoured for non-``claude-`` model ids). Dropping it
    silently puts every MiniMax session back on 200k — invisible until a
    long session dies on ``invalid_request: Prompt is too long``.
    """
    from app.services.agentic_cli.provider_env import (
        MINIMAX_AUTO_COMPACT_WINDOW,
        MINIMAX_DEFAULT_MODEL,
        MINIMAX_MAX_CONTEXT_TOKENS,
        PROVIDER_MINIMAX,
        build_provider_env,
    )

    env = build_provider_env(PROVIDER_MINIMAX, cli_id="claude-code")
    assert env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "1000000"
    assert env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == MINIMAX_AUTO_COMPACT_WINDOW
    assert int(MINIMAX_AUTO_COMPACT_WINDOW) <= int(MINIMAX_MAX_CONTEXT_TOKENS), (
        "auto-compact must not sit above the declared model max, or the CLI's "
        "min() silently discards it"
    )
    assert not env["ANTHROPIC_MODEL"].startswith("claude-"), (
        f"the CLI only honours CLAUDE_CODE_MAX_CONTEXT_TOKENS for non-claude- "
        f"model ids; {MINIMAX_DEFAULT_MODEL!r} must keep its bare MiniMax form"
    )


# ---------------------------------------------------------------------------
# Claude-Code-specific keys MUST NOT leak to non-Claude CLIs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cli_id", ["codex-cli", "open-code", "copilot-cli", "mimo-code"])
def test_bedrock_never_sets_claude_use_bedrock_flag_on_non_claude_cli(cli_id):
    """The non-claude branches never emit CLAUDE_CODE_USE_BEDROCK, no matter
    which CLI is asking. Today the bug exists only in MiniMax / compatible
    branches; the test pins the contract for every CLI for future-proofing.
    """
    from app.services.agentic_cli.provider_env import PROVIDER_BEDROCK, build_provider_env
    env = build_provider_env(
        PROVIDER_BEDROCK,
        region="us-east-1",
        aws_profile="x",
        model="m",
        cli_id=cli_id,
    )
    assert "CLAUDE_CODE_USE_BEDROCK" not in env


def test_minimax_never_leaks_claude_compact_window_to_opencode():
    """CLAUDE_CODE_AUTO_COMPACT_WINDOW is Claude-Code-specific; OpenCode
    (the one non-Claude CLI that successfully translates MiniMax into its
    own env contract) must never receive it. (Today the buggy code leaks
    it on PROVIDER_MINIMAX for every CLI — this test pins the fix.)
    Codex/Copilot/MiMo raise instead of returning an env for MiniMax (see
    the dedicated raise-tests below), so they're out of scope here — there
    is no returned dict to inspect for a leak.
    """
    from app.services.agentic_cli.provider_env import PROVIDER_MINIMAX, build_provider_env
    env = build_provider_env(PROVIDER_MINIMAX, cli_id="open-code")
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in env
    assert "CLAUDE_CODE_MAX_CONTEXT_TOKENS" not in env


@pytest.mark.parametrize("cli_id", ["copilot-cli", "mimo-code"])
def test_compatible_never_leaks_anthropic_env_to_clis_without_endpoint_support(cli_id):
    """Copilot / MiMo do not honour ANTHROPIC_BASE_URL. The provider-env
    builder must NOT silently emit those keys for them — leaking them would
    pretend the routing works while the spawned CLI ignores them entirely.
    """
    from app.services.agentic_cli.provider_env import PROVIDER_COMPATIBLE, build_provider_env
    with pytest.raises(ValueError, match="endpoint"):
        build_provider_env(
            PROVIDER_COMPATIBLE,
            base_url="https://api.example.com/anthropic",
            model="m",
            cli_id=cli_id,
        )


@pytest.mark.parametrize("cli_id", ["copilot-cli", "mimo-code"])
def test_minimax_raises_for_clis_without_endpoint_support(cli_id):
    from app.services.agentic_cli.provider_env import PROVIDER_MINIMAX, build_provider_env
    with pytest.raises(ValueError, match="endpoint"):
        build_provider_env(PROVIDER_MINIMAX, cli_id=cli_id)


# ---------------------------------------------------------------------------
# Codex CLI — only Bedrock supported; everything else rejected cleanly
# ---------------------------------------------------------------------------


def test_codex_anthropic_returns_empty():
    """Default Claude/Anthropic API needs no env override for Codex. The
    spawn command is unchanged and the spawned Codex calls the Anthropic
    endpoint via its own provider config."""
    from app.services.agentic_cli.provider_env import PROVIDER_ANTHROPIC, build_provider_env
    assert build_provider_env(PROVIDER_ANTHROPIC, cli_id="codex-cli") == {}


def test_codex_compatible_raises_with_actionable_message():
    """Codex's native endpoint-routing lives in ``config.toml``; the spawn
    layer does not yet materialise a per-spawn config file. The provider-env
    builder signals this carve-out by raising — better a clean failure than
    silently routing to the default Anthropic endpoint while the user
    believes they've routed to a custom one.
    """
    from app.services.agentic_cli.provider_env import PROVIDER_COMPATIBLE, build_provider_env
    with pytest.raises(ValueError) as excinfo:
        build_provider_env(
            PROVIDER_COMPATIBLE,
            base_url="https://api.example.com/anthropic",
            model="m",
            cli_id="codex-cli",
        )
    assert "codex-cli" in str(excinfo.value)
    assert "config.toml" in str(excinfo.value)


def test_codex_minimax_raises_with_actionable_message():
    from app.services.agentic_cli.provider_env import PROVIDER_MINIMAX, build_provider_env
    with pytest.raises(ValueError, match="codex-cli"):
        build_provider_env(PROVIDER_MINIMAX, cli_id="codex-cli")


# ---------------------------------------------------------------------------
# OpenCode CLI — full per-spawn endpoint translation via OPENCODE_CONFIG_CONTENT
# ---------------------------------------------------------------------------


def test_opencode_anthropic_returns_empty():
    from app.services.agentic_cli.provider_env import PROVIDER_ANTHROPIC, build_provider_env
    assert build_provider_env(PROVIDER_ANTHROPIC, cli_id="open-code") == {}


def test_opencode_bedrock_only_sets_shared_aws_env():
    """OpenCode's Bedrock support reads AWS_* env vars natively (per
    OpenCode docs: AWS_PROFILE, AWS_REGION, AWS_BEARER_TOKEN_BEDROCK).
    Claude-Code-specific flags must not leak."""
    from app.services.agentic_cli.provider_env import PROVIDER_BEDROCK, build_provider_env
    env = build_provider_env(
        PROVIDER_BEDROCK,
        region="us-east-2",
        aws_profile="opencode-bedrock",
        model="anthropic.claude-opus-4-8",
        cli_id="open-code",
    )
    assert env == {
        "AWS_REGION": "us-east-2",
        "AWS_PROFILE": "opencode-bedrock",
    }
    assert "CLAUDE_CODE_USE_BEDROCK" not in env
    assert "ANTHROPIC_MODEL" not in env


def test_opencode_compatible_emits_config_content_with_provider_entry():
    """OpenCode accepts inline config via ``OPENCODE_CONFIG_CONTENT`` (per
    https://opencode.ai/docs/config — "…allowing runtime overrides without
    modifying config files"). The minimal Anthropic-compatible provider
    entry is the one documented at
    https://opencode.ai/docs/providers/ — ``npm: "@ai-sdk/anthropic"``
    with options.baseURL + options.apiKey (the latter using ``{env:VAR}``),
    plus a single model declaration.

    This is the canonical proof that a non-Claude CLI can be routed to a
    non-default endpoint through ``build_provider_env``: OpenCode reads
    the env at startup and resolves the provider config from the JSON
    string — no on-disk state, no clobbered user config.
    """
    from app.services.agentic_cli.provider_env import (
        PROVIDER_COMPATIBLE,
        build_provider_env,
    )
    env = build_provider_env(
        PROVIDER_COMPATIBLE,
        base_url="https://api.example.com/anthropic",
        model="claude-sonnet-4-5",
        auth_token="sk-test-token",
        cli_id="open-code",
    )

    assert "OPENCODE_CONFIG_CONTENT" in env
    assert env["CCK_OPENCODE_AUTH_TOKEN"] == "sk-test-token" or any(
        k.startswith("CCK_OPENCODE") for k in env
    ), "OpenCode adapter must expose the resolved auth token via an env var"

    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    # provider is a dict keyed by the chosen provider id.
    providers = config["provider"]
    assert len(providers) == 1
    provider_id, provider = next(iter(providers.items()))
    assert provider["npm"] == "@ai-sdk/anthropic"
    assert provider["options"]["baseURL"] == "https://api.example.com/anthropic"
    # The apiKey is always a {env:VAR} substitution — never a literal —
    # so the secret never lives in the config string passed to tmux.
    api_key_value = provider["options"]["apiKey"]
    assert api_key_value.startswith("{env:")
    assert api_key_value.endswith("}")
    referenced_var = api_key_value[len("{env:"):-1]
    assert env[referenced_var] == "sk-test-token"
    # Model is declared with at least one entry referencing the requested model.
    model_ids = list(provider["models"].keys())
    assert model_ids == ["claude-sonnet-4-5"]
    # Stable, model-independent provider id: open_code.py's
    # build_spawn_command needs to construct "--model <id>/<model>" without
    # inspecting this generated config, so both sides agree on a fixed
    # constant rather than a value derived from the (variable) model string.
    from app.services.agentic_cli.provider_env import OPEN_CODE_ENDPOINT_PROVIDER_ID
    assert provider_id == OPEN_CODE_ENDPOINT_PROVIDER_ID


def test_opencode_compatible_without_auth_token_still_emits_config():
    """A configured endpoint may rely on ambient credentials (the spec
    already supports this via ``credential_name=None``). The
    ``{env:VAR}`` substitution remains; the env var is just absent."""
    from app.services.agentic_cli.provider_env import (
        PROVIDER_COMPATIBLE,
        build_provider_env,
    )
    env = build_provider_env(
        PROVIDER_COMPATIBLE,
        base_url="https://api.example.com/anthropic",
        model="m",
        auth_token=None,
        cli_id="open-code",
    )
    assert "OPENCODE_CONFIG_CONTENT" in env
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    provider = next(iter(config["provider"].values()))
    api_key_value = provider["options"]["apiKey"]
    referenced_var = api_key_value[len("{env:"):-1]
    assert referenced_var not in env


def test_opencode_minimax_translates_via_config_content():
    """OpenCode MiniMax path: route via the same OPENCODE_CONFIG_CONTENT
    mechanism, just with MiniMax defaults — base URL and model — when the
    caller does not pin them. This is the OpenCode counterpart of the
    Claude-Code MiniMax branch and is the second "free, on its own
    CLI" path the card wanted to unlock.
    """
    from app.services.agentic_cli.provider_env import (
        MINIMAX_BASE_URL_INTERNATIONAL,
        MINIMAX_DEFAULT_MODEL,
        PROVIDER_MINIMAX,
        build_provider_env,
    )
    env = build_provider_env(PROVIDER_MINIMAX, cli_id="open-code")
    assert "OPENCODE_CONFIG_CONTENT" in env
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    provider = next(iter(config["provider"].values()))
    assert provider["options"]["baseURL"] == MINIMAX_BASE_URL_INTERNATIONAL
    model_ids = list(provider["models"].keys())
    assert model_ids == [MINIMAX_DEFAULT_MODEL]
    # Claude-Code-specific compact window never leaks to OpenCode.
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in env


# ---------------------------------------------------------------------------
# OpenCode CLI --model wiring — the injected config and the spawned CLI
# argv must agree on the provider id, or the resolved endpoint is
# unreachable even though the env looks correct.
# ---------------------------------------------------------------------------


def test_opencode_build_spawn_command_prefixes_model_for_compatible_provider():
    """``build_spawn_command`` must send ``--model <provider_id>/<model>``
    for the ``anthropic-compatible`` provider — OpenCode's own ``--model``
    flag requires the ``provider/model`` form (confirmed against the real
    CLI: a bare model id raises ``ProviderModelNotFoundError``). The
    provider id must be the same constant ``_build_opencode_endpoint_env``
    used as the injected config's provider key, or the resolved endpoint is
    unreachable even though ``OPENCODE_CONFIG_CONTENT`` looks correct.
    """
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.agentic_cli.open_code import OpenCodeCli
    from app.services.agentic_cli.provider_env import (
        OPEN_CODE_ENDPOINT_PROVIDER_ID,
        PROVIDER_COMPATIBLE,
    )

    command = OpenCodeCli().build_spawn_command(SpawnCommandOptions(
        directory="/tmp", mode="plain",
        provider=PROVIDER_COMPATIBLE, model="claude-sonnet-4-5",
    ))
    assert "--model" in command
    idx = command.index("--model")
    assert command[idx + 1] == f"{OPEN_CODE_ENDPOINT_PROVIDER_ID}/claude-sonnet-4-5"


def test_opencode_build_spawn_command_prefixes_model_for_minimax_provider():
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.agentic_cli.open_code import OpenCodeCli
    from app.services.agentic_cli.provider_env import (
        OPEN_CODE_ENDPOINT_PROVIDER_ID,
        PROVIDER_MINIMAX,
    )

    command = OpenCodeCli().build_spawn_command(SpawnCommandOptions(
        directory="/tmp", mode="plain",
        provider=PROVIDER_MINIMAX, model="MiniMax-M3",
    ))
    idx = command.index("--model")
    assert command[idx + 1] == f"{OPEN_CODE_ENDPOINT_PROVIDER_ID}/MiniMax-M3"


def test_opencode_build_spawn_command_does_not_prefix_model_for_native_anthropic():
    """The default ``anthropic`` provider uses OpenCode's own auth/config;
    ``options.model`` is expected to already be in ``provider/model`` form
    (e.g. an OpenCode-native model id) and must pass through unchanged —
    prefixing it would break normal, non-endpoint model selection.
    """
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.agentic_cli.open_code import OpenCodeCli

    command = OpenCodeCli().build_spawn_command(SpawnCommandOptions(
        directory="/tmp", mode="plain",
        provider="anthropic", model="anthropic/claude-sonnet-4-6",
    ))
    idx = command.index("--model")
    assert command[idx + 1] == "anthropic/claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# Explicit "not implemented" — not silent
# ---------------------------------------------------------------------------


def test_unknown_cli_id_bedrock_gets_bare_shared_env_like_any_other_cli():
    """An unregistered/future CLI id is treated exactly like Codex/OpenCode/
    Copilot/MiMo for Bedrock: shared, non-secret AWS env only, no
    Claude-Code flags. This preserves the pre-existing contract (any
    ``cli_id != "claude-code"`` gets the bare env) for CLIs this module
    doesn't know about yet — safer than guessing they want Claude's flags.
    """
    from app.services.agentic_cli.provider_env import (
        PROVIDER_BEDROCK,
        build_provider_env,
    )
    env = build_provider_env(
        PROVIDER_BEDROCK,
        region="us-east-1",
        model="m",
        cli_id="some-future-cli",
    )
    assert env == {"AWS_REGION": "us-east-1"}
    assert "CLAUDE_CODE_USE_BEDROCK" not in env


def test_unknown_cli_id_compatible_raises_explicitly_not_silently():
    """An unregistered CLI id gets the same explicit refusal as Copilot/MiMo
    for endpoint-routing providers — never a silent no-op that pretends the
    routing worked. This is the acceptance criterion: "CLI ondersteunt geen
    custom endpoint" as an explicit, non-silent case applies even to CLIs
    this module has never heard of.
    """
    from app.services.agentic_cli.provider_env import (
        PROVIDER_COMPATIBLE,
        build_provider_env,
    )
    with pytest.raises(ValueError, match="endpoint"):
        build_provider_env(
            PROVIDER_COMPATIBLE,
            base_url="https://api.example.com",
            model="m",
            cli_id="some-future-cli",
        )


# ---------------------------------------------------------------------------
# kaart 27317b4871… (FCR gap 8): pin the final ANTHROPIC_* env dict for
# PROVIDER_COMPATIBLE on Claude-Code, the canonical "happy path" the
# dispatcher sends every anthropic-compatible card through. A regression
# that drops ``ANTHROPIC_AUTH_TOKEN`` between resolve and env-merge
# would not be caught by transport-recording tests (they capture kwargs,
# not the env dict), so we pin the env directly here.
# ---------------------------------------------------------------------------


def test_claude_code_compatible_pins_full_anthropic_env_dict():
    """Positive: all three carriers (base_url, model, auth_token) set →
    the canonical Claude-Code Anthropic-compatible env contract."""
    from app.services.agentic_cli.provider_env import (
        PROVIDER_COMPATIBLE,
        build_provider_env,
    )
    env = build_provider_env(
        PROVIDER_COMPATIBLE,
        base_url="https://router.example.com/v1",
        model="claude-sonnet-4-6",
        auth_token="sk-test-secret",
        cli_id="claude-code",
    )
    assert env == {
        "ANTHROPIC_BASE_URL": "https://router.example.com/v1",
        "ANTHROPIC_MODEL": "claude-sonnet-4-6",
        "ANTHROPIC_AUTH_TOKEN": "sk-test-secret",
    }


def test_claude_code_compatible_without_auth_token_omits_token():
    """Ambient-credential pattern: ``credential_name=None`` → caller is
    expected to find the credential in its own environment, and the
    explicit ``ANTHROPIC_AUTH_TOKEN`` key is omitted so a stale ambient
    one can't be silently clobbered (or vice-versa)."""
    from app.services.agentic_cli.provider_env import (
        PROVIDER_COMPATIBLE,
        build_provider_env,
    )
    env = build_provider_env(
        PROVIDER_COMPATIBLE,
        base_url="https://router.example.com/v1",
        model="claude-sonnet-4-6",
        cli_id="claude-code",
    )
    assert env == {
        "ANTHROPIC_BASE_URL": "https://router.example.com/v1",
        "ANTHROPIC_MODEL": "claude-sonnet-4-6",
    }
    assert "ANTHROPIC_AUTH_TOKEN" not in env


def test_claude_code_compatible_strips_whitespace_only_credentials():
    """A whitespace-only auth_token must be treated the same as None —
    otherwise the dispatched CLI would inherit the host's ambient
    ANTHROPIC_AUTH_TOKEN (potentially the production Anthropic key)
    instead of the named endpoint's credential. ``build_provider_env``
    calls ``_clean`` which strips; the test pins that discipline."""
    from app.services.agentic_cli.provider_env import (
        PROVIDER_COMPATIBLE,
        build_provider_env,
    )
    env = build_provider_env(
        PROVIDER_COMPATIBLE,
        base_url="https://router.example.com/v1",
        model="claude-sonnet-4-6",
        auth_token="   ",
        cli_id="claude-code",
    )
    assert "ANTHROPIC_AUTH_TOKEN" not in env
