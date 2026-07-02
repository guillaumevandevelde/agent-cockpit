# Kanban Dispatch Controls + Centralized Project Transport — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the per-project auto-dispatch toggle, add a per-project concurrent-session cap (default 4) and a per-project Default-transport dropdown, and make session auto-close fully free a slot for every transport.

**Architecture:** Per-project settings live as key/value rows in the existing `KanbanMeta` table (same pattern as `shipmode:` / `skip_permissions:`). The backend dispatch tick (already polling every 10s) gates on these. The frontend re-adds the removed toggle plus two new controls in the Kanban toolbar. Auto-close on Done is hardened to cancel sandcastle runs and release the card claim.

**Tech Stack:** FastAPI + async SQLAlchemy (backend), React 19 + TypeScript + shadcn/ui (frontend), pytest (backend tests).

## Global Constraints

- Per-project settings are stored in `KanbanMeta` (key/value), keyed by `project_key`. No DB migration (generic table).
- Default concurrent-session cap: **4** (copied verbatim from spec).
- Default project transport: **`worktree`**.
- Transport values: exactly `worktree` | `sandcastle`.
- A "slot" is a card in an **agent column** (anything not in `COLUMNS = ["Backlog", "Impediment", "Done"]`) claimed by a claimant starting with `agent:`.
- Frontend tests are not set up (per CLAUDE.md); frontend tasks verify via `npm run lint` + `npm run build` and a manual check, not automated tests.
- Backend tests: `cd backend && source venv/bin/activate && pytest <path> -v`.
- Spec: `docs/superpowers/specs/2026-06-29-kanban-dispatch-transport-design.md`.

---

### Task 1: Backend — per-project session cap counting

**Files:**
- Modify: `backend/app/kanban/dispatch.py` (add prefix/const + getters/setters near line 30–97; replace `_project_is_busy` at `:368`; update `dispatch_project` at `:539`)
- Test: `backend/tests/test_kanban_dispatch.py`

**Interfaces:**
- Produces:
  - `MAX_SESSIONS_PREFIX = "max_sessions:"`, `DEFAULT_MAX_SESSIONS = 4`
  - `async def get_max_sessions(session, project_key: str) -> int`
  - `async def set_max_sessions(session, project_key: str, n: int) -> None`
  - `def _active_session_count(cards: Iterable[KanbanCard]) -> int`
  - `dispatch_project(...)` dispatches up to the number of free slots in one tick.
- Consumes: existing `KanbanMeta`, `CLAIMANT_PREFIX = "agent:"`, `COLUMNS`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_kanban_dispatch.py`. The file already defines, at module level: `KanbanSessionLocal = TestSessionLocal()`, an autouse `_tables` fixture, `PK`, `_make_card(s, ...)`, and `RecordingTransport`. Reuse those. `_active_session_count` is a pure function so its test builds bare `KanbanCard` objects:

```python
from app.kanban.models import KanbanCard


def _bare_card(column, claimed_by):
    c = KanbanCard(id="x", project_key=PK, title="t", description="",
                   column=column, rank="1")
    c.claimed_by = claimed_by
    return c


def test_active_session_count_counts_agent_claims_in_agent_columns():
    cards = [
        _bare_card("engineer", "agent:a"),
        _bare_card("review", "agent:b"),
        _bare_card("Backlog", "agent:c"),   # fixed column: excluded
        _bare_card("Done", "agent:d"),       # fixed column: excluded
        _bare_card("engineer", "me@ui"),     # human claim: excluded
        _bare_card("engineer", None),         # unclaimed: excluded
    ]
    assert dispatch._active_session_count(cards) == 2


@pytest.mark.asyncio
async def test_get_max_sessions_defaults_to_4():
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_max_sessions(s, PK) == 4


@pytest.mark.asyncio
async def test_set_then_get_max_sessions_roundtrips():
    async with KanbanSessionLocal() as s:
        await dispatch.set_max_sessions(s, PK, 2)
        await s.commit()
        assert await dispatch.get_max_sessions(s, PK) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py -k "active_session_count or max_sessions" -v`
Expected: FAIL — `AttributeError: module 'app.kanban.dispatch' has no attribute '_active_session_count'` (and `get_max_sessions`).

- [ ] **Step 3: Add the constant + meta helpers**

In `backend/app/kanban/dispatch.py`, after the `SKIP_PERMISSIONS_PREFIX` line (`:30`), add:

```python
MAX_SESSIONS_PREFIX = "max_sessions:"
DEFAULT_MAX_SESSIONS = 4
```

After `set_skip_permissions` (`:78`), add:

```python
async def get_max_sessions(session, project_key: str) -> int:
    row = await session.get(KanbanMeta, MAX_SESSIONS_PREFIX + project_key)
    if row is None:
        return DEFAULT_MAX_SESSIONS
    try:
        n = int(row.value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_SESSIONS
    return n if n >= 1 else DEFAULT_MAX_SESSIONS


async def set_max_sessions(session, project_key: str, n: int) -> None:
    if n < 1:
        raise ValueError("max_sessions must be >= 1")
    key = MAX_SESSIONS_PREFIX + project_key
    row = await session.get(KanbanMeta, key)
    if row is None:
        session.add(KanbanMeta(key=key, value=str(n)))
    else:
        row.value = str(n)
    await session.flush()
```

- [ ] **Step 4: Replace `_project_is_busy` with a count**

Replace the whole `_project_is_busy` function (`:368-374`) with:

```python
def _active_session_count(cards: Iterable[KanbanCard]) -> int:
    """Number of occupied dispatch slots: cards in agent columns (not Backlog,
    Impediment, or Done) held by an `agent:` claim."""
    from app.kanban.schemas import COLUMNS
    return sum(
        1 for c in cards
        if c.column not in COLUMNS and (c.claimed_by or "").startswith(CLAIMANT_PREFIX)
    )
```

- [ ] **Step 5: Run the new unit tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py -k "active_session_count or max_sessions" -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Update `dispatch_project` to honor the cap and fill free slots**

Replace the body of `dispatch_project` from the `card = _next_card(cards)` line through the final `return await _run_card(...)` (`:562-576`) with:

```python
    cap = await get_max_sessions(session, project_key)
    last_result: Optional[dict] = None

    # Fill every free slot in this tick, re-listing after each dispatch so the
    # claim just made counts toward the cap.
    while _active_session_count(cards) < cap:
        card = _next_card(cards)
        if card is None:
            break

        if transport is None:
            transport = await get_transport_for_project(project_path)

        last_result = await _run_card(
            session, card=card, project_key=project_key,
            project_path=project_path, transport=transport,
        )
        if last_result is None:
            break  # dispatch failed (e.g. memory) — let the tick queue/retry
        cards = await list_cards(session, project_key)

    return last_result
```

Keep the reaping block above this (`:554-560`) unchanged.

- [ ] **Step 7: Update the existing busy test for the new cap default**

The existing `test_dispatch_skips_when_project_already_busy` (`:187`) asserts that a single
busy card blocks dispatch — true only when the cap is 1, but the default is now 4. Pin the
cap to 1 in that test. Right after the `await _make_card(s, title="waiting", column="Backlog")`
line and before `await s.commit()`, insert:

```python
        await dispatch.set_max_sessions(s, PK, 1)
```

(`test_dispatch_card_bypasses_busy_cap` at `:206` exercises `dispatch_card`, not
`dispatch_project`, and is unaffected.)

- [ ] **Step 8: Add a cap-gating test**

Add to `backend/tests/test_kanban_dispatch.py`, reusing `_make_card`, `RecordingTransport`, `PK`:

```python
@pytest.mark.asyncio
async def test_dispatch_fills_up_to_cap_then_stops():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await dispatch.set_max_sessions(s, PK, 2)
        for i in range(4):
            await _make_card(s, title=f"c{i}", column="Backlog")
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport,
        )
        await s.commit()
    assert result is not None
    assert len(transport.calls) == 2  # fills exactly the 2 free slots in one tick


@pytest.mark.asyncio
async def test_dispatch_freed_slot_is_reusable():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await dispatch.set_max_sessions(s, PK, 1)
        busy = await _make_card(s, title="busy", column="engineer")
        await apply_operation(
            s, op_type="claim", entity_type="card", project_key=PK,
            entity_id=busy, payload={"claimed_by": "agent:k-x-0001"},
        )
        await _make_card(s, title="waiting", column="Backlog")
        await s.commit()
        # cap full -> no dispatch
        assert await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport) is None
        # free the slot, then exactly one dispatches
        await apply_operation(
            s, op_type="release", entity_type="card", project_key=PK,
            entity_id=busy, payload={})
        await s.commit()
        result = await dispatch.dispatch_project(
            s, project_key=PK, project_path="/p", transport=transport)
        await s.commit()
    assert result is not None
    assert len(transport.calls) == 1
```

- [ ] **Step 9: Run the full dispatch test module**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py -v`
Expected: PASS (all, including pre-existing tests).

- [ ] **Step 10: Commit**

```bash
git add backend/app/kanban/dispatch.py backend/tests/test_kanban_dispatch.py
git commit -m "feat(kanban): per-project session cap (default 4) replacing binary busy gate

Claude-Session: https://claude.ai/code/session_01V61ANwoNXEBeLApVTjNndq"
```

---

### Task 2: Backend — centralized project default transport

**Files:**
- Modify: `backend/app/kanban/dispatch.py` (add prefix/consts + getters/setters; rewrite `get_transport_for_project` at `:843`)
- Test: `backend/tests/test_kanban_dispatch.py`

**Interfaces:**
- Produces:
  - `TRANSPORT_PREFIX = "transport:"`, `TRANSPORTS = ("worktree", "sandcastle")`, `DEFAULT_TRANSPORT = "worktree"`
  - `async def get_default_transport(session, project_key: str) -> str`
  - `async def set_default_transport(session, project_key: str, value: str) -> None` (also syncs `SandcastleConfig.enabled`)
  - `get_transport_for_project(project_path)` resolves from the `transport:` meta.
- Consumes: `resolve_project_key`, `SandcastleConfig`, `make_worktree_transport`, `sandcastle_transport`, `get_skip_permissions`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_kanban_dispatch.py` (reuse the module-level `KanbanSessionLocal` and `PK`):

```python
@pytest.mark.asyncio
async def test_get_default_transport_defaults_to_worktree():
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_default_transport(s, PK) == "worktree"


@pytest.mark.asyncio
async def test_set_then_get_default_transport_roundtrips(monkeypatch):
    async def _noop(project_key, enabled):
        return None
    monkeypatch.setattr(dispatch, "_sync_sandcastle_enabled", _noop)
    async with KanbanSessionLocal() as s:
        await dispatch.set_default_transport(s, PK, "sandcastle")
        await s.commit()
        assert await dispatch.get_default_transport(s, PK) == "sandcastle"


@pytest.mark.asyncio
async def test_set_default_transport_rejects_unknown():
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await dispatch.set_default_transport(s, PK, "podman")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py -k default_transport -v`
Expected: FAIL — `AttributeError: ... has no attribute 'get_default_transport'`.

- [ ] **Step 3: Add the constants + getter/setter**

In `backend/app/kanban/dispatch.py`, after the `MAX_SESSIONS_PREFIX` block from Task 1, add:

```python
TRANSPORT_PREFIX = "transport:"
TRANSPORTS = ("worktree", "sandcastle")
DEFAULT_TRANSPORT = "worktree"
```

After `set_max_sessions` (from Task 1), add:

```python
async def get_default_transport(session, project_key: str) -> str:
    row = await session.get(KanbanMeta, TRANSPORT_PREFIX + project_key)
    if row and row.value in TRANSPORTS:
        return row.value
    return DEFAULT_TRANSPORT


async def set_default_transport(session, project_key: str, value: str) -> None:
    if value not in TRANSPORTS:
        raise ValueError(f"unknown transport: {value}")
    key = TRANSPORT_PREFIX + project_key
    row = await session.get(KanbanMeta, key)
    if row is None:
        session.add(KanbanMeta(key=key, value=value))
    else:
        row.value = value
    await session.flush()
    await _sync_sandcastle_enabled(project_key, value == "sandcastle")


async def _sync_sandcastle_enabled(project_key: str, enabled: bool) -> None:
    """Keep SandcastleConfig.enabled aligned with the project's default transport so
    the two never drift. Resolves the project path from the registry; no-op if the
    project isn't locally registered."""
    from app.database import AsyncSessionLocal
    from app.models.database import Project
    from app.models.sandcastle import SandcastleConfig
    from sqlalchemy import select

    try:
        async with AsyncSessionLocal() as db:
            paths = (await db.execute(select(Project.path))).scalars().all()
            target = next(
                (p for p in paths if _safe_resolve_key(p) == project_key), None
            )
            if target is None:
                return
            cfg = (await db.execute(
                select(SandcastleConfig).where(SandcastleConfig.project_path == target)
            )).scalar_one_or_none()
            if cfg is None:
                if enabled:
                    db.add(SandcastleConfig(project_path=target, enabled=True))
                    await db.commit()
                return
            if cfg.enabled != enabled:
                cfg.enabled = enabled
                await db.commit()
    except Exception:
        logger.exception("failed to sync sandcastle enabled for %s", project_key)


def _safe_resolve_key(path: str) -> Optional[str]:
    try:
        return resolve_project_key(path)
    except Exception:
        return None
```

- [ ] **Step 4: Run the getter/setter tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py -k default_transport -v`
Expected: PASS (3 tests). (`_sync_sandcastle_enabled` is a no-op when the project path isn't registered, which is the case in these unit tests.)

- [ ] **Step 5: Rewrite `get_transport_for_project` to read the meta**

Replace the body of `get_transport_for_project` (`:843-885`) with:

```python
async def get_transport_for_project(project_path: str) -> SpawnTransport:
    """Get the appropriate transport for a project.

    The authoritative source is the `transport:<project_key>` meta (worktree |
    sandcastle), set via the project's Default-transport control. Worktree honors
    the per-project skip_permissions flag.
    """
    from app.kanban.db import KanbanSessionLocal

    project_key = _safe_resolve_key(project_path)
    if project_key is None:
        return make_worktree_transport(skip_permissions=True)

    async with KanbanSessionLocal() as ks:
        transport_name = await get_default_transport(ks, project_key)
        if transport_name == "sandcastle":
            return sandcastle_transport
        skip = await get_skip_permissions(ks, project_key)

    return make_worktree_transport(skip_permissions=skip)
```

- [ ] **Step 6: Add a resolution test**

Add to `backend/tests/test_kanban_dispatch.py` — uses monkeypatch so no real project registry is needed:

```python
@pytest.mark.asyncio
async def test_get_transport_for_project_uses_meta_sandcastle(monkeypatch):
    async def _noop(project_key, enabled):
        return None
    monkeypatch.setattr(dispatch, "_sync_sandcastle_enabled", _noop)
    monkeypatch.setattr(dispatch, "_safe_resolve_key", lambda p: PK)
    async with KanbanSessionLocal() as s:
        await dispatch.set_default_transport(s, PK, "sandcastle")
        await s.commit()
    t = await dispatch.get_transport_for_project("/any/path")
    assert t is dispatch.sandcastle_transport


@pytest.mark.asyncio
async def test_get_transport_for_project_defaults_worktree(monkeypatch):
    monkeypatch.setattr(dispatch, "_safe_resolve_key", lambda p: PK)
    t = await dispatch.get_transport_for_project("/any/path")
    assert t is not dispatch.sandcastle_transport  # a worktree transport callable
```

`get_transport_for_project` opens `app.kanban.db.KanbanSessionLocal`, which conftest has patched to the in-memory test DB, so it sees the meta written above.

- [ ] **Step 7: Run tests**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py -k "transport_for_project or default_transport" -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/kanban/dispatch.py backend/tests/test_kanban_dispatch.py
git commit -m "feat(kanban): centralized project default transport meta (auto resolves from it)

Claude-Session: https://claude.ai/code/session_01V61ANwoNXEBeLApVTjNndq"
```

---

### Task 3: Backend — API endpoints for max-sessions and transport

**Files:**
- Modify: `backend/app/kanban/schemas.py` (add request models after `SkipPermissionsRequest:109`)
- Modify: `backend/app/api/v1/kanban/router.py` (add endpoints after `/skip-permissions:387`; extend the schema import at `:16`)
- Test: `backend/tests/test_kanban_api.py`

**Interfaces:**
- Consumes: `dispatch.get_max_sessions/set_max_sessions/get_default_transport/set_default_transport` (Tasks 1–2).
- Produces: `GET|POST /api/v1/kanban/max-sessions`, `GET|POST /api/v1/kanban/transport`.

- [ ] **Step 1: Write the failing API tests**

Add to `backend/tests/test_kanban_api.py`. The file already imports `AsyncClient, ASGITransport` and `from app.main import app`, and has the autouse `_tables` fixture; each test builds its own client inline (no `client` fixture). The `transport=sandcastle` POST triggers `_sync_sandcastle_enabled`, which reads the main DB read-only and is exception-guarded — it won't affect these assertions since `p2` isn't a registered project:

```python
@pytest.mark.asyncio
async def test_max_sessions_defaults_to_4():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/v1/kanban/max-sessions", params={"project_key": "p1"})
        assert r.status_code == 200
        assert r.json()["max_sessions"] == 4


@pytest.mark.asyncio
async def test_set_max_sessions_roundtrip():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/max-sessions",
                          json={"project_key": "p1", "max_sessions": 3})
        assert r.status_code == 200
        g = await ac.get("/api/v1/kanban/max-sessions", params={"project_key": "p1"})
        assert g.json()["max_sessions"] == 3


@pytest.mark.asyncio
async def test_set_max_sessions_rejects_zero():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/max-sessions",
                          json={"project_key": "p1", "max_sessions": 0})
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_transport_defaults_worktree_and_roundtrips():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/v1/kanban/transport", params={"project_key": "p2"})
        assert r.json()["transport"] == "worktree"
        s = await ac.post("/api/v1/kanban/transport",
                          json={"project_key": "p2", "transport": "sandcastle"})
        assert s.status_code == 200
        g = await ac.get("/api/v1/kanban/transport", params={"project_key": "p2"})
        assert g.json()["transport"] == "sandcastle"


@pytest.mark.asyncio
async def test_transport_rejects_unknown():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/transport",
                          json={"project_key": "p2", "transport": "podman"})
        assert r.status_code == 422
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_api.py -k "max_sessions or transport" -v`
Expected: FAIL — 404 (routes don't exist yet).

- [ ] **Step 3: Add request schemas**

In `backend/app/kanban/schemas.py`, after `SkipPermissionsRequest` (`:109`), add:

```python
class MaxSessionsRequest(BaseModel):
    project_key: str
    max_sessions: int


class DefaultTransportRequest(BaseModel):
    project_key: str
    transport: str
```

- [ ] **Step 4: Import the new schemas in the router**

In `backend/app/api/v1/kanban/router.py`, extend the import at `:16`:

```python
    AutodispatchRequest, ShipModeRequest, SkipPermissionsRequest,
    MaxSessionsRequest, DefaultTransportRequest,
```

(Add the second line to the existing multi-line import; keep the rest of that import statement intact.)

- [ ] **Step 5: Add the endpoints**

In `backend/app/api/v1/kanban/router.py`, after `set_skip_permissions` (`:387`), add:

```python
@router.get("/max-sessions")
async def get_max_sessions(project_key: str = Query(...)):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {"project_key": project_key,
                "max_sessions": await dispatch.get_max_sessions(s, project_key)}


@router.post("/max-sessions")
async def set_max_sessions(payload: MaxSessionsRequest):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            await dispatch.set_max_sessions(s, payload.project_key, payload.max_sessions)
        except ValueError as e:
            raise HTTPException(422, str(e))
        await s.commit()
    return {"project_key": payload.project_key, "max_sessions": payload.max_sessions}


@router.get("/transport")
async def get_transport(project_key: str = Query(...)):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {"project_key": project_key,
                "transport": await dispatch.get_default_transport(s, project_key)}


@router.post("/transport")
async def set_transport(payload: DefaultTransportRequest):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            await dispatch.set_default_transport(s, payload.project_key, payload.transport)
        except ValueError as e:
            raise HTTPException(422, str(e))
        await s.commit()
    return {"project_key": payload.project_key, "transport": payload.transport}
```

- [ ] **Step 6: Run the API tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_api.py -k "max_sessions or transport" -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/kanban/schemas.py backend/app/api/v1/kanban/router.py backend/tests/test_kanban_api.py
git commit -m "feat(kanban): API for per-project max-sessions and default transport

Claude-Session: https://claude.ai/code/session_01V61ANwoNXEBeLApVTjNndq"
```

---

### Task 4: Backend — auto-close hardening (sandcastle cancel + claim release on Done)

**Files:**
- Modify: `backend/app/kanban/session_cleanup.py` (`cleanup_session_for_card:96`)
- Test: `backend/tests/test_kanban_session_cleanup.py` (create if absent)

**Interfaces:**
- Consumes: `sandcastle_service.cancel_run(run_id)`, `SandcastleRun` (keyed by `branch == session_name`), `apply_operation(op_type="release")`, card `transport` + resolved project transport.
- Produces: `cleanup_session_for_card` cancels a running sandcastle run for sandcastle cards and releases the card's `agent:` claim; tmux worktree path unchanged.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_kanban_session_cleanup.py`:

```python
import pytest
from app.kanban import session_cleanup


@pytest.mark.asyncio
async def test_cancel_sandcastle_run_cancels_matching_running_run(monkeypatch):
    cancelled = []

    class FakeRun:
        id = 7
        branch = "k-foo-1234"
        status = "running"

    async def fake_find(session_name):
        return FakeRun() if session_name == "k-foo-1234" else None

    async def fake_cancel(run_id):
        cancelled.append(run_id)
        return True

    monkeypatch.setattr(session_cleanup, "_find_running_sandcastle_run", fake_find)
    monkeypatch.setattr(
        session_cleanup.sandcastle_service, "cancel_run", fake_cancel, raising=False
    )

    ok = await session_cleanup._cancel_sandcastle_run("k-foo-1234")
    assert ok is True
    assert cancelled == [7]


@pytest.mark.asyncio
async def test_cancel_sandcastle_run_noop_when_no_run(monkeypatch):
    async def fake_find(session_name):
        return None

    monkeypatch.setattr(session_cleanup, "_find_running_sandcastle_run", fake_find)
    assert await session_cleanup._cancel_sandcastle_run("k-none-0000") is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_session_cleanup.py -v`
Expected: FAIL — `AttributeError: module 'app.kanban.session_cleanup' has no attribute '_cancel_sandcastle_run'`.

- [ ] **Step 3: Add the sandcastle-cancel helpers**

In `backend/app/kanban/session_cleanup.py`, add the import near the top and these helpers above `cleanup_session_for_card`:

```python
from app.services.sandcastle_service import sandcastle_service


async def _find_running_sandcastle_run(session_name: str):
    """Return the pending/running SandcastleRun whose branch == session_name, or None."""
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.sandcastle import SandcastleRun

    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(SandcastleRun).where(
                SandcastleRun.branch == session_name,
                SandcastleRun.status.in_(("pending", "running")),
            )
        )).scalar_one_or_none()


async def _cancel_sandcastle_run(session_name: str) -> bool:
    """Cancel the sandcastle run backing this session, if any. Returns True if a run
    was found and cancellation was attempted."""
    run = await _find_running_sandcastle_run(session_name)
    if run is None:
        return False
    try:
        await sandcastle_service.cancel_run(run.id)
    except Exception:
        logger.exception("failed to cancel sandcastle run %s", run.id)
    return True
```

- [ ] **Step 4: Wire cancel + claim-release into `cleanup_session_for_card`**

In `cleanup_session_for_card` (`:96`), replace the block from `result["session_name"] = session_name` (`:123`) through the final `logger.info("Cleaned up session %s for completed card %s", session_name, card_id)` (`:138`) with:

```python
            result["session_name"] = session_name

        # Sandcastle sessions have no tmux session: cancel the run instead.
        if await _cancel_sandcastle_run(session_name):
            await _release_claim(card_id, project_key)
            result["cleaned"] = True
            logger.info("Cancelled sandcastle run for completed card %s", card_id)
            return result

        if not _kill_tmux_session(session_name):
            result["error"] = "failed_to_kill_session"
            return result

        project_path = await _get_project_path(project_key)
        if project_path:
            _remove_worktree_at(session_name, project_path)
        else:
            logger.warning(
                "No registered path for project %s; worktree not removed", project_key
            )

        await _release_claim(card_id, project_key)
        result["cleaned"] = True
        logger.info("Cleaned up session %s for completed card %s", session_name, card_id)
```

Then add the `_release_claim` helper above `cleanup_session_for_card`:

```python
async def _release_claim(card_id: str, project_key: str) -> None:
    """Clear the card's agent: claim so a Done card is never shown as claimed."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.operations import apply_operation

    try:
        async with KanbanSessionLocal() as session:
            await apply_operation(
                session, op_type="release", entity_type="card",
                project_key=project_key, entity_id=card_id, payload={},
            )
            await session.commit()
    except Exception:
        logger.exception("failed to release claim on card %s", card_id)
```

- [ ] **Step 5: Run the cleanup tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_session_cleanup.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Run the wider kanban suite for regressions**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py tests/test_kanban_api.py tests/test_kanban_session_cleanup.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/kanban/session_cleanup.py backend/tests/test_kanban_session_cleanup.py
git commit -m "fix(kanban): auto-close cancels sandcastle runs and releases the claim on Done

Claude-Session: https://claude.ai/code/session_01V61ANwoNXEBeLApVTjNndq"
```

---

### Task 5: Frontend — API client methods

**Files:**
- Modify: `frontend/src/features/kanban/api.ts` (add to the `kanbanApi` object before the closing `};` at `:202`)

**Interfaces:**
- Produces on `kanbanApi`:
  - `getAutodispatch(projectKey) -> Promise<{ enabled: boolean }>`
  - `setAutodispatch(projectKey, enabled) -> Promise<{ enabled: boolean }>`
  - `getMaxSessions(projectKey) -> Promise<{ max_sessions: number }>`
  - `setMaxSessions(projectKey, n) -> Promise<{ max_sessions: number }>`
  - `getDefaultTransport(projectKey) -> Promise<{ transport: string }>`
  - `setDefaultTransport(projectKey, transport) -> Promise<{ transport: string }>`

- [ ] **Step 1: Add the methods**

In `frontend/src/features/kanban/api.ts`, immediately before the final `};` (`:202`), add:

```typescript
  getAutodispatch: (projectKey: string): Promise<{ enabled: boolean }> =>
    apiClient<{ enabled: boolean }>(
      `${BASE}/autodispatch?project_key=${encodeURIComponent(projectKey)}`
    ),

  setAutodispatch: (projectKey: string, enabled: boolean): Promise<{ enabled: boolean }> =>
    apiClient<{ enabled: boolean }>(`${BASE}/autodispatch`, {
      method: "POST",
      body: JSON.stringify({ project_key: projectKey, enabled }),
    }),

  getMaxSessions: (projectKey: string): Promise<{ max_sessions: number }> =>
    apiClient<{ max_sessions: number }>(
      `${BASE}/max-sessions?project_key=${encodeURIComponent(projectKey)}`
    ),

  setMaxSessions: (projectKey: string, n: number): Promise<{ max_sessions: number }> =>
    apiClient<{ max_sessions: number }>(`${BASE}/max-sessions`, {
      method: "POST",
      body: JSON.stringify({ project_key: projectKey, max_sessions: n }),
    }),

  getDefaultTransport: (projectKey: string): Promise<{ transport: string }> =>
    apiClient<{ transport: string }>(
      `${BASE}/transport?project_key=${encodeURIComponent(projectKey)}`
    ),

  setDefaultTransport: (projectKey: string, transport: string): Promise<{ transport: string }> =>
    apiClient<{ transport: string }>(`${BASE}/transport`, {
      method: "POST",
      body: JSON.stringify({ project_key: projectKey, transport }),
    }),
```

- [ ] **Step 2: Lint**

Run: `cd frontend && npm run lint`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/kanban/api.ts
git commit -m "feat(kanban): api client for autodispatch, max-sessions, default transport

Claude-Session: https://claude.ai/code/session_01V61ANwoNXEBeLApVTjNndq"
```

---

### Task 6: Frontend — AutodispatchToggle component

**Files:**
- Create: `frontend/src/features/kanban/components/AutodispatchToggle.tsx`

**Interfaces:**
- Consumes: `kanbanApi.getAutodispatch/setAutodispatch` (Task 5).
- Produces: `AutodispatchToggle({ projectKey }: { projectKey: string })`.

- [ ] **Step 1: Create the component** (mirrors `SkipPermissionsToggle.tsx`)

```typescript
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { kanbanApi } from "../api";

export function AutodispatchToggle({ projectKey }: { projectKey: string }) {
  const [enabled, setEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    if (!projectKey) return;
    kanbanApi
      .getAutodispatch(projectKey)
      .then((r) => setEnabled(r.enabled))
      .catch(() => setEnabled(false));
  }, [projectKey]);

  if (!projectKey || enabled === null) return null;

  const toggle = async () => {
    const next = !enabled;
    try {
      await kanbanApi.setAutodispatch(projectKey, next);
      setEnabled(next);
      toast.success(next ? "Auto-dispatch: on" : "Auto-dispatch: off");
    } catch {
      toast.error("Failed to change auto-dispatch");
    }
  };

  return (
    <Button
      size="sm"
      variant={enabled ? "default" : "outline"}
      onClick={toggle}
      title="When on, the poller automatically picks up Backlog cards up to the session cap"
    >
      {enabled ? "Auto-dispatch: on" : "Auto-dispatch: off"}
    </Button>
  );
}
```

- [ ] **Step 2: Lint**

Run: `cd frontend && npm run lint`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/kanban/components/AutodispatchToggle.tsx
git commit -m "feat(kanban): restore AutodispatchToggle component

Claude-Session: https://claude.ai/code/session_01V61ANwoNXEBeLApVTjNndq"
```

---

### Task 7: Frontend — MaxSessionsControl component

**Files:**
- Create: `frontend/src/features/kanban/components/MaxSessionsControl.tsx`

**Interfaces:**
- Consumes: `kanbanApi.getMaxSessions/setMaxSessions` (Task 5).
- Produces: `MaxSessionsControl({ projectKey }: { projectKey: string })`.

- [ ] **Step 1: Create the component** (a compact −/N/+ stepper, min 1)

```typescript
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { kanbanApi } from "../api";

export function MaxSessionsControl({ projectKey }: { projectKey: string }) {
  const [value, setValue] = useState<number | null>(null);

  useEffect(() => {
    if (!projectKey) return;
    kanbanApi
      .getMaxSessions(projectKey)
      .then((r) => setValue(r.max_sessions))
      .catch(() => setValue(4));
  }, [projectKey]);

  if (!projectKey || value === null) return null;

  const commit = async (next: number) => {
    if (next < 1) return;
    const prev = value;
    setValue(next);
    try {
      await kanbanApi.setMaxSessions(projectKey, next);
    } catch {
      setValue(prev);
      toast.error("Failed to set max sessions");
    }
  };

  return (
    <div
      className="inline-flex items-center gap-1 rounded-md border px-1"
      title="Maximum concurrent agent sessions auto-dispatched for this project"
    >
      <Button size="sm" variant="ghost" className="h-7 w-7 p-0"
        onClick={() => commit(value - 1)} disabled={value <= 1}>−</Button>
      <span className="min-w-[5.5rem] text-center text-xs tabular-nums">
        Max sessions: {value}
      </span>
      <Button size="sm" variant="ghost" className="h-7 w-7 p-0"
        onClick={() => commit(value + 1)}>+</Button>
    </div>
  );
}
```

- [ ] **Step 2: Lint**

Run: `cd frontend && npm run lint`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/kanban/components/MaxSessionsControl.tsx
git commit -m "feat(kanban): MaxSessionsControl stepper

Claude-Session: https://claude.ai/code/session_01V61ANwoNXEBeLApVTjNndq"
```

---

### Task 8: Frontend — DefaultTransportSelect component

**Files:**
- Create: `frontend/src/features/kanban/components/DefaultTransportSelect.tsx`

**Interfaces:**
- Consumes: `kanbanApi.getDefaultTransport/setDefaultTransport` (Task 5); shadcn `Select` (already used in `CardEditDialog.tsx:199`).
- Produces: `DefaultTransportSelect({ projectKey }: { projectKey: string })`.

- [ ] **Step 1: Create the component**

```typescript
import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { kanbanApi } from "../api";

export function DefaultTransportSelect({ projectKey }: { projectKey: string }) {
  const [transport, setTransport] = useState<string | null>(null);

  useEffect(() => {
    if (!projectKey) return;
    kanbanApi
      .getDefaultTransport(projectKey)
      .then((r) => setTransport(r.transport))
      .catch(() => setTransport("worktree"));
  }, [projectKey]);

  if (!projectKey || transport === null) return null;

  const onChange = async (next: string) => {
    const prev = transport;
    setTransport(next);
    try {
      await kanbanApi.setDefaultTransport(projectKey, next);
      toast.success(`Default transport: ${next}`);
    } catch {
      setTransport(prev);
      toast.error("Failed to set default transport");
    }
  };

  return (
    <div className="inline-flex items-center gap-2">
      <Select value={transport} onValueChange={onChange}>
        <SelectTrigger
          className="h-8 w-[170px]"
          title="Transport that card 'auto' resolves to for this project"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="worktree">Transport: worktree</SelectItem>
          <SelectItem value="sandcastle">Transport: sandcastle</SelectItem>
        </SelectContent>
      </Select>
      {transport === "sandcastle" && (
        <a href="/sandcastle" className="text-xs text-muted-foreground underline">
          Configure sandcastle
        </a>
      )}
    </div>
  );
}
```

Note: confirm the Sandcastle route path. If `frontend/src/App.tsx` registers the Sandcastle page under a different path than `/sandcastle`, use that exact path in the link.

- [ ] **Step 2: Lint**

Run: `cd frontend && npm run lint`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/features/kanban/components/DefaultTransportSelect.tsx
git commit -m "feat(kanban): DefaultTransportSelect dropdown

Claude-Session: https://claude.ai/code/session_01V61ANwoNXEBeLApVTjNndq"
```

---

### Task 9: Frontend — wire controls into the Kanban toolbar

**Files:**
- Modify: `frontend/src/features/kanban/KanbanPage.tsx` (imports near `:12-13`; toolbar near `:145-146`)

**Interfaces:**
- Consumes: `AutodispatchToggle`, `MaxSessionsControl`, `DefaultTransportSelect` (Tasks 6–8).

- [ ] **Step 1: Add imports**

In `frontend/src/features/kanban/KanbanPage.tsx`, after the `SkipPermissionsToggle` import (`:13`), add:

```typescript
import { AutodispatchToggle } from "./components/AutodispatchToggle";
import { MaxSessionsControl } from "./components/MaxSessionsControl";
import { DefaultTransportSelect } from "./components/DefaultTransportSelect";
```

- [ ] **Step 2: Render the controls in the toolbar**

In the toolbar (`:145-146`), after `<SkipPermissionsToggle projectKey={projectKey} />`, add:

```tsx
          <AutodispatchToggle projectKey={projectKey} />
          <MaxSessionsControl projectKey={projectKey} />
          <DefaultTransportSelect projectKey={projectKey} />
```

- [ ] **Step 3: Lint + build**

Run: `cd frontend && npm run lint && npm run build`
Expected: lint clean, build succeeds.

- [ ] **Step 4: Manual verification**

Start the stack (`./scripts/cockpit.sh start`), open the Kanban page for a project, and confirm: the **Auto-dispatch** toggle, **Max sessions** stepper, and **Transport** dropdown render next to Ship/Perms; toggling each persists across a page reload; setting Transport to `sandcastle` shows the configure link.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/kanban/KanbanPage.tsx
git commit -m "feat(kanban): surface auto-dispatch, max-sessions, transport in toolbar

Claude-Session: https://claude.ai/code/session_01V61ANwoNXEBeLApVTjNndq"
```

---

### Task 10: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Backend suite green**

Run: `cd backend && source venv/bin/activate && pytest tests/ -q`
Expected: PASS (no regressions).

- [ ] **Step 2: Live dispatch smoke test**

With the stack running and a project's auto-dispatch toggled **on**: confirm the poller picks up a Backlog card within ~10s, that it stops at the Max-sessions cap, and that moving a card to **Done** frees a slot so the next card dispatches. Confirm the previously-wedged stale claim (dead `agent:` session) is reaped once auto-dispatch is on.

- [ ] **Step 3: Final commit if any verification fixups were needed**

```bash
git add -A
git commit -m "test(kanban): verification fixups for dispatch controls

Claude-Session: https://claude.ai/code/session_01V61ANwoNXEBeLApVTjNndq"
```
