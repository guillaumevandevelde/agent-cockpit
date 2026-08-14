"""Reader for the Claude Code statusline rate-limit capture.

``scripts/statusline-capture.sh`` writes Anthropic's official
``rate_limits`` blob to ``~/.claude-registry/rate-limits.json`` every
time Claude Code renders a statusline. This module turns that file into
``UsageWindow``s. See that script's header for why the statusline is the
only available channel.

The captured file looks like::

    {"captured_at": "2026-08-14T13:31:28Z",
     "subscription_type": "pro",
     "rate_limits_available": true,
     "rate_limits": {
       "five_hour":        {"utilization": 37.5, "resets_at": 1786730000},
       "seven_day":        {"utilization": 81.2, "resets_at": 1787200000},
       "seven_day_sonnet": null}}

Two deliberate accommodations:

**Both percentage spellings are accepted.** The CC binary contains
``utilization`` (in its own ``formatRateLimits``) *and*
``used_percentage`` (in a documented jq statusline example). Which one
reaches the payload was not resolvable by reading the binary, so the
reader takes either.

The first real capture on 2026-08-14 (CC 2.1.232) settled it: the live
payload uses **``used_percentage``**. ``utilization`` is kept as a
fallback rather than deleted — it is what the CLI's own formatter reads,
so it is the more likely of the two to reappear after a refactor. Note
that the two top-level fields the capture script also extracts,
``subscription_type`` and ``rate_limits_available``, both came back
``null`` in that same capture while ``rate_limits`` was fully populated;
nothing here branches on them, which is why that does not matter.

**Failure is loud, not silent.** The dangerous outcome for this whole
feature is a CC upgrade renaming a field so parsing quietly fails and
the Anthropic row slides back to its old estimate while still looking
fine. So a file that exists but yields nothing logs a warning naming the
keys it did find. ``freshness`` is judged per window against its own
``resets_at``, the same rule the codex provider uses.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from app.services.subscriptions.base import UsageWindow

logger = logging.getLogger(__name__)

#: Must match ``STATE_FILE`` in ``scripts/statusline-capture.sh``. Lives
#: beside ``kanban.db`` in the portable one-per-machine registry dir.
DEFAULT_STATE_PATH = Path.home() / ".claude-registry" / "rate-limits.json"

#: ``rate_limits`` key -> window label. ``seven_day_sonnet`` is present
#: only for max/team accounts; on Pro the CLI gates it out and the value
#: is null, which is why the reader skips nulls rather than treating a
#: missing model-scoped window as an error.
WINDOW_KEYS: tuple[tuple[str, str], ...] = (
    ("five_hour", "5h"),
    ("seven_day", "weekly"),
    ("seven_day_sonnet", "weekly (Sonnet)"),
)

#: Accepted spellings for the consumed percentage, in priority order.
PERCENT_KEYS: tuple[str, ...] = ("utilization", "used_percentage")


def _percent(limit: dict) -> float | None:
    for key in PERCENT_KEYS:
        value = limit.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value < 0:
            continue
        return float(value)
    return None


def _resets_at(limit: dict) -> datetime | None:
    value = limit.get("resets_at")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        # Epoch seconds, matching codex — not the milliseconds MiniMax
        # and opencode use.
        return datetime.fromtimestamp(value, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def read_windows(
    path: Path | None = None, *, now: datetime | None = None,
) -> list[UsageWindow]:
    """Return the fresh windows in the capture, or ``[]`` when unusable.

    Empty means "no official signal" for any reason — no file, malformed
    JSON, the account not publishing limits, or every window expired.
    Callers fall back to their own lower-confidence source.
    """
    state_path = path or DEFAULT_STATE_PATH
    try:
        raw = state_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        payload = json.loads(raw)
    except ValueError:
        logger.warning(
            "statusline capture at %s is not valid JSON — Anthropic usage "
            "will fall back to the local estimate", state_path,
        )
        return []

    if not isinstance(payload, dict):
        return []

    limits = payload.get("rate_limits")
    if limits is None:
        # An account that publishes nothing is a legitimate quiet state,
        # not a parse failure — say so at debug volume only.
        logger.debug(
            "statusline capture at %s has no rate_limits "
            "(rate_limits_available=%s)",
            state_path, payload.get("rate_limits_available"),
        )
        return []
    if not isinstance(limits, dict):
        return []

    now = now or datetime.now(UTC)
    windows: list[UsageWindow] = []
    for key, label in WINDOW_KEYS:
        limit = limits.get(key)
        if not isinstance(limit, dict):
            continue
        used = _percent(limit)
        if used is None:
            continue
        resets_at = _resets_at(limit)
        # A window past its reset describes a finished period.
        if resets_at is not None and resets_at <= now:
            continue
        windows.append(
            UsageWindow(
                label=label,
                used_fraction=used / 100.0,
                resets_at=resets_at,
                verbruikt=used,
                limiet=100.0,
                eenheid="%",
            )
        )

    if not windows:
        logger.warning(
            "statusline capture at %s parsed but yielded no usable window; "
            "rate_limits keys were %s. If Claude Code renamed a field this "
            "is where it shows up.",
            state_path, sorted(limits.keys()),
        )
    return windows
