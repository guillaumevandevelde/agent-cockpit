"""opencode Go usage provider — local spend against published caps.

opencode Go is a $10/month subscription whose limits are **denominated
in dollars**, not tokens or requests (https://opencode.ai/docs/go/,
read 2026-08-14)::

    5 hour limit   $12 of usage
    Weekly limit   $30 of usage
    Monthly limit  $60 of usage

There is no quota API — the docs point at the web console. But unlike
the Anthropic case, we do not need one: the limits are **published
constants**, and opencode records the dollar cost of every assistant
message locally in ``opencode.db``. Cost divided by a published cap is a
real denominator, which is what makes this row honest where the old
"absolute token count" row was not.

Cross-checked on 2026-08-14: summing ``cost`` for ``providerID =
'opencode-go'`` over the whole DB gives $57.8765, matching what
``opencode stats`` prints for the same account to the cent. So the
column we read is the one the CLI itself reports on.

Why ``schatting`` and not ``exact``
-----------------------------------
Two independent reasons, either alone sufficient:

1. **The cost is opencode's own computation**, from its pricing table at
   the time each message was written. It is what opencode believes it
   spent, not a figure Go's billing confirmed.
2. **The windows are rolling, because the anchor is unknowable.** A
   "weekly limit" resets on some boundary tied to the billing cycle, and
   nothing on disk records it. So we measure the trailing 5h / 7d / 30d
   instead. That over-reports relative to a fixed window — trailing-7d
   spend can exceed $30 while the billing week is under it — which
   errs toward "pause the lane early" rather than "route onto an
   exhausted lane". ``resets_at`` is therefore ``None``: a trailing
   window has no reset instant, and inventing one would be the same
   class of decoration this whole feature exists to remove.

A consequence worth knowing: ``used_fraction`` above 1.0 is legal here.
Go's "Use balance" option lets spend continue past a cap by drawing on
Zen credits instead of blocking, so 120% is a real state, not a bug.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.services.agentic_cli.open_code import _opencode_db_path
from app.services.subscriptions.base import (
    SubscriptionUsage,
    SubscriptionUsageProvider,
    UsageWindow,
)

logger = logging.getLogger(__name__)

#: ``providerID`` written by opencode for Go-routed messages. Messages
#: from other providers share the table and must not be summed in — this
#: account also holds 1,256 plain ``opencode`` (free-model) messages that
#: cost nothing against the Go cap.
GO_PROVIDER_ID = "opencode-go"

#: Published caps, in USD. Source: https://opencode.ai/docs/go/#usage-limits
#: ``(label, window_seconds, cap_usd)``.
GO_LIMITS: tuple[tuple[str, int, float], ...] = (
    ("5h", 5 * 60 * 60, 12.0),
    ("weekly", 7 * 24 * 60 * 60, 30.0),
    ("monthly", 30 * 24 * 60 * 60, 60.0),
)

#: SQLite busy timeout, seconds. Mirrors ``open_code.last_session_write``:
#: a hung writer must not stall a usage request.
DB_TIMEOUT_SECONDS = 1


class OpencodeGoUsageProvider(SubscriptionUsageProvider):
    """Local-spend provider for the opencode Go subscription.

    Args:
        data_dir: opencode data home; defaults to the live one. Tests
            pass a fake directory containing an ``opencode.db``.
        now: injectable clock (UTC) so window arithmetic is testable.
        subscription_id: stable id, default ``"open-code:open-code"``.
        subscription_label: human-readable label for the UI.
    """

    DEFAULT_ID = "open-code:open-code"
    DEFAULT_LABEL = "opencode (Go)"

    def __init__(
        self,
        data_dir: Path | None = None,
        now: datetime | None = None,
        subscription_id: str = DEFAULT_ID,
        subscription_label: str = DEFAULT_LABEL,
    ):
        self._data_dir = data_dir
        self._now = now
        self.id = subscription_id
        self.label = subscription_label

    def _no_signal(self, bron: str) -> SubscriptionUsage:
        return SubscriptionUsage(
            subscription_id=self.id,
            subscription_label=self.label,
            beschikbaar=True,
            drempel_gebruikt=None,
            bron=bron,
            betrouwbaarheid="onbekend",
        )

    def _spend_since(self, connection: sqlite3.Connection, cutoff_ms: int) -> float:
        """Total Go spend, in USD, for messages written since ``cutoff_ms``.

        ``message.time_created`` is Unix milliseconds and was verified to
        equal the ``data.time.created`` field byte-for-byte, so the
        indexed column can drive the range scan while the JSON blob only
        has to answer "was this Go, and what did it cost".
        """
        total = 0.0
        for (blob,) in connection.execute(
            "SELECT data FROM message WHERE time_created >= ?", (cutoff_ms,),
        ):
            try:
                message = json.loads(blob)
            except (TypeError, ValueError):
                continue
            if not isinstance(message, dict):
                continue
            if message.get("providerID") != GO_PROVIDER_ID:
                continue
            cost = message.get("cost")
            # Aborted turns write cost 0; booleans would sum as 1.
            if isinstance(cost, bool) or not isinstance(cost, (int, float)):
                continue
            total += float(cost)
        return total

    async def get_usage(self) -> SubscriptionUsage:
        db_path = _opencode_db_path(self._data_dir)
        if not db_path.is_file():
            return self._no_signal("opencode_db:absent")

        now = self._now or datetime.now(UTC)
        now_ms = int(now.timestamp() * 1000)

        try:
            connection = sqlite3.connect(
                f"{db_path.resolve().as_uri()}?mode=ro",
                uri=True,
                timeout=DB_TIMEOUT_SECONDS,
            )
            try:
                windows = []
                for label, seconds, cap in GO_LIMITS:
                    spend = self._spend_since(connection, now_ms - seconds * 1000)
                    windows.append(
                        UsageWindow(
                            label=label,
                            used_fraction=spend / cap,
                            resets_at=None,
                            verbruikt=round(spend, 4),
                            limiet=cap,
                            eenheid="$",
                        )
                    )
            finally:
                connection.close()
        except sqlite3.Error:
            logger.warning(
                "opencode Go usage: could not read %s", db_path, exc_info=True,
            )
            return self._no_signal("opencode_db:unreadable")

        return SubscriptionUsage.from_windows(
            subscription_id=self.id,
            subscription_label=self.label,
            bron="opencode_db:message_cost",
            betrouwbaarheid="schatting",
            windows=windows,
        )
