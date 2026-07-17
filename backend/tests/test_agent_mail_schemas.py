from app.models.agent_mail_schemas import (
    MAIL_MESSAGE_KINDS,
    MAIL_REQUEST_KINDS,
    MailAgentRegisterRequest,
    MailMessageCreate,
)


def test_message_kinds():
    assert MAIL_MESSAGE_KINDS == ["message", "broadcast", "context_request", "handoff", "answer"]
    assert MAIL_REQUEST_KINDS == ["context_request", "handoff"]


def test_message_create_defaults_to_message_kind():
    req = MailMessageCreate(body_markdown="hi")
    assert req.kind == "message"
    assert req.recipient_member_id is None


def test_register_request_requires_cwd_and_session_key():
    req = MailAgentRegisterRequest(source="hook", cwd="/repo", session_key="cc:1")
    assert req.cli == "unknown"
