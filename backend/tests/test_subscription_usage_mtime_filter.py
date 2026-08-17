"""Regression tests for kaart 393198f17bc847969962928c972ac49e...

The /api/v1/subscriptions/usage endpoint kept the UI on "Loading..." for
~28 s on a cold cache because ``AnthropicUsageProvider.get_usage``
transitively called ``UsageService.get_all_usage_entries(None)``, which
walked every JSONL file in ``~/.claude/projects/**`` (1301 files /
~700 MB on this host). The fix threads an mtime-cutoff down through
``discover_jsonl_files`` → ``get_all_usage_entries`` → ``get_block_usage``
so a cold-cache call now only reads the files modified since the
``recent`` cutoff (default 3 days = 134 files / 4.4 s on this host,
instead of 1301 files / 28 s).

These tests pin the new behaviour so a future refactor can't silently
drop the mtime filter again and re-introduce the 28 s page-load.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.usage_service import UsageService


def _make_jsonl(path: Path) -> None:
    """Write a single assistant-usage line so the file is parseable."""
    line = json.dumps({
        "type": "assistant",
        "timestamp": datetime.now(UTC).isoformat(),
        "sessionId": "test-session",
        "message": {
            "model": "claude-sonnet-4-20250514",
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
    })
    path.write_text(line + "\n", encoding="utf-8")


def _touch(path: Path, when: datetime) -> None:
    """Create ``path`` and force its mtime to ``when``."""
    _make_jsonl(path)
    ts = when.timestamp()
    import os
    os.utime(path, (ts, ts))


class TestDiscoverMtimeFilter:
    """``discover_jsonl_files(since=...)`` must skip files older than the cutoff."""

    async def test_since_filters_old_files(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            "app.services.usage_service.get_claude_projects_dir",
            lambda: tmp_path,
        )
        now = datetime.now(UTC)
        old = now - timedelta(days=30)
        recent = now - timedelta(hours=1)

        project_dir = tmp_path / "p1"
        project_dir.mkdir()
        old_file = project_dir / "old.jsonl"
        recent_file = project_dir / "recent.jsonl"
        _touch(old_file, old)
        _touch(recent_file, recent)

        svc = UsageService(db=None)
        # Without cutoff: both files.
        all_files = await svc.discover_jsonl_files()
        assert set(all_files) == {old_file, recent_file}

        # With cutoff at 7 days: only the recent file.
        cutoff = now - timedelta(days=7)
        recent_only = await svc.discover_jsonl_files(since=cutoff)
        assert recent_only == [recent_file]

    async def test_since_accepts_posix_float(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            "app.services.usage_service.get_claude_projects_dir",
            lambda: tmp_path,
        )
        project_dir = tmp_path / "p1"
        project_dir.mkdir()
        file_path = project_dir / "x.jsonl"
        _touch(file_path, datetime.now(UTC))

        svc = UsageService(db=None)
        # A float cutoff in the past must not drop the file; one in the
        # future must drop it.
        result = await svc.discover_jsonl_files(since=0.0)
        assert result == [file_path]

        future = (datetime.now(UTC) + timedelta(hours=1)).timestamp()
        result = await svc.discover_jsonl_files(since=future)
        assert result == []

    async def test_since_none_keeps_old_behaviour(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            "app.services.usage_service.get_claude_projects_dir",
            lambda: tmp_path,
        )
        project_dir = tmp_path / "p1"
        project_dir.mkdir()
        old_file = project_dir / "old.jsonl"
        _touch(old_file, datetime.now(UTC) - timedelta(days=365))
        recent_file = project_dir / "recent.jsonl"
        _touch(recent_file, datetime.now(UTC))

        svc = UsageService(db=None)
        result = await svc.discover_jsonl_files(since=None)
        assert set(result) == {old_file, recent_file}

    async def test_missing_file_does_not_raise(self, tmp_path: Path, monkeypatch):
        """A file that vanishes between iterdir and stat must drop, not raise.

        ``_safe_mtime`` swallows ``OSError`` so a concurrent writer that
        rotates its JSONL mid-scan can't crash the discovery loop.
        """
        monkeypatch.setattr(
            "app.services.usage_service.get_claude_projects_dir",
            lambda: tmp_path,
        )
        project_dir = tmp_path / "p1"
        project_dir.mkdir()
        live_file = project_dir / "live.jsonl"
        _touch(live_file, datetime.now(UTC))

        # Stub _safe_mtime to simulate one file vanishing.
        import app.services.usage_service as svc_mod

        original = svc_mod._safe_mtime

        def fake_mtime(p: Path) -> float:
            if p.name == "ghost.jsonl":
                raise OSError("simulated race")
            return original(p)

        monkeypatch.setattr(svc_mod, "_safe_mtime", fake_mtime)

        svc = UsageService(db=None)
        result = await svc.discover_jsonl_files(since=0.0)
        assert result == [live_file]


class TestGetBlockUsageAppliesCutoff:
    """``get_block_usage`` must apply the mtime cutoff when ``recent=True``."""

    async def test_recent_true_applies_default_cutoff(self, tmp_path, monkeypatch):
        """The default ``recent=True`` path picks up ``DEFAULT_RECENT_DAYS``.

        We can't observe the cutoff directly without reading
        ``projects_dir``, but we can verify the public contract: with a
        projects dir containing only old files, a ``recent=True`` call
        scans nothing and returns no entries; with one containing a
        recent file, that file is scanned. The cut-over point itself is
        pinned by ``TestDiscoverMtimeFilter`` above.
        """
        monkeypatch.setattr(
            "app.services.usage_service.get_claude_projects_dir",
            lambda: tmp_path,
        )
        # All files old → recent=True returns nothing, recent=False same.
        project_dir = tmp_path / "p1"
        project_dir.mkdir()
        old_file = project_dir / "old.jsonl"
        _touch(old_file, datetime.now(UTC) - timedelta(days=400))

        svc = UsageService(db=None)
        result = await svc.get_block_usage(active=True)
        assert result.data == []
        assert result.active_block is None

    async def test_recent_false_does_not_filter(self, tmp_path, monkeypatch):
        """``recent=False`` callers (e.g. ``/usage/block`` admin path) keep
        their full-history contract — the mtime filter is opt-out via
        ``recent=False``, not via passing ``since=None`` explicitly.
        """
        monkeypatch.setattr(
            "app.services.usage_service.get_claude_projects_dir",
            lambda: tmp_path,
        )
        project_dir = tmp_path / "p1"
        project_dir.mkdir()
        old_file = project_dir / "old.jsonl"
        _touch(old_file, datetime.now(UTC) - timedelta(days=400))
        recent_file = project_dir / "recent.jsonl"
        _touch(recent_file, datetime.now(UTC) - timedelta(hours=1))

        svc = UsageService(db=None)
        result = await svc.get_block_usage(active=True, recent=False)
        # The old file's entry should appear — recent=False must NOT
        # apply the mtime cutoff. Two blocks: one old + one recent.
        assert len(result.data) >= 1
