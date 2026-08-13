"""Smoke tests for the multi-provider API surface."""
import pytest


async def test_provider_registry_smoke_exposes_claude_and_codex_statuses():
    from app.api.v1 import providers as providers_api

    response = await providers_api.list_providers()

    provider_ids = {provider["id"] for provider in response["providers"]}
    assert response["count"] == 5
    assert provider_ids == {"claude-code", "codex-cli", "copilot-cli", "mimo-code", "open-code"}

    claude_status = await providers_api.get_provider_status("claude-code")
    codex_status = await providers_api.get_provider_status("codex-cli")

    assert claude_status["capabilities"]["sessions"] is True
    assert claude_status["capabilities"]["usage"] is True
    assert codex_status["capabilities"]["sessions"] is True
    assert codex_status["capabilities"]["doctor"] is True
    assert codex_status["capabilities"]["usage"] is False


async def test_agent_bridge_session_filter_smoke(monkeypatch):
    from app.api.v1.runs import router as agent_bridge_api

    calls = []

    def fake_discover(cli=None):
        calls.append(cli)
        return [
            {
                "provider": cli or "claude-code",
                "session_name": "session-1",
                "tmux_target": "session-1:0.0",
            }
        ]

    monkeypatch.setattr(agent_bridge_api, "discover_agent_sessions", fake_discover)

    all_response = await agent_bridge_api.list_sessions(cli=None)
    codex_response = await agent_bridge_api.list_sessions(cli="codex-cli")

    assert calls == [None, "codex-cli"]
    assert all_response["count"] == 1
    assert codex_response["sessions"][0]["provider"] == "codex-cli"


@pytest.mark.asyncio
async def test_agent_bridge_spawn_smoke_passes_codex_options(monkeypatch, tmp_path):
    from app.api.v1.runs import router as agent_bridge_api

    captured = {}

    def fake_spawn(cli_id, options, session_name=None):
        captured["cli_id"] = cli_id
        captured["options"] = options
        return {
            "cli": cli_id,
            "cli_display_name": "Codex CLI",
            "tmux_target": "codex-smoke:0.0",
            "session_name": "codex-smoke",
        }

    monkeypatch.setattr(agent_bridge_api, "spawn_session", fake_spawn)

    response = await agent_bridge_api.spawn_session_endpoint(
        agent_bridge_api.SpawnRequest(
            cli="codex-cli",
            directory=str(tmp_path),
            mode="plain",
            profile="default",
            sandbox="workspace-write",
            approval_policy="on-request",
            search=True,
            no_alt_screen=True,
        ),
        db=None,
    )

    assert response["cli"] == "codex-cli"
    assert captured["cli_id"] == "codex-cli"
    assert captured["options"].profile == "default"
    assert captured["options"].sandbox == "workspace-write"
    assert captured["options"].approval_policy == "on-request"
    assert captured["options"].search is True
    assert captured["options"].no_alt_screen is True


@pytest.mark.asyncio
async def test_agent_bridge_spawn_unknown_provider_smoke(tmp_path):
    from app.api.v1.runs import router as agent_bridge_api

    with pytest.raises(agent_bridge_api.HTTPException) as exc_info:
        await agent_bridge_api.spawn_session_endpoint(
            agent_bridge_api.SpawnRequest(
                cli="unknown-provider",
                directory=str(tmp_path),
            ),
            db=None,
        )

    assert exc_info.value.status_code == 400
    assert "Unknown CLI" in exc_info.value.detail


async def test_provider_specific_inventory_smoke_rejects_wrong_provider():
    from app.api.v1 import providers as providers_api

    with pytest.raises(providers_api.HTTPException) as exc_info:
        await providers_api.get_provider_mcp_inventory("claude-code")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail["code"] == "unsupported_provider_operation"
    assert exc_info.value.detail["provider"] == "claude-code"
    assert exc_info.value.detail["operation"] == "MCP inventory"
    assert exc_info.value.detail["supported_providers"] == ["codex-cli"]
