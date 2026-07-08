# Subscription usage leftover — show each provider's quota at a glance

**Date:** 2026-07-08
**Status:** Design — pending implementation
**Scope:** Add per-provider usage/quota display to the **Subscriptions** page (`/subscriptions`) for the two agent providers that are on subscription plans: **Anthropic** (Claude Code, via Anthropic OAuth) and **MiniMax** (via `MINIMAX_API_KEY`). One PR, two providers — MiniMax implemented first, then Anthropic.

## Problem

The Subscriptions page (which was renamed from "Providers" in commit `075b4c3`) currently renders only `MinimaxCredentialsCard` — a Save/Change/Clear form for the MiniMax API key. There is no visibility into "how much of each subscription do I have left?" — neither the 5-hour rate window (Anthropic Pro/Max) nor the weekly window. The user running two subscriptions in parallel (Anthropic + MiniMax) has no single screen that tells them which provider to route work to right now.

A related analysis doc (formerly `docs/cockpit/analyse-optimaal-gebruik-abonnementen.md`, relocated to `.local-notes/` in commit `a0d0cdc` because it discusses personal subscription cost strategy) calls this gap out explicitly: *"Kostenzichtbaarheid: `usage_service.py` volgt vandaag alleen Anthropic-gebruik (lokale Claude Code JSONL-logs). Voor een eerlijke vergelijking 'hoeveel heb ik op elk platform gebruikt' zou ook MiniMax-verbruik gemeten moeten worden."*

## Decisions (locked)

- **Per-provider display, not unified.** Each provider surfaces its own labels (Anthropic: "5h rate" + "Weekly"; MiniMax: whatever its API exposes — possibly "Monthly quota", "RPM", etc.). The shared envelope is one row per period; the labels stay verbatim from the provider. Faking equivalence would be dishonest (Anthropic and MiniMax enforce different things under those names).
- **Anthropic: local + user-entered plan tier.** Anthropic does not publish a public usage/quota API for Pro/Max, so `AnthropicUsageProvider` combines the existing local `UsageService.identify_session_blocks()` for the 5h window with a new local weekly aggregator, against a user-selected plan tier (`pro` / `max_5x` / `max_20x` / `team`) that supplies the limit.
- **MiniMax: remote API.** `MinimaxUsageProvider` calls the MiniMax API. The exact endpoint(s) are unknown as of design time and will be discovered by a probe script in the first commit on the branch. If the probe finds no usable endpoint, the card ships with a truthful "MiniMax does not expose usage data" empty state and the gap is documented — no fabrication.
- **Plan tier in the UI, not the backend.** The Anthropic plan tier is picked from a `<Select>` on the Subscriptions page itself, not a `.env` setting. Discoverable; visible to the user; persisted in the SQLite DB.
- **5-minute in-memory cache, per provider.** Mirrors `UsageService.CACHE_TTL_MINUTES`. Invalidated on plan-tier change and on MiniMax key set/clear. No persistent cache, no DB row for usage data — Anthropic's is local, MiniMax's 5-min window is short enough that we just refetch.
- **No auto-refresh polling.** Card fetches on mount and on tab focus. Matches today's `UsagePage` behaviour.

## Approach

A new module `backend/app/services/subscriptions/`, mirroring the `services/providers/` shape. A generic `SubscriptionUsageProvider` abstract class. One concrete subclass per provider. The frontend renders any number of period rows from the snapshot — different labels per provider, same component.

### Alternatives considered (rejected)

- **Two ad-hoc service functions, no abstraction.** Rejected: when the third subscription arrives (Bedrock is the obvious next candidate per the analyse doc), three ad-hoc functions will have drifted in shape. The `AgentProvider`/`SpawnCommandOptions` pattern in `services/providers/` is the existing "small, existing-pattern-following extension" the analyse doc praises — same idea, new module.
- **Compute leftover purely from local data (no MiniMax API call).** Rejected: this is exactly the gap the analyse doc calls out — "MiniMax-verbruik moet ook gemeten worden". The user's request is to *show* what each subscription has left, which requires the MiniMax side to have a data source.
- **Refuse the word "leftover" and show only "used so far".** Rejected by user clarification: they specifically want to compare against the limit so they can decide where to route work.

## Components & data flow

### Backend — new module

```
backend/app/services/subscriptions/
├── __init__.py            # registry: get_usage_provider(provider_id) -> SubscriptionUsageProvider
├── base.py                # SubscriptionUsageProvider ABC, PeriodUsage + SubscriptionUsageSnapshot dataclasses, exceptions
├── minimax.py             # MinimaxUsageProvider (remote: MiniMax usage API)
└── anthropic.py           # AnthropicUsageProvider (local: UsageService + plan tier)

backend/app/services/subscriptions/
```

- **`base.py`** — no I/O. Defines:
  - `PeriodUsage` frozen dataclass: `label: str`, `used: float`, `limit: float | None`, `unit: str`, `reset_at: datetime | None`, `source: Literal["api", "local", "manual"]`, `note: str | None = None`.
  - `ErrorCode = Literal["not_configured", "unauthorized", "unreachable", "malformed", "no_endpoint", "plan_unknown"]` — a module-level type alias so both the dataclass and the Pydantic response can share it.
  - `SubscriptionUsageSnapshot` frozen dataclass: `provider: str`, `plan_label: str | None`, `periods: tuple[PeriodUsage, ...]`, `fetched_at: datetime`, `error: str | None = None`, `error_code: ErrorCode | None = None`.
  - `SubscriptionUsageProvider` abstract class with one method: `async def get_snapshot(self) -> SubscriptionUsageSnapshot`. Construction is provider-specific — the `AnthropicUsageProvider` takes a `db: AsyncSession` (mirrors how `UsageService` is constructed), the `MinimaxUsageProvider` takes an `httpx.AsyncClient` (or a sync `requests.Session` wrapped in `asyncio.to_thread`, matching how `provider.get_status()` already uses `asyncio.to_thread` in `providers.py:272`).

- **`minimax.py`** — calls the MiniMax usage/balance endpoint(s) found by the probe. Uses `settings.minimax_api_key` from `.env` (already in `Settings`). Catches: missing key → `not_configured`; 401 → `unauthorized`; 5xx/timeout/DNS → `unreachable`; JSON parse error → `malformed`. Maps whatever the API returns into `PeriodUsage` rows verbatim — no client-side reshaping beyond `label`/`used`/`limit`/`unit`/`reset_at`/`source` extraction.

- **`anthropic.py`** — purely local. Reads the Anthropic plan tier from the `subscription_prefs` DB row (see Schema below). If absent, returns `plan_unknown` with empty periods. Otherwise:
  - **5h rate period:** wraps `UsageService.identify_session_blocks(...)`, takes the most recent block, and computes `used` as the sum of `input_tokens + output_tokens + cache_creation_tokens + cache_read_tokens` for the block. `limit = ANTHROPIC_PLAN_LIMITS[tier]["5h_tokens"]` (the value Anthropic publishes for that tier's 5h window). `unit = "tokens"`. `reset_at = block.end_time`. `note = "Based on local JSONL; reflects usage, not Anthropic's server-side counter"`.
  - **Weekly period:** new `UsageService.aggregate_weekly(...)` (or a thin function on the same service) that returns the same token sum over the rolling 7-day window ending at `now`. `limit = ANTHROPIC_PLAN_LIMITS[tier]["weekly_tokens"]` which is `None` for all four tiers today (Anthropic does not publish Max weekly token limits). `unit = "tokens"`. `reset_at = (now + 7 days - weekday_offset) rounded to a Monday 00:00 UTC` so the user can predict when the window flips. When `limit is None`, the UI renders "Weekly: limit not published by Anthropic" instead of a progress bar.

- **Plan tier constants** (in `anthropic.py`):
  ```python
  ANTHROPIC_PLAN_LIMITS = {
      "pro":      {"5h_tokens": 44_000,   "weekly_tokens": None},
      "max_5x":   {"5h_tokens": 220_000,  "weekly_tokens": None},
      "max_20x":  {"5h_tokens": 880_000,  "weekly_tokens": None},
      "team":     {"5h_tokens": 880_000,  "weekly_tokens": None},
  }
  ```
  These constants are a **first-pass best estimate** based on the analyse-doc sister file `analyse-sessie-limieten-claude-code.md` (gitignored) and public Anthropic docs. The implementation step has a hard requirement: **commit 4 (Anthropic card) may not land until these numbers are re-verified against the current Anthropic plan docs**, with the verification source URL pasted into the commit message body. If any tier's number cannot be verified, that tier renders with `limit = None` (same as the weekly case) rather than a guessed number. The card UI shows a "limits may have changed — verify before trusting" small-print line when a plan is selected, so the user isn't quietly misled if Anthropic shifts a number.

### Backend — new endpoints

Registered in `backend/app/api/v1/agent_bridge/router.py` (next to the existing `minimax_credentials` routes). Always 200; errors are in the body.

```
GET  /api/v1/agent-bridge/subscriptions/{provider_id}/usage
  → SubscriptionUsageResponse  (Pydantic mirror of SubscriptionUsageSnapshot)

GET  /api/v1/agent-bridge/subscriptions/anthropic/plan-tier
  → { "tier": "pro" | "max_5x" | "max_20x" | "team" | null }

PUT  /api/v1/agent-bridge/subscriptions/anthropic/plan-tier
  body: { "tier": "..." }
  → 200 { "tier": "..." }
```

Caching: a tiny in-process dict `_snapshot_cache: dict[provider_id, tuple[fetched_at, SubscriptionUsageSnapshot]]` with a 5-minute TTL. Invalidated on `PUT /plan-tier` (for `anthropic`) and on `set_minimax_api_key` / `clear_minimax_api_key` (for `minimax`).

### Backend — new DB row

A `subscription_prefs` table (one row per `(provider_id, key)`), created via the existing `create_all` pattern — no migrations:

```python
class SubscriptionPref(Base):
    __tablename__ = "subscription_prefs"
    provider_id: Mapped[str]      # "anthropic", "minimax"
    key: Mapped[str]              # "plan_tier"
    value: Mapped[str]
    updated_at: Mapped[datetime]
    __table_args__ = (UniqueConstraint("provider_id", "key"),)
```

Only `anthropic.plan_tier` is set today; the table is shaped to accept future per-provider keys (e.g. a future `minimax.refresh_strategy`) without a schema change.

### Backend — Pydantic response shapes

```python
class PeriodUsageResponse(BaseModel):
    label: str
    used: float
    limit: float | None
    unit: str
    reset_at: datetime | None
    source: str
    note: str | None = None

class SubscriptionUsageResponse(BaseModel):
    provider: str
    plan_label: str | None
    periods: list[PeriodUsageResponse]
    fetched_at: datetime
    error: str | None = None
    error_code: Literal[
        "not_configured", "unauthorized", "unreachable",
        "malformed", "no_endpoint", "plan_unknown",
    ] | None = None
```

(`error_code` is a Pydantic `Literal[...]`, not a free `str`, so the frontend gets the exact six codes the dataclass defines and TypeScript can mirror them in a discriminated union — the dataclass and the wire type stay in lock-step.)

### Frontend — new files

All in `frontend/src/features/subscriptions/`. The existing folder is tiny today (one page + one card), so this is a contained change.

```
subscriptions/
├── SubscriptionsPage.tsx          # updated: renders one SubscriptionUsageCard per provider
├── MinimaxCredentialsCard.tsx     # unchanged; rendered as a child of SubscriptionUsageCard when key is unconfigured
├── AnthropicCredentialsCard.tsx   # NEW — bare plan-tier <Select>; no API key to store
├── SubscriptionUsageCard.tsx      # NEW — generic card chrome; renders periods + error states + plan picker
├── UsagePeriodRow.tsx             # NEW — one row: label, used/limit, progress bar, reset_at
├── api.ts                         # NEW: fetchSubscriptionUsage, getAnthropicPlanTier, setAnthropicPlanTier
└── types.ts                       # NEW: SubscriptionUsageResponse, PeriodUsageResponse, PlanTier union
```

#### `SubscriptionUsageCard` render rules

| Snapshot state | Renders |
|---|---|
| `error_code == "not_configured"` | `MinimaxCredentialsCard` above; usage area shows "Set your API key to see usage." |
| `error_code == "plan_unknown"` | `AnthropicCredentialsCard` `<Select>`; usage area hidden until tier picked |
| `error_code in {"unauthorized", "unreachable", "malformed", "no_endpoint"}` | Red badge with `error` text; periods list empty |
| `error is None` | One `<UsagePeriodRow>` per `periods` entry |
| `limit is None` on a period | Row shows "limit not published by Anthropic" instead of a progress bar |
| `fetched_at` older than 5 min (i.e. backend returned a cached snapshot instead of a fresh one) | A small "(refreshed N min ago)" footer line so the user knows the number is not live |

#### `AnthropicCredentialsCard` content

A single `<Select>` (`frontend/src/components/ui/select.tsx`):
- Options: `Pro`, `Max 5x`, `Max 20x`, `Team`.
- Below the select, a small muted line: *"These limits may have shifted since the last Anthropic plan change — verify at anthropic.com before trusting the percentages."*
- PUT on change. On success, refetch the snapshot.

#### `UsagePeriodRow` content

- Left: `label` ("5h rate", "Weekly", "Monthly quota", …).
- Center: when `limit` present, a `<Progress>` bar (existing `frontend/src/components/ui/progress.tsx`) with `value = (used / limit) * 100`. When `limit` absent, a muted "limit not published" line.
- Right: `used` formatted by `unit` — tokens via `formatTokens`, USD via `formatCost`, otherwise raw number. Below: `reset_at` formatted via `getRelativeTime` if present.

#### Refresh behaviour

- Fetch on mount (`useEffect`).
- Refetch on `visibilitychange` (tab focus). No `setInterval`. Matches today's `UsagePage` convention.

## Testing

Per-provider unit tests, mirroring the existing `test_platform_env.py` / `test_minimax_config.py` / `test_minimax_credentials.py` pattern:

```
backend/tests/
├── test_subscription_usage_minimax.py    # mock httpx; cover all 5 error codes + happy path
├── test_subscription_usage_anthropic.py # mock UsageService + plan tier DB read; cover 4 tiers + plan_unknown
├── test_subscription_usage_endpoint.py   # FastAPI test client; covers the 3 new endpoints + caching + invalidation
└── test_anthropic_plan_tier.py           # PUT/GET plan tier; cache invalidation
```

Frontend: a Vitest + Testing Library test (matches existing convention for components in this repo) covering the 4 error render paths and the happy path with N periods.

No live MiniMax API integration test in CI — the probe is local-only, gated on `MINIMAX_API_KEY` being set in the test environment.

## Rollout order inside the one PR

Five commits, shippable at any boundary:

1. **`chore(subscriptions): probe MiniMax API for usage/balance endpoints`** — `scripts/probe_minimax_usage.py`, prints whatever the API returns. Run once manually against the real API during implementation; paste the captured response into the commit message body as provenance. If nothing usable: commit the script anyway, then commit 2 references it and the `no_endpoint` error code.
2. **`feat(subscriptions): add SubscriptionUsageProvider abstraction + base dataclass`** — the registry, `base.py`, the Pydantic schemas, the new endpoints (returning `plan_unknown` / `no_endpoint` placeholder snapshots), the `subscription_prefs` table. No concrete providers yet — both endpoints return placeholder errors. The frontend renders placeholder error states. PR is shippable here, the cards just show "no data".
3. **`feat(subscriptions): add MinimaxUsageProvider`** — `minimax.py`, wired into the registry. The MiniMax card becomes live (real data or the truthful error from the probe step). Anthropic still shows `plan_unknown`.
4. **`feat(subscriptions): add AnthropicUsageProvider + plan-tier dropdown`** — `anthropic.py`, the `AnthropicCredentialsCard` `<Select>`, the cache invalidation on plan change. Both cards are live.
5. **`docs(subscriptions): usage card + honest limits gap`** — short doc in `docs/subscriptions/usage.md` (new dir) covering: what the card shows, why weekly is "not published" for Anthropic, what the MiniMax probe found (or didn't), and the "limits may have changed" note. PR description links to this doc.

## Out of scope

- Bedrock subscription card (the third subscription per the analyse doc). The abstraction is shaped to add it as a fifth commit in a follow-up PR, not a redesign.
- Auto-refresh polling.
- Persistent (DB-backed) usage cache.
- Per-project or per-card platform routing (the kanban-dispatch work tracked in `k-werk-limieten-6c29` and friends — separate cards).
- Any new endpoints for the kanban auto-dispatcher's quota-aware failover.
- Backwards-compat for the existing `frontend/src/features/providers/` route. Note: that route is being renamed to `/subscriptions` in commit `075b4c3` already; we continue from there, not from the older `/providers` route.

## Risks & honesty

- **Anthropic weekly limit is not published.** All four tiers get `weekly_tokens=None` in `ANTHROPIC_PLAN_LIMITS`. The card says so. We do not fabricate a number.
- **Anthropic published limits drift.** Pro and Max plan limits have changed historically. The constants in `ANTHROPIC_PLAN_LIMITS` are checked at implementation time against current Anthropic docs; the card surfaces a "verify before trusting" note anyway.
- **MiniMax API surface is unknown at design time.** The probe script is the literal first commit. If the probe finds nothing, the card ships empty with a clear message — better than shipping a fabricated UI.
- **Per-session plan selection vs. global plan selection.** The card is global (one plan tier for the whole Cockpit instance) — matches how MiniMax is a single key today. If we ever need per-project tiers, that's a separate redesign.
- **Caching across provider-version skew.** The 5-min cache is per `provider_id`, not per `tier`. Changing the Anthropic plan tier invalidates the cache. Changing the MiniMax key invalidates the cache. No other invalidation is needed.
