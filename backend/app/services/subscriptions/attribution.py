"""Maps a JSONL ``message.model`` string to a subscription_id.

MiniMax runs through the same ``claude`` CLI as Anthropic (only
``ANTHROPIC_BASE_URL`` differs, see ``agentic_cli/provider_env.py``), so
both write to the same ``~/.claude/projects/**/*.jsonl`` tree and the
only distinguishing signal is ``message.model``. This is the single
function all usage attribution routes through (analyse §6, acceptance
criterion 1 of kaart d160d13f...) — do not duplicate the mapping at
call sites.

Matching is **prefix-based, not exact-match**: ``provider_env.py``
declares ``MINIMAX_DEFAULT_MODEL = "MiniMax-M3[1m]"`` (with a
context-window suffix) while the JSONL logs record the bare
``"MiniMax-M3"`` — an exact-match mapping would silently miss every
MiniMax entry.

An unrecognized model is **not guessed**: it maps to
``UNKNOWN_SUBSCRIPTION_ID``, mirroring the ``betrouwbaarheid`` honesty
pattern in ``subscriptions/base.py`` — no fabrication.
"""
from __future__ import annotations

UNKNOWN_SUBSCRIPTION_ID = "unknown"

# Ordered (prefix, subscription_id) pairs, checked case-insensitively.
# subscription_id follows the {cli}:{provider} convention (analyse §3 /
# decisions.md 2026-07-14).
_MODEL_PREFIXES: tuple[tuple[str, str], ...] = (
    ("minimax-", "claude-code:minimax"),
    ("claude-", "claude-code:anthropic"),
)


def subscription_id_for_model(model: str | None) -> str:
    """Return the subscription_id a JSONL ``message.model`` belongs to.

    Falls back to ``UNKNOWN_SUBSCRIPTION_ID`` for any model that doesn't
    match a known prefix, including ``None``/empty and the literal
    ``"unknown"`` that ``UsageService.parse_usage_from_jsonl`` defaults to
    when a row has no ``message.model`` field.
    """
    if not model:
        return UNKNOWN_SUBSCRIPTION_ID
    normalized = model.strip().lower()
    for prefix, subscription_id in _MODEL_PREFIXES:
        if normalized.startswith(prefix):
            return subscription_id
    return UNKNOWN_SUBSCRIPTION_ID
