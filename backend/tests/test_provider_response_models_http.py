"""HTTP-level tests proving the new provider response_models actually validate
FastAPI's real serialization path (calling the coroutine directly, as most
other provider tests do, bypasses response_model validation entirely)."""
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1 import providers as providers_api
from app.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_list_providers_matches_response_model():
    async with _client() as ac:
        r = await ac.get("/api/v1/providers")
    assert r.status_code == 200, r.text
    body = r.json()
    providers_api.ProviderListResponse.model_validate(body)
    assert body["count"] == len(body["providers"])


@pytest.mark.asyncio
async def test_get_provider_status_matches_response_model():
    async with _client() as ac:
        r = await ac.get("/api/v1/providers/codex-cli/status")
    assert r.status_code == 200, r.text
    providers_api.ProviderStatus.model_validate(r.json())


@pytest.mark.asyncio
async def test_get_provider_capabilities_matches_response_model():
    async with _client() as ac:
        r = await ac.get("/api/v1/providers/codex-cli/capabilities")
    assert r.status_code == 200, r.text
    providers_api.ProviderCapabilitiesResponse.model_validate(r.json())


@pytest.mark.asyncio
async def test_get_provider_doctor_matches_response_model(monkeypatch):
    class FakeExecutor:
        binary_path = "/usr/bin/codex"

        def execute(self, command, args, timeout=30):
            return SimpleNamespace(
                stdout='{"overallStatus":"ok"}', stderr="", exit_code=0,
            )

    monkeypatch.setattr(providers_api, "AgenticCliExecutor", lambda cli_id: FakeExecutor())

    async with _client() as ac:
        r = await ac.get("/api/v1/providers/codex-cli/doctor")
    assert r.status_code == 200, r.text
    providers_api.ProviderDoctorResponse.model_validate(r.json())


@pytest.mark.asyncio
async def test_get_provider_mcp_inventory_matches_response_model(monkeypatch):
    class FakeExecutor:
        binary_path = "/usr/bin/codex"

        def execute(self, command, args, timeout=30):
            return SimpleNamespace(
                stdout='{"servers":{"local":{"command":"node"}}}', stderr="", exit_code=0,
            )

    monkeypatch.setattr(providers_api, "AgenticCliExecutor", lambda cli_id: FakeExecutor())

    async with _client() as ac:
        r = await ac.get("/api/v1/providers/codex-cli/mcp")
    assert r.status_code == 200, r.text
    providers_api.ProviderMcpInventoryResponse.model_validate(r.json())


@pytest.mark.asyncio
async def test_add_provider_mcp_server_matches_response_model(monkeypatch):
    class FakeExecutor:
        binary_path = "/usr/bin/codex"

        def execute(self, command, args, timeout=30):
            return SimpleNamespace(stdout="added", stderr="", exit_code=0)

    monkeypatch.setattr(providers_api, "AgenticCliExecutor", lambda cli_id: FakeExecutor())

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/providers/codex-cli/mcp",
            json={"name": "linear", "command": "npx", "args": ["-y", "@linear/mcp"]},
        )
    assert r.status_code == 200, r.text
    providers_api.ProviderMcpMutationResponse.model_validate(r.json())


@pytest.mark.asyncio
async def test_remove_provider_mcp_server_matches_response_model(monkeypatch):
    class FakeExecutor:
        binary_path = "/usr/bin/codex"

        def execute(self, command, args, timeout=30):
            return SimpleNamespace(stdout="removed", stderr="", exit_code=0)

    monkeypatch.setattr(providers_api, "AgenticCliExecutor", lambda cli_id: FakeExecutor())

    async with _client() as ac:
        r = await ac.delete("/api/v1/providers/codex-cli/mcp/linear")
    assert r.status_code == 200, r.text
    providers_api.ProviderMcpMutationResponse.model_validate(r.json())


@pytest.mark.asyncio
async def test_get_provider_plugin_inventory_matches_response_model(monkeypatch):
    class FakeExecutor:
        binary_path = "/usr/bin/codex"

        def execute(self, command, args, timeout=30):
            return SimpleNamespace(
                stdout=(
                    f"{'PLUGIN':<28}{'STATUS':<16}{'VERSION':<12}PATH\n"
                    f"{'linear@openai-curated':<28}{'installed':<16}{'0.1.0':<12}/x\n"
                ),
                stderr="",
                exit_code=0,
            )

    monkeypatch.setattr(providers_api, "AgenticCliExecutor", lambda cli_id: FakeExecutor())

    async with _client() as ac:
        r = await ac.get("/api/v1/providers/codex-cli/plugins")
    assert r.status_code == 200, r.text
    providers_api.ProviderPluginInventoryResponse.model_validate(r.json())


@pytest.mark.asyncio
async def test_get_provider_feature_inventory_matches_response_model(monkeypatch):
    class FakeExecutor:
        binary_path = "/usr/bin/codex"

        def execute(self, command, args, timeout=30):
            return SimpleNamespace(
                stdout="goals                stable             true\n",
                stderr="",
                exit_code=0,
            )

    monkeypatch.setattr(providers_api, "AgenticCliExecutor", lambda cli_id: FakeExecutor())

    async with _client() as ac:
        r = await ac.get("/api/v1/providers/codex-cli/features")
    assert r.status_code == 200, r.text
    providers_api.ProviderFeatureInventoryResponse.model_validate(r.json())


@pytest.mark.asyncio
async def test_install_provider_plugin_matches_response_model(monkeypatch):
    class FakeExecutor:
        binary_path = "/usr/bin/codex"

        def execute(self, command, args, timeout=60):
            return SimpleNamespace(stdout="installed", stderr="", exit_code=0)

    monkeypatch.setattr(providers_api, "AgenticCliExecutor", lambda cli_id: FakeExecutor())

    async with _client() as ac:
        r = await ac.post(
            "/api/v1/providers/codex-cli/plugins",
            json={"name": "linear", "marketplace": "openai-curated"},
        )
    assert r.status_code == 200, r.text
    providers_api.ProviderPluginMutationResponse.model_validate(r.json())


@pytest.mark.asyncio
async def test_remove_provider_plugin_matches_response_model(monkeypatch):
    class FakeExecutor:
        binary_path = "/usr/bin/codex"

        def execute(self, command, args, timeout=60):
            return SimpleNamespace(stdout="removed", stderr="", exit_code=0)

    monkeypatch.setattr(providers_api, "AgenticCliExecutor", lambda cli_id: FakeExecutor())

    async with _client() as ac:
        r = await ac.delete("/api/v1/providers/codex-cli/plugins/linear")
    assert r.status_code == 200, r.text
    providers_api.ProviderPluginMutationResponse.model_validate(r.json())


@pytest.mark.asyncio
async def test_get_provider_history_diagnostics_matches_response_model():
    async with _client() as ac:
        r = await ac.get("/api/v1/providers/codex-cli/history-diagnostics")
    assert r.status_code == 200, r.text
    providers_api.ProviderHistoryDiagnosticsResponse.model_validate(r.json())


@pytest.mark.asyncio
async def test_get_provider_usage_context_diagnostics_matches_response_model():
    async with _client() as ac:
        r = await ac.get("/api/v1/providers/codex-cli/usage-context-diagnostics")
    assert r.status_code == 200, r.text
    providers_api.ProviderUsageContextDiagnosticsResponse.model_validate(r.json())
