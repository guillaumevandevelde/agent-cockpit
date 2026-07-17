"""Regression test for kanban card 103718db...

The card caught ``scripts/run-single-test.sh`` hanging 5+ minutes on a test
that instantiated a real ``UsageService`` and called
``AnthropicUsageProvider.get_usage()`` against this host's actual
``~/.claude/projects/**`` tree (956 JSONL files / 523 MB). The fix lives in
``backend/tests/conftest.py::_isolate_usage_service_projects_dir``, which
autouse-pins ``UsageService.projects_dir`` to a per-test empty tmp dir.

These tests pin the conftest safeguard so it can't quietly regress (someone
removes the autouse fixture → a future test that forgets to mock
``get_all_usage_entries`` hangs the dispatch again). The critical assertion
is the *non-equality* with the real on-disk path — without the autouse, the
constructed ``UsageService`` would carry the host's ``~/.claude/projects``
path and ``discover_jsonl_files`` would happily iterate over it.

We do NOT assert the autouse fixture by name (renames would be brittle). We
assert *its observable behaviour*: ``UsageService(db=None).projects_dir`` is
not the prod directory, and ``discover_jsonl_files()`` returns ``[]`` without
any test-side mocking.
"""
from __future__ import annotations

import pytest

from app.services.usage_service import UsageService
from app.utils.path_utils import get_claude_projects_dir as _prod_get_claude_projects_dir


def test_usage_service_default_projects_dir_is_not_prod():
    """A bare ``UsageService()`` must NOT carry the prod ``~/.claude/projects``
    path — that's the whole point of the conftest autouse.

    Without the autouse, this attribute would be
    ``Path('~/.claude/projects').expanduser()`` and any unmocked
    ``get_block_usage()`` / ``get_all_usage_entries()`` call would scan the
    real on-disk tree (956 JSONL files / 523 MB on this host as of
    2026-07-17) — the hang kaart 103718db... was filed against.
    """
    service = UsageService(db=None)

    prod_path = _prod_get_claude_projects_dir()
    assert service.projects_dir != prod_path, (
        "conftest autouse _isolate_usage_service_projects_dir is not "
        f"redirecting projects_dir (still equals prod path: {prod_path})"
    )


def test_usage_service_default_projects_dir_is_empty():
    """The autouse redirected dir must be empty — otherwise the test could
    still be reading stale JSONL data from a sibling test that wrote there.

    Per-test ``tmp_path`` cleanup makes this trivially true today, but the
    assertion pins the contract: the autouse fixture provides an empty
    directory, not e.g. a sibling ``tmp_path`` that some other fixture
    happened to populate.
    """
    service = UsageService(db=None)

    assert service.projects_dir.is_dir()
    assert list(service.projects_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_discover_jsonl_files_returns_empty_against_default():
    """End-to-end regression for the original hang: a test that does the
    natural thing — construct a real ``UsageService`` and call the real
    disk-scanning method — must complete instantly with an empty result,
    not hang for minutes iterating the prod ``~/.claude/projects`` tree.

    Pre-fix this was the line that hung kaart 103718db... for 5+ minutes;
    post-fix the conftest autouse redirects ``projects_dir`` to an empty
    per-test dir, ``Path.iterdir()`` returns immediately, and the call
    completes in milliseconds.
    """
    service = UsageService(db=None)
    files = await service.discover_jsonl_files()
    assert files == []


@pytest.mark.asyncio
async def test_get_all_usage_entries_returns_empty_against_default():
    """Same hazard as ``test_discover_jsonl_files_returns_empty_against_default``
    but exercises the method that ``AnthropicUsageProvider.get_usage`` calls
    transitively. Without the conftest autouse, this is the call that
    triggered the original hang — synchronous ``Path.iterdir()`` over
    ``~/.claude/projects/**`` inside an ``async def``, blocking the asyncio
    event loop and starving ``pytest-timeout``'s SIGALRM-based safety net.
    """
    service = UsageService(db=None)
    entries = await service.get_all_usage_entries()
    assert entries == []


@pytest.mark.asyncio
async def test_get_block_usage_returns_empty_response_against_default():
    """Top-level smoke: ``get_block_usage()`` against the autouse-empty
    projects_dir returns an empty ``BlockUsageListResponse`` with no
    active block, no totals — exactly the "no real data" state the rest
    of the suite assumes when it doesn't mock these methods.

    If the autouse regresses, this hangs the test for minutes (the original
    symptom); if the autouse works, this returns immediately.
    """
    service = UsageService(db=None)
    response = await service.get_block_usage()

    assert response.data == []
    assert response.active_block is None
    assert response.totals.input_tokens == 0
    assert response.totals.output_tokens == 0
    assert response.total_cost == 0.0


def test_autouse_does_not_break_test_side_overrides(monkeypatch):
    """The conftest autouse must yield to a test that explicitly wants a
    populated ``projects_dir`` (e.g. ``test_card_usage_endpoint`` /
    ``test_dispatch_usage_service`` write synthetic JSONL files under
    ``tmp_path`` and patch the consumer to point there). ``monkeypatch`` is
    function-scoped, so the test's ``monkeypatch.setattr`` runs AFTER the
    autouse setup and wins; this test pins that contract.
    """
    from pathlib import Path

    synthetic = Path("/tmp/_isolated_projects_dir_for_test_override")
    synthetic.mkdir(parents=True, exist_ok=True)
    try:
        monkeypatch.setattr(
            "app.services.usage_service.get_claude_projects_dir",
            lambda: synthetic,
        )
        service = UsageService(db=None)
        assert service.projects_dir == synthetic
    finally:
        synthetic.rmdir()