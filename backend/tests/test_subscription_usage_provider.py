"""Tests for SubscriptionUsageProvider — per-subscription overschot-signaal.

Phase 1a of the usage-aware routing plan (see
``docs/cockpit/subscription-flexibiliteit-analyse.md`` §5 / §8 #2):
deliver a normalised, honestly-labelled signal per subscription so phase 1b
can route against it. The signal is heterogeneous in quality per
subscription (analysis §2.4) — the output must not paper over that.

Each test class corresponds to one concrete provider. Tests pin:
- output schema (beschikbaar / drempel_gebruikt / bron / betrouwbaarheid);
- betrouwbaarheid labels per analyse §6.1 (Anthropic = schatting, never
  exact; MiniMax = exact only when the probe returns data; fallback =
  onbekend by construction);
- no fabrication: providers without a usable signal return
  ``betrouwbaarheid="onbekend"`` and ``drempel_gebruikt=None``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.subscriptions.anthropic import AnthropicUsageProvider
from app.services.subscriptions.base import SubscriptionUsage, SubscriptionUsageProvider
from app.services.subscriptions.minimax import MinimaxUsageProvider
from app.services.subscriptions.unknown import UnknownUsageProvider


def _make_block(
    *,
    total_tokens: int,
    is_active: bool = True,
    start_time: datetime | None = None,
) -> SimpleNamespace:
    """Mimic a SessionBlock enough for the providers' tests."""
    return SimpleNamespace(
        is_active=is_active,
        start_time=(start_time or datetime.now(UTC)).isoformat(),
        input_tokens=total_tokens // 4,
        output_tokens=total_tokens // 4,
        cache_creation_tokens=total_tokens // 4,
        cache_read_tokens=total_tokens // 4,
    )


class TestBaseContract:
    """The output shape is part of the public contract."""

    def test_subscription_usage_is_frozen(self):
        usage = SubscriptionUsage(
            subscription_id="claude-code:anthropic",
            subscription_label="Claude Code (Anthropic)",
            beschikbaar=True,
            drempel_gebruikt=0.5,
            bron="usage_service:active_block",
            betrouwbaarheid="schatting",
        )
        with pytest.raises((AttributeError, TypeError)):
            usage.beschikbaar = False  # type: ignore[misc]

    def test_provider_subclasses_must_override_get_usage(self):
        # Subclass without get_usage should not be instantiable.
        with pytest.raises(TypeError):

            class Incomplete(SubscriptionUsageProvider):
                id = "x:y"
                label = "x"

            Incomplete()


class TestUnknownUsageProvider:
    """Eerlijke fallback voor Codex/Copilot/OpenCode — géén fabricage."""

    def setup_method(self):
        self.provider = UnknownUsageProvider(
            subscription_id="codex-cli:codex",
            subscription_label="Codex",
        )

    async def test_id_and_label_propagate(self):
        usage = await self.provider.get_usage()
        assert usage.subscription_id == "codex-cli:codex"
        assert usage.subscription_label == "Codex"

    async def test_signal_is_onbekend(self):
        usage = await self.provider.get_usage()
        assert usage.betrouwbaarheid == "onbekend"

    async def test_drempel_gebruikt_is_none(self):
        # No fabrication: if there's no signal, the ratio is None, not 0.0.
        usage = await self.provider.get_usage()
        assert usage.drempel_gebruikt is None

    async def test_beschikbaar_is_true(self):
        # Analyse §6.3: subscriptions without a signal must be treated as
        # "available until the per-provider pause catches them" — the
        # router depends on this contract for fase 1b.
        usage = await self.provider.get_usage()
        assert usage.beschikbaar is True

    async def test_bron_is_explicit(self):
        usage = await self.provider.get_usage()
        assert usage.bron == "geen_signaal"


class TestAnthropicUsageProvider:
    """5h-venster-schatting uit UsageService + plan-tier limiet.

    Per analyse §6.1: dit is altijd ``schatting``, nooit ``exact`` — er
    is geen usage-API voor Pro/Max en de weekly-limiet is ongepubliceerd.
    """

    def setup_method(self):
        self.usage_service = MagicMock()
        self.plan_limit = 100_000
        self.provider = AnthropicUsageProvider(
            usage_service=self.usage_service,
            plan_tier_limit_tokens=self.plan_limit,
            subscription_id="claude-code:anthropic",
            subscription_label="Claude Code (Anthropic)",
        )

    async def test_active_block_below_limit_is_beschikbaar_and_schatting(self):
        block = _make_block(total_tokens=40_000)
        self.usage_service.get_block_usage = AsyncMock(
            return_value=SimpleNamespace(active_block=block)
        )
        usage = await self.provider.get_usage()
        assert usage.betrouwbaarheid == "schatting"
        assert usage.drempel_gebruikt == pytest.approx(0.4)
        assert usage.beschikbaar is True
        assert usage.bron == "usage_service:active_block"
        assert usage.subscription_id == "claude-code:anthropic"

    async def test_active_block_at_or_above_limit_is_not_beschikbaar(self):
        block = _make_block(total_tokens=110_000)
        self.usage_service.get_block_usage = AsyncMock(
            return_value=SimpleNamespace(active_block=block)
        )
        usage = await self.provider.get_usage()
        assert usage.beschikbaar is False
        assert usage.drempel_gebruikt == pytest.approx(1.1)
        assert usage.betrouwbaarheid == "schatting"

    async def test_no_active_block_returns_onbekend(self):
        # No block at all → can't estimate usage.
        self.usage_service.get_block_usage = AsyncMock(
            return_value=SimpleNamespace(active_block=None)
        )
        usage = await self.provider.get_usage()
        assert usage.betrouwbaarheid == "onbekend"
        assert usage.drempel_gebruikt is None
        assert usage.beschikbaar is True

    async def test_no_plan_limit_returns_onbekend(self):
        # Without a user-selected plan-tier limit we cannot compute the
        # ratio — fabricate nothing, mark the signal "onbekend" so the UI
        # can show "limit not published".
        provider = AnthropicUsageProvider(
            usage_service=self.usage_service,
            plan_tier_limit_tokens=None,
            subscription_id="claude-code:anthropic",
            subscription_label="Claude Code (Anthropic)",
        )
        block = _make_block(total_tokens=10_000)
        self.usage_service.get_block_usage = AsyncMock(
            return_value=SimpleNamespace(active_block=block)
        )
        usage = await provider.get_usage()
        assert usage.betrouwbaarheid == "onbekend"
        assert usage.drempel_gebruikt is None
        assert usage.bron == "geen_plan_tier"

    async def test_betrouwbaarheid_is_never_exact(self):
        # Belt-and-braces: even on a perfect-looking block we label this
        # ``schatting`` because Anthropic publishes no usage API. Pinning
        # this here so a future refactor can't accidentally upgrade the
        # label.
        block = _make_block(total_tokens=1)
        self.usage_service.get_block_usage = AsyncMock(
            return_value=SimpleNamespace(active_block=block)
        )
        usage = await self.provider.get_usage()
        assert usage.betrouwbaarheid == "schatting"
        assert usage.betrouwbaarheid != "exact"

    async def test_zero_limit_does_not_divide_by_zero(self):
        # A bogus plan-tier limit of 0 must not crash and must not report
        # a fake ratio — return onbekend instead.
        provider = AnthropicUsageProvider(
            usage_service=self.usage_service,
            plan_tier_limit_tokens=0,
            subscription_id="claude-code:anthropic",
            subscription_label="Claude Code (Anthropic)",
        )
        block = _make_block(total_tokens=10_000)
        self.usage_service.get_block_usage = AsyncMock(
            return_value=SimpleNamespace(active_block=block)
        )
        usage = await provider.get_usage()
        assert usage.betrouwbaarheid == "onbekend"
        assert usage.drempel_gebruikt is None


class TestMinimaxUsageProvider:
    """MiniMax remote API — exact wanneer de probe werkt, anders onbekend.

    Per ``subscriptions.md``: als de probe geen bruikbaar endpoint
    vindt, geven we een eerlijke lege staat terug — geen fabricage.
    """

    def setup_method(self):
        self.api_key = "test-key"
        self.base_url = "https://api.minimax.io/anthropic"
        self.probe_url = "https://api.minimax.io/anthropic/account/usage"
        self.provider = MinimaxUsageProvider(
            api_key=self.api_key,
            probe_url=self.probe_url,
            subscription_id="claude-code:minimax",
            subscription_label="Claude Code (MiniMax)",
        )

    async def test_no_api_key_returns_onbekend_no_fabrication(self):
        # Without credentials we can't probe — don't fabricate.
        provider = MinimaxUsageProvider(
            api_key=None,
            probe_url=self.probe_url,
            subscription_id="claude-code:minimax",
            subscription_label="Claude Code (MiniMax)",
        )
        usage = await provider.get_usage()
        assert usage.betrouwbaarheid == "onbekend"
        assert usage.drempel_gebruikt is None
        assert usage.beschikbaar is True
        assert usage.bron == "minimax_api:no_credentials"

    async def test_no_probe_url_returns_onbekend(self):
        # We can't guess MiniMax's usage endpoint — absent an explicit
        # probe URL we mark the signal onbekend (subscriptions.md:
        # "no fabrication").
        provider = MinimaxUsageProvider(
            api_key=self.api_key,
            probe_url=None,
            subscription_id="claude-code:minimax",
            subscription_label="Claude Code (MiniMax)",
        )
        usage = await provider.get_usage()
        assert usage.betrouwbaarheid == "onbekend"
        assert usage.bron == "minimax_api:no_probe_url"

    async def test_successful_probe_with_remaining_ratio_returns_exact(self):
        # The probe returns a structured payload with a 0-1 remaining
        # ratio. Convert to drempel_gebruikt = 1 - remaining.
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"remaining_ratio": 0.4}
        response.raise_for_status = MagicMock()

        with patch("app.services.subscriptions.minimax.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=response)
            mock_client_cls.return_value = mock_client

            usage = await self.provider.get_usage()

        assert usage.betrouwbaarheid == "exact"
        assert usage.drempel_gebruikt == pytest.approx(0.6)
        assert usage.beschikbaar is True
        assert usage.bron == "minimax_api:probe"
        assert usage.subscription_id == "claude-code:minimax"

    async def test_successful_probe_at_or_near_limit_not_beschikbaar(self):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"remaining_ratio": 0.0}
        response.raise_for_status = MagicMock()

        with patch("app.services.subscriptions.minimax.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=response)
            mock_client_cls.return_value = mock_client

            usage = await self.provider.get_usage()

        assert usage.betrouwbaarheid == "exact"
        assert usage.drempel_gebruikt == pytest.approx(1.0)
        assert usage.beschikbaar is False

    async def test_probe_http_error_returns_onbekend(self):
        import httpx

        with patch("app.services.subscriptions.minimax.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(
                side_effect=httpx.HTTPError("boom")
            )
            mock_client_cls.return_value = mock_client

            usage = await self.provider.get_usage()

        assert usage.betrouwbaarheid == "onbekend"
        assert usage.drempel_gebruikt is None
        assert usage.beschikbaar is True
        assert usage.bron == "minimax_api:probe_failed"

    async def test_probe_unparseable_payload_returns_onbekend(self):
        # The endpoint returned 200 but with no usable usage signal.
        response = MagicMock()
        response.status_code = 200
        response.json.side_effect = ValueError("not json")
        response.raise_for_status = MagicMock()

        with patch("app.services.subscriptions.minimax.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=response)
            mock_client_cls.return_value = mock_client

            usage = await self.provider.get_usage()

        assert usage.betrouwbaarheid == "onbekend"
        assert usage.bron == "minimax_api:probe_unparseable"


class TestNoCrossVendorNormalization:
    """Analyse §6.2: signals stay per-subscription, never normalised
    into a single comparable score."""

    async def test_known_and_unknown_providers_return_independent_signals(self):
        # An UnknownUsageProvider and an AnthropicUsageProvider with a
        # tiny block must each emit their own betrouwbaarheid / bron —
        # they do not collapse into one comparable number.
        unknown = UnknownUsageProvider(
            subscription_id="codex-cli:codex",
            subscription_label="Codex",
        )
        usage_service = MagicMock()
        usage_service.get_block_usage = AsyncMock(
            return_value=SimpleNamespace(
                active_block=_make_block(total_tokens=50)
            )
        )
        anthropic = AnthropicUsageProvider(
            usage_service=usage_service,
            plan_tier_limit_tokens=100_000,
            subscription_id="claude-code:anthropic",
            subscription_label="Claude Code (Anthropic)",
        )

        u_usage = await unknown.get_usage()
        a_usage = await anthropic.get_usage()

        assert u_usage.betrouwbaarheid == "onbekend"
        assert a_usage.betrouwbaarheid == "schatting"
        # They are NOT the same field — betrouwbaarheid itself carries the
        # inequality, so callers can render "schatting vs onbekend" rather
        # than treating both as numbers.
        assert u_usage.betrouwbaarheid != a_usage.betrouwbaarheid
        # And the drempel_gebruikt is None for one, a fraction for the
        # other — different units of comparison by design.
        assert u_usage.drempel_gebruikt is None
        assert a_usage.drempel_gebruikt is not None