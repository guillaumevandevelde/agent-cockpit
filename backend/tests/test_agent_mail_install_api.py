import pytest
from httpx import ASGITransport, AsyncClient

from app.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
async def _create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_install_status_reports_missing_when_nothing_installed(tmp_path, monkeypatch):
    from app.services.agent_mail import codex_hooks, hook_installer
    monkeypatch.setattr(hook_installer, "get_claude_user_settings_file", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(codex_hooks, "get_codex_home", lambda: tmp_path / "codex")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/agent-mail/install/status")
        assert r.status_code == 200
        body = r.json()
        assert set(body["claude_code_hooks_missing"]) == {"SessionStart", "UserPromptSubmit", "SessionEnd", "PostToolUse"}


@pytest.mark.asyncio
async def test_apply_claude_code_requires_confirmation(tmp_path, monkeypatch):
    from app.services.agent_mail import hook_installer
    monkeypatch.setattr(hook_installer, "get_claude_user_settings_file", lambda: tmp_path / "settings.json")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/agent-mail/install/claude-code/apply", json={})
        assert r.status_code == 400

        r2 = await client.post("/api/v1/agent-mail/install/claude-code/apply", json={"confirmed": True})
        assert r2.status_code == 200
        assert r2.json()["claude_code_hooks_missing"] == []


@pytest.mark.asyncio
async def test_snippets_endpoint_returns_codex_hook_snippet():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/agent-mail/install/snippets")
        assert r.status_code == 200
        assert "codex_hook_shim.py" in r.json()["codex_hooks_snippet"]
