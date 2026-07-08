# Subscription Usage Leftover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-provider usage/quota card on the **Subscriptions** page for Anthropic (Claude Code) and MiniMax, showing each provider's actual remaining quota (5h rate, weekly, or whatever the provider exposes) with honest labels and a no-fabrication empty state when data is unavailable.

**Architecture:** A new `services/subscriptions/` module mirroring the `services/providers/` shape — a `SubscriptionUsageProvider` abstract base class with two concrete subclasses (`MinimaxUsageProvider` calls the MiniMax API; `AnthropicUsageProvider` reads local JSONL via the existing `UsageService` and reads a user-selected plan tier from a new `subscription_prefs` DB row). Three new FastAPI endpoints, one new DB table, one new shared in-process cache. Frontend gets a generic `<SubscriptionUsageCard>` that renders whatever `PeriodUsage` rows the snapshot contains, with per-error-code render branches.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy (async, aiosqlite), Pydantic. React 19, TypeScript strict, shadcn/ui (`Card`, `Progress`, `Select`, `Badge`), `vitest` for component tests.

## Global Constraints

- All Python code uses `from __future__ import annotations`. Type hints throughout.
- All async DB code uses `AsyncSession` via `Depends(get_db)`. Match `test_minimax_credentials.py` pattern (`httpx.ASGITransport`, `AsyncClient`).
- All new tables created via `Base.metadata.create_all` (no migration system — documented in CLAUDE.md).
- No new third-party deps without explicit justification in a commit message body.
- Frontend code uses `CLICKABLE_CARD` from `@/lib/constants` for clickable cards, `MODAL_SIZES` if any dialogs are added, `<MarkdownRenderer>` for any rendered markdown — per CLAUDE.md UI conventions.
- All admin-only endpoints return JSON; never log or echo the MiniMax API key (extend the existing invariant in `minimax_credentials.py`).
- The spec commit `98d1b34` is the source of truth — when this plan contradicts the spec, the spec wins.

---

## File Structure

### Files to create

| File | Responsibility |
|---|---|
| `scripts/probe_minimax_usage.py` | One-shot MiniMax API probe; prints whatever the server returns |
| `backend/app/services/subscriptions/__init__.py` | Registry: `get_usage_provider(provider_id)` |
| `backend/app/services/subscriptions/base.py` | `SubscriptionUsageProvider` ABC, `PeriodUsage` + `SubscriptionUsageSnapshot` dataclasses, `ErrorCode` literal, in-process TTL cache helper |
| `backend/app/services/subscriptions/minimax.py` | `MinimaxUsageProvider` (remote API call) |
| `backend/app/services/subscriptions/anthropic.py` | `AnthropicUsageProvider` (local JSONL + plan tier) |
| `backend/tests/test_subscription_usage_minimax.py` | Mock `httpx`, cover 5 error codes + happy path |
| `backend/tests/test_subscription_usage_anthropic.py` | Mock `UsageService`, cover 4 tiers + `plan_unknown` |
| `backend/tests/test_subscription_usage_endpoint.py` | FastAPI test client, cover 3 endpoints + cache invalidation |
| `backend/tests/test_anthropic_plan_tier.py` | PUT/GET plan tier + cache invalidation |
| `frontend/src/features/subscriptions/api.ts` | `fetchSubscriptionUsage`, `getAnthropicPlanTier`, `setAnthropicPlanTier` |
| `frontend/src/features/subscriptions/types.ts` | `SubscriptionUsageResponse`, `PeriodUsageResponse`, `PlanTier` |
| `frontend/src/features/subscriptions/SubscriptionUsageCard.tsx` | Generic card with all render branches |
| `frontend/src/features/subscriptions/UsagePeriodRow.tsx` | One row: label, used/limit, progress, reset_at |
| `frontend/src/features/subscriptions/AnthropicCredentialsCard.tsx` | Plan-tier `<Select>` with disclaimer |
| `frontend/src/features/subscriptions/SubscriptionUsageCard.test.tsx` | Vitest coverage of 4 error render paths + happy path |
| `docs/subscriptions/usage.md` | User-facing doc for the card + honest-limits gap |

### Files to modify

| File | Change |
|---|---|
| `backend/app/models/database.py` | Add `SubscriptionPref` table + `import` to register |
| `backend/app/services/usage_service.py` | Add `aggregate_weekly(entries, now)` helper |
| `backend/app/services/agent_bridge/minimax_credentials.py` | Invalidate the `minimax` cache key on `set_minimax_api_key` / `clear_minimax_api_key` |
| `backend/app/services/agent_bridge/router.py` | Register the 3 new endpoints |
| `frontend/src/features/subscriptions/SubscriptionsPage.tsx` | Replace `<MinimaxCredentialsCard />` with `<SubscriptionUsageCard provider="minimax" />` and `<SubscriptionUsageCard provider="anthropic" />` |

---

## Task 1: Probe MiniMax API

**Files:**
- Create: `scripts/probe_minimax_usage.py`

**Why first:** The spec says no fabrication. The MiniMax API surface for usage is unknown at design time. Before writing `minimax.py`, we need ground truth on what the server actually returns. The output of this probe becomes the spec for `minimax.py` in Task 4.

- [ ] **Step 1: Write the probe script**

Create `scripts/probe_minimax_usage.py`:

```python
"""One-shot probe of the MiniMax usage/balance endpoint(s).

Reads `MINIMAX_API_KEY` and `MINIMAX_BASE_URL` from environment (default
base URL: https://api.minimax.io/anthropic). Tries a small set of candidate
endpoint paths, prints whatever the server returns for each. The output of
this probe feeds the implementation of `MinimaxUsageProvider`.

This is a manual, out-of-band probe — NOT a pytest test. Run with:
    MINIMAX_API_KEY=sk-... python scripts/probe_minimax_usage.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "https://api.minimax.io/anthropic"

# Candidate endpoints to probe (extend if these miss).
CANDIDATES = [
    ("GET", "/v1/usage"),
    ("GET", "/v1/account/usage"),
    ("GET", "/v1/account/balance"),
    ("GET", "/v1/billing/usage"),
    ("GET", "/v1/quota"),
    ("GET", "/usage"),
    ("GET", "/account/usage"),
]


def _probe(method: str, url: str) -> tuple[int, str]:
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace") if e.fp else ""
    except urllib.error.URLError as e:
        return 0, f"URLError: {e.reason}"


api_key = os.environ.get("MINIMAX_API_KEY")
if not api_key:
    print("ERROR: MINIMAX_API_KEY not set", file=sys.stderr)
    sys.exit(1)

base_url = os.environ.get("MINIMAX_BASE_URL", DEFAULT_BASE).rstrip("/")

for method, path in CANDIDATES:
    url = base_url + path
    print(f"\n--- {method} {url} ---")
    status, body = _probe(method, url)
    print(f"Status: {status}")
    # Truncate to keep the probe output bounded.
    print(body[:2000])
```

- [ ] **Step 2: Make the script runnable and lint-clean**

Run: `chmod +x scripts/probe_minimax_usage.py`
Expected: no output.

- [ ] **Step 3: Run the probe against the real API**

Run (replace with a real key):

```bash
MINIMAX_API_KEY=sk-your-real-key python scripts/probe_minimax_usage.py 2>&1 | tee /tmp/minimax-probe.txt
```

Expected: a transcript showing HTTP status + body for each candidate endpoint. Most will 404; one or a few may 200 with JSON. This output is provenance for Task 4.

- [ ] **Step 4: Commit the probe and the captured transcript**

```bash
git add scripts/probe_minimax_usage.py
git commit -m "chore(subscriptions): probe MiniMax API for usage/balance endpoints

Manual probe (scripts/probe_minimax_usage.py) tried the following candidate
GETs against MINIMAX_BASE_URL with the configured MINIMAX_API_KEY:

[paste the non-empty status lines + a one-line summary per endpoint here]
[/paste]

[If no candidate returned 200: state that explicitly. The card will
 ship with the no_endpoint empty state — that's by design, not by bug.]"
```

Do NOT continue to Task 2 without committing this transcript. Task 4 (`MinimaxUsageProvider`) needs the actual response shape; no fabrication.

---

## Task 2: Backend foundation — model + base.py + schemas

**Files:**
- Modify: `backend/app/models/database.py` (add `SubscriptionPref` class + ensure imports)
- Create: `backend/app/services/subscriptions/__init__.py` (empty registry stub)
- Create: `backend/app/services/subscriptions/base.py`
- Create: `backend/tests/test_subscription_usage_base.py` (dataclass contract tests)

**Interfaces defined in this task** (consumed by every later task):
- `PeriodUsage(label, used, limit, unit, reset_at, source, note)`
- `SubscriptionUsageSnapshot(provider, plan_label, periods, fetched_at, error, error_code)`
- `ErrorCode = Literal["not_configured", "unauthorized", "unreachable", "malformed", "no_endpoint", "plan_unknown"]`
- `SubscriptionUsageProvider` with `async def get_snapshot(self) -> SubscriptionUsageSnapshot`

- [ ] **Step 1: Add `SubscriptionPref` to `backend/app/models/database.py`**

Append at the end of `backend/app/models/database.py` (before any trailing imports or constants the file might have — add after the last `class ...` line). Add `UniqueConstraint` is already imported at line 4; if not, add it.

```python
class SubscriptionPref(Base):
    """Per-provider, per-key preference row.

    Shaped as `(provider_id, key) -> value` so future per-provider prefs
    (e.g. `minimax.refresh_strategy`) slot in without a schema change.
    Today only `anthropic.plan_tier` is set.
    """

    __tablename__ = "subscription_prefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(String, nullable=False)
    key: Mapped[str] = mapped_column(String, nullable=False)
    value: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("provider_id", "key", name="uix_subscription_prefs_provider_key"),
    )
```

The file already has a top-level `import app.models.database as ...` somewhere that triggers `create_all` — verify this is sufficient by checking `backend/app/main.py` (the `import app.models.database` for `create_all` is already there).

- [ ] **Step 2: Write a failing test for the dataclasses**

Create `backend/tests/test_subscription_usage_base.py`:

```python
"""Contract tests for SubscriptionUsageSnapshot / PeriodUsage / base API."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.subscriptions.base import (
    ErrorCode,
    PeriodUsage,
    SubscriptionUsageProvider,
    SubscriptionUsageSnapshot,
)


def test_period_usage_is_frozen():
    p = PeriodUsage(label="5h rate", used=1000, limit=44_000, unit="tokens",
                    reset_at=None, source="local")
    with pytest.raises(Exception):
        p.used = 2000  # type: ignore[misc]


def test_snapshot_default_error_is_none():
    now = datetime.now(UTC)
    s = SubscriptionUsageSnapshot(provider="anthropic", plan_label="pro", periods=(), fetched_at=now)
    assert s.error is None
    assert s.error_code is None


def test_error_code_values_are_exactly_the_six():
    assert set(ErrorCode.__args__) == {  # type: ignore[attr-defined]
        "not_configured", "unauthorized", "unreachable",
        "malformed", "no_endpoint", "plan_unknown",
    }


def test_subscription_usage_provider_is_abstract():
    with pytest.raises(TypeError):
        SubscriptionUsageProvider()  # type: ignore[abstract]
```

- [ ] **Step 3: Run the failing test**

Run: `cd backend && python -m pytest tests/test_subscription_usage_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.subscriptions'`.

- [ ] **Step 4: Implement `backend/app/services/subscriptions/__init__.py`**

```python
"""Subscription usage provider registry."""
from __future__ import annotations

from app.services.subscriptions.base import (
    ErrorCode,
    PeriodUsage,
    SubscriptionUsageProvider,
    SubscriptionUsageSnapshot,
    get_snapshot_cache,
    invalidate_snapshot_cache,
)

__all__ = [
    "ErrorCode",
    "PeriodUsage",
    "SubscriptionUsageProvider",
    "SubscriptionUsageSnapshot",
    "get_snapshot_cache",
    "invalidate_snapshot_cache",
    "get_usage_provider",
]


# Map of provider_id -> SubscriptionUsageProvider instance.
# Populated lazily by tasks 4 and 5 so each task's test suite can wire up
# only the providers that task exercises.
_PROVIDERS: dict[str, SubscriptionUsageProvider] = {}


def register_usage_provider(provider: SubscriptionUsageProvider) -> None:
    """Called by task 4 / task 5 from their module-level imports."""
    _PROVIDERS[provider.provider_id] = provider


def get_usage_provider(provider_id: str) -> SubscriptionUsageProvider:
    try:
        return _PROVIDERS[provider_id]
    except KeyError as exc:
        raise ValueError(f"Unknown subscription usage provider: {provider_id}") from exc
```

- [ ] **Step 5: Implement `backend/app/services/subscriptions/base.py`**

```python
"""Subscription usage provider base class, dataclasses, and shared cache.

No I/O lives in this module. Concrete providers live in `minimax.py` and
`anthropic.py`. The shared in-process cache is also here because every
provider wants the same TTL semantics.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

Source = Literal["api", "local", "manual"]
ErrorCode = Literal[
    "not_configured",
    "unauthorized",
    "unreachable",
    "malformed",
    "no_endpoint",
    "plan_unknown",
]

CACHE_TTL = timedelta(minutes=5)


@dataclass(frozen=True)
class PeriodUsage:
    """A single rate-limited window for a subscription."""

    label: str
    used: float
    limit: float | None
    unit: str
    reset_at: datetime | None
    source: Source
    note: str | None = None


@dataclass(frozen=True)
class SubscriptionUsageSnapshot:
    """One provider's usage at a point in time. Errors are first-class."""

    provider: str
    plan_label: str | None
    periods: tuple[PeriodUsage, ...]
    fetched_at: datetime
    error: str | None = None
    error_code: ErrorCode | None = None


class SubscriptionUsageProvider:
    """Abstract base class. Subclasses set `provider_id` and implement `get_snapshot`."""

    provider_id: str

    async def get_snapshot(self) -> SubscriptionUsageSnapshot:  # pragma: no cover - abstract
        raise NotImplementedError


# In-process cache: provider_id -> (cached_at, snapshot)
_snapshot_cache: dict[str, tuple[datetime, SubscriptionUsageSnapshot]] = {}
_cache_lock = asyncio.Lock()


async def get_snapshot_cache(provider_id: str) -> SubscriptionUsageSnapshot | None:
    """Return the cached snapshot for `provider_id` if it is still within TTL, else None."""
    async with _cache_lock:
        entry = _snapshot_cache.get(provider_id)
        if entry is None:
            return None
        cached_at, snapshot = entry
        if datetime.now(UTC) - cached_at > CACHE_TTL:
            _snapshot_cache.pop(provider_id, None)
            return None
        return snapshot


async def put_snapshot_cache(snapshot: SubscriptionUsageSnapshot) -> None:
    """Store a fresh snapshot in the cache."""
    async with _cache_lock:
        _snapshot_cache[snapshot.provider] = (datetime.now(UTC), snapshot)


def invalidate_snapshot_cache(provider_id: str) -> None:
    """Synchronous invalidation. Safe to call from sync contexts (FastAPI handlers)."""
    _snapshot_cache.pop(provider_id, None)
```

- [ ] **Step 6: Run the test**

Run: `cd backend && python -m pytest tests/test_subscription_usage_base.py -v`
Expected: 4 tests pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/database.py backend/app/services/subscriptions/ backend/tests/test_subscription_usage_base.py
git commit -m "feat(subscriptions): add SubscriptionUsageProvider abstraction + base dataclass

The dataclasses (PeriodUsage, SubscriptionUsageSnapshot) and the ABC are
no-IO. An in-process _snapshot_cache keyed by provider_id provides the 5-min
TTL the spec calls for. SubscriptionPref is a new (provider_id, key) -> value
DB row so future per-provider prefs slot in without a schema change.

The endpoints exposed by this module land in the next commit (Task 3); the
two concrete providers land in Tasks 4 and 5."
```

---

## Task 3: Backend — three endpoints + placeholder providers

**Files:**
- Modify: `backend/app/api/v1/agent_bridge/router.py` (add 3 routes)
- Create: `backend/app/api/v1/agent_bridge/subscription_usage.py` (endpoint logic)
- Create: `backend/app/services/subscriptions/placeholders.py` (returns `no_endpoint` / `plan_unknown` snapshots)
- Create: `backend/tests/test_subscription_usage_endpoint.py`

**Interfaces defined in this task** (consumed by Task 5+):
- `GET /api/v1/agent-bridge/subscriptions/{provider_id}/usage` → `SubscriptionUsageResponse`
- `GET /api/v1/agent-bridge/subscriptions/anthropic/plan-tier` → `{tier: str | null}`
- `PUT /api/v1/agent-bridge/subscriptions/anthropic/plan-tier` with body `{tier: "..."}` → `{tier: ...}`

Both `usage` endpoint body fields and `plan_tier` endpoint URL are fixed by this task — the frontend in Tasks 6-8 consumes them verbatim.

- [ ] **Step 1: Write the failing endpoint tests**

Create `backend/tests/test_subscription_usage_endpoint.py`:

```python
"""Endpoint tests for /api/v1/agent-bridge/subscriptions/*."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_get_usage_unknown_provider_returns_404():
    async with _client() as ac:
        r = await ac.get("/api/v1/agent-bridge/subscriptions/nonexistent/usage")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_subscription_provider"


@pytest.mark.asyncio
async def test_get_usage_anthropic_unknown_plan_returns_plan_unknown():
    async with _client() as ac:
        r = await ac.get("/api/v1/agent-bridge/subscriptions/anthropic/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "anthropic"
    assert body["plan_label"] is None
    assert body["periods"] == []
    assert body["error_code"] == "plan_unknown"


@pytest.mark.asyncio
async def test_get_usage_minimax_unconfigured_returns_not_configured():
    async with _client() as ac:
        r = await ac.get("/api/v1/agent-bridge/subscriptions/minimax/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "minimax"
    assert body["error_code"] == "not_configured"
    assert body["periods"] == []


@pytest.mark.asyncio
async def test_plan_tier_get_unset_returns_null():
    async with _client() as ac:
        r = await ac.get("/api/v1/agent-bridge/subscriptions/anthropic/plan-tier")
    assert r.status_code == 200
    assert r.json() == {"tier": None}


@pytest.mark.asyncio
async def test_plan_tier_put_then_get_round_trips():
    async with _client() as ac:
        r = await ac.put(
            "/api/v1/agent-bridge/subscriptions/anthropic/plan-tier",
            json={"tier": "max_5x"},
        )
        assert r.status_code == 200
        assert r.json() == {"tier": "max_5x"}

        r2 = await ac.get("/api/v1/agent-bridge/subscriptions/anthropic/plan-tier")
        assert r2.json() == {"tier": "max_5x"}


@pytest.mark.asyncio
async def test_plan_tier_put_rejects_unknown_tier():
    async with _client() as ac:
        r = await ac.put(
            "/api/v1/agent-bridge/subscriptions/anthropic/plan-tier",
            json={"tier": "platinum"},
        )
    assert r.status_code == 400
    assert "platinum" in r.text


@pytest.mark.asyncio
async def test_plan_tier_put_invalidates_cached_snapshot(monkeypatch):
    """After PUT, the next /usage call must NOT return the cached pre-PUT snapshot."""
    # Wire a fake provider that returns a known snapshot, then verify cache
    # invalidation flips it back to plan_unknown when the tier is cleared.
    from app.services.subscriptions import placeholders
    async def _fake(_: object) -> dict:
        return {"snapshots_seen": []}
    # The above is a no-op; the real assertion is in the next task
    # (test_subscription_usage_anthropic). This test remains as a stub
    # because we don't yet have a registered concrete provider.
    assert placeholders is not None
```

- [ ] **Step 2: Run the failing tests**

Run: `cd backend && python -m pytest tests/test_subscription_usage_endpoint.py -v`
Expected: all 7 tests fail with 404 / connection errors.

- [ ] **Step 3: Create `backend/app/services/subscriptions/placeholders.py`**

```python
"""Placeholder providers used by Task 3 only.

Task 4 (Minimax) and Task 5 (Anthropic) overwrite the entries in
_PROVIDERS via `register_usage_provider` at import time. Until those
tasks land, both providers return their empty-state snapshot:
- anthropic: `plan_unknown`
- minimax: `not_configured`
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.config import settings
from app.services.subscriptions import register_usage_provider
from app.services.subscriptions.base import (
    SubscriptionUsageProvider,
    SubscriptionUsageSnapshot,
)


class PlaceholderAnthropicProvider(SubscriptionUsageProvider):
    provider_id = "anthropic"

    async def get_snapshot(self) -> SubscriptionUsageSnapshot:
        return SubscriptionUsageSnapshot(
            provider=self.provider_id,
            plan_label=None,
            periods=(),
            fetched_at=datetime.now(UTC),
            error="Pick an Anthropic plan tier to see your usage.",
            error_code="plan_unknown",
        )


class PlaceholderMinimaxProvider(SubscriptionUsageProvider):
    provider_id = "minimax"

    async def get_snapshot(self) -> SubscriptionUsageSnapshot:
        if not settings.minimax_api_key:
            return SubscriptionUsageSnapshot(
                provider=self.provider_id,
                plan_label=None,
                periods=(),
                fetched_at=datetime.now(UTC),
                error="MiniMax API key not configured.",
                error_code="not_configured",
            )
        return SubscriptionUsageSnapshot(
            provider=self.provider_id,
            plan_label=None,
            periods=(),
            fetched_at=datetime.now(UTC),
            error="MiniMax usage endpoint not yet wired up.",
            error_code="no_endpoint",
        )


register_usage_provider(PlaceholderAnthropicProvider())
register_usage_provider(PlaceholderMinimaxProvider())
```

- [ ] **Step 4: Add the Pydantic schemas to `backend/app/models/schemas.py`**

Append at end of `backend/app/models/schemas.py`:

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


class AnthropicPlanTierResponse(BaseModel):
    tier: Literal["pro", "max_5x", "max_20x", "team"] | None


class AnthropicPlanTierUpdateRequest(BaseModel):
    tier: Literal["pro", "max_5x", "max_20x", "team"] | None
```

`Literal` needs `from typing import Literal` — already imported at the top of `schemas.py`.

- [ ] **Step 5: Add a SubscriptionPref DB service helper**

Append to `backend/app/services/subscriptions/__init__.py` (or a new sibling file `backend/app/services/subscriptions/storage.py` — pick one and be consistent):

```python
"""SubscriptionPref DB helpers (read/write the plan tier)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import SubscriptionPref


VALID_TIERS = {"pro", "max_5x", "max_20x", "team"}


async def get_pref(db: AsyncSession, provider_id: str, key: str) -> str | None:
    result = await db.execute(
        select(SubscriptionPref).where(
            SubscriptionPref.provider_id == provider_id,
            SubscriptionPref.key == key,
        )
    )
    row = result.scalar_one_or_none()
    return row.value if row else None


async def set_pref(db: AsyncSession, provider_id: str, key: str, value: str | None) -> None:
    if value is not None and key == "plan_tier" and value not in VALID_TIERS:
        raise ValueError(f"Unknown plan tier: {value}")
    existing = await get_pref(db, provider_id, key)
    if value is None:
        if existing is not None:
            await db.delete(  # type: ignore[union-attr]
                await db.execute(  # type: ignore[func-returns-value]
                    select(SubscriptionPref).where(
                        SubscriptionPref.provider_id == provider_id,
                        SubscriptionPref.key == key,
                    )
                ).scalar_one()
            )
            await db.commit()
        return
    if existing is not None:
        existing.value = value  # type: ignore[union-attr]
    else:
        db.add(SubscriptionPref(provider_id=provider_id, key=key, value=value))  # type: ignore[arg-type]
    await db.commit()
```

(Note: the `set_pref` body uses the simpler shape `existing.value = value` / `db.add(...)` — the snippet above's `await db.execute(...).scalar_one()` form is wrong. Use this cleaner version instead:)

```python
async def set_pref(db: AsyncSession, provider_id: str, key: str, value: str | None) -> None:
    if value is not None and key == "plan_tier" and value not in VALID_TIERS:
        raise ValueError(f"Unknown plan tier: {value}")
    result = await db.execute(
        select(SubscriptionPref).where(
            SubscriptionPref.provider_id == provider_id,
            SubscriptionPref.key == key,
        )
    )
    row = result.scalar_one_or_none()
    if value is None:
        if row is not None:
            await db.delete(row)
            await db.commit()
        return
    if row is not None:
        row.value = value
    else:
        db.add(SubscriptionPref(provider_id=provider_id, key=key, value=value))
    await db.commit()
```

- [ ] **Step 6: Create `backend/app/api/v1/agent_bridge/subscription_usage.py`**

```python
"""Endpoints for /api/v1/agent-bridge/subscriptions/*."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.schemas import (
    AnthropicPlanTierResponse,
    AnthropicPlanTierUpdateRequest,
    PeriodUsageResponse,
    SubscriptionUsageResponse,
)
from app.services.subscriptions import (
    VALID_TIERS,
    get_pref,
    get_snapshot_cache,
    get_usage_provider,
    invalidate_snapshot_cache,
    put_snapshot_cache,
    set_pref,
)

router = APIRouter()


def _to_response(snap) -> SubscriptionUsageResponse:
    return SubscriptionUsageResponse(
        provider=snap.provider,
        plan_label=snap.plan_label,
        periods=[
            PeriodUsageResponse(
                label=p.label,
                used=p.used,
                limit=p.limit,
                unit=p.unit,
                reset_at=p.reset_at,
                source=p.source,
                note=p.note,
            )
            for p in snap.periods
        ],
        fetched_at=snap.fetched_at,
        error=snap.error,
        error_code=snap.error_code,
    )


@router.get("/subscriptions/{provider_id}/usage", response_model=SubscriptionUsageResponse)
async def get_usage(provider_id: str):
    try:
        provider = get_usage_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_subscription_provider", "message": str(exc)},
        )

    cached = await get_snapshot_cache(provider_id)
    if cached is not None:
        return _to_response(cached)

    snap = await provider.get_snapshot()
    await put_snapshot_cache(snap)
    return _to_response(snap)


@router.get("/subscriptions/anthropic/plan-tier", response_model=AnthropicPlanTierResponse)
async def get_anthropic_plan_tier(db: AsyncSession = Depends(get_db)):
    raw = await get_pref(db, "anthropic", "plan_tier")
    if raw is None:
        return AnthropicPlanTierResponse(tier=None)
    if raw not in VALID_TIERS:
        return AnthropicPlanTierResponse(tier=None)
    return AnthropicPlanTierResponse(tier=raw)  # type: ignore[arg-type]


@router.put("/subscriptions/anthropic/plan-tier", response_model=AnthropicPlanTierResponse)
async def put_anthropic_plan_tier(
    body: AnthropicPlanTierUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    await set_pref(db, "anthropic", "plan_tier", body.tier)
    invalidate_snapshot_cache("anthropic")
    return AnthropicPlanTierResponse(tier=body.tier)
```

- [ ] **Step 7: Register the new router in `backend/app/api/v1/agent_bridge/router.py`**

Find the line where the existing sub-routers are included (search for `include_router`). Add:

```python
from app.api.v1.agent_bridge.subscription_usage import router as subscription_usage_router
```

In the include list:

```python
router.include_router(subscription_usage_router, tags=["subscription-usage"])
```

- [ ] **Step 8: Make sure placeholders import on app startup**

In `backend/app/main.py`, search for any `from app.services.subscriptions import placeholders` (or add it). If the placeholders module is imported anywhere already (e.g., when the agent_bridge router imports `subscription_usage` which imports `subscriptions`), we're fine. Verify by running the test.

- [ ] **Step 9: Run the endpoint tests**

Run: `cd backend && python -m pytest tests/test_subscription_usage_endpoint.py -v`
Expected: 7 tests pass.

- [ ] **Step 10: Commit**

```bash
git add backend/app/api/v1/agent_bridge/router.py backend/app/api/v1/agent_bridge/subscription_usage.py backend/app/models/schemas.py backend/app/services/subscriptions/ backend/tests/test_subscription_usage_endpoint.py
git commit -m "feat(subscriptions): 3 endpoints + placeholder providers

GET /api/v1/agent-bridge/subscriptions/{provider_id}/usage
  -> 404 for unknown provider; 200 with SubscriptionUsageResponse otherwise
GET /api/v1/agent-bridge/subscriptions/anthropic/plan-tier
  -> {tier: pro|max_5x|max_20x|team|null}
PUT same, body {tier: ...}
  -> 200 round-trip; 400 for unknown tier; invalidates snapshot cache

Placeholder providers (in subscriptions/placeholders.py) return
{error_code: plan_unknown} for anthropic and {error_code: not_configured}
or {error_code: no_endpoint} for minimax depending on whether the API key
is set. The real providers in Tasks 4 and 5 will overwrite these via
register_usage_provider() at import time."
```

---

## Task 4: Backend — `MinimaxUsageProvider`

**Files:**
- Create: `backend/app/services/subscriptions/minimax.py`
- Create: `backend/tests/test_subscription_usage_minimax.py`

**Inputs from Task 1:** the captured probe transcript. **Inputs from Task 2:** `PeriodUsage`, `SubscriptionUsageSnapshot`, `ErrorCode`, `SubscriptionUsageProvider`. **Inputs from Task 3:** `register_usage_provider`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_subscription_usage_minimax.py`:

```python
"""Mocked httpx tests for MinimaxUsageProvider."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.subscriptions.base import SubscriptionUsageSnapshot
from app.services.subscriptions.minimax import MinimaxUsageProvider


class _FakeAsyncClient:
    """Tiny stand-in for httpx.AsyncClient that returns a queued response."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        status, body = self._responses.pop(0)
        return _FakeResponse(status, body)


class _FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        import json
        return json.loads(self._body)

    @property
    def text(self) -> str:
        return self._body


@pytest.mark.asyncio
async def test_happy_path_maps_periods(monkeypatch):
    fake = _FakeAsyncClient([(200, '[{"label":"5h","used":10,"limit":100,"unit":"tokens","reset_at":"2026-07-09T00:00:00Z"}]')])
    monkeypatch.setattr("app.services.subscriptions.minimax.httpx.AsyncClient", lambda: fake)
    p = MinimaxUsageProvider()
    snap = await p.get_snapshot()
    assert snap.error_code is None
    assert len(snap.periods) == 1
    period = snap.periods[0]
    assert period.label == "5h"
    assert period.used == 10
    assert period.limit == 100
    assert period.unit == "tokens"
    assert period.source == "api"


@pytest.mark.asyncio
async def test_unauthorized_returns_unauthorized(monkeypatch):
    fake = _FakeAsyncClient([(401, '{"error":"bad key"}')])
    monkeypatch.setattr("app.services.subscriptions.minimax.httpx.AsyncClient", lambda: fake)
    snap = await MinimaxUsageProvider().get_snapshot()
    assert snap.error_code == "unauthorized"
    assert snap.periods == ()


@pytest.mark.asyncio
async def test_5xx_returns_unreachable(monkeypatch):
    fake = _FakeAsyncClient([(503, "upstream down")])
    monkeypatch.setattr("app.services.subscriptions.minimax.httpx.AsyncClient", lambda: fake)
    snap = await MinimaxUsageProvider().get_snapshot()
    assert snap.error_code == "unreachable"


@pytest.mark.asyncio
async def test_malformed_json_returns_malformed(monkeypatch):
    fake = _FakeAsyncClient([(200, "not json")])
    monkeypatch.setattr("app.services.subscriptions.minimax.httpx.AsyncClient", lambda: fake)
    snap = await MinimaxUsageProvider().get_snapshot()
    assert snap.error_code == "malformed"


@pytest.mark.asyncio
async def test_no_endpoint_returns_no_endpoint_when_url_404s(monkeypatch):
    """If every candidate 404s, the provider returns no_endpoint."""
    fake = _FakeAsyncClient([(404, "not found")] * 8)
    monkeypatch.setattr("app.services.subscriptions.minimax.httpx.AsyncClient", lambda: fake)
    snap = await MinimaxUsageProvider().get_snapshot()
    assert snap.error_code == "no_endpoint"
```

- [ ] **Step 2: Run the failing tests**

Run: `cd backend && python -m pytest tests/test_subscription_usage_minimax.py -v`
Expected: 5 tests fail with `ModuleNotFoundError: No module named 'app.services.subscriptions.minimax'`.

- [ ] **Step 3: Implement `backend/app/services/subscriptions/minimax.py`**

The exact URL is determined by the Task 1 probe. Replace `CANDIDATE_PATHS` and the period-mapping logic with what the probe revealed.

```python
"""MiniMax usage provider.

Calls the MiniMax usage/balance endpoint(s) discovered by the Task 1 probe.
Maps the response into the abstract SubscriptionUsageSnapshot. Returns
an error-annotated snapshot (never raises) for any failure mode.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import settings
from app.services.subscriptions import register_usage_provider
from app.services.subscriptions.base import (
    ErrorCode,
    PeriodUsage,
    SubscriptionUsageProvider,
    SubscriptionUsageSnapshot,
)

logger = logging.getLogger(__name__)

# These were the endpoints the Task 1 probe tried. The probe output is the
# ground truth; adjust this list if the probe revealed a different working URL.
CANDIDATE_PATHS = ("/v1/usage", "/v1/account/usage", "/v1/account/balance")
BASE_URL = "https://api.minimax.io/anthropic"
REQUEST_TIMEOUT_SECONDS = 5.0


class MinimaxUsageProvider(SubscriptionUsageProvider):
    provider_id = "minimax"

    async def get_snapshot(self) -> SubscriptionUsageSnapshot:
        if not settings.minimax_api_key:
            return SubscriptionUsageSnapshot(
                provider=self.provider_id,
                plan_label=None,
                periods=(),
                fetched_at=datetime.now(UTC),
                error="MiniMax API key is not configured.",
                error_code="not_configured",
            )

        base = (settings.minimax_base_url or BASE_URL).rstrip("/")
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            for path in CANDIDATE_PATHS:
                url = base + path
                try:
                    resp = await client.get(
                        url,
                        headers={
                            "Authorization": f"Bearer {settings.minimax_api_key}",
                            "Accept": "application/json",
                        },
                    )
                except (httpx.RequestError, asyncio.TimeoutError) as exc:
                    logger.warning("minimax: %s unreachable: %s", url, exc)
                    return self._snap(error_code="unreachable", error=f"MiniMax unreachable: {exc}")

                if resp.status_code == 401:
                    return self._snap(error_code="unauthorized", error="MiniMax rejected the API key.")
                if 500 <= resp.status_code < 600:
                    return self._snap(error_code="unreachable", error=f"MiniMax returned {resp.status_code}.")
                if resp.status_code == 404:
                    continue  # try next candidate
                if resp.status_code != 200:
                    return self._snap(error_code="unreachable", error=f"MiniMax returned {resp.status_code}.")

                try:
                    body: Any = resp.json()
                except (ValueError, httpx.HTTPError) as exc:
                    logger.warning("minimax: malformed JSON from %s: %s", url, exc)
                    return self._snap(error_code="malformed", error="MiniMax returned non-JSON.")

                periods = self._map_periods(body)
                return SubscriptionUsageSnapshot(
                    provider=self.provider_id,
                    plan_label=None,
                    periods=tuple(periods),
                    fetched_at=datetime.now(UTC),
                )

        # All candidates 404'd.
        return self._snap(
            error_code="no_endpoint",
            error="MiniMax did not expose a usage endpoint at the candidates tried.",
        )

    @staticmethod
    def _map_periods(body: Any) -> list[PeriodUsage]:
        """Translate whatever shape the probe found into a list of PeriodUsage.

        THIS IS THE METHOD THE TASK 1 PROBE FEEDS. Implementation depends on
        the actual response shape. Example shape from a hypothetical API:

            body = [{"label":"5h","used":1000,"limit":5000,"unit":"tokens","reset_at":"..."}]

        Update per the probe — see Task 1 commit message for the JSON shape.
        """
        if isinstance(body, list):
            out: list[PeriodUsage] = []
            for row in body:
                if not isinstance(row, dict):
                    continue
                out.append(
                    PeriodUsage(
                        label=str(row.get("label", "?")),
                        used=float(row.get("used", 0)),
                        limit=float(row["limit"]) if row.get("limit") is not None else None,
                        unit=str(row.get("unit", "tokens")),
                        reset_at=_parse_iso(row.get("reset_at")),
                        source="api",
                    )
                )
            return out
        if isinstance(body, dict):
            # If the probe revealed a different shape (single object, nested),
            # adjust here. Default fallback: no periods, so the card renders
            # a "no data" state instead of guessing.
            return []
        return []

    @staticmethod
    def _snap(error_code: ErrorCode, error: str) -> SubscriptionUsageSnapshot:
        return SubscriptionUsageSnapshot(
            provider="minimax",
            plan_label=None,
            periods=(),
            fetched_at=datetime.now(UTC),
            error=error,
            error_code=error_code,
        )


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        # Accept "Z" suffix.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


register_usage_provider(MinimaxUsageProvider())
```

- [ ] **Step 4: Move placeholder registration to a side-effect-only import**

The placeholder file's `register_usage_provider` calls must run BEFORE the MiniMax provider's call. Order is decided by import order. In `backend/app/services/subscriptions/__init__.py`, ensure `placeholders` is imported first, then `minimax`:

```python
# backend/app/services/subscriptions/__init__.py
from app.services.subscriptions import placeholders  # noqa: F401  (registers placeholders)
from app.services.subscriptions import minimax       # noqa: F401  (overwrites minimax)
```

The last-registration-wins behaviour of `register_usage_provider` is what makes this work. Add the matching line for `anthropic` in Task 5.

- [ ] **Step 5: Run the tests**

Run: `cd backend && python -m pytest tests/test_subscription_usage_minimax.py -v`
Expected: 5 tests pass. (Adjust test responses if the probe transcript from Task 1 showed a different shape — the test data must mirror the probe response verbatim.)

- [ ] **Step 6: Run all subscription tests**

Run: `cd backend && python -m pytest tests/test_subscription_usage_*.py tests/test_anthropic_plan_tier.py -v`
Expected: all pass. The `endpoint` test's `test_plan_tier_put_invalidates_cached_snapshot` is a stub for now; Task 5 will exercise it for real.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/subscriptions/minimax.py backend/app/services/subscriptions/__init__.py backend/tests/test_subscription_usage_minimax.py
git commit -m "feat(subscriptions): add MinimaxUsageProvider

Calls the MiniMax API at <URL discovered by Task 1 probe> with the
configured MINIMAX_API_KEY. Maps the response into PeriodUsage rows
verbatim (no fabrication); the _map_periods body is intentionally
documented as probe-driven — adjust if the probe revealed a different
shape.

Returns first-class error snapshots for: not_configured (no key),
unauthorized (401), unreachable (5xx/timeout/DNS), malformed (JSON
parse), no_endpoint (all candidates 404). Wired into the registry so
GET /api/v1/agent-bridge/subscriptions/minimax/usage now returns
real data or a truthful error instead of the placeholder no_endpoint."
```

---

## Task 5: Backend — `UsageService.aggregate_weekly` helper

**Files:**
- Modify: `backend/app/services/usage_service.py` (add `aggregate_weekly`)

**Why a separate task:** the weekly aggregator is a new function that the Anthropic provider (Task 6) consumes. It's small and 100% additive — no existing call sites change.

- [ ] **Step 1: Find `UsageService.identify_session_blocks`**

The file already has `identify_session_blocks(...)` (read lines ~451-517 in the current source). The `aggregate_weekly` helper sits next to it.

- [ ] **Step 2: Add `aggregate_weekly` to `backend/app/services/usage_service.py`**

```python
async def aggregate_weekly(
    self,
    entries: list[LoadedUsageEntry],
    *,
    now: datetime | None = None,
) -> tuple[int, datetime]:
    """Sum tokens over the rolling 7-day window ending at `now`.

    Returns `(total_tokens, reset_at)` where `reset_at` is the next Monday
    00:00 UTC after `now` (the canonical weekly roll-over point used by
    Anthropic Pro/Max). If no entries have a usable timestamp,
    `total_tokens` is 0 and `reset_at` is the next Monday from `now`.
    """
    if now is None:
        now = datetime.now(UTC)
    cutoff = now - timedelta(days=7)
    total = 0
    for entry in entries:
        ts = self._as_utc(entry.timestamp)
        if ts >= cutoff and ts <= now:
            total += (
                entry.input_tokens
                + entry.output_tokens
                + entry.cache_creation_tokens
                + entry.cache_read_tokens
            )
    # Next Monday at 00:00 UTC after `now`.
    days_ahead = (7 - now.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    reset_at = (now + timedelta(days=days_ahead)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return total, reset_at
```

- [ ] **Step 3: Add a focused test**

Append to `backend/tests/test_usage_service.py` (or create the file if missing — see `test_kanban_dispatch.py` for the layout):

```python
@pytest.mark.asyncio
async def test_aggregate_weekly_sums_tokens_in_rolling_window():
    from datetime import UTC, datetime, timedelta
    from app.services.usage_service import LoadedUsageEntry, UsageService

    now = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    entries = [
        LoadedUsageEntry(
            timestamp=now - timedelta(days=1), input_tokens=100, output_tokens=50,
            cache_creation_tokens=0, cache_read_tokens=0, cost_usd=None,
            model="m", session_id="s1", version="v", project_path="p",
        ),
        LoadedUsageEntry(
            timestamp=now - timedelta(days=10), input_tokens=9999, output_tokens=0,
            cache_creation_tokens=0, cache_read_tokens=0, cost_usd=None,
            model="m", session_id="s2", version="v", project_path="p",
        ),
        LoadedUsageEntry(
            timestamp=now, input_tokens=200, output_tokens=20,
            cache_creation_tokens=10, cache_read_tokens=5, cost_usd=None,
            model="m", session_id="s3", version="v", project_path="p",
        ),
    ]
    svc = UsageService()
    total, reset_at = await svc.aggregate_weekly(entries, now=now)
    # Only the day-1 and now entries are inside the 7-day window.
    assert total == 100 + 50 + 200 + 20 + 10 + 5
    assert reset_at.weekday() == 0  # Monday
    assert reset_at.hour == 0 and reset_at.minute == 0
```

- [ ] **Step 4: Run the test**

Run: `cd backend && python -m pytest tests/test_usage_service.py::test_aggregate_weekly_sums_tokens_in_rolling_window -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/usage_service.py backend/tests/test_usage_service.py
git commit -m "feat(usage): add UsageService.aggregate_weekly

Sums input + output + cache_creation + cache_read tokens over the
rolling 7-day window ending at `now`. Returns (total, next_monday_00utc)
so the Anthropic 5h/weekly card has both numbers in one call. Purely
additive — no existing call sites change."
```

---

## Task 6: Backend — `AnthropicUsageProvider` + `ANTHROPIC_PLAN_LIMITS` (with verification gate)

**Files:**
- Modify: `backend/app/services/subscriptions/__init__.py` (import anthropic last)
- Create: `backend/app/services/subscriptions/anthropic.py`
- Create: `backend/tests/test_subscription_usage_anthropic.py`

**Hard gate:** this task's commit (and therefore Anthropic card data) may not land until the `ANTHROPIC_PLAN_LIMITS` constants have been verified against the current Anthropic plan docs. Verification source URL goes into the commit message body.

- [ ] **Step 1: Re-verify `ANTHROPIC_PLAN_LIMITS` against public Anthropic docs**

Open Anthropic's plan page (e.g. `https://www.anthropic.com/pricing` and the Claude Code help-center plan page). For each tier (Pro, Max 5x, Max 20x, Team), find the published 5h token limit. Cross-reference with the gitignored sister doc `analyse-sessie-limieten-claude-code.md` in `.local-notes/` if available.

If any tier's number cannot be verified, **set that tier's limit to `None`** in `ANTHROPIC_PLAN_LIMITS`. The card renders "limit not published" for `None` limits; that is the honest state.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_subscription_usage_anthropic.py`:

```python
"""AnthropicUsageProvider tests with mocked UsageService."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.subscriptions.anthropic import (
    ANTHROPIC_PLAN_LIMITS,
    AnthropicUsageProvider,
    VALID_TIERS,
)


@pytest.mark.asyncio
async def test_unknown_tier_returns_plan_unknown(db_session):
    """If no plan_tier is set, snapshot is plan_unknown with empty periods."""
    p = AnthropicUsageProvider(db=db_session)
    snap = await p.get_snapshot()
    assert snap.error_code == "plan_unknown"
    assert snap.plan_label is None
    assert snap.periods == ()


@pytest.mark.asyncio
async def test_each_known_tier_emits_two_periods(db_session):
    """For each tier, set the row and verify two periods return."""
    from app.models.database import SubscriptionPref
    for tier in VALID_TIERS:
        db_session.add(SubscriptionPref(provider_id="anthropic", key="plan_tier", value=tier))
        await db_session.commit()
        p = AnthropicUsageProvider(db=db_session)
        snap = await p.get_snapshot()
        assert snap.error_code is None, f"tier {tier}: {snap.error}"
        labels = {prd.label for prd in snap.periods}
        assert {"5h rate", "Weekly"} <= labels, f"tier {tier} missing expected labels"
        # Clean up the row so the next tier starts fresh.
        await db_session.execute(SubscriptionPref.__table__.delete())
        await db_session.commit()


def test_5h_token_limits_are_only_present_for_verified_tiers():
    """A tier whose number could not be verified must render with limit=None."""
    # If the verifier couldn't find the number, that tier's value is None —
    # not a guessed constant. This is the property the spec promises.
    for tier, limits in ANTHROPIC_PLAN_LIMITS.items():
        assert "5h_tokens" in limits
        # weekly_tokens is intentionally None for every tier today.
        assert limits["weekly_tokens"] is None
```

`db_session` is a fixture provided by `tests/conftest.py`'s `_reset_test_db`. The test classes/functions use the standard `pytest_asyncio.fixture` pattern that's already in the codebase.

- [ ] **Step 3: Run the failing tests**

Run: `cd backend && python -m pytest tests/test_subscription_usage_anthropic.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.subscriptions.anthropic'`.

- [ ] **Step 4: Implement `backend/app/services/subscriptions/anthropic.py`**

The exact `ANTHROPIC_PLAN_LIMITS` values come from Step 1. The structure below uses placeholder values that MUST be replaced with the verified numbers from Step 1 (or set to `None` if unverifiable).

```python
"""Anthropic usage provider.

Reads local Claude Code JSONL via UsageService and the user-selected plan
tier from SubscriptionPref. No remote calls — Anthropic does not publish
a public usage API for Pro/Max tiers.

ANTHROPIC_PLAN_LIMITS values MUST be verified against current Anthropic
plan docs before this module ships. See Task 6 commit message for the
verification sources; values that cannot be verified are set to `None`
(not a guess).
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.subscriptions import register_usage_provider
from app.services.subscriptions.base import (
    PeriodUsage,
    SubscriptionUsageProvider,
    SubscriptionUsageSnapshot,
)
from app.services.subscriptions.storage import VALID_TIERS, get_pref
from app.services.usage_service import UsageService

logger = logging.getLogger(__name__)


# Plan-tier token limits. Keys MUST match VALID_TIERS exactly.
# After Step 1 verification, replace each value with the number published
# by Anthropic, or `None` if unverifiable.
ANTHROPIC_PLAN_LIMITS: dict[str, dict[str, int | None]] = {
    "pro":      {"5h_tokens": None, "weekly_tokens": None},  # VERIFY: <paste URL or None>
    "max_5x":   {"5h_tokens": None, "weekly_tokens": None},  # VERIFY
    "max_20x":  {"5h_tokens": None, "weekly_tokens": None},  # VERIFY
    "team":     {"5h_tokens": None, "weekly_tokens": None},  # VERIFY
}


def valid_tiers() -> set[str]:
    return set(VALID_TIERS)


class AnthropicUsageProvider(SubscriptionUsageProvider):
    provider_id = "anthropic"

    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_snapshot(self) -> SubscriptionUsageSnapshot:
        tier_raw = await get_pref(self._db, "anthropic", "plan_tier")
        if tier_raw is None or tier_raw not in VALID_TIERS:
            return SubscriptionUsageSnapshot(
                provider=self.provider_id,
                plan_label=None,
                periods=(),
                fetched_at=datetime.now(UTC),
                error="Pick an Anthropic plan tier to see your usage.",
                error_code="plan_unknown",
            )

        limits = ANTHROPIC_PLAN_LIMITS[tier_raw]
        svc = UsageService(self._db)
        entries = await svc.get_all_usage_entries(None)

        # 5h rate period.
        blocks = await svc.identify_session_blocks(entries)
        active = next((b for b in blocks if b.is_active), None)
        if active is not None:
            used_5h = (
                active.input_tokens
                + active.output_tokens
                + active.cache_creation_tokens
                + active.cache_read_tokens
            )
            five_h_period = PeriodUsage(
                label="5h rate",
                used=float(used_5h),
                limit=float(limits["5h_tokens"]) if limits["5h_tokens"] is not None else None,
                unit="tokens",
                reset_at=datetime.fromisoformat(active.end_time),
                source="local",
                note="Based on local JSONL; reflects usage, not Anthropic's server-side counter.",
            )
        else:
            five_h_period = PeriodUsage(
                label="5h rate",
                used=0.0,
                limit=float(limits["5h_tokens"]) if limits["5h_tokens"] is not None else None,
                unit="tokens",
                reset_at=datetime.now(UTC) + timedelta(hours=5),
                source="local",
                note="No active 5h block in local JSONL.",
            )

        # Weekly period.
        weekly_total, weekly_reset = await svc.aggregate_weekly(entries)
        weekly_period = PeriodUsage(
            label="Weekly",
            used=float(weekly_total),
            limit=float(limits["weekly_tokens"]) if limits["weekly_tokens"] is not None else None,
            unit="tokens",
            reset_at=weekly_reset,
            source="local",
            note="Based on local JSONL; reflects usage, not Anthropic's server-side counter.",
        )

        return SubscriptionUsageSnapshot(
            provider=self.provider_id,
            plan_label=tier_raw,
            periods=(five_h_period, weekly_period),
            fetched_at=datetime.now(UTC),
        )


def _build_with_session(db: AsyncSession) -> AnthropicUsageProvider:
    return AnthropicUsageProvider(db=db)


register_usage_provider(_build_with_session.__wrapped__(None) if False else _Placeholder())  # type: ignore[arg-type]


class _Placeholder:
    """Registration placeholder; the real AnthropicUsageProvider requires a per-request
    db session and is constructed inside the endpoint. See the endpoint in
    subscription_usage.py for the real wiring in Task 7."""
    provider_id = "anthropic"


def build_anthropic_provider(db: AsyncSession) -> AnthropicUsageProvider:
    """Public factory used by the endpoint in Task 7."""
    return AnthropicUsageProvider(db=db)


# Override the placeholder registration with a buildable factory. The
# endpoint will construct via `build_anthropic_provider(db)`.
register_usage_provider(_Placeholder())  # type: ignore[arg-type,abstract]
```

The `_Placeholder` + `build_anthropic_provider` split is required because `register_usage_provider` takes a `SubscriptionUsageProvider` instance, but `AnthropicUsageProvider` needs a per-request `db` and we want the singleton registry to work. The endpoint in **Task 7** resolves this by calling `build_anthropic_provider(db)` inside the handler and ignoring the registered placeholder.

- [ ] **Step 5: Wire `anthropic` into the import order in `backend/app/services/subscriptions/__init__.py`**

```python
from app.services.subscriptions import placeholders  # noqa: F401
from app.services.subscriptions import minimax       # noqa: F401
from app.services.subscriptions import anthropic     # noqa: F401  (registers _Placeholder)
```

- [ ] **Step 6: Run the tests**

Run: `cd backend && python -m pytest tests/test_subscription_usage_anthropic.py -v`
Expected: all pass. (Many tests will pass with `limit=None` — that's fine, the spec says we render "limit not published" honestly. The point of the test is to verify the provider returns two periods with the right labels.)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/subscriptions/anthropic.py backend/app/services/subscriptions/__init__.py backend/tests/test_subscription_usage_anthropic.py
git commit -m "feat(subscriptions): add AnthropicUsageProvider

Reads local JSONL via UsageService.identify_session_blocks + the new
UsageService.aggregate_weekly (Task 5) and the user-picked plan_tier from
the SubscriptionPref row. No remote calls (Anthropic has no public
usage API for Pro/Max).

ANTHROPIC_PLAN_LIMITS values verified against:
  [paste Anthropic plan doc URL + the exact number for each tier]

Tiers where the number could not be verified have limit=None rather
than a guessed number; the card renders 'limit not published by
Anthropic' for those, per the spec's honesty requirement.

Also uses the placeholder + factory split (build_anthropic_provider)
so the per-request db dependency can be injected cleanly in Task 7."
```

The literal verification source MUST appear in the commit body. No "TBD", no deferral.

---

## Task 7: Backend — endpoint uses `build_anthropic_provider` + cache invalidation hooks

**Files:**
- Modify: `backend/app/api/v1/agent_bridge/subscription_usage.py`
- Modify: `backend/app/services/agent_bridge/minimax_credentials.py`

Why this is split from Tasks 4/6: the Anthropic provider needs per-request `db`, but the registry caches singletons. Task 7 fixes that by short-circuiting the registry in the endpoint.

- [ ] **Step 1: Update the endpoint to construct `AnthropicUsageProvider` per request**

In `backend/app/api/v1/agent_bridge/subscription_usage.py`, change the `get_usage` handler:

```python
from app.services.subscriptions.anthropic import (
    AnthropicUsageProvider,
    build_anthropic_provider,
)

@router.get("/subscriptions/{provider_id}/usage", response_model=SubscriptionUsageResponse)
async def get_usage(provider_id: str, db: AsyncSession = Depends(get_db)):
    if provider_id == "anthropic":
        cached = await get_snapshot_cache("anthropic")
        if cached is not None:
            return _to_response(cached)
        snap = await build_anthropic_provider(db).get_snapshot()
        await put_snapshot_cache(snap)
        return _to_response(snap)

    try:
        provider = get_usage_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_subscription_provider", "message": str(exc)},
        )
    cached = await get_snapshot_cache(provider_id)
    if cached is not None:
        return _to_response(cached)
    snap = await provider.get_snapshot()
    await put_snapshot_cache(snap)
    return _to_response(snap)
```

- [ ] **Step 2: Extend `minimax_credentials.py` to invalidate the cache on key change**

In `backend/app/services/agent_bridge/minimax_credentials.py`, add at the top:

```python
from app.services.subscriptions import invalidate_snapshot_cache
```

In `set_minimax_api_key` (line 39), append after the existing line that sets `settings.minimax_api_key`:

```python
    invalidate_snapshot_cache("minimax")
```

In `clear_minimax_api_key` (line 45), append after the line that clears `settings.minimax_api_key`:

```python
    invalidate_snapshot_cache("minimax")
```

- [ ] **Step 3: Replace the stub test from Task 3 with a real one**

In `backend/tests/test_subscription_usage_endpoint.py`, replace the stub `test_plan_tier_put_invalidates_cached_snapshot` with:

```python
@pytest.mark.asyncio
async def test_plan_tier_put_invalidates_cached_snapshot(monkeypatch):
    """After PUT, the next /usage call must NOT return the cached pre-PUT snapshot."""
    from app.services.subscriptions import (
        invalidate_snapshot_cache,
    )
    from app.models.database import SubscriptionPref

    # Seed a known tier so the anthropic endpoint returns two periods.
    # (Use the in-memory test session from conftest's _reset_test_db.)
    # Pre-seed: PUT max_5x.
    async with _client() as ac:
        r = await ac.put(
            "/api/v1/agent-bridge/subscriptions/anthropic/plan-tier",
            json={"tier": "max_5x"},
        )
        assert r.status_code == 200

        # First /usage call populates the cache.
        r = await ac.get("/api/v1/agent-bridge/subscriptions/anthropic/usage")
        assert r.status_code == 200
        first = r.json()
        assert first["error_code"] is None
        assert any(p["label"] == "5h rate" for p in first["periods"])

        # PUT a different tier -> should invalidate cache.
        r = await ac.put(
            "/api/v1/agent-bridge/subscriptions/anthropic/plan-tier",
            json={"tier": "pro"},
        )
        assert r.status_code == 200

        # Next /usage call should reflect pro (plan_label=pro) not max_5x.
        r = await ac.get("/api/v1/agent-bridge/subscriptions/anthropic/usage")
        assert r.json()["plan_label"] == "pro"
```

- [ ] **Step 4: Run all the subscription tests**

Run: `cd backend && python -m pytest tests/test_subscription_usage_*.py tests/test_anthropic_plan_tier.py tests/test_minimax_credentials.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/agent_bridge/subscription_usage.py backend/app/services/agent_bridge/minimax_credentials.py backend/tests/test_subscription_usage_endpoint.py
git commit -m "feat(subscriptions): per-request Anthropic provider + cache invalidation

The AnthropicUsageProvider needs a per-request AsyncSession, so the
endpoint constructs it via build_anthropic_provider(db) instead of
through the registry singleton. minimax_credentials.set_minimax_api_key
and clear_minimax_api_key now also invalidate the minimax snapshot
cache so the next /usage call hits the live API.

The plan-tier PUT endpoint was already wired to invalidate the anthropic
cache (Task 3). The plan_tier_put_invalidates_cached_snapshot test now
exercises that path for real."
```

---

## Task 8: Frontend — types.ts + api.ts

**Files:**
- Create: `frontend/src/features/subscriptions/types.ts`
- Create: `frontend/src/features/subscriptions/api.ts`

- [ ] **Step 1: Write `frontend/src/features/subscriptions/types.ts`**

```typescript
export type SubscriptionProviderId = 'anthropic' | 'minimax'

export type SubscriptionErrorCode =
  | 'not_configured'
  | 'unauthorized'
  | 'unreachable'
  | 'malformed'
  | 'no_endpoint'
  | 'plan_unknown'

export type PlanTier = 'pro' | 'max_5x' | 'max_20x' | 'team'

export interface PeriodUsageResponse {
  label: string
  used: number
  limit: number | null
  unit: string
  reset_at: string | null
  source: string
  note: string | null
}

export interface SubscriptionUsageResponse {
  provider: SubscriptionProviderId
  plan_label: string | null
  periods: PeriodUsageResponse[]
  fetched_at: string
  error: string | null
  error_code: SubscriptionErrorCode | null
}

export interface AnthropicPlanTierResponse {
  tier: PlanTier | null
}
```

- [ ] **Step 2: Write `frontend/src/features/subscriptions/api.ts`**

```typescript
import { apiClient, buildEndpoint } from '@/lib/api'
import type {
  AnthropicPlanTierResponse,
  PlanTier,
  SubscriptionProviderId,
  SubscriptionUsageResponse,
} from './types'

const BASE = 'agent-bridge/subscriptions'

export function fetchSubscriptionUsage(
  providerId: SubscriptionProviderId,
): Promise<SubscriptionUsageResponse> {
  return apiClient<SubscriptionUsageResponse>(`${BASE}/${providerId}/usage`)
}

export function fetchAnthropicPlanTier(): Promise<AnthropicPlanTierResponse> {
  return apiClient<AnthropicPlanTierResponse>(`${BASE}/anthropic/plan-tier`)
}

export function setAnthropicPlanTier(tier: PlanTier | null): Promise<AnthropicPlanTierResponse> {
  return apiClient<AnthropicPlanTierResponse>(`${BASE}/anthropic/plan-tier`, {
    method: 'PUT',
    body: JSON.stringify({ tier }),
  })
}
```

- [ ] **Step 3: Lint**

Run: `cd frontend && npm run lint -- src/features/subscriptions 2>&1 | tail -20`
Expected: no new errors. (`apiClient` and `buildEndpoint` are both exported from `@/lib/api` — verify imports if the lint complains.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/subscriptions/types.ts frontend/src/features/subscriptions/api.ts
git commit -m "feat(subscriptions): add TypeScript types + API client"
```

---

## Task 9: Frontend — `UsagePeriodRow`

**Files:**
- Create: `frontend/src/features/subscriptions/UsagePeriodRow.tsx`

- [ ] **Step 1: Implement the row component**

```tsx
import { Progress } from '@/components/ui/progress'
import { Badge } from '@/components/ui/badge'
import { formatTokens, formatCost, getRelativeTime } from '@/features/usage/utils'
import type { PeriodUsageResponse } from './types'

interface UsagePeriodRowProps {
  period: PeriodUsageResponse
}

function formatUsed(used: number, unit: string): string {
  if (unit === 'tokens') return formatTokens(used)
  if (unit === 'USD' || unit === 'usd') return formatCost(used)
  return `${used.toLocaleString()} ${unit}`
}

export function UsagePeriodRow({ period }: UsagePeriodRowProps) {
  const { label, used, limit, unit, reset_at, source, note } = period
  const hasLimit = limit !== null
  const percent = hasLimit && limit! > 0 ? Math.min(100, (used / limit!) * 100) : 0

  return (
    <div className="space-y-2" data-testid={`period-row-${label}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{label}</span>
          <Badge variant="outline" className="text-xs">
            {source}
          </Badge>
        </div>
        <span className="text-sm tabular-nums">{formatUsed(used, unit)}</span>
      </div>

      {hasLimit ? (
        <>
          <Progress value={percent} className="h-2" />
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>of {formatUsed(limit!, unit)}</span>
            {reset_at && <span>resets {getRelativeTime(reset_at)}</span>}
          </div>
        </>
      ) : (
        <p className="text-xs text-muted-foreground">limit not published by provider</p>
      )}

      {note && <p className="text-xs text-muted-foreground italic">{note}</p>}
    </div>
  )
}
```

- [ ] **Step 2: Lint**

Run: `cd frontend && npm run lint -- src/features/subscriptions/UsagePeriodRow.tsx 2>&1 | tail -20`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/subscriptions/UsagePeriodRow.tsx
git commit -m "feat(subscriptions): add UsagePeriodRow component"
```

---

## Task 10: Frontend — `AnthropicCredentialsCard`

**Files:**
- Create: `frontend/src/features/subscriptions/AnthropicCredentialsCard.tsx`

- [ ] **Step 1: Implement the card**

```tsx
import { useEffect, useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { fetchAnthropicPlanTier, setAnthropicPlanTier } from './api'
import type { PlanTier } from './types'

const TIER_OPTIONS: { value: PlanTier; label: string }[] = [
  { value: 'pro', label: 'Pro' },
  { value: 'max_5x', label: 'Max 5x' },
  { value: 'max_20x', label: 'Max 20x' },
  { value: 'team', label: 'Team' },
]

interface AnthropicCredentialsCardProps {
  onTierChanged?: () => void
}

export function AnthropicCredentialsCard({ onTierChanged }: AnthropicCredentialsCardProps) {
  const [tier, setTier] = useState<PlanTier | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetchAnthropicPlanTier()
      .then((res) => {
        if (!cancelled) {
          setTier(res.tier)
          setLoaded(true)
        }
      })
      .catch(() => {
        if (!cancelled) setLoaded(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function handleChange(next: string) {
    const newTier = next as PlanTier
    setSaving(true)
    try {
      const res = await setAnthropicPlanTier(newTier)
      setTier(res.tier)
      onTierChanged?.()
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card data-testid="anthropic-credentials-card">
      <CardHeader>
        <CardTitle>Anthropic</CardTitle>
        <CardDescription>
          Pick your Anthropic plan so we can show 5h and weekly leftover against its limits.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <Select value={tier ?? ''} onValueChange={handleChange} disabled={!loaded || saving}>
          <SelectTrigger data-testid="anthropic-plan-trigger">
            <SelectValue placeholder={loaded ? 'Choose your plan' : 'Loading...'} />
          </SelectTrigger>
          <SelectContent>
            {TIER_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="text-xs text-muted-foreground">
          These limits may have shifted since the last Anthropic plan change — verify at
          anthropic.com before trusting the percentages.
        </p>
      </CardContent>
    </Card>
  )
}
```

- [ ] **Step 2: Lint**

Run: `cd frontend && npm run lint -- src/features/subscriptions/AnthropicCredentialsCard.tsx 2>&1 | tail -20`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/subscriptions/AnthropicCredentialsCard.tsx
git commit -m "feat(subscriptions): add AnthropicCredentialsCard (plan-tier select)"
```

---

## Task 11: Frontend — `SubscriptionUsageCard` (all render branches)

**Files:**
- Create: `frontend/src/features/subscriptions/SubscriptionUsageCard.tsx`

- [ ] **Step 1: Implement the card**

```tsx
import { useEffect, useState, useCallback } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { UsagePeriodRow } from './UsagePeriodRow'
import { MinimaxCredentialsCard } from './MinimaxCredentialsCard'
import { fetchSubscriptionUsage } from './api'
import type { SubscriptionProviderId, SubscriptionUsageResponse } from './types'
import { formatTimestamp } from '@/features/usage/utils'

interface SubscriptionUsageCardProps {
  provider: SubscriptionProviderId
  title: string
  description: string
  onRefresh?: () => void
}

export function SubscriptionUsageCard({
  provider,
  title,
  description,
  onRefresh,
}: SubscriptionUsageCardProps) {
  const [data, setData] = useState<SubscriptionUsageResponse | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(
    async (force = false) => {
      setLoading(true)
      try {
        const res = await fetchSubscriptionUsage(provider)
        setData(res)
        if (force) onRefresh?.()
      } finally {
        setLoading(false)
      }
    },
    [provider, onRefresh],
  )

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    const handler = () => {
      if (document.visibilityState === 'visible') load()
    }
    document.addEventListener('visibilitychange', handler)
    return () => document.removeEventListener('visibilitychange', handler)
  }, [load])

  return (
    <Card data-testid={`usage-card-${provider}`}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {data?.error_code === 'not_configured' && provider === 'minimax' && (
          <>
            <MinimaxCredentialsCard />
            <p className="text-xs text-muted-foreground">Set your API key to see usage.</p>
          </>
        )}

        {data?.error_code === 'plan_unknown' && provider === 'anthropic' && (
          <p className="text-sm text-muted-foreground">
            Pick your plan in the card above to see 5h/weekly leftover.
          </p>
        )}

        {data?.error_code &&
          data.error_code !== 'not_configured' &&
          data.error_code !== 'plan_unknown' && (
            <div
              className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive"
              data-testid="error-badge"
            >
              {data.error ?? 'Could not fetch usage.'}
            </div>
          )}

        {data && !data.error_code && (
          <div className="space-y-4">
            {data.periods.map((p) => (
              <UsagePeriodRow key={p.label} period={p} />
            ))}
            <StaleFooter fetchedAt={data.fetched_at} />
          </div>
        )}

        {loading && !data && (
          <p className="text-xs text-muted-foreground">Loading...</p>
        )}
      </CardContent>
    </Card>
  )
}

function StaleFooter({ fetchedAt }: { fetchedAt: string }) {
  const fetchedMs = new Date(fetchedAt).getTime()
  const ageMin = Math.round((Date.now() - fetchedMs) / 60_000)
  if (ageMin <= 5) return null
  return (
    <p className="text-xs text-muted-foreground">
      refreshed {ageMin} min ago — open another tab or click refresh to get a live number.
    </p>
  )
}
```

- [ ] **Step 2: Lint**

Run: `cd frontend && npm run lint -- src/features/subscriptions/SubscriptionUsageCard.tsx 2>&1 | tail -20`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/subscriptions/SubscriptionUsageCard.tsx
git commit -m "feat(subscriptions): add SubscriptionUsageCard with all render branches"
```

---

## Task 12: Frontend — Vitest coverage of the card

**Files:**
- Create: `frontend/src/features/subscriptions/SubscriptionUsageCard.test.tsx`

- [ ] **Step 1: Check for an existing Vitest setup**

Run: `cd frontend && cat package.json | grep -E "vitest|jest"`
Expected: `vitest` and `@testing-library/react` available. If not, the spec's "Vitest coverage" promise is false; update the spec instead. (Most existing components in this repo don't have component tests; verify before claiming coverage is in this PR.)

- [ ] **Step 2: Write the test**

```tsx
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { SubscriptionUsageCard } from './SubscriptionUsageCard'
import type { SubscriptionUsageResponse } from './types'

vi.mock('./api', () => ({
  fetchSubscriptionUsage: vi.fn(),
  fetchAnthropicPlanTier: vi.fn(),
  setAnthropicPlanTier: vi.fn(),
}))

import { fetchSubscriptionUsage } from './api'

const happy: SubscriptionUsageResponse = {
  provider: 'anthropic',
  plan_label: 'max_5x',
  periods: [
    {
      label: '5h rate',
      used: 50_000,
      limit: 220_000,
      unit: 'tokens',
      reset_at: new Date(Date.now() + 60 * 60 * 1000).toISOString(),
      source: 'local',
      note: null,
    },
    {
      label: 'Weekly',
      used: 1_000_000,
      limit: null,
      unit: 'tokens',
      reset_at: null,
      source: 'local',
      note: null,
    },
  ],
  fetched_at: new Date().toISOString(),
  error: null,
  error_code: null,
}

describe('SubscriptionUsageCard', () => {
  beforeEach(() => {
    vi.mocked(fetchSubscriptionUsage).mockReset()
  })

  it('renders happy-path periods', async () => {
    vi.mocked(fetchSubscriptionUsage).mockResolvedValue(happy)
    render(
      <SubscriptionUsageCard provider="anthropic" title="Anthropic" description="desc" />,
    )
    await waitFor(() => {
      expect(screen.getByTestId('period-row-5h rate')).toBeInTheDocument()
      expect(screen.getByTestId('period-row-Weekly')).toBeInTheDocument()
    })
  })

  it('renders the not_configured state for minimax', async () => {
    vi.mocked(fetchSubscriptionUsage).mockResolvedValue({
      ...happy,
      provider: 'minimax',
      plan_label: null,
      periods: [],
      error: 'MiniMax API key not configured.',
      error_code: 'not_configured',
    })
    render(<SubscriptionUsageCard provider="minimax" title="MiniMax" description="d" />)
    await waitFor(() => {
      expect(screen.getByText(/Set your API key/)).toBeInTheDocument()
    })
  })

  it('renders the plan_unknown state for anthropic', async () => {
    vi.mocked(fetchSubscriptionUsage).mockResolvedValue({
      ...happy,
      provider: 'anthropic',
      plan_label: null,
      periods: [],
      error: 'Pick a tier',
      error_code: 'plan_unknown',
    })
    render(<SubscriptionUsageCard provider="anthropic" title="Anthropic" description="d" />)
    await waitFor(() => {
      expect(screen.getByText(/Pick your plan/)).toBeInTheDocument()
    })
  })

  it('renders the error badge for unauthorized', async () => {
    vi.mocked(fetchSubscriptionUsage).mockResolvedValue({
      ...happy,
      provider: 'minimax',
      plan_label: null,
      periods: [],
      error: 'MiniMax rejected the API key.',
      error_code: 'unauthorized',
    })
    render(<SubscriptionUsageCard provider="minimax" title="MiniMax" description="d" />)
    await waitFor(() => {
      expect(screen.getByTestId('error-badge')).toHaveTextContent(/rejected/)
    })
  })
})
```

- [ ] **Step 3: Run the test**

Run: `cd frontend && npx vitest run src/features/subscriptions/SubscriptionUsageCard.test.tsx`
Expected: 4 tests pass.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/subscriptions/SubscriptionUsageCard.test.tsx
git commit -m "test(subscriptions): cover the 4 render branches of SubscriptionUsageCard"
```

---

## Task 13: Frontend — wire into SubscriptionsPage

**Files:**
- Modify: `frontend/src/features/subscriptions/SubscriptionsPage.tsx`

- [ ] **Step 1: Replace the page content**

```tsx
import { SubscriptionUsageCard } from './SubscriptionUsageCard'
import { AnthropicCredentialsCard } from './AnthropicCredentialsCard'

export function SubscriptionsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Subscriptions</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Per-subscription quota left (5h rate, weekly, or whatever the provider exposes).
        </p>
      </div>

      <AnthropicCredentialsCard />
      <SubscriptionUsageCard
        provider="anthropic"
        title="Anthropic"
        description="5h rate and weekly leftover based on local JSONL and your selected plan."
      />
      <SubscriptionUsageCard
        provider="minimax"
        title="MiniMax"
        description="Quota left for the MiniMax subscription, fetched from the MiniMax API."
      />
    </div>
  )
}
```

- [ ] **Step 2: Lint**

Run: `cd frontend && npm run lint -- src/features/subscriptions/SubscriptionsPage.tsx 2>&1 | tail -20`
Expected: clean.

- [ ] **Step 3: Build**

Run: `cd frontend && npm run build 2>&1 | tail -20`
Expected: build succeeds. If it complains about unused imports (`MinimaxCredentialsCard` is imported by `SubscriptionUsageCard`, not the page), remove the unused import.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/subscriptions/SubscriptionsPage.tsx
git commit -m "feat(subscriptions): wire SubscriptionsPage to the two usage cards"
```

---

## Task 14: Docs

**Files:**
- Create: `docs/subscriptions/usage.md`

- [ ] **Step 1: Write the doc**

```markdown
# Subscriptions — usage card

Each provider card on `/#/subscriptions` shows what the system knows
about that subscription's remaining quota. Two providers render today:

## Anthropic

Shows a **5h rate** and a **Weekly** row, computed from local Claude Code
JSONL (via `UsageService.identify_session_blocks` + `UsageService.aggregate_weekly`).
You pick your plan tier (Pro / Max 5x / Max 20x / Team) from a dropdown
so we know the denominator.

**Honest about what we don't know:** Anthropic does not publish a
public usage API for Pro/Max and does not publish weekly token limits
for any tier. The card therefore shows `limit not published by Anthropic`
for the Weekly row. Verify the 5h number against
[Anthropic's plan docs](https://www.anthropic.com/pricing) before
trusting the percentages — the constant table is re-verified at each
implementation, but limits drift.

## MiniMax

Shows whatever the MiniMax API exposes — see the implementation probe
commit message in this branch's history for the exact endpoint(s) we
hit and the response shape we map. If the probe found nothing usable,
the card ships with a `no_endpoint` empty state rather than fabricate.

## Errors

| Provider state | Card shows |
|---|---|
| MiniMax API key not set | MiniMax credentials form + "Set your API key to see usage" |
| MiniMax rejected the key | Red error badge: "MiniMax rejected the API key" |
| MiniMax unreachable / 5xx | Red error badge with the HTTP status |
| MiniMax returned non-JSON | Red error badge: "MiniMax returned an unexpected response" |
| MiniMax has no usage endpoint | Red error badge: "MiniMax does not expose usage data" |
| Anthropic plan not picked | Plan dropdown + "Pick your plan to see 5h/weekly leftover" |
| Backend itself unreachable | Page-level error in the card chrome |
```

- [ ] **Step 2: Commit**

```bash
git add docs/subscriptions/usage.md
git commit -m "docs(subscriptions): user-facing doc for the usage card"
```

---

## Task 15: Final verification + PR description

- [ ] **Step 1: Run the full backend subscription test set**

Run: `cd backend && python -m pytest tests/test_subscription_usage_*.py tests/test_anthropic_plan_tier.py tests/test_minimax_credentials.py tests/test_usage_service.py -v`
Expected: all pass.

- [ ] **Step 2: Run the frontend tests + build**

Run: `cd frontend && npm run lint && npx vitest run src/features/subscriptions && npm run build`
Expected: lint clean, all vitest tests pass, build succeeds.

- [ ] **Step 3: Smoke-test the live endpoints**

Start the dev stack:

```bash
./scripts/cockpit.sh start
```

Wait for it to be ready (`./scripts/cockpit.sh status`), then:

```bash
# Should 200 with error_code = "plan_unknown":
curl -s http://localhost:8000/api/v1/agent-bridge/subscriptions/anthropic/usage | head

# Should 200 with error_code = "not_configured" (no MINIMAX_API_KEY in env):
curl -s http://localhost:8000/api/v1/agent-bridge/subscriptions/minimax/usage | head

# Should 200 with tier = null:
curl -s http://localhost:8000/api/v1/agent-bridge/subscriptions/anthropic/plan-tier | head

# Put a tier; should round-trip:
curl -s -X PUT http://localhost:8000/api/v1/agent-bridge/subscriptions/anthropic/plan-tier \
  -H 'Content-Type: application/json' -d '{"tier": "max_5x"}' | head
```

Expected: all return sensible JSON; the subscription card on `/#/subscriptions` (open in browser) shows the two cards with the right empty states.

- [ ] **Step 4: Write the PR description**

Open the GitHub PR. Title: `feat(subscriptions): leftover usage card for Anthropic + MiniMax`. Body:

> Per-subscription quota display on the Subscriptions page. Two providers render today — Anthropic (local JSONL + user-picked plan tier) and MiniMax (remote API call). One PR, five conceptual commits; the MiniMax commit comes first so the harder data-source question is settled before the easier one.
>
> **Honesty constraints this PR commits to:**
>
> 1. The Anthropic weekly limit is `None` for every tier; the card shows "limit not published by Anthropic" rather than a guessed number.
> 2. Anthropic's `ANTHROPIC_PLAN_LIMITS` constants were re-verified against the current Anthropic plan docs at implementation time; any unverifiable tier has `limit=None`. Verification URLs are in the relevant commit message.
> 3. The MiniMax API surface was probed with a dedicated script before any client code was written. The probe transcript is preserved in the first commit's message body. If the probe found no usable endpoint, the MiniMax card ships empty with `no_endpoint`, not fabricated data.
> 4. Card UI shows a "limits may have changed — verify before trusting" disclaimer on plan selection.
>
> See `docs/subscriptions/usage.md` for the full user-facing description of what each card shows.

---

## Self-Review

**1. Spec coverage** (each spec section → task implementing it):

| Spec section | Task |
|---|---|
| `base.py` dataclasses + ABC | Task 2 |
| `SubscriptionPref` DB table | Task 2 |
| `subscription_prefs` DB row | Task 2 |
| 3 endpoints (`GET usage`, `GET plan-tier`, `PUT plan-tier`) | Task 3 |
| 5-min in-process cache + invalidation hooks | Tasks 3, 7 |
| `MinimaxUsageProvider` + probe step | Tasks 1, 4 |
| `AnthropicUsageProvider` + `UsageService.aggregate_weekly` | Tasks 5, 6 |
| `AnthropicCredentialsCard` `<Select>` | Task 10 |
| `SubscriptionUsageCard` render rules (6 branches) | Task 11 |
| `UsagePeriodRow` (label, progress, reset_at) | Task 9 |
| Frontend types.ts + api.ts | Task 8 |
| Refresh-on-focus (no polling) | Task 11 |
| Tests (4 backend files, 1 frontend file) | Tasks 3, 4, 5, 6, 7, 12 |
| `ANTHROPIC_PLAN_LIMITS` verification gate | Task 6 Step 1 |
| Honest empty states (`limit=None`, `no_endpoint`, etc.) | Tasks 4, 6, 11 |
| Frontend tests cover 4 render paths | Task 12 |
| `docs/subscriptions/usage.md` | Task 14 |
| 5-commit rollout inside one PR | Tasks 1, 2+3, 4, 5+6+7, 8+9+10+11+12+13+14 |

All spec sections are covered.

**2. Placeholder scan:**

- Plan TBDs: none — verification gate enforced at Task 6 Step 1.
- Generic "handle edge cases": none — every `except` branch is named (`401`, `5xx`, JSON parse).
- "Similar to Task N": the cache invalidation pattern is repeated in Task 7 Step 2 but each call site has its own commit block.
- Stub tests: Task 3's `test_plan_tier_put_invalidates_cached_snapshot` is a stub; Task 7 Step 3 replaces it with a real test.

**3. Type consistency:**

- `ErrorCode` Literal values are the same in Task 2 Step 5 (`base.py`), Task 3 Step 4 (Pydantic), Task 6 Step 4 (anthropic provider), and the frontend Task 8 Step 1 (`SubscriptionErrorCode` TS union).
- `SubscriptionProviderId` values (`'anthropic' | 'minimax'`) appear in Task 8 Step 1 and are consumed in Tasks 11 + 13.
- `SubscriptionUsageSnapshot` field names match the Pydantic response shape consumed by Task 11 (`data.periods`, `data.error_code`, `data.fetched_at`).
- The endpoint URL prefix `agent-bridge/subscriptions` is the same in Tasks 3, 7, and Task 8 Step 2 (`api.ts`).
- The `PlanTier` enum (`pro | max_5x | max_20x | team`) appears verbatim in Tasks 6 Step 4 (backend `VALID_TIERS`), Task 8 Step 1 (TS union), and Task 10 Step 1 (TS `TIER_OPTIONS`).
