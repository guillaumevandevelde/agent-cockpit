"""Tests for the model -> subscription_id attribution function.

Kaart d160d13f...: ``UsageService.get_block_usage()`` had no model filter,
so MiniMax tokens (same ``claude`` CLI, only ``ANTHROPIC_BASE_URL`` swapped
via ``provider_env.py``) were counted into the Anthropic plan-tier ratio.
``subscription_id_for_model`` is the single function every call-site must
route attribution through (analyse §6, acceptance criterion 1).
"""
from __future__ import annotations

from app.services.subscriptions.attribution import (
    UNKNOWN_SUBSCRIPTION_ID,
    subscription_id_for_model,
)


class TestSubscriptionIdForModel:
    def test_anthropic_model_maps_to_anthropic_subscription(self):
        assert (
            subscription_id_for_model("claude-sonnet-4-20250514")
            == "claude-code:anthropic"
        )

    def test_another_anthropic_model_alias_maps_to_anthropic_subscription(self):
        assert subscription_id_for_model("claude-opus-4-7") == "claude-code:anthropic"

    def test_bare_minimax_model_from_jsonl_maps_to_minimax_subscription(self):
        # The JSONL logs record the model exactly as MiniMax reports it in
        # the API response, e.g. "MiniMax-M3" — no "[1m]" suffix.
        assert subscription_id_for_model("MiniMax-M3") == "claude-code:minimax"

    def test_configured_minimax_model_with_context_window_suffix_also_matches(self):
        # provider_env.MINIMAX_DEFAULT_MODEL declares "MiniMax-M3[1m]" — an
        # exact-match mapping would fail on this value ever appearing in a
        # JSONL row (e.g. echoed back verbatim by some future code path).
        # Prefix matching must catch both forms.
        assert subscription_id_for_model("MiniMax-M3[1m]") == "claude-code:minimax"

    def test_matching_is_case_insensitive(self):
        assert subscription_id_for_model("minimax-m3") == "claude-code:minimax"

    def test_unrecognized_model_maps_to_unknown_not_guessed(self):
        assert subscription_id_for_model("gpt-4o") == UNKNOWN_SUBSCRIPTION_ID

    def test_literal_unknown_model_string_maps_to_unknown(self):
        # parse_usage_from_jsonl defaults to the literal string "unknown"
        # when a JSONL row has no message.model field.
        assert subscription_id_for_model("unknown") == UNKNOWN_SUBSCRIPTION_ID

    def test_none_model_maps_to_unknown_without_crashing(self):
        assert subscription_id_for_model(None) == UNKNOWN_SUBSCRIPTION_ID

    def test_empty_string_model_maps_to_unknown(self):
        assert subscription_id_for_model("") == UNKNOWN_SUBSCRIPTION_ID
