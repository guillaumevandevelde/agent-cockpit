"""Setting the MiniMax API key from the UI writes it to the backend .env file and
updates the running Settings immediately (no restart needed) — it must never be
persisted to the database or echoed back in a response body."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.fixture(autouse=True)
def _isolated_env_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "minimax_api_key", None)
    yield
    monkeypatch.setattr(settings, "minimax_api_key", None)


@pytest.mark.asyncio
async def test_set_credentials_updates_settings_immediately():
    async with _client() as ac:
        r = await ac.post(
            "/api/v1/agent-bridge/platforms/minimax/credentials",
            json={"minimax_api_key": "sk-new-key"},
        )
    assert r.status_code == 200, r.text
    assert r.json() == {"configured": True}
    assert settings.minimax_api_key == "sk-new-key"


@pytest.mark.asyncio
async def test_set_credentials_never_echoes_the_key():
    async with _client() as ac:
        r = await ac.post(
            "/api/v1/agent-bridge/platforms/minimax/credentials",
            json={"minimax_api_key": "sk-super-secret"},
        )
    assert "sk-super-secret" not in r.text


@pytest.mark.asyncio
async def test_set_credentials_persists_to_env_file(tmp_path):
    async with _client() as ac:
        await ac.post(
            "/api/v1/agent-bridge/platforms/minimax/credentials",
            json={"minimax_api_key": "sk-persisted"},
        )
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "MINIMAX_API_KEY=sk-persisted\n"


@pytest.mark.asyncio
async def test_set_credentials_preserves_other_env_lines(tmp_path):
    (tmp_path / ".env").write_text("SOME_OTHER_VAR=keep-me\n", encoding="utf-8")
    async with _client() as ac:
        await ac.post(
            "/api/v1/agent-bridge/platforms/minimax/credentials",
            json={"minimax_api_key": "sk-persisted"},
        )
    content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "SOME_OTHER_VAR=keep-me" in content
    assert "MINIMAX_API_KEY=sk-persisted" in content


@pytest.mark.asyncio
async def test_set_credentials_overwrites_previous_key(tmp_path):
    (tmp_path / ".env").write_text("MINIMAX_API_KEY=sk-old\n", encoding="utf-8")
    async with _client() as ac:
        await ac.post(
            "/api/v1/agent-bridge/platforms/minimax/credentials",
            json={"minimax_api_key": "sk-new"},
        )
    content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert content == "MINIMAX_API_KEY=sk-new\n"


@pytest.mark.asyncio
async def test_set_credentials_rejects_newline_injection(tmp_path):
    async with _client() as ac:
        r = await ac.post(
            "/api/v1/agent-bridge/platforms/minimax/credentials",
            json={"minimax_api_key": "sk-test\nEVIL_VAR=1"},
        )
    assert r.status_code == 400
    assert not (tmp_path / ".env").exists()


@pytest.mark.asyncio
async def test_set_credentials_rejects_blank_key():
    async with _client() as ac:
        r = await ac.post(
            "/api/v1/agent-bridge/platforms/minimax/credentials",
            json={"minimax_api_key": "   "},
        )
    assert r.status_code in (400, 422)


@pytest.mark.asyncio
async def test_clear_credentials_removes_key_and_line(tmp_path):
    (tmp_path / ".env").write_text("MINIMAX_API_KEY=sk-old\nSOME_OTHER_VAR=keep-me\n", encoding="utf-8")
    settings.minimax_api_key = "sk-old"

    async with _client() as ac:
        r = await ac.delete("/api/v1/agent-bridge/platforms/minimax/credentials")

    assert r.status_code == 200, r.text
    assert r.json() == {"configured": False}
    assert settings.minimax_api_key is None
    content = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "MINIMAX_API_KEY" not in content
    assert "SOME_OTHER_VAR=keep-me" in content


@pytest.mark.asyncio
async def test_status_reflects_configured_after_set():
    async with _client() as ac:
        await ac.post(
            "/api/v1/agent-bridge/platforms/minimax/credentials",
            json={"minimax_api_key": "sk-abc"},
        )
        r = await ac.get("/api/v1/agent-bridge/platforms/minimax/status")
    assert r.json() == {"configured": True}
