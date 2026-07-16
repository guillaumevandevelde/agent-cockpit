"""Tests for per-card dispatch token telemetry.

Bridges the kanban dispatch (`card.dispatch_*` fields written by
`app.kanban.dispatch._run_card`) to the existing JSONL-derived usage data
(`app.services.usage_service.parse_usage_from_jsonl`). Acceptance criteria
for kanban card 8a2ad986: per dispatched card, after the fact, surface the
session's token usage + model so the Sonnet/Opus comparison (R1) becomes
measurable instead of guesswork.
"""
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.dispatch_usage_service import (
    aggregate_dispatch_entries,
    find_dispatch_session_id,
    get_card_usage,
)

# --- helpers ---------------------------------------------------------------


def _make_entry(
    *,
    session_id: str = "sess-x",
    model: str = "claude-sonnet-4-5",
    input_tokens: int = 1000,
    output_tokens: int = 200,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    cost_usd: float | None = None,
    timestamp: datetime | None = None,
    project_folder: str = "-home-test-worktree",
):
    return SimpleNamespace(
        session_id=session_id,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
        cost_usd=cost_usd,
        timestamp=timestamp or datetime(2026, 7, 15, 10, 0, 0, tzinfo=UTC),
        project_path=project_folder,
    )


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def _assistant_line(
    *,
    session_id: str,
    timestamp: str,
    model: str,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> dict:
    return {
        "type": "assistant",
        "sessionId": session_id,
        "timestamp": timestamp,
        "message": {
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": cache_creation_tokens,
                "cache_read_input_tokens": cache_read_tokens,
            },
        },
    }


# --- aggregate_dispatch_entries -------------------------------------------


class TestAggregateDispatchEntries:
    def test_sums_input_output_cache(self):
        entries = [
            _make_entry(input_tokens=1000, output_tokens=200),
            _make_entry(input_tokens=500, output_tokens=100, cache_read_tokens=2000),
        ]
        usage = aggregate_dispatch_entries(entries)
        assert usage.input_tokens == 1500
        assert usage.output_tokens == 300
        assert usage.cache_read_tokens == 2000
        assert usage.total_tokens == 1500 + 300 + 2000

    def test_groups_by_model(self):
        entries = [
            _make_entry(model="claude-sonnet-4-5", input_tokens=1000, output_tokens=200),
            _make_entry(model="claude-opus-4-8", input_tokens=500, output_tokens=100),
            _make_entry(model="claude-sonnet-4-5", input_tokens=200, output_tokens=50),
        ]
        usage = aggregate_dispatch_entries(entries)
        models = {b.model: b for b in usage.model_breakdowns}
        assert set(models) == {"claude-sonnet-4-5", "claude-opus-4-8"}
        assert models["claude-sonnet-4-5"].input_tokens == 1200
        assert models["claude-sonnet-4-5"].output_tokens == 250
        assert models["claude-opus-4-8"].input_tokens == 500

    def test_empty_entries_returns_zero_usage(self):
        usage = aggregate_dispatch_entries([])
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.total_tokens == 0
        assert usage.model_breakdowns == []

    def test_preserves_first_and_last_timestamp(self):
        ts1 = datetime(2026, 7, 15, 9, 0, 0, tzinfo=UTC)
        ts2 = datetime(2026, 7, 15, 10, 0, 0, tzinfo=UTC)
        ts3 = datetime(2026, 7, 15, 11, 0, 0, tzinfo=UTC)
        usage = aggregate_dispatch_entries(
            [_make_entry(timestamp=ts1), _make_entry(timestamp=ts3), _make_entry(timestamp=ts2)]
        )
        assert usage.first_activity == ts1
        assert usage.last_activity == ts3


# --- find_dispatch_session_id ---------------------------------------------


class TestFindDispatchSessionId:
    def test_returns_newest_jsonl_modified_after_started_at(self, tmp_path, monkeypatch):
        # Build a synthetic projects dir + worktree folder
        projects_dir = tmp_path / "projects"
        folder = projects_dir / "-test-worktree"
        folder.mkdir(parents=True)
        started = datetime(2026, 7, 15, 9, 0, 0, tzinfo=UTC)

        # An old transcript from before dispatch (should be ignored)
        old = folder / "old-session.jsonl"
        old.touch()
        import os
        old_time = (started - timedelta(hours=1)).timestamp()
        os.utime(old, (old_time, old_time))

        # A new transcript created after dispatch (should win)
        new = folder / "new-session.jsonl"
        new.touch()
        new_time = (started + timedelta(minutes=5)).timestamp()
        os.utime(new, (new_time, new_time))

        monkeypatch.setattr(
            "app.services.dispatch_usage_service.get_claude_projects_dir",
            lambda: projects_dir,
        )

        result = find_dispatch_session_id(
            project_folder="-test-worktree",
            dispatch_started_at=started,
        )
        assert result == "new-session"

    def test_returns_none_when_no_jsonl_after_started_at(self, tmp_path, monkeypatch):
        projects_dir = tmp_path / "projects"
        folder = projects_dir / "-test-worktree"
        folder.mkdir(parents=True)
        started = datetime(2026, 7, 15, 9, 0, 0, tzinfo=UTC)

        # Only pre-dispatch transcript
        old = folder / "stale.jsonl"
        old.touch()
        import os
        old_time = (started - timedelta(hours=2)).timestamp()
        os.utime(old, (old_time, old_time))

        monkeypatch.setattr(
            "app.services.dispatch_usage_service.get_claude_projects_dir",
            lambda: projects_dir,
        )
        assert find_dispatch_session_id(
            project_folder="-test-worktree", dispatch_started_at=started,
        ) is None

    def test_returns_none_when_folder_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "app.services.dispatch_usage_service.get_claude_projects_dir",
            lambda: tmp_path / "projects",
        )
        assert find_dispatch_session_id(
            project_folder="-missing", dispatch_started_at=datetime.now(UTC),
        ) is None


# --- get_card_usage (integration of all the above) -------------------------


class TestGetCardUsage:
    def _make_card(self, **overrides):
        defaults = dict(
            id="card-123",
            dispatch_started_at=datetime(2026, 7, 15, 9, 0, 0, tzinfo=UTC),
            dispatch_project_folder="-home-test-worktree",
            dispatch_session_id=None,
            dispatch_model="sonnet",
            column="Done",
        )
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    @pytest.mark.asyncio
    async def test_returns_none_when_no_dispatch_fields(self, tmp_path):
        card = self._make_card(
            dispatch_started_at=None,
            dispatch_project_folder=None,
        )
        assert await get_card_usage(card, projects_dir=tmp_path / "p") is None

    @pytest.mark.asyncio
    async def test_aggregates_from_jsonl_after_started_at(self, tmp_path, monkeypatch):
        card = self._make_card()
        folder = tmp_path / "projects" / "-home-test-worktree"
        folder.mkdir(parents=True)
        _write_jsonl(
            folder / "session-abc.jsonl",
            [
                _assistant_line(
                    session_id="session-abc",
                    timestamp="2026-07-15T09:01:00Z",
                    model="claude-sonnet-4-5",
                    input_tokens=1000,
                    output_tokens=200,
                ),
                _assistant_line(
                    session_id="session-abc",
                    timestamp="2026-07-15T09:05:00Z",
                    model="claude-sonnet-4-5",
                    input_tokens=500,
                    output_tokens=100,
                    cache_read_tokens=2000,
                ),
            ],
        )

        result = await get_card_usage(card, projects_dir=tmp_path / "projects")
        assert result is not None
        assert result.input_tokens == 1500
        assert result.output_tokens == 300
        assert result.cache_read_tokens == 2000
        assert result.session_id == "session-abc"
        assert result.recorded_model == "sonnet"
        assert len(result.model_breakdowns) == 1
        assert result.model_breakdowns[0].model == "claude-sonnet-4-5"

    @pytest.mark.asyncio
    async def test_returns_empty_usage_when_no_jsonl_yet(self, tmp_path):
        card = self._make_card()
        # project_folder exists but contains no transcript yet
        folder = tmp_path / "projects" / "-home-test-worktree"
        folder.mkdir(parents=True)

        result = await get_card_usage(card, projects_dir=tmp_path / "projects")
        assert result is not None
        assert result.session_id is None
        assert result.input_tokens == 0
        assert result.output_tokens == 0
        assert result.first_activity is None
        assert result.last_activity is None

    @pytest.mark.asyncio
    async def test_handles_missing_project_folder(self, tmp_path):
        card = self._make_card()
        # Don't create the folder — transcript never appeared (card failed
        # before the session wrote anything).
        result = await get_card_usage(card, projects_dir=tmp_path / "projects")
        assert result is not None
        assert result.session_id is None
        assert result.input_tokens == 0

    @pytest.mark.asyncio
    async def test_filters_entries_before_dispatch_started_at(self, tmp_path):
        """Sanity: a stale transcript file with mtime after started_at but
        entries dated BEFORE started_at must still be aggregated — the
        transcript was written *during* the session, even if its first
        entry lands before we recorded the timestamp (sub-second drift). We
        only filter by file mtime at the discovery layer; once we are
        reading a file, we take all entries. This is by design and tested
        so a future contributor doesn't tighten it into a regression.
        """
        card = self._make_card(
            dispatch_started_at=datetime(2026, 7, 15, 9, 0, 0, tzinfo=UTC),
        )
        folder = tmp_path / "projects" / "-home-test-worktree"
        folder.mkdir(parents=True)
        _write_jsonl(
            folder / "session-abc.jsonl",
            [
                _assistant_line(
                    session_id="session-abc",
                    timestamp="2026-07-15T08:59:55Z",  # 5s before started_at
                    model="claude-sonnet-4-5",
                    input_tokens=999,
                    output_tokens=0,
                ),
            ],
        )
        # Ensure file mtime is after started_at (file was written by the
        # session, even though first entry is slightly before)
        import os
        new_time = card.dispatch_started_at.timestamp() + 60
        os.utime(folder / "session-abc.jsonl", (new_time, new_time))

        result = await get_card_usage(card, projects_dir=tmp_path / "projects")
        assert result is not None
        # The pre-dispatch entry is included because the file's mtime is
        # post-started_at — see comment above.
        assert result.input_tokens == 999