"""Codex usage provider — rate limits from the rollout the CLI writes.

OpenAI exposes Codex quota only inside a running session: the CLI reads
it from API response headers and shows it in ``/status``. There is no
endpoint to poll and nothing in ``auth.json`` about consumption. But
codex **persists** every snapshot into its own rollout transcript, so we
can read one without running anything::

    ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl

Each ``event_msg`` of type ``token_count`` carries::

    "rate_limits": {
      "limit_id": "codex",
      "primary":   {"used_percent": 0.0, "window_minutes": 43200,
                    "resets_at": 1789301790},
      "secondary": null,
      "credits":   {"has_credits": false, "unlimited": false,
                    "balance": null},
      "plan_type": "go"
    }

That makes codex the cheapest of the four to read — a file the CLI
already maintains, no hook, no wrapper, no extra API call. Captured from
a live ChatGPT Go account on 2026-08-14.

**Window count is per plan, not per product.** The measured Go account
reported a single 43,200-minute (30-day) window with ``secondary: null``
— *not* the 5h + weekly pair the Codex docs describe for Plus/Pro. So
the labels are derived from ``window_minutes`` rather than assumed from
position: reading ``primary`` as "the 5h window" would have mislabelled
a monthly figure as a session one on this very account.

Staleness is the window's own boundary, as elsewhere in this package:
a snapshot is usable while ``now < resets_at``. Past that the window has
rolled over and the percentage describes a dead period. Unlike Anthropic
there is no local fallback to degrade to, so a stale snapshot returns
``onbekend`` rather than a lower-quality number.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.services.agentic_cli.codex_cli import get_codex_home
from app.services.subscriptions.base import (
    SubscriptionUsage,
    SubscriptionUsageProvider,
    UsageWindow,
)

logger = logging.getLogger(__name__)

#: ``window_minutes`` -> label. Anything unlisted falls back to a
#: minute-count label rather than being forced into one of these buckets;
#: a wrong label is worse than an ugly one.
WINDOW_LABELS: dict[int, str] = {
    300: "5h",
    10_080: "weekly",
    43_200: "monthly",
}

#: How many rollout files to inspect, newest first, before giving up.
#: The newest file is almost always the answer; the small budget covers
#: a just-created session that has not yet made an API call (and so has
#: no ``token_count`` event to read).
MAX_ROLLOUTS_SCANNED = 5


def _label_for(window_minutes: object) -> str:
    if not isinstance(window_minutes, int) or isinstance(window_minutes, bool):
        return "window"
    return WINDOW_LABELS.get(window_minutes, f"{window_minutes}m")


def _window_from_limit(limit: object) -> UsageWindow | None:
    """Convert one ``primary``/``secondary`` block into a window."""
    if not isinstance(limit, dict):
        return None
    used = limit.get("used_percent")
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        return None
    if used < 0:
        return None
    resets_at = limit.get("resets_at")
    resets_dt: datetime | None = None
    if isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool):
        try:
            # Seconds, not milliseconds — unlike opencode and MiniMax.
            resets_dt = datetime.fromtimestamp(resets_at, tz=UTC)
        except (OverflowError, OSError, ValueError):
            resets_dt = None
    return UsageWindow(
        label=_label_for(limit.get("window_minutes")),
        used_fraction=float(used) / 100.0,
        resets_at=resets_dt,
        verbruikt=float(used),
        limiet=100.0,
        eenheid="%",
    )


def _latest_rate_limits(sessions_dir: Path) -> dict | None:
    """Newest ``rate_limits`` payload across recent rollout files.

    Rollouts are append-only, so the last ``token_count`` line in a file
    holds its freshest snapshot.
    """
    candidates: list[tuple[float, Path]] = []
    for path in sessions_dir.rglob("rollout-*.jsonl"):
        try:
            candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue
    candidates.sort(key=lambda item: item[0], reverse=True)

    for _, path in candidates[:MAX_ROLLOUTS_SCANNED]:
        found: dict | None = None
        try:
            with path.open(encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    line = line.strip()
                    if not line.startswith("{") or "rate_limits" not in line:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    payload = event.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    if payload.get("type") != "token_count":
                        continue
                    limits = payload.get("rate_limits")
                    if isinstance(limits, dict):
                        found = limits
        except OSError:
            continue
        if found is not None:
            return found
    return None


class CodexUsageProvider(SubscriptionUsageProvider):
    """Rate-limit provider for Codex on a ChatGPT subscription.

    Args:
        data_dir: CODEX_HOME; defaults to the live one. Tests pass a
            fake directory containing ``sessions/``.
        now: injectable clock (UTC) for the staleness check.
        subscription_id: stable id, default ``"codex-cli:codex"``.
        subscription_label: human-readable label for the UI.
    """

    DEFAULT_ID = "codex-cli:codex"
    DEFAULT_LABEL = "Codex CLI (ChatGPT)"

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

    async def get_usage(self) -> SubscriptionUsage:
        sessions_dir = (self._data_dir or get_codex_home()) / "sessions"
        if not sessions_dir.is_dir():
            return self._no_signal("codex_rollout:no_sessions")

        limits = _latest_rate_limits(sessions_dir)
        if limits is None:
            return self._no_signal("codex_rollout:no_snapshot")

        windows = [
            w
            for w in (
                _window_from_limit(limits.get("primary")),
                _window_from_limit(limits.get("secondary")),
            )
            if w is not None
        ]
        if not windows:
            return self._no_signal("codex_rollout:no_windows")

        # A window whose reset has passed describes a period that is
        # over. Dropping it beats reporting last month's percentage as
        # today's.
        now = self._now or datetime.now(UTC)
        fresh = [w for w in windows if w.resets_at is None or w.resets_at > now]
        if not fresh:
            return self._no_signal("codex_rollout:stale")

        return SubscriptionUsage.from_windows(
            subscription_id=self.id,
            subscription_label=self.label,
            bron="codex_rollout:token_count",
            betrouwbaarheid="exact",
            windows=fresh,
        )
