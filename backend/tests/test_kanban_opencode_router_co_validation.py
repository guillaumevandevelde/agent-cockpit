"""Router co-validation tests for OpenCode Go + Zen column saves.

``_allowed_models_for_provider`` now returns the curated catalog for
``opencode-go`` and ``opencode`` (mirrored from
``opencode_catalogs.MODEL_CATALOG``), so an ``update_column`` PATCH
co-validation accepts known opencode-go models and rejects unknown ids
with the same 422 that ``anthropic`` / ``minimax`` already raise.
Also covers ``service._validate_default_provider`` accepting the two
new providers (the first gate any column-save PATCH hits).
"""
import asyncio

import pytest

from app.api.v1.kanban.router import _allowed_models_for_provider
from app.kanban.service import _validate_default_provider


def test_allowed_models_contains_known_opencode_go_model() -> None:
    allowed = asyncio.run(_allowed_models_for_provider(None, "opencode-go"))
    assert "glm-5.2" in allowed
    assert "deepseek-v4-flash" in allowed


def test_co_validation_rejects_claude_alias_under_opencode_go() -> None:
    allowed = asyncio.run(_allowed_models_for_provider(None, "opencode-go"))
    assert "opus" not in allowed
    assert "sonnet" not in allowed


def test_co_validation_rejects_go_only_model_under_zen() -> None:
    allowed = asyncio.run(_allowed_models_for_provider(None, "opencode"))
    assert "glm-5.1" in allowed
    assert "kimi-k3" not in allowed


def test_service_validate_default_provider_accepts_opencode_providers() -> None:
    _validate_default_provider(None)
    _validate_default_provider("opencode-go")
    _validate_default_provider("opencode")
    with pytest.raises(ValueError):
        _validate_default_provider("opencode-bogus")