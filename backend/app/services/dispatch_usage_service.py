"""Per-card dispatch token telemetry.

Bridges the kanban dispatch (`KanbanCard.dispatch_*` fields written by
`app.kanban.dispatch._run_card`) to the existing JSONL-derived usage data
(`app.services.usage_service.parse_usage_from_jsonl`). Acceptance criteria
for kanban card 8a2ad986: per dispatched card, after the fact, surface the
session's token usage + model so the Sonnet/Opus comparison (R1) becomes
measurable instead of guesswork.

Key design constraints (from the card's acceptance criteria):

1. **No second telemetry stream.** Reuses the existing JSONL files
   Claude Code already writes — the spawned session never sees a new
   tool/turn, so its own token bill is unaffected.

2. **No token cost in the measured session.** All aggregation happens
   here, on the backend, after the session has ended.

3. **Model + session identity per dispatch.** The dispatch layer
   persists `dispatch_started_at`, `dispatch_project_folder`, and
   `dispatch_model` at spawn time. The session's actual UUID is
   discovered lazily by scanning `~/.claude/projects/<folder>/` for the
   newest JSONL file written after `dispatch_started_at` — that file's
   stem IS the Claude Code session id, matching the convention used by
   `usage_service.parse_usage_from_jsonl`.

A card without `dispatch_started_at` (e.g. older dispatches before this
card landed, or cards still in Backlog) returns `None` from
`get_card_usage` — there's nothing to attribute to.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.services.usage_service import LoadedUsageEntry, UsageService
from app.utils.path_utils import get_claude_projects_dir

logger = logging.getLogger(__name__)


@dataclass
class ModelBreakdown:
    """Per-model token totals — mirrors usage_service's shape so the
    frontend can reuse the existing breakdown renderer."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0


@dataclass
class CardUsage:
    """Aggregated per-dispatch usage, returned by get_card_usage."""

    session_id: str | None  # resolved from JSONL stem; None until transcript appears
    recorded_model: str | None  # the model the dispatcher recorded at spawn time
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    first_activity: datetime | None = None
    last_activity: datetime | None = None
    model_breakdowns: list[ModelBreakdown] = field(default_factory=list)


def _ensure_aware(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC and normalize aware datetimes to UTC.

    SQLite drops tzinfo on DateTime(timezone=True) columns (see the note
    in app/kanban/db.py), so any dispatch_started_at we read back is naive.
    `dispatch_started_at` is stored as an ISO8601 *string* (mirrors
    `scheduled_at` — see models.KanbanCard), so callers that already have
    a string should use `_parse_started_at` instead.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _parse_started_at(value) -> datetime | None:
    """Accept the ISO-string form (DB row) or a datetime (in-memory ORM
    attribute) and return an aware datetime in UTC. Returns None for
    None / empty / unparseable input."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _ensure_aware(value)
    try:
        return _ensure_aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except (ValueError, TypeError):
        logger.warning("dispatch-usage: could not parse dispatch_started_at=%r", value)
        return None


def aggregate_dispatch_entries(entries: Iterable[LoadedUsageEntry]) -> CardUsage:
    """Sum input/output/cache tokens + group per-model breakdowns."""
    entries = list(entries)
    totals = {"in": 0, "out": 0, "cc": 0, "cr": 0, "cost": 0.0}
    per_model: dict[str, dict] = defaultdict(
        lambda: {"in": 0, "out": 0, "cc": 0, "cr": 0}
    )
    timestamps: list[datetime] = []
    pricing = UsageService().pricing

    for e in entries:
        totals["in"] += e.input_tokens
        totals["out"] += e.output_tokens
        totals["cc"] += e.cache_creation_tokens
        totals["cr"] += e.cache_read_tokens
        cost = e.cost_usd if (e.cost_usd is not None and e.cost_usd > 0) else pricing.calculate_cost(
            input_tokens=e.input_tokens,
            output_tokens=e.output_tokens,
            cache_creation_tokens=e.cache_creation_tokens,
            cache_read_tokens=e.cache_read_tokens,
            model=e.model,
        )
        totals["cost"] += cost
        bucket = per_model[e.model]
        bucket["in"] += e.input_tokens
        bucket["out"] += e.output_tokens
        bucket["cc"] += e.cache_creation_tokens
        bucket["cr"] += e.cache_read_tokens
        timestamps.append(e.timestamp)

    breakdowns = [
        ModelBreakdown(
            model=model,
            input_tokens=b["in"],
            output_tokens=b["out"],
            cache_creation_tokens=b["cc"],
            cache_read_tokens=b["cr"],
            total_tokens=b["in"] + b["out"] + b["cc"] + b["cr"],
        )
        for model, b in per_model.items()
    ]
    breakdowns.sort(key=lambda b: b.model)

    return CardUsage(
        session_id=None,  # set by caller after JSONL discovery
        recorded_model=None,
        input_tokens=totals["in"],
        output_tokens=totals["out"],
        cache_creation_tokens=totals["cc"],
        cache_read_tokens=totals["cr"],
        total_tokens=totals["in"] + totals["out"] + totals["cc"] + totals["cr"],
        total_cost_usd=round(totals["cost"], 6),
        first_activity=min(timestamps) if timestamps else None,
        last_activity=max(timestamps) if timestamps else None,
        model_breakdowns=breakdowns,
    )


def find_dispatch_session_id(
    *,
    project_folder: str | None,
    dispatch_started_at: datetime | None,
    projects_dir: Path | None = None,
) -> str | None:
    """Discover the Claude Code session_id for this dispatch.

    The dispatched session writes its transcript to
    `~/.claude/projects/<project_folder>/<uuid>.jsonl`. We don't know the
    UUID at spawn time, but the file's mtime is a reliable proxy: any
    transcript modified at-or-after dispatch_started_at belongs to this
    dispatch (the worktree is freshly created per card, so no other
    session has written there before).

    Returns the session_id (JSONL stem), or None when no transcript has
    appeared yet — the caller is expected to retry on a later request.
    """
    if not project_folder or dispatch_started_at is None:
        return None
    base = projects_dir if projects_dir is not None else get_claude_projects_dir()
    folder_dir = Path(base) / project_folder
    if not folder_dir.is_dir():
        return None
    started = _parse_started_at(dispatch_started_at)
    if started is None:
        return None
    started_ts = started.timestamp()
    candidates: list[tuple[float, Path]] = []
    for path in folder_dir.glob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        # Strictly after the dispatch_started_at — anything older is a
        # leftover from before this card's worktree existed.
        if mtime < started_ts:
            continue
        candidates.append((mtime, path))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1].stem


async def _load_dispatch_entries(
    *,
    project_folder: str | None,
    dispatch_started_at: datetime | None,
    projects_dir: Path | None = None,
) -> list[LoadedUsageEntry]:
    """Read every JSONL in the dispatch's project_folder, then filter the
    surviving entries to those that belong to the resolved session_id.

    Filtering happens in two steps to keep the discovery O(folder-size)
    and the parsing O(dispatch-size):

    1. **File-level filter (mtime).** Skip JSONL files older than
       dispatch_started_at — the worktree was freshly created, so a file
       older than the spawn time is guaranteed to be someone else's
       transcript.
    2. **Entry-level filter (session_id).** Once we've picked the
       dispatched session's JSONL, take only its entries. We do this
       defensively even though the mtime filter should already exclude
       other sessions' writes, because a multi-session concurrent
       scenario (theoretically impossible per worktree today, but cheap
       to guard against) would silently inflate the totals.
    """
    if not project_folder or dispatch_started_at is None:
        return []
    base = projects_dir if projects_dir is not None else get_claude_projects_dir()
    folder_dir = Path(base) / project_folder
    if not folder_dir.is_dir():
        return []
    started = _parse_started_at(dispatch_started_at)
    if started is None:
        return []
    started_ts = started.timestamp()

    # 1. Resolve the session_id first (newest post-dispatch JSONL wins)
    session_id = find_dispatch_session_id(
        project_folder=project_folder,
        dispatch_started_at=dispatch_started_at,
        projects_dir=projects_dir,
    )

    # 2. Parse every JSONL newer than the dispatch start; filter to the
    #    resolved session_id at the entry level.
    service = UsageService()
    all_entries: list[LoadedUsageEntry] = []
    for path in folder_dir.glob("*.jsonl"):
        try:
            if path.stat().st_mtime < started_ts:
                continue
        except OSError:
            continue
        try:
            entries = await service.parse_usage_from_jsonl(path)
        except Exception:
            logger.warning("dispatch-usage: failed to parse %s", path, exc_info=True)
            continue
        all_entries.extend(entries)

    if session_id is None:
        # No session resolved yet — return empty so the caller knows to
        # retry. We deliberately don't return the loose entries because
        # unattributed usage can't be safely bucketed under this card.
        return []
    return [e for e in all_entries if e.session_id == session_id]


async def get_card_usage(card, *, projects_dir: Path | None = None) -> CardUsage | None:
    """Aggregate the card's dispatch_* telemetry with the matching JSONL
    transcript.

    Returns None when the card has no dispatch_started_at (e.g. legacy
    card dispatched before this feature landed, or a card still on
    Backlog). Returns an empty `CardUsage` (zero tokens) when the
    dispatch is recorded but the transcript hasn't appeared yet — the
    UI distinguishes "no data yet" from "no dispatch" by checking
    session_id is None vs the whole response being None.

    Async because `_load_dispatch_entries` reads JSONL with aiofiles;
    calling this from inside FastAPI requires an awaitable so we don't
    hit `RuntimeError: asyncio.run() cannot be called from a running
    event loop` (see test_card_usage_endpoint).
    """
    started = _parse_started_at(getattr(card, "dispatch_started_at", None))
    folder = getattr(card, "dispatch_project_folder", None)
    recorded_model = getattr(card, "dispatch_model", None)
    known_session_id = getattr(card, "dispatch_session_id", None)
    if started is None or folder is None:
        return None

    entries = await _load_dispatch_entries(
        project_folder=folder,
        dispatch_started_at=started,
        projects_dir=projects_dir,
    )
    usage = aggregate_dispatch_entries(entries)
    usage.recorded_model = recorded_model
    usage.session_id = known_session_id or usage.session_id or find_dispatch_session_id(
        project_folder=folder,
        dispatch_started_at=started,
        projects_dir=projects_dir,
    )
    return usage