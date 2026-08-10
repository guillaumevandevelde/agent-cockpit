from app.services.context_service import get_context_limit


def test_sonnet_5_context_limit():
    assert get_context_limit("claude-sonnet-5") == 1_000_000
    assert get_context_limit("anthropic.claude-sonnet-5-20260620-v1:0") == 1_000_000


def test_existing_context_limit_aliases_still_match():
    assert get_context_limit("claude-sonnet-4-6") == 1_000_000
    assert get_context_limit("claude-opus-4-7") == 1_000_000


def test_claude_opus_5_context_limit():
    """CC 2.1.219 makes claude-opus-5 the default Opus with a 1M context window."""
    # Bare alias
    assert get_context_limit("claude-opus-5") == 1_000_000
    # Date-suffix variant (post-launch YYYYMMDD) must normalize and resolve
    assert get_context_limit("claude-opus-5-20260724") == 1_000_000
    # Provider-prefixed Bedrock-style name must resolve via longest-match fallback
    assert get_context_limit("anthropic.claude-opus-5-20260724-v1:0") == 1_000_000


def test_claude_opus_5_does_not_collide_with_opus_4_aliases():
    """The new claude-opus-5 entry must win over the catch-all claude-opus-4 key."""
    assert get_context_limit("claude-opus-5") > get_context_limit("claude-opus-4-6")
