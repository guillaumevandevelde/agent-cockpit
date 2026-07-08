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
