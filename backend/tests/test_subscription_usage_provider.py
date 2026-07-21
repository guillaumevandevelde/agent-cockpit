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

Kaart 390756e6... voegt ``TestRouterUsageProvider`` en
``TestRouterTokensDoNotPolluteAnthropicCount`` toe: de eerste
vergrendelt het "no fabrication"-contract voor router-eindpunten
(geen betrouwbare quota-bron — attribueer geen cijfer); de tweede
bewijst dat router-upstream-modellenamen
(``gpt-4o``/``gemini-*``/etc.) niet in de Anthropic 5h-window
terechtkomen — het regressie-schild tegen herhaling van de
``a410468d…`` 36,9%-vervuiling.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.subscriptions.anthropic import AnthropicUsageProvider
from app.services.subscriptions.base import SubscriptionUsage, SubscriptionUsageProvider
from app.services.subscriptions.minimax import MinimaxUsageProvider
from app.services.subscriptions.router import RouterUsageProvider
from app.services.subscriptions.unknown import UnknownUsageProvider


def _make_block(
    *,
    total_tokens: int,
    is_active: bool = True,
    start_time: datetime | None = None,
) -> SimpleNamespace:
    """Mimic a SessionBlock enough for the providers' tests."""
    start = start_time or datetime.now(UTC)
    return SimpleNamespace(
        is_active=is_active,
        start_time=start.isoformat(),
        end_time=(start + timedelta(hours=5)).isoformat(),
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

    async def test_display_fields_default_to_none(self):
        # No fabrication: a provider without a raw count must not
        # synthesize verbruikt/limiet/venster_label.
        usage = await self.provider.get_usage()
        assert usage.verbruikt is None
        assert usage.limiet is None
        assert usage.venster_label is None
        assert usage.reset_op is None
        assert usage.eenheid == "tokens"


class TestAnthropicPlanTiers:
    """The tier constants consumed by the Subscriptions-pagina plan-tier
    picker (kaart 9bce091a...). Never labelled ``exact`` — these are
    best-effort estimates, not published by Anthropic (analyse §7.2)."""

    def test_known_tiers_have_label_and_token_budget(self):
        from app.services.subscriptions.anthropic import ANTHROPIC_PLAN_TIERS

        assert set(ANTHROPIC_PLAN_TIERS) == {"pro", "max_5x", "max_20x"}
        for tier in ANTHROPIC_PLAN_TIERS.values():
            assert isinstance(tier["label"], str) and tier["label"]
            assert isinstance(tier["tokens_5h"], int) and tier["tokens_5h"] > 0


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

    async def test_active_block_populates_display_fields(self):
        # kaart 9bce091a...: the Subscriptions-pagina needs raw
        # verbruikt/limiet/venster/reset_op, not just the fraction.
        block = _make_block(total_tokens=40_000)
        self.usage_service.get_block_usage = AsyncMock(
            return_value=SimpleNamespace(active_block=block)
        )
        usage = await self.provider.get_usage()
        assert usage.verbruikt == 40_000
        assert usage.limiet == self.plan_limit
        assert usage.eenheid == "tokens"
        assert usage.venster_label == "5h rate"
        assert usage.reset_op is not None
        assert usage.reset_op.isoformat() == block.end_time

    async def test_no_active_block_leaves_display_fields_none(self):
        self.usage_service.get_block_usage = AsyncMock(
            return_value=SimpleNamespace(active_block=None)
        )
        usage = await self.provider.get_usage()
        assert usage.verbruikt is None
        assert usage.limiet is None
        assert usage.venster_label is None
        assert usage.reset_op is None

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


class TestAnthropicUsageProviderModelAttribution:
    """Regression test for kaart d160d13f...: MiniMax tokens counted as
    Anthropic plan-tier usage.

    Uses a real ``UsageService`` (not a mocked ``get_block_usage``) so the
    ``subscription_id`` filter is actually exercised end-to-end rather than
    bypassed by a mock that already returns a pre-summed block.
    """

    async def test_mixed_anthropic_and_minimax_traffic_excludes_minimax(self):
        from app.services.usage_service import LoadedUsageEntry, UsageService

        usage_service = UsageService(db=None)
        now = datetime.now(UTC)

        def entry(model: str, input_tokens: int) -> LoadedUsageEntry:
            return LoadedUsageEntry(
                timestamp=now,
                input_tokens=input_tokens,
                output_tokens=0,
                cache_creation_tokens=0,
                cache_read_tokens=0,
                cost_usd=0.0,
                model=model,
                session_id="s1",
                version="1.0.0",
                project_path="p",
            )

        # 10k Anthropic tokens + 90k MiniMax tokens in the same active
        # block. Pre-fix, get_block_usage summed both -> drempel_gebruikt
        # would be 100_000 / 100_000 = 1.0 (not beschikbaar). Post-fix,
        # only the 10k Anthropic tokens should count.
        usage_service.get_all_usage_entries = AsyncMock(
            return_value=[
                entry("claude-sonnet-4-20250514", 10_000),
                entry("MiniMax-M3", 90_000),
            ]
        )

        provider = AnthropicUsageProvider(
            usage_service=usage_service,
            plan_tier_limit_tokens=100_000,
            subscription_id="claude-code:anthropic",
            subscription_label="Claude Code (Anthropic)",
        )

        usage = await provider.get_usage()

        assert usage.drempel_gebruikt == pytest.approx(0.1)
        assert usage.beschikbaar is True


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


class TestRouterUsageProvider:
    """Eerlijke ``onbekend`` voor router-eindpunten (9router / LiteLLM).

    Kaart 390756e6...: een router-provider verbergt meerdere upstreams
    achter één lokaal endpoint, dus er is **geen** betrouwbare
    quota-bron waarop een ``drempel_gebruikt`` gebaseerd kan worden.
    De provider geeft standaard ``betrouwbaarheid="onbekend"`` terug
    zolang er geen externe quota-bron is geconfigureerd.

    De verplichting die deze testset vastpint: dezelfde
    "no fabrication"-discipline die ``UnknownUsageProvider`` al
    afdwingt voor Codex/Copilot/OpenCode, maar dan voor de expliciete
    router-subscription. Documentatie van de bron (``bron``) mag wel
    iets router-specifieks zeggen zodat de UI duidelijk kan maken
    waarom deze rij onzeker is.
    """

    def setup_method(self):
        self.provider = RouterUsageProvider(
            subscription_id="claude-code:anthropic-compatible",
            subscription_label="Claude Code (Router)",
        )

    async def test_id_and_label_propagate(self):
        usage = await self.provider.get_usage()
        assert usage.subscription_id == "claude-code:anthropic-compatible"
        assert usage.subscription_label == "Claude Code (Router)"

    async def test_signal_is_onbekend_not_fabricated(self):
        # Kaart 390756e6... AC#2: betrouwbaarheid="onbekend" zolang er
        # geen betrouwbare quota-bron is. De router belooft geen
        # upstream-numbers — we laten de UI eerlijk "Unknown" tonen.
        usage = await self.provider.get_usage()
        assert usage.betrouwbaarheid == "onbekend"

    async def test_drempel_gebruikt_is_none_no_fabrication(self):
        # Geen cijfer verzinnen: een drempel vereist een externe
        # quota-bron die een router niet kan bieden.
        usage = await self.provider.get_usage()
        assert usage.drempel_gebruikt is None

    async def test_beschikbaar_is_true(self):
        # Analyse §6.3 — ook zonder signaal wordt de subscription als
        # "available" gemarkeerd zodat de pool-router hem niet
        # voorbij-loopt op een ontbrekende meting; de per-provider
        # pause blijft de gatekeeper.
        usage = await self.provider.get_usage()
        assert usage.beschikbaar is True

    async def test_bron_is_router_specific(self):
        # De UI moet kunnen uitleggen waarom de rij onzeker is; een
        # generieke "geen_signaal" verliest de router-context.
        usage = await self.provider.get_usage()
        assert usage.bron.startswith("router_eindpunt")

    async def test_display_fields_default_to_none(self):
        usage = await self.provider.get_usage()
        assert usage.verbruikt is None
        assert usage.limiet is None
        assert usage.venster_label is None
        assert usage.reset_op is None
        assert usage.eenheid == "tokens"


class TestRouterTokensDoNotPolluteAnthropicCount:
    """Regression-guard voor kaart 390756e6... AC#1 + AC#4.

    Router-verkeer (achter een Anthropic-compatible eindpunt zoals
    9router of LiteLLM) landt in dezelfde JSONL-tree als direct
    Anthropic-verkeer, maar ``AnthropicUsageProvider`` moet het
    uitsluiten — anders herhaalt de MiniMax-vermenging uit kaart
    ``a410468d…`` (36,9% van alle tokens op deze host) zich.

    Het mechanisme is dezelfde prefix-attribution als voor MiniMax,
    maar het scenario is anders: de router kan upstream-modelnamen
    doorgeven zoals ``gpt-4o`` of ``gemini-1.5-pro``, dus de
    huidige attributie zal ze als onbekend classificeren — niet als
    Anthropic. Deze testset vergrendelt dat contract vanaf de
    provider-kant: zelfs wanneer een router per ongeluk een upstream
    model exposeert dat op een Anthropic- of MiniMax-prefix lijkt,
    of wanneer een router op de Anthropic-passthrough draait, telt
    het **niet** mee in de Anthropic 5h-window.
    """

    async def test_routed_gpt_tokens_excluded_from_anthropic(self):
        from app.services.usage_service import LoadedUsageEntry, UsageService

        usage_service = UsageService(db=None)
        now = datetime.now(UTC)

        def entry(model: str, input_tokens: int) -> LoadedUsageEntry:
            return LoadedUsageEntry(
                timestamp=now,
                input_tokens=input_tokens,
                output_tokens=0,
                cache_creation_tokens=0,
                cache_read_tokens=0,
                cost_usd=0.0,
                model=model,
                session_id="s1",
                version="1.0.0",
                project_path="p",
            )

        # 5k directe Anthropic + 95k router-upstream "gpt-4o". De
        # Anthropic-prefix matcht enkel het eerste; de tweede is een
        # upstream-model waar de AnthropicUsageProvider niets van
        # weten wil.
        usage_service.get_all_usage_entries = AsyncMock(
            return_value=[
                entry("claude-sonnet-4-20250514", 5_000),
                entry("gpt-4o", 95_000),
            ]
        )

        provider = AnthropicUsageProvider(
            usage_service=usage_service,
            plan_tier_limit_tokens=100_000,
        )

        usage = await provider.get_usage()

        assert usage.drempel_gebruikt == pytest.approx(0.05)
        assert usage.beschikbaar is True

    async def test_routed_gemini_tokens_excluded_from_anthropic(self):
        from app.services.usage_service import LoadedUsageEntry, UsageService

        usage_service = UsageService(db=None)
        now = datetime.now(UTC)

        def entry(model: str, input_tokens: int) -> LoadedUsageEntry:
            return LoadedUsageEntry(
                timestamp=now,
                input_tokens=input_tokens,
                output_tokens=0,
                cache_creation_tokens=0,
                cache_read_tokens=0,
                cost_usd=0.0,
                model=model,
                session_id="s1",
                version="1.0.0",
                project_path="p",
            )

        usage_service.get_all_usage_entries = AsyncMock(
            return_value=[
                entry("claude-opus-4-7", 7_000),
                entry("gemini-1.5-pro", 50_000),
            ]
        )

        provider = AnthropicUsageProvider(
            usage_service=usage_service,
            plan_tier_limit_tokens=70_000,
        )

        usage = await provider.get_usage()

        assert usage.drempel_gebruikt == pytest.approx(0.1)
        assert usage.verbruikt == 7_000

    async def test_attribution_function_returns_unknown_for_router_upstreams(self):
        # De ``subscription_id_for_model`` attributie-functie is de
        # single-source-of-truth die de UsageService gebruikt; het
        # moet consequent "unknown" teruggeven voor upstream-model
        # namen van een router (gpt-*, gemini-*, llama-*, etc.) —
        # anders lekt router-verkeer alsnog in een
        # vendor-specifieke subscription-attributie.
        from app.services.subscriptions.attribution import (
            UNKNOWN_SUBSCRIPTION_ID,
            subscription_id_for_model,
        )

        for upstream in (
            "gpt-4o", "gpt-4-turbo", "gemini-1.5-pro",
            "llama-3.1-70b", "mistral-large-2",
            "deepseek-chat",
        ):
            assert subscription_id_for_model(upstream) == UNKNOWN_SUBSCRIPTION_ID, (
                f"router-upstream {upstream!r} must not match any vendor prefix; "
                "would re-create the a410468d 36.9% pollusion"
            )


class TestRegistrySeeding:
    """``register_default_providers`` (kaart ea7e038b… D2) — populates
    the registry with honest no-signal stubs for the known
    ``(cli, provider)`` pairs. The pool router's snapshot path needs
    the registry non-empty; before D2, ``_PROVIDERS`` was ``{}`` at
    startup and ``get_provider_for`` always returned ``None``."""

    @pytest.fixture(autouse=True)
    def _isolated_registry(self):
        # Snapshot module-level state so the test doesn't leak.
        # Self-improve kanban card 7a8788af...: this dance now lives
        # in ``registry.cleared_registry_for_tests`` (sibling of
        # ``seeded_registry_for_tests``) — these tests want a clean
        # registry because they call ``register_default_providers``
        # themselves to verify what happens after a fresh seed.
        from app.services.subscriptions import registry as reg
        with reg.cleared_registry_for_tests():
            yield

    def test_seeds_all_supported_providers(self):
        from app.services.subscriptions import registry as reg
        reg.register_default_providers()
        # Every (cli, provider) the pool allow-list permits gets a stub.
        # Includes `anthropic-compatible` since kaart 333af652e... shipped
        # the data-driven provider, and kaart 390756e6... (this card)
        # wired its router-eindpunt to an honest onbekend signal.
        for prov in ("anthropic", "bedrock", "minimax", "anthropic-compatible"):
            assert reg.get_provider_for(
                cli="claude-code", provider=prov,
            ) is not None, f"missing default provider for {prov}"

    def test_seeds_per_cli_no_signal_stubs(self):
        """Kaart 8f40d443… (quota-pool CLI-agnostisch): the seed covers
        every registered CLI × provider combination — not just
        ``claude-code``.

        Without per-CLI stubs the pool router's per-CLI filter would
        silently skip a non-claude-code entry (``get_provider_for``
        returns ``None`` → ``_gather_pool_usage_snapshots`` omits
        the row → "no signal" with no honest stub to render on the
        Subscriptions-pagina). The acceptance criterion is "the
        Subscriptions-pagina UI shows the operator an honest
        'geen signaal-bron'-badge for each non-claude-code CLI"
        — the per-CLI seed is what makes that possible.
        """
        from app.services.subscriptions import registry as reg
        reg.register_default_providers()
        # Every CLI in the agentic_cli registry gets a row per provider
        # (incl. ``anthropic-compatible`` — same router-eindpunt shape
        # as claude-code today, but seeded as a generic UnknownUsageProvider
        # for non-claude-code CLIs since the router concept is
        # claude-code-only — see registry._supported_cli_ids).
        cli_ids = ("claude-code", "codex-cli", "copilot-cli",
                   "mimo-code", "open-code")
        for cli_id in cli_ids:
            for prov in ("anthropic", "bedrock", "minimax",
                         "anthropic-compatible"):
                provider = reg.get_provider_for(cli=cli_id, provider=prov)
                assert provider is not None, (
                    f"missing default no-signal stub for "
                    f"{cli_id}:{prov}"
                )

    async def test_seeded_providers_return_onbekend_snapshots(self):
        from app.services.subscriptions import registry as reg
        reg.register_default_providers()
        # Each stub is an UnknownUsageProvider — honest no-signal, no
        # fabrication (analyse §6.1 / §6.3). The router's _is_above_threshold
        # treats drempel_gebruikt=None as "no signal → available", which
        # is the pre-D2 behaviour — so the seed doesn't change routing
        # decisions, it just stops the snapshot path from short-circuiting.
        for prov in ("anthropic", "bedrock", "minimax", "anthropic-compatible"):
            provider = reg.get_provider_for(cli="claude-code", provider=prov)
            usage = await provider.get_usage()
            assert usage.betrouwbaarheid == "onbekend"
            assert usage.drempel_gebruikt is None
            assert usage.beschikbaar is True

    async def test_per_cli_stubs_return_onbekend_snapshots(self):
        """Kaart 8f40d443…: every per-CLI stub returns the same honest
        no-signal shape — ``onbekend``/``None``/``True`` — so the
        router treats every CLI uniformly under analyse §6.3. The
        UI then renders the same "geen signaal-bron"-badge across
        every registered CLI regardless of which vendor (claude,
        minimax, router) the registry entry targets."""
        from app.services.subscriptions import registry as reg
        reg.register_default_providers()
        cli_ids = ("claude-code", "codex-cli", "copilot-cli",
                   "mimo-code", "open-code")
        for cli_id in cli_ids:
            provider = reg.get_provider_for(
                cli=cli_id, provider="anthropic",
            )
            usage = await provider.get_usage()
            assert usage.betrouwbaarheid == "onbekend"
            assert usage.drempel_gebruikt is None
            assert usage.beschikbaar is True
            # And the snapshot id is the per-CLI keyed id — without
            # this the pool router's ``usages.get(f"{cli}:{provider}")``
            # lookup would still see "no signal" and the per-CLI seed
            # would silently degrade to the pre-feature behaviour.
            assert usage.subscription_id == f"{cli_id}:anthropic"

    async def test_anthropic_compatible_seeded_as_router_provider(self):
        # Kaart 390756e6... AC#2: de `anthropic-compatible` (router)
        # subscription-rij krijgt een RouterUsageProvider, niet de
        # generieke UnknownUsageProvider — anders verliest de UI de
        # context die deze provider wel levert (de `bron` begint met
        # ``router_eindpunt`` zodat de UI kan uitleggen waarom de rij
        # onzeker is).
        from app.services.subscriptions import registry as reg
        reg.register_default_providers()
        provider = reg.get_provider_for(
            cli="claude-code", provider="anthropic-compatible",
        )
        assert isinstance(provider, RouterUsageProvider)

    async def test_registered_provider_replaces_default(self):
        """A real AnthropicUsageProvider (with plan_tier) registered
        after the seed takes over by id — proves the seed doesn't lock
        the registry in. This is the upgrade path once a user has
        configured a real plan-tier: the call site does
        ``register_provider(real)`` and the stub is silently gone."""
        from app.services.subscriptions import registry as reg
        from app.services.subscriptions.anthropic import AnthropicUsageProvider

        reg.register_default_providers()
        stub = reg.get_provider_for(cli="claude-code", provider="anthropic")
        stub_usage = await stub.get_usage()
        assert stub_usage.betrouwbaarheid == "onbekend"

        real = AnthropicUsageProvider(
            usage_service=MagicMock(),
            plan_tier_limit_tokens=100_000,
        )
        reg.register_provider(real)
        assert reg.get_provider_for(cli="claude-code", provider="anthropic") is real

    def test_seeding_is_idempotent(self):
        from app.services.subscriptions import registry as reg
        reg.register_default_providers()
        first_keys = set(reg._PROVIDERS.keys())
        reg.register_default_providers()
        # No duplicates, same key set — re-running ``register_default_providers``
        # (e.g. lifespan re-entry, multiple workers, accidental double-call)
        # is safe and doesn't grow the registry. The values are fresh
        # ``UnknownUsageProvider`` instances — that doesn't matter, the
        # router only reads ``subscription_id`` / ``drempel_gebruikt`` from
        # the snapshot, both of which are derived from the registered id.
        assert set(reg._PROVIDERS.keys()) == first_keys
        assert len(reg._PROVIDERS) == len(first_keys)


class TestSeededRegistryForTests:
    """``seeded_registry_for_tests`` — the canonical test-side mirror of
    ``main.lifespan``'s ``register_default_providers`` seed.

    Self-improve kanban card 7a8788af... (analyst leaf-spike): the
    save/clear/seed/restore dance was copy-pasted across four test
    files (this one, ``test_subscriptions_endpoint.py``,
    ``test_subscription_prefs_service.py``, and the
    ``_registry_state`` contextmanager in
    ``test_subscription_pool_dispatch.py``). The helper standardises
    that shape so a future endpoint-test gets the realistic lifespan
    state without having to know the dance — and so the registry
    module owns the lifecycle of its own mutable state.
    """

    def test_helper_seeds_default_providers_while_inside(self):
        from app.services.subscriptions import registry as reg

        reg._PROVIDERS.clear()
        try:
            with reg.seeded_registry_for_tests() as reg_ctx:
                # Mirror of what ``main.lifespan`` puts in the registry
                # at startup — every supported (cli, provider) resolves
                # to a non-None provider, so endpoint-loop tests see
                # the same lookup-table as production.
                for prov in (
                    "anthropic", "bedrock", "minimax", "anthropic-compatible",
                ):
                    assert reg_ctx.get_provider_for(
                        cli="claude-code", provider=prov,
                    ) is not None, f"missing default provider for {prov}"
        finally:
            reg._PROVIDERS.clear()

    def test_helper_restores_pre_entry_state_on_exit(self):
        # Whatever was registered before the context must come back
        # after the context exits — including empty registries (no
        # lifespan ever ran) and registries with custom fakes a
        # sibling test registered.
        from app.services.subscriptions import registry as reg

        # Case 1: pre-entry was empty -> post-exit is empty.
        reg._PROVIDERS.clear()
        with reg.seeded_registry_for_tests():
            pass
        assert reg._PROVIDERS == {}

        # Case 2: pre-entry had a custom fake -> post-exit has the
        # same fake (not the seeded defaults).
        sentinel = UnknownUsageProvider(
            subscription_id="claude-code:custom-fake",
            subscription_label="custom-fake",
        )
        try:
            reg.register_provider(sentinel)
            assert reg.get_provider_for(
                cli="claude-code", provider="custom-fake",
            ) is sentinel
            with reg.seeded_registry_for_tests():
                # Inside the context the sentinel is replaced by the
                # seeded defaults; the registry doesn't merge.
                assert reg.get_provider_for(
                    cli="claude-code", provider="custom-fake",
                ) is None
            # Outside, the sentinel is back.
            assert reg.get_provider_for(
                cli="claude-code", provider="custom-fake",
            ) is sentinel
        finally:
            reg._PROVIDERS.clear()

    def test_helper_restores_even_when_body_raises(self):
        # try/finally inside the helper means a test that throws
        # doesn't leave the global registry in a seeded state and
        # leak the defaults into the next test.
        from app.services.subscriptions import registry as reg

        reg._PROVIDERS.clear()
        try:
            with pytest.raises(RuntimeError, match="boom"):
                with reg.seeded_registry_for_tests():
                    raise RuntimeError("boom")
            assert reg._PROVIDERS == {}
        finally:
            reg._PROVIDERS.clear()

    def test_helper_anthropic_compatible_seeds_router_provider(self):
        # Same lock as ``TestRegistrySeeding`` — the
        # ``anthropic-compatible`` slot gets a ``RouterUsageProvider``,
        # not the generic ``UnknownUsageProvider``, so endpoint-tests
        # see the router-row's ``bron`` / ``subscription_label`` they
        # assert against (kaart 390756e6...).
        from app.services.subscriptions import registry as reg

        reg._PROVIDERS.clear()
        try:
            with reg.seeded_registry_for_tests():
                provider = reg.get_provider_for(
                    cli="claude-code", provider="anthropic-compatible",
                )
            assert isinstance(provider, RouterUsageProvider)
        finally:
            reg._PROVIDERS.clear()


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