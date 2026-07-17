"""Regression: conftest must clear per-id singleton state between tests.

Catches the failure mode from self-improve kanban card 42f44a05: the conftest
resets the DB to a fresh schema per test, so auto-increment ids (e.g.
``MailTeamMember.id``) restart at 1. ``agent_mail_service._last_auto_nudge_at``
is keyed by ``member_id`` on the singleton, so without a per-test reset, the
second test inherits a leftover cooldown entry from the first test and a
fresh ``auto_nudge_members`` call spuriously skips its wake.

The fix lives in ``conftest._reset_singleton_state`` →
``app.services._testing.reset_all_singleton_test_state``. If a future change
removes that fixture, this test fails — so the regression can't reappear
silently.
"""
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.agent_mail import MailAgentSession
from app.services.agent_mail_service import agent_mail_service


def _discovered_pane(pane_id, tmp_path, provider="claude-code"):
    return [{"pane_id": pane_id, "cwd": str(tmp_path), "provider": provider, "tmux_target": "sess:0.0", "pid": 999}]


@pytest.mark.asyncio
async def test_auto_nudge_first_test_fills_cooldown(tmp_path):
    """First test: populates ``_last_auto_nudge_at[member_id=1]`` on the singleton."""
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=_discovered_pane("%z1", tmp_path)), \
         patch("app.services.agent_mail_service.send_text", return_value=True) as mock_send:
        async with AsyncSessionLocal() as s:
            await agent_mail_service.sync_observed_sessions(s)
            row = (await s.execute(
                select(MailAgentSession).where(MailAgentSession.session_key == "tmux:%z1")
            )).scalar_one()
            member_id = row.member_id

            nudged = await agent_mail_service.auto_nudge_members(s, {member_id})
            assert len(nudged) == 1
            assert mock_send.call_count == 1


@pytest.mark.asyncio
async def test_auto_nudge_second_test_does_not_inherit_cooldown(tmp_path):
    """Second test: would inherit leftover cooldown if conftest didn't reset.

    ``_reset_singleton_state`` clears ``_last_auto_nudge_at`` between tests;
    without it, ``auto_nudge_members`` would see ``_last_auto_nudge_at[1]``
    from the previous test and skip the wake with a spurious 0-result.
    """
    with patch("app.services.agent_mail_service.discover_agent_sessions", return_value=_discovered_pane("%z2", tmp_path)), \
         patch("app.services.agent_mail_service.send_text", return_value=True):
        async with AsyncSessionLocal() as s:
            await agent_mail_service.sync_observed_sessions(s)
            row = (await s.execute(
                select(MailAgentSession).where(MailAgentSession.session_key == "tmux:%z2")
            )).scalar_one()
            member_id = row.member_id

            nudged = await agent_mail_service.auto_nudge_members(s, {member_id})
            assert len(nudged) == 1, (
                f"expected nudge, got {len(nudged)} — leftover "
                f"_last_auto_nudge_at={agent_mail_service._last_auto_nudge_at}"
            )
