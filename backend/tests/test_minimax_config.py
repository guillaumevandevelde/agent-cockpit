"""MiniMax credentials live only in the backend process environment (Settings),
never in the database or the API response body."""
from app.config import Settings


def test_minimax_api_key_defaults_to_none():
    assert Settings().minimax_api_key is None


def test_minimax_base_url_defaults_to_none():
    assert Settings().minimax_base_url is None


def test_minimax_settings_are_overridable_via_env(monkeypatch):
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-test-key")
    monkeypatch.setenv("MINIMAX_BASE_URL", "https://api.minimax.io/anthropic")
    s = Settings()
    assert s.minimax_api_key == "sk-test-key"
    assert s.minimax_base_url == "https://api.minimax.io/anthropic"
