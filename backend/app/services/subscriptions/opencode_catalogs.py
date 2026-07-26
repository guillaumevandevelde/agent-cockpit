"""Hardcoded seed model catalogs for OpenCode's hosted-subscription
providers (``opencode-go`` and ``opencode`` / Zen).

Both catalogs live in OpenCode's own CLI catalog (see
https://opencode.ai/docs/go/models and https://opencode.ai/docs/zen),
authenticated via ``~/.local/share/opencode/auth.json`` or
``OPENCODE_API_KEY``. OpenCode publishes no durable authenticated model
endpoint, so this module seeds the known catalog at code-time — the
``opencode run --model opencode-go/<id>`` flag is the only contract the
spawn layer relies on, and that contract is satisfied once these ids
are surfaced to the kanban subscription pool / dispatch picker.

Why hardcoded and not fetched:

* OpenCode's metadata endpoint is unauthenticated, which means it lists
  models a subscriber may not actually be entitled to (per the
  ``julien.cloud/opencode-go-models`` aggregator). A runtime fetcher
  would give a list that's wider than what the user's key grants —
  worse UX than a curated seed.
* The Go catalog is small (16 models) and changes slowly; a quarterly
  manual update here is cheaper than validating a fetcher.
* A future card can swap this for a SecretStore-backed fetcher that
  queries OpenCode Zen's user-scoped model endpoint — the consumer
  surface (``MODEL_CATALOG["opencode-go"]``) stays the same.

All model ids are the bare ids as documented in OpenCode's
subscription tables; the spawn layer (``open_code.py:
build_spawn_command``) prefixes them with the OpenCode provider id to
produce the required ``provider/model`` form for ``--model``.
"""
from __future__ import annotations

from app.services.agentic_cli.provider_env import (
    PROVIDER_OPENCODE_GO,
    PROVIDER_OPENCODE_ZEN,
)

MODEL_CATALOG: dict[str, tuple[str, ...]] = {
    # Source: https://opencode.ai/docs/go (subscription models table).
    # Sorted by display name, mirroring the docs order so diffs are
    # easy to eyeball when this list is refreshed.
    PROVIDER_OPENCODE_GO: (
        "deepseek-v4-flash",
        "deepseek-v4-pro",
        "glm-5.1",
        "glm-5.2",
        "grok-4.5",
        "hy3",
        "kimi-k2.6",
        "kimi-k2.7-code",
        "kimi-k3",
        "mimo-v2.5",
        "mimo-v2.5-pro",
        "minimax-m2.7",
        "minimax-m3",
        "qwen3.6-plus",
        "qwen3.7-max",
        "qwen3.7-plus",
    ),
    # Source: https://opencode.ai/docs/zen. Zen's pay-as-you-go catalog
    # is large (OpenAI/Anthropic/Google + the same open models plus
    # some free-tier variants); only the curated subset that mirrors the
    # Go subscription's most-used ids is seeded here. Callers that need
    # the full Zen catalog must add it explicitly — keeping the seed
    # minimal avoids picking a Zen pay-as-you-go model by accident when
    # a Go-equivalent was the intent.
    PROVIDER_OPENCODE_ZEN: (
        "glm-5",
        "glm-5.1",
        "glm-5.2",
    ),
}


def models_for(provider: str) -> tuple[str, ...]:
    """Return the seeded model ids for ``provider`` (Go or Zen).

    Returns an empty tuple for an unknown provider rather than raising,
    so the dispatch picker falls through to "no model pin" instead of
    wedging on a KeyError mid-spawn — same fail-soft contract the
    subscription-pool router uses (``subscription_pool._deserialize_entries``).
    """
    return MODEL_CATALOG.get(provider, ())