# backend/tests/test_kanban_model_options.py
from unittest.mock import patch

import pytest
import pytest_asyncio

from app.kanban import dispatch
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()

# Captured verbatim from `claude -p "/model"` (Claude Code 2.1.206, 2026-07-10).
SAMPLE_CLI_OUTPUT = (
    "Current model: Sonnet 5 (default)\n"
    "Usage: /model <name>. Available: sonnet, opus, haiku, fable, best, "
    "sonnet[1m], opus[1m], fable[1m], opusplan, default, or a full model ID.\n"
)


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


def test_parse_model_options_extracts_available_list():
    options = dispatch._parse_model_options(SAMPLE_CLI_OUTPUT)
    assert options == [
        "sonnet", "opus", "haiku", "fable", "best",
        "sonnet[1m]", "opus[1m]", "fable[1m]", "opusplan", "default",
    ]


def test_parse_model_options_returns_empty_list_when_marker_absent():
    assert dispatch._parse_model_options("something unexpected\n") == []


@pytest.mark.asyncio
async def test_get_cached_model_options_returns_seed_when_never_refreshed():
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_cached_model_options(s) == list(dispatch.MODEL_OPTIONS_SEED)


@pytest.mark.asyncio
async def test_refresh_claude_model_options_caches_parsed_list():
    async with KanbanSessionLocal() as s:
        with patch.object(dispatch, "refresh_claude_model_options_sync",
                          return_value=["sonnet", "opus"]):
            options = await dispatch.refresh_claude_model_options(s)
            await s.commit()
        assert options == ["sonnet", "opus"]
        assert await dispatch.get_cached_model_options(s) == ["sonnet", "opus"]


@pytest.mark.asyncio
async def test_refresh_with_empty_result_does_not_clobber_cache():
    async with KanbanSessionLocal() as s:
        with patch.object(dispatch, "refresh_claude_model_options_sync",
                          return_value=["sonnet", "opus"]):
            await dispatch.refresh_claude_model_options(s)
            await s.commit()
        with patch.object(dispatch, "refresh_claude_model_options_sync",
                          return_value=[]):
            options = await dispatch.refresh_claude_model_options(s)
            await s.commit()
        assert options == []
        # Cache is untouched -- still the last good list, not wiped.
        assert await dispatch.get_cached_model_options(s) == ["sonnet", "opus"]
