import pytest

from app.database import AsyncSessionLocal
from app.models.agent_mail_schemas import (
    ExternalAgentMailContextRequest,
    MailAgentRegisterRequest,
    MailExternalActorCreate,
)
from app.services.agent_mail_service import agent_mail_service
from app.services.external_agent_mail_service import (
    ExternalAgentMailAuthError,
    ExternalAgentMailRateLimitError,
    external_agent_mail_service,
)
# Schema is created by ``_reset_app_database_tables`` in conftest.
# Per-test reset of ``external_agent_mail_service._send_windows`` lives in
# ``conftest.py:_reset_singleton_state`` so the next service that gains
# per-id state can be wired in one place.


@pytest.mark.asyncio
async def test_create_actor_then_authenticate(tmp_path):
    async with AsyncSessionLocal() as s:
        created = await external_agent_mail_service.create_actor(
            s, MailExternalActorCreate(actor_key="openclaw", display_name="OpenClaw"),
        )
        actor = await external_agent_mail_service.authenticate_actor(s, created.token)
        assert actor.actor_key == "openclaw"

        with pytest.raises(ExternalAgentMailAuthError):
            await external_agent_mail_service.authenticate_actor(s, "wrong-token")


@pytest.mark.asyncio
async def test_send_context_request_reports_delivery_state(tmp_path):
    async with AsyncSessionLocal() as s:
        member, _ = await agent_mail_service.register_session(
            s, MailAgentRegisterRequest(source="hook", cwd=str(tmp_path), session_key="cc:1"),
        )
        created = await external_agent_mail_service.create_actor(
            s, MailExternalActorCreate(actor_key="tool", display_name="Tool"),
        )
        actor = await external_agent_mail_service.authenticate_actor(s, created.token)

        response = await external_agent_mail_service.send_context_request(
            s, actor, ExternalAgentMailContextRequest(
                recipient_member_id=member.id, body_markdown="need context", why_needed="testing",
            ),
        )
        assert response.message.sender_type == "external_actor"
        assert response.delivery_state in {"stored_offline", "delivered_waiting", "stored"}


@pytest.mark.asyncio
async def test_rate_limit_after_30_messages(tmp_path):
    async with AsyncSessionLocal() as s:
        created = await external_agent_mail_service.create_actor(
            s, MailExternalActorCreate(actor_key="spammer", display_name="Spammer"),
        )
        actor = await external_agent_mail_service.authenticate_actor(s, created.token)
        for _ in range(30):
            external_agent_mail_service.check_send_rate_limit(actor.id)
        with pytest.raises(ExternalAgentMailRateLimitError):
            external_agent_mail_service.check_send_rate_limit(actor.id)
