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

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.subscriptions.anthropic import AnthropicUsageProvider
from app.services.subscriptions.base import SubscriptionUsage, SubscriptionUsageProvider
from app.services.subscriptions.minimax import MinimaxUsageProvider
from app.services.subscriptions.router import RouterUsageProvider
from app.services.subscriptions.unknown import UnknownUsageProvider


@pytest.fixture(autouse=True)
def _no_real_statusline_capture(monkeypatch, tmp_path):
    """Keep the Anthropic ladder on its local-estimate rung by default.

    ``read_windows`` otherwise reads this developer's real
    ``~/.claude-registry/rate-limits.json``. Once the statusline wrapper
    is installed that file exists, and every assertion in this module
    about ``schatting``/``verbruikt`` would flip to ``exact`` depending
    on whether a terminal happened to be open — a suite that passes or
    fails based on host state. Tests that want the official rung build
    their own capture file and pass its path explicitly.
    """
    monkeypatch.setattr(
        "app.services.subscriptions.statusline_state.DEFAULT_STATE_PATH",
        tmp_path / "no-capture.json",
    )


def _make_block(
    *,
    total_tokens: int,
    cache_read_tokens: int = 0,
    is_active: bool = True,
    start_time: datetime | None = None,
) -> SimpleNamespace:
    """Mimic a SessionBlock enough for the providers' tests.

    ``total_tokens`` is the quota-counted total (input + output +
    cache_creation), split evenly across those three buckets.
    ``cache_read_tokens`` is added verbatim to the block but must NOT
    affect the quota estimate — per docs/cockpit/cache-read-quota-decision.md
    cache_read costs no subscription quota.
    """
    start = start_time or datetime.now(UTC)
    third = total_tokens // 3
    return SimpleNamespace(
        is_active=is_active,
        start_time=start.isoformat(),
        end_time=(start + timedelta(hours=5)).isoformat(),
        input_tokens=third,
        output_tokens=third,
        cache_creation_tokens=total_tokens - 2 * third,
        cache_read_tokens=cache_read_tokens,
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


class TestAnthropicUsageProvider:
    """Absolute 5h-block usage from UsageService — no ratio, no limit.

    The plan-tier denominator was removed (see the module docstring in
    ``app/services/subscriptions/anthropic.py``): every measured 5h block
    on the reference machine exceeded even the Max 20x community estimate,
    so the percentage was a ratio against a guess. The provider now reports
    the measured token count and leaves ``limiet`` / ``drempel_gebruikt``
    unset. ``betrouwbaarheid`` stays ``schatting`` — the count comes from
    local JSONL logs, not from Anthropic.
    """

    def setup_method(self):
        self.usage_service = MagicMock()
        self.provider = AnthropicUsageProvider(
            usage_service=self.usage_service,
            subscription_id="claude-code:anthropic",
            subscription_label="Claude Code (Anthropic)",
        )

    async def test_active_block_reports_absolute_usage_without_ratio(self):
        block = _make_block(total_tokens=40_000)
        self.usage_service.get_block_usage = AsyncMock(
            return_value=SimpleNamespace(active_block=block)
        )
        usage = await self.provider.get_usage()
        assert usage.betrouwbaarheid == "schatting"
        assert usage.verbruikt == 40_000
        assert usage.drempel_gebruikt is None
        assert usage.limiet is None
        assert usage.bron == "usage_service:active_block"
        assert usage.subscription_id == "claude-code:anthropic"

    async def test_active_block_populates_display_fields(self):
        block = _make_block(total_tokens=40_000)
        self.usage_service.get_block_usage = AsyncMock(
            return_value=SimpleNamespace(active_block=block)
        )
        usage = await self.provider.get_usage()
        assert usage.verbruikt == 40_000
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

    async def test_no_active_block_returns_onbekend(self):
        self.usage_service.get_block_usage = AsyncMock(
            return_value=SimpleNamespace(active_block=None)
        )
        usage = await self.provider.get_usage()
        assert usage.betrouwbaarheid == "onbekend"
        assert usage.drempel_gebruikt is None
        assert usage.beschikbaar is True

    async def test_huge_block_stays_beschikbaar(self):
        # The whole point of dropping the denominator: a block far above any
        # community tier estimate must NOT flip ``beschikbaar`` to False and
        # pause the lane. The real backstop is the per-provider rate-limit
        # pause, not a guessed budget.
        block = _make_block(total_tokens=3_500_000)
        self.usage_service.get_block_usage = AsyncMock(
            return_value=SimpleNamespace(active_block=block)
        )
        usage = await self.provider.get_usage()
        assert usage.beschikbaar is True
        assert usage.drempel_gebruikt is None
        assert usage.verbruikt == 3_500_000

    async def test_betrouwbaarheid_is_never_exact(self):
        # The count is summed from local logs, so it measures what this
        # machine recorded, not what Anthropic billed. Pinned so a refactor
        # can't quietly upgrade the label.
        block = _make_block(total_tokens=1)
        self.usage_service.get_block_usage = AsyncMock(
            return_value=SimpleNamespace(active_block=block)
        )
        usage = await self.provider.get_usage()
        assert usage.betrouwbaarheid == "schatting"
        assert usage.betrouwbaarheid != "exact"

    async def test_cache_read_is_excluded_from_the_count(self):
        # Regressie voor kaart d63e83f0... — docs/cockpit/cache-read-quota-decision.md
        # (Scenario B, gemeten w≈0): cache_read kost geen abonnementsquotum en
        # mag het gerapporteerde verbruik dus niet opblazen.
        block = _make_block(total_tokens=40_000, cache_read_tokens=5_000_000)
        self.usage_service.get_block_usage = AsyncMock(
            return_value=SimpleNamespace(active_block=block)
        )
        usage = await self.provider.get_usage()
        assert usage.verbruikt == 40_000
        assert usage.beschikbaar is True

    async def test_usage_service_failure_returns_onbekend(self):
        self.usage_service.get_block_usage = AsyncMock(side_effect=RuntimeError("boom"))
        usage = await self.provider.get_usage()
        assert usage.betrouwbaarheid == "onbekend"
        assert usage.drempel_gebruikt is None
        assert usage.bron == "usage_service:fout"


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
        # block. Pre-fix, get_block_usage summed both and reported 100k as
        # Anthropic usage. Post-fix, only the 10k Anthropic tokens count.
        usage_service.get_all_usage_entries = AsyncMock(
            return_value=[
                entry("claude-sonnet-4-20250514", 10_000),
                entry("MiniMax-M3", 90_000),
            ]
        )

        provider = AnthropicUsageProvider(
            usage_service=usage_service,
            subscription_id="claude-code:anthropic",
            subscription_label="Claude Code (Anthropic)",
        )

        usage = await provider.get_usage()

        assert usage.verbruikt == 10_000
        assert usage.beschikbaar is True


#: Verbatim shape of ``GET /v1/token_plan/remains``, captured from a live
#: Coding Plan key on 2026-08-14. Interval window ``end-start`` is exactly
#: 18,000,000 ms (5h); weekly is exactly 604,800,000 ms (7d). The account
#: was at 100% interval remaining and 56% weekly remaining, i.e. 44% of
#: the week consumed — the numbers the assertions below are pinned to.
MINIMAX_LIVE_PAYLOAD = {
    "model_remains": [
        {
            "start_time": 1786701600000,
            "end_time": 1786719600000,
            "remains_time": 9864695,
            "current_interval_total_count": 0,
            "current_interval_usage_count": 0,
            "model_name": "general",
            "current_weekly_total_count": 0,
            "current_weekly_usage_count": 0,
            "weekly_start_time": 1786320000000,
            "weekly_end_time": 1786924800000,
            "weekly_remains_time": 215064695,
            "current_interval_status": 1,
            "current_interval_remaining_percent": 100,
            "current_weekly_status": 1,
            "current_weekly_remaining_percent": 56,
        },
        {
            # Separate product on its own windows — must never be summed
            # into, or mistaken for, the text/coding quota.
            "start_time": 1786665600000,
            "end_time": 1786752000000,
            "model_name": "video",
            "weekly_start_time": 1786320000000,
            "weekly_end_time": 1786924800000,
            "current_interval_status": 3,
            "current_interval_remaining_percent": 100,
            "current_weekly_status": 3,
            "current_weekly_remaining_percent": 100,
        },
    ],
    "base_resp": {"status_code": 0, "status_msg": "success"},
}


class TestMinimaxUsageProvider:
    """MiniMax remote API — exact wanneer de probe werkt, anders onbekend.

    Per ``subscriptions.md``: als de probe geen bruikbaar endpoint
    vindt, geven we een eerlijke lege staat terug — geen fabricage.
    """

    def setup_method(self):
        self.api_key = "test-key"
        self.probe_url = "https://api.minimax.io/v1/token_plan/remains"
        self.provider = MinimaxUsageProvider(
            api_key=self.api_key,
            probe_url=self.probe_url,
            subscription_id="claude-code:minimax",
            subscription_label="Claude Code (MiniMax)",
        )

    @staticmethod
    @contextmanager
    def _probe_returns(payload=None, *, json_error=False, http_error=False):
        """Patch the httpx client used by the provider for one probe."""
        import httpx

        response = MagicMock()
        response.status_code = 200
        response.raise_for_status = MagicMock()
        if json_error:
            response.json.side_effect = ValueError("not json")
        else:
            response.json.return_value = payload

        with patch(
            "app.services.subscriptions.minimax.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(
                side_effect=httpx.HTTPError("boom") if http_error else None,
                return_value=None if http_error else response,
            )
            mock_client_cls.return_value = mock_client
            yield

    async def test_no_api_key_returns_onbekend_no_fabrication(self):
        # Without credentials we can't probe — don't fabricate.
        provider = MinimaxUsageProvider(api_key=None, probe_url=self.probe_url)
        usage = await provider.get_usage()
        assert usage.betrouwbaarheid == "onbekend"
        assert usage.drempel_gebruikt is None
        assert usage.beschikbaar is True
        assert usage.bron == "minimax_api:no_credentials"

    async def test_explicit_none_probe_url_returns_onbekend(self):
        provider = MinimaxUsageProvider(api_key=self.api_key, probe_url=None)
        usage = await provider.get_usage()
        assert usage.betrouwbaarheid == "onbekend"
        assert usage.bron == "minimax_api:no_probe_url"

    async def test_default_probe_url_is_the_international_host(self):
        # The quota probe and the dispatch traffic must share a hostname;
        # a default of None would silently disable the whole provider.
        provider = MinimaxUsageProvider(api_key=self.api_key)
        assert provider._probe_url == (
            "https://api.minimax.io/v1/token_plan/remains"
        )

    async def test_live_payload_yields_both_windows_exact(self):
        with self._probe_returns(MINIMAX_LIVE_PAYLOAD):
            usage = await self.provider.get_usage()

        assert usage.betrouwbaarheid == "exact"
        assert usage.bron == "minimax_api:token_plan_remains"
        assert [w.label for w in usage.windows] == ["5h", "weekly"]

    async def test_remaining_percent_is_inverted_to_used(self):
        # The endpoint reports what is LEFT. 56% remaining is 44% used;
        # shipping 0.56 here would tell the router the opposite of the
        # truth and route onto the more-exhausted lane.
        with self._probe_returns(MINIMAX_LIVE_PAYLOAD):
            usage = await self.provider.get_usage()

        five_h, weekly = usage.windows
        assert five_h.used_fraction == pytest.approx(0.0)
        assert weekly.used_fraction == pytest.approx(0.44)
        assert weekly.verbruikt == pytest.approx(44.0)
        assert weekly.limiet == pytest.approx(100.0)
        assert weekly.eenheid == "%"

    async def test_drempel_gebruikt_is_the_worst_window(self):
        # 0% of the 5h and 44% of the week -> the scalar the router reads
        # must be 0.44, not the average and not the first window.
        with self._probe_returns(MINIMAX_LIVE_PAYLOAD):
            usage = await self.provider.get_usage()

        assert usage.drempel_gebruikt == pytest.approx(0.44)
        assert usage.venster_label == "weekly"
        assert usage.beschikbaar is True

    async def test_reset_timestamps_parsed_from_epoch_ms(self):
        with self._probe_returns(MINIMAX_LIVE_PAYLOAD):
            usage = await self.provider.get_usage()

        five_h, weekly = usage.windows
        assert five_h.resets_at == datetime.fromtimestamp(1786719600, tz=UTC)
        assert weekly.resets_at == datetime.fromtimestamp(1786924800, tz=UTC)
        # Window durations are a property of the payload, not an assumption.
        assert (1786719600000 - 1786701600000) == 5 * 60 * 60 * 1000
        assert (1786924800000 - 1786320000000) == 7 * 24 * 60 * 60 * 1000

    async def test_video_entry_is_never_read_as_the_text_quota(self):
        # ``video`` sits at 100% remaining; picking model_remains[0] or
        # merging entries would mask a nearly-exhausted coding quota.
        payload = {
            "model_remains": [
                MINIMAX_LIVE_PAYLOAD["model_remains"][1],
                {
                    **MINIMAX_LIVE_PAYLOAD["model_remains"][0],
                    "current_weekly_remaining_percent": 5,
                },
            ],
            "base_resp": {"status_code": 0},
        }
        with self._probe_returns(payload):
            usage = await self.provider.get_usage()

        assert usage.drempel_gebruikt == pytest.approx(0.95)

    async def test_missing_text_plan_returns_onbekend(self):
        payload = {
            "model_remains": [MINIMAX_LIVE_PAYLOAD["model_remains"][1]],
            "base_resp": {"status_code": 0},
        }
        with self._probe_returns(payload):
            usage = await self.provider.get_usage()

        assert usage.betrouwbaarheid == "onbekend"
        assert usage.bron == "minimax_api:no_text_plan"

    async def test_application_error_under_http_200_returns_onbekend(self):
        # MiniMax answers 200 for auth/quota failures; the verdict is in
        # base_resp.status_code. Trusting the HTTP status would turn an
        # error body into a fabricated "0% used".
        payload = {
            "base_resp": {"status_code": 1004, "status_msg": "auth failed"},
        }
        with self._probe_returns(payload):
            usage = await self.provider.get_usage()

        assert usage.betrouwbaarheid == "onbekend"
        assert usage.bron == "minimax_api:probe_error"

    async def test_inactive_window_status_is_dropped_not_guessed(self):
        payload = {
            "model_remains": [
                {
                    **MINIMAX_LIVE_PAYLOAD["model_remains"][0],
                    "current_interval_status": 3,
                }
            ],
            "base_resp": {"status_code": 0},
        }
        with self._probe_returns(payload):
            usage = await self.provider.get_usage()

        assert [w.label for w in usage.windows] == ["weekly"]
        assert usage.betrouwbaarheid == "exact"

    async def test_all_windows_inactive_returns_onbekend(self):
        payload = {
            "model_remains": [
                {
                    **MINIMAX_LIVE_PAYLOAD["model_remains"][0],
                    "current_interval_status": 3,
                    "current_weekly_status": 3,
                }
            ],
            "base_resp": {"status_code": 0},
        }
        with self._probe_returns(payload):
            usage = await self.provider.get_usage()

        assert usage.betrouwbaarheid == "onbekend"
        assert usage.drempel_gebruikt is None

    async def test_out_of_range_percent_is_rejected(self):
        payload = {
            "model_remains": [
                {
                    **MINIMAX_LIVE_PAYLOAD["model_remains"][0],
                    "current_interval_remaining_percent": 140,
                    "current_weekly_remaining_percent": -3,
                }
            ],
            "base_resp": {"status_code": 0},
        }
        with self._probe_returns(payload):
            usage = await self.provider.get_usage()

        assert usage.betrouwbaarheid == "onbekend"

    async def test_probe_http_error_returns_onbekend(self):
        with self._probe_returns(http_error=True):
            usage = await self.provider.get_usage()

        assert usage.betrouwbaarheid == "onbekend"
        assert usage.drempel_gebruikt is None
        assert usage.beschikbaar is True
        assert usage.bron == "minimax_api:probe_failed"

    async def test_probe_unparseable_payload_returns_onbekend(self):
        with self._probe_returns(json_error=True):
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
        )

        usage = await provider.get_usage()

        assert usage.verbruikt == 5_000
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
        )

        usage = await provider.get_usage()

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
        )
        reg.register_provider(real)
        assert reg.get_provider_for(cli="claude-code", provider="anthropic") is real

    def test_seeds_every_registered_cli_not_just_claude_code(self):
        """Kaart 8f40d443… (AC4): the pool router discriminates per
        ``{cli, provider}``, so *every* registered CLI needs a
        no-signal stub — not just ``claude-code``.

        Without this, ``get_provider_for(cli="open-code",
        provider="anthropic")`` returns ``None``, the entry is skipped
        from ``_gather_pool_usage_snapshots``' map entirely, and the
        operator has no way to tell "this CLI has no quota source" from
        "this subscription is at 0%". Both keep the entry eligible
        (analyse §6.3) but they are different stories (§6.1 "no
        fabrication")."""
        from app.services.agentic_cli import get_agentic_clis
        from app.services.subscriptions import registry as reg
        reg.register_default_providers()
        cli_ids = [cli.id for cli in get_agentic_clis()]
        assert len(cli_ids) > 1, "expected multiple registered CLI adapters"
        for cli_id in cli_ids:
            for prov in (
                "anthropic", "bedrock", "minimax", "anthropic-compatible",
            ):
                assert reg.get_provider_for(
                    cli=cli_id, provider=prov,
                ) is not None, f"missing no-signal stub for {cli_id}:{prov}"

    async def test_non_claude_code_stubs_degrade_explicitly(self):
        """Kaart 8f40d443… (AC4): a seeded stub for a non-claude-code
        CLI reports ``betrouwbaarheid="onbekend"`` /
        ``drempel_gebruikt=None`` — an *explicit* no-signal degradation,
        never a fabricated 0%. ``beschikbaar`` stays True so routing is
        unchanged (analyse §6.3); only the honesty channel changes."""
        from app.services.subscriptions import registry as reg
        reg.register_default_providers()
        for cli_id in ("open-code", "codex-cli", "copilot-cli", "mimo-code"):
            provider = reg.get_provider_for(cli=cli_id, provider="anthropic")
            usage = await provider.get_usage()
            assert usage.subscription_id == f"{cli_id}:anthropic"
            assert usage.betrouwbaarheid == "onbekend"
            assert usage.drempel_gebruikt is None
            assert usage.beschikbaar is True

    async def test_router_provider_stays_claude_code_only(self):
        """The ``RouterUsageProvider`` seed is a claude-code concept
        (router endpoints live under the claude-code transport — see
        ``agentic_cli/endpoints.py``). Other CLIs get the generic
        ``UnknownUsageProvider`` for ``anthropic-compatible`` so the UI
        shows an honest "geen signaal-bron" rather than an unearned
        router badge."""
        from app.services.subscriptions import registry as reg
        reg.register_default_providers()
        assert isinstance(
            reg.get_provider_for(
                cli="claude-code", provider="anthropic-compatible",
            ),
            RouterUsageProvider,
        )
        other = reg.get_provider_for(
            cli="open-code", provider="anthropic-compatible",
        )
        assert other is not None
        assert not isinstance(other, RouterUsageProvider)

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
        # Neither publishes a ratio: the stub has no signal at all, the
        # Anthropic row has a measured absolute count but no honest
        # denominator. ``verbruikt`` is what separates them.
        assert u_usage.drempel_gebruikt is None
        assert a_usage.drempel_gebruikt is None
        assert u_usage.verbruikt is None
        assert a_usage.verbruikt is not None