"""Tests for the Telegram outbound notifier.

The notifier is the durable answer to cockpit-richting-decision.md §4: push
on breakage or blockage only. A failure here must never crash the caller —
the reconciler and CI workflow rely on that contract.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.services.notifications import telegram as tg


def _mock_response(status_code: int = 200, text: str = "") -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    return r


class _FakeClient:
    """Async context manager with a stubbed ``.post`` for httpx."""

    def __init__(self, status: int = 200, text: str = "") -> None:
        self._resp = _mock_response(status, text)
        self.post = AsyncMock(return_value=self._resp)

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *exc) -> None:
        return None


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "TOKEN", raising=False)
    monkeypatch.setattr(settings, "telegram_chat_id", "CHAT", raising=False)


async def test_no_op_when_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", None, raising=False)
    monkeypatch.setattr(settings, "telegram_chat_id", "CHAT", raising=False)

    with patch("app.services.notifications.telegram.httpx.AsyncClient") as MockClient:
        ok = await tg.send_telegram("hello")

    assert ok is False
    MockClient.assert_not_called()


async def test_no_op_when_chat_id_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", "TOKEN", raising=False)
    monkeypatch.setattr(settings, "telegram_chat_id", None, raising=False)

    with patch("app.services.notifications.telegram.httpx.AsyncClient") as MockClient:
        ok = await tg.send_telegram("hello")

    assert ok is False
    MockClient.assert_not_called()


async def test_sends_to_correct_url_with_text(configured) -> None:
    captured: dict = {}

    with patch("app.services.notifications.telegram.httpx.AsyncClient") as MockClient:
        MockClient.return_value = _FakeClient(200)
        ok = await tg.send_telegram("hello world")
        captured["args"], captured["kwargs"] = MockClient.return_value.post.call_args

    assert ok is True
    args, kwargs = captured["args"], captured["kwargs"]
    assert args[0] == "https://api.telegram.org/botTOKEN/sendMessage"
    assert kwargs["json"] == {"chat_id": "CHAT", "text": "hello world"}


async def test_returns_false_on_http_error(configured) -> None:
    with patch("app.services.notifications.telegram.httpx.AsyncClient") as MockClient:
        MockClient.return_value = _FakeClient(403, "forbidden")
        ok = await tg.send_telegram("hello")

    assert ok is False


async def test_returns_false_on_exception(configured) -> None:
    with patch(
        "app.services.notifications.telegram.httpx.AsyncClient",
        side_effect=RuntimeError("network down"),
    ):
        ok = await tg.send_telegram("hello")

    assert ok is False


async def test_passes_parse_mode(configured) -> None:
    captured: dict = {}

    with patch("app.services.notifications.telegram.httpx.AsyncClient") as MockClient:
        MockClient.return_value = _FakeClient(200)
        await tg.send_telegram("*bold*", parse_mode="MarkdownV2")
        captured["kwargs"] = MockClient.return_value.post.call_args.kwargs

    assert captured["kwargs"]["json"]["parse_mode"] == "MarkdownV2"


async def test_omits_parse_mode_when_unset(configured) -> None:
    captured: dict = {}

    with patch("app.services.notifications.telegram.httpx.AsyncClient") as MockClient:
        MockClient.return_value = _FakeClient(200)
        await tg.send_telegram("plain")
        captured["kwargs"] = MockClient.return_value.post.call_args.kwargs

    assert "parse_mode" not in captured["kwargs"]["json"]
