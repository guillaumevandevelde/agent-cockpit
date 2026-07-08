from app.services.context_service import get_context_limit


def test_sonnet_5_context_limit():
    assert get_context_limit("claude-sonnet-5") == 1_000_000
    assert get_context_limit("anthropic.claude-sonnet-5-20260620-v1:0") == 1_000_000


def test_existing_context_limit_aliases_still_match():
    assert get_context_limit("claude-sonnet-4-6") == 1_000_000
    assert get_context_limit("claude-opus-4-7") == 1_000_000
