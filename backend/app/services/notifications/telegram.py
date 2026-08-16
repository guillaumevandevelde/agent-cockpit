"""Telegram outbound notifier.

Push only when the rule from ``docs/cockpit/cockpit-richting-decision.md`` §4
fires — something is broken, or a promise is overdue. Two consumers today:
the ``open-issue-on-red-master`` job in ``.github/workflows/auto-fix-on-red-ci.yml``
(replaced by this channel as of kaart ``dbba16c9…``) and the overdue-promise
branch in ``app.services.scheduling.reconciler``.

Bot-token + chat-id are process-env only via ``Settings``. Neither value
ever enters the database, an API response, or a log line. With either
missing, ``send_telegram`` is a silent no-op so a bot-less dev machine
keeps booting.
"""
from __future__ import annotations

import logging
from typing import Final

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_TELEGRAM_API_BASE: Final[str] = "https://api.telegram.org"
_TIMEOUT_SECONDS: Final[float] = 10.0


async def send_telegram(text: str, *, parse_mode: str | None = None) -> bool:
    """Push a message to the configured Telegram chat.

    Returns ``True`` when Telegram accepted the message, ``False`` otherwise
    (unset config, HTTP error, Telegram API rejection, network failure).
    Failures are logged at WARNING/EXCEPTION but never raised — a notifier
    must not crash the reconciler, the CI workflow, or whatever else called
    it.
    """
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        logger.debug(
            "telegram not configured (token=%s chat_id=%s) — skipping",
            "set" if token else "unset",
            "set" if chat_id else "unset",
        )
        return False

    url = f"{_TELEGRAM_API_BASE}/bot{token}/sendMessage"
    payload: dict[str, str] = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=payload)
    except Exception:
        logger.exception("telegram send failed")
        return False

    if resp.status_code != 200:
        logger.warning(
            "telegram returned %s: %s", resp.status_code, resp.text[:200],
        )
        return False
    return True
