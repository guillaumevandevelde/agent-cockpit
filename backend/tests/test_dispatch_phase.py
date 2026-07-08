"""Tests for the phase-aware provider/persona helpers extracted in Task 5.

These helpers are pure functions over a duck-typed card object, so the tests
do not need the kanban test database.
"""
import pytest

from app.kanban import dispatch
from app.kanban.dispatch import _phase_provider_id, _phase_target_agent


class _FakeCard:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_phase_provider_analyst_uses_analyst_field():
    card = _FakeCard(analyst_agent_id="claude-code", agent="engineer")
    assert _phase_provider_id(card, phase="analyst") == "claude-code"


def test_phase_provider_executor_uses_executor_field():
    card = _FakeCard(executor_agent_id="mimo-code", agent="engineer")
    assert _phase_provider_id(card, phase="executor") == "mimo-code"


def test_phase_provider_executor_falls_back_to_card_agent():
    card = _FakeCard(agent="codex-cli", executor_agent_id=None)
    assert _phase_provider_id(card, phase="executor") == "codex-cli"


def test_phase_provider_executor_default_claude_code():
    card = _FakeCard(agent=None, executor_agent_id=None)
    assert _phase_provider_id(card, phase="executor") == "claude-code"


def test_phase_provider_analyst_default_claude_code():
    card = _FakeCard(analyst_agent_id=None)
    assert _phase_provider_id(card, phase="analyst") == "claude-code"


def test_phase_target_agent_analyst_is_analyst():
    card = _FakeCard()
    assert _phase_target_agent(card, project_path="/tmp", phase="analyst",
                               source_column="Backlog") == "analyst"


def test_phase_target_agent_executor_honors_agent_override_persona(tmp_path):
    """An agent_override that's a persona name (not a registered provider id)
    wins over card.agent and the column-derived fallback — preserves the legacy
    `dispatch_card(..., agent_override='developer')` path that maps the card to
    the developer column."""
    (tmp_path / ".claude" / "agents").mkdir(parents=True)
    (tmp_path / ".claude" / "agents" / "developer.md").write_text("# developer persona")
    card = _FakeCard()
    assert _phase_target_agent(card, project_path=str(tmp_path), phase="executor",
                               source_column="Backlog",
                               agent_override="developer") == "developer"