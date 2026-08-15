from app.models.agent_mail_schemas import MailAgentRegisterRequest


def test_register_request_requires_cwd_and_session_key():
    req = MailAgentRegisterRequest(source="hook", cwd="/repo", session_key="cc:1")
    assert req.cli == "unknown"
