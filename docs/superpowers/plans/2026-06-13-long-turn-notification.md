# Long-Turn (>5s) Completion Notification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Een desktopnotificatie bij een voltooide beurt enkel laten vuren wanneer die langer dan 5 seconden duurde, zodat korte antwoorden geen ruis geven.

**Architecture:** De backend berekent bij het `Stop`-event de beurtduur uit het laatst opgeslagen `UserPromptSubmit`-tijdstip (reeds aanwezig in `presence_events`) en geeft die mee als `last_turn_duration_s` in de presence-response/WS-boodschap. De frontend laat de bestaande "stopped"-notificatie alleen nog vuren wanneer die duur ≥ 5s is. Geen DB-schemawijziging (puur berekend, geen nieuwe kolom — dit project heeft geen migratiesysteem).

**Tech Stack:** FastAPI + async SQLAlchemy + aiosqlite (backend), pytest (backend tests); React 19 + TypeScript + Vite (frontend, geen testopzet).

---

## File Structure

- `backend/app/services/presence_service.py` — `Stop`-tak berekent de beurtduur via nieuwe helper `_compute_turn_duration`; `_to_response` krijgt een optionele `turn_duration_s`-param.
- `backend/app/models/schemas.py` — `PresenceSessionResponse` krijgt veld `last_turn_duration_s`.
- `backend/tests/test_presence_turn_duration.py` — nieuwe pytest-module voor de duurberekening.
- `frontend/src/types/presence.ts` — `PresenceSession` krijgt veld `last_turn_duration_s`.
- `frontend/src/hooks/useAttentionNotifications.ts` — drempel-constante + gate op de `stopped`-tak.

---

## Task 1: Backend — beurtduur berekenen bij Stop en exposen

**Files:**
- Modify: `backend/app/models/schemas.py:1774-1795` (`PresenceSessionResponse`)
- Modify: `backend/app/services/presence_service.py` (`process_event` Stop-tak ~regel 169-171, `_to_response` ~regel 352-372; nieuwe helper)
- Test: `backend/tests/test_presence_turn_duration.py`

- [ ] **Step 1: Schrijf de falende test**

Maak `backend/tests/test_presence_turn_duration.py`:

```python
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import select

from app.database import Base, engine, AsyncSessionLocal
from app.models.database import PresenceEvent
from app.services.presence_service import PresenceService


@pytest.mark.asyncio
async def test_stop_after_prompt_yields_duration():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    service = PresenceService()
    async with AsyncSessionLocal() as db:
        await service.process_event(
            {"session_id": "sess-dur-1", "hook_event_name": "UserPromptSubmit", "cwd": "/home/guillaume/dev/a"},
            db,
        )
        resp = await service.process_event(
            {"session_id": "sess-dur-1", "hook_event_name": "Stop", "cwd": "/home/guillaume/dev/a"},
            db,
        )
        await db.commit()
    assert resp.last_turn_duration_s is not None
    assert resp.last_turn_duration_s >= 0


@pytest.mark.asyncio
async def test_stop_without_prompt_yields_none():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    service = PresenceService()
    async with AsyncSessionLocal() as db:
        resp = await service.process_event(
            {"session_id": "sess-dur-2", "hook_event_name": "Stop", "cwd": "/home/guillaume/dev/b"},
            db,
        )
        await db.commit()
    assert resp.last_turn_duration_s is None


@pytest.mark.asyncio
async def test_backdated_prompt_yields_large_duration():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    service = PresenceService()
    async with AsyncSessionLocal() as db:
        await service.process_event(
            {"session_id": "sess-dur-3", "hook_event_name": "UserPromptSubmit", "cwd": "/home/guillaume/dev/c"},
            db,
        )
        await db.flush()
        result = await db.execute(
            select(PresenceEvent).where(
                PresenceEvent.session_id == "sess-dur-3",
                PresenceEvent.event_type == "UserPromptSubmit",
            )
        )
        ev = result.scalars().first()
        ev.timestamp = datetime.now(timezone.utc) - timedelta(seconds=12)
        await db.flush()
        resp = await service.process_event(
            {"session_id": "sess-dur-3", "hook_event_name": "Stop", "cwd": "/home/guillaume/dev/c"},
            db,
        )
        await db.commit()
    assert resp.last_turn_duration_s is not None
    assert resp.last_turn_duration_s >= 11
```

- [ ] **Step 2: Voer de test uit en bevestig dat hij faalt**

Run: `cd backend && source venv/bin/activate && pytest tests/test_presence_turn_duration.py -v`
Expected: FAIL — `AttributeError`/`ValidationError` rond `last_turn_duration_s` (veld bestaat nog niet op `PresenceSessionResponse`).

- [ ] **Step 3: Voeg het veld toe aan het Pydantic-schema**

In `backend/app/models/schemas.py`, in `class PresenceSessionResponse`, na `ended_at: Optional[str] = None` (regel 1794):

```python
    ended_at: Optional[str] = None
    last_turn_duration_s: Optional[float] = None
```

- [ ] **Step 4: Voeg de helper toe in `presence_service.py`**

In `backend/app/services/presence_service.py`, binnen `class PresenceService` (bv. net vóór `_to_response`, ~regel 352):

```python
    async def _compute_turn_duration(self, session_id: str, now: datetime, db: AsyncSession) -> Optional[float]:
        """Seconds between the most recent UserPromptSubmit for this session and `now`.

        Reads the timestamps already stored in presence_events, so no schema change
        is needed. Returns None when no prompt is on record (the turn can't be timed).
        """
        result = await db.execute(
            select(func.max(PresenceEvent.timestamp)).where(
                PresenceEvent.session_id == session_id,
                PresenceEvent.event_type == "UserPromptSubmit",
            )
        )
        prompt_time = result.scalar_one_or_none()
        if prompt_time is None:
            return None
        # SQLite may return naive datetimes; treat them as UTC like elsewhere here.
        if prompt_time.tzinfo is None:
            prompt_time = prompt_time.replace(tzinfo=timezone.utc)
        return (now - prompt_time).total_seconds()
```

- [ ] **Step 5: Initialiseer `turn_duration_s` en zet hem in de Stop-tak**

In `process_event`, vóór de event-type dispatch (net na `if pane:`-blok, vóór `if event_type == "Notification":`, ~regel 113):

```python
        turn_duration_s: Optional[float] = None

```

En in de `Stop`-tak (regel 169-171), uitbreiden naar:

```python
        elif event_type == "Stop":
            session.status = SessionStatus.STOPPED
            session.status_text = "Waiting for input"
            turn_duration_s = await self._compute_turn_duration(session_id, now, db)
```

- [ ] **Step 6: Geef de duur door aan `_to_response`**

Pas de `return` onderaan `process_event` aan (regel 210):

```python
        return self._to_response(session, turn_duration_s)
```

Pas de signatuur en body van `_to_response` aan (regel 352):

```python
    def _to_response(self, session: PresenceSession, turn_duration_s: Optional[float] = None) -> PresenceSessionResponse:
```

en voeg in de `PresenceSessionResponse(...)`-constructie, na de `ended_at=...`-regel (regel 371), toe:

```python
            ended_at=session.ended_at.isoformat() if session.ended_at else None,
            last_turn_duration_s=turn_duration_s,
        )
```

- [ ] **Step 7: Voer de tests uit en bevestig dat ze slagen**

Run: `cd backend && source venv/bin/activate && pytest tests/test_presence_turn_duration.py -v`
Expected: PASS (3 tests).

- [ ] **Step 8: Draai de bredere presence-tests om geen regressie te bevestigen**

Run: `cd backend && source venv/bin/activate && pytest tests/test_presence_tmux_pane.py -v`
Expected: PASS (4 tests).

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/schemas.py backend/app/services/presence_service.py backend/tests/test_presence_turn_duration.py
git commit -m "feat(presence): compute turn duration at Stop and expose last_turn_duration_s"
```

---

## Task 2: Frontend — notificatie pas vuren bij beurt ≥ 5s

**Files:**
- Modify: `frontend/src/types/presence.ts` (interface `PresenceSession`)
- Modify: `frontend/src/hooks/useAttentionNotifications.ts` (constante + `detectAttention` stopped-tak, regel 32-39)

- [ ] **Step 1: Voeg het veld toe aan het frontend-type**

In `frontend/src/types/presence.ts`, in `interface PresenceSession`, na `last_command_exit?: number | null`:

```typescript
  last_command_exit?: number | null
  last_turn_duration_s?: number
```

- [ ] **Step 2: Voeg de drempel-constante toe**

In `frontend/src/hooks/useAttentionNotifications.ts`, na de imports (na regel 5), vóór `interface TrackedState`:

```typescript
/** Only notify on a completed turn when it took at least this long (seconds). */
const LONG_TURN_THRESHOLD_S = 5
```

- [ ] **Step 3: Gate de stopped-tak op de duur**

In `detectAttention`, vervang de bestaande "Waiting for input"-tak (regel 32-39):

```typescript
  // Waiting for input: just stopped.
  if (next.status === 'stopped' && prev.status !== 'stopped') {
    events.push({
      title: `🟡 ${label} wacht op je input`,
      body: next.status_text || 'Waiting for input',
      tag: `${next.session_id}:input`,
    })
  }
```

door:

```typescript
  // Answer ready: just stopped, and the turn took long enough that attention
  // likely drifted. Quick turns (< threshold) and turns we can't time stay silent.
  if (
    next.status === 'stopped' &&
    prev.status !== 'stopped' &&
    next.last_turn_duration_s != null &&
    next.last_turn_duration_s >= LONG_TURN_THRESHOLD_S
  ) {
    events.push({
      title: `🟡 ${label} wacht op je input`,
      body: `Antwoord klaar na ${Math.round(next.last_turn_duration_s)}s`,
      tag: `${next.session_id}:input`,
    })
  }
```

- [ ] **Step 4: Lint**

Run: `cd frontend && npm run lint`
Expected: geen nieuwe fouten/waarschuwingen in `useAttentionNotifications.ts` of `presence.ts`.

- [ ] **Step 5: Build (frontend wordt uit dist geserveerd op :8000)**

Run: `cd frontend && npm run build`
Expected: build slaagt zonder type-fouten.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/presence.ts frontend/src/hooks/useAttentionNotifications.ts
git commit -m "feat(presence): only notify on completed turns longer than 5s"
```

---

## Manuele verificatie (na beide taken)

Met attention-notificaties aan (toggle in de UI, browser-permissie verleend):

1. Stel een korte vraag (antwoord < 5s) → **geen** notificatie bij voltooiing.
2. Stel een vraag die > 5s "kookt" → notificatie **"🟡 <label> wacht op je input"** met body **"Antwoord klaar na Ns"**.
3. Klik de notificatie → focust het venster en opent de Agent Bridge/Presence voor die sessie (ongewijzigd bestaand gedrag).

---

## Self-Review

**Spec coverage:**
- Backend berekent duur uit `presence_events` bij `Stop` → Task 1, Step 4-5. ✅
- `_to_response` optionele param, andere oproepen `None` → Task 1, Step 6 (default `None`). ✅
- `PresenceSessionResponse.last_turn_duration_s` (geen DB-kolom) → Task 1, Step 3. ✅
- tz-naïef als UTC → Task 1, Step 4 (`replace(tzinfo=timezone.utc)`); getest in Step 1 (backdated). ✅
- Frontend-type → Task 2, Step 1. ✅
- `LONG_TURN_THRESHOLD_S = 5` + gate `!= null && >= 5` → Task 2, Step 2-3. ✅
- Body "Antwoord klaar na Ns", titel/tag onveranderd → Task 2, Step 3. ✅
- Randgeval geen prompt → duur `None` → geen notificatie: backend Step 4 + frontend `!= null`-gate; getest in Task 1, Step 1 (`test_stop_without_prompt_yields_none`). ✅
- Randgeval error/narrative ongemoeid → die takken niet aangeraakt. ✅

**Placeholder scan:** geen TBD/TODO; alle code-stappen bevatten volledige code. ✅

**Type consistency:** `last_turn_duration_s` (snake_case backend/JSON, zelfde naam frontend-veld), `_compute_turn_duration(session_id, now, db)` overal identiek aangeroepen, `turn_duration_s` doorgegeven aan `_to_response(session, turn_duration_s)`. ✅
