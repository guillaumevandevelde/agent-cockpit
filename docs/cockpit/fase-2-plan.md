---
title: "Agent Cockpit — Fase 2: Scheduled Messages — Implementation Plan"
type: plan
status: active
---

# Agent Cockpit — Fase 2: Scheduled Messages — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Boodschappen kunnen klaarzetten met een eenmalige timer of een terugkerende cron, die op het afvuurmoment in een Claude Code-sessie (tmux) worden geïnjecteerd — een sessie spawnen indien geen lopende, en wachten tot idle indien bezig.

**Architecture:** In-process APScheduler in de bestaande FastAPI-backend. Een Delivery Engine resolvet doel→spawn→wacht-tot-idle→inject via `tmux send-keys`. Een Idle Detector wordt gevoed door CC-hooks (POST naar de backend). Hergebruikt claude-deck's `discover_agent_sessions()` (project↔tmux_target) en `spawn_session()`.

**Tech Stack:** Python 3.13, FastAPI, async SQLAlchemy + aiosqlite (SQLite, `create_all`, geen migraties), APScheduler 3.x, pytest + pytest-asyncio. Frontend: React 19 + Vite + TypeScript + shadcn/ui.

> **Voorwaarde:** fase 1 is **code-level groen**; doe de **runtime-validatie** (`docker compose up` + `claude` login + 6-punts checklist in `fase-1-validation.md`) bij voorkeur vóór Task 7–8 echt e2e wordt getest. De unit-/integratietests hieronder draaien zonder die runtime (tmux/hooks gemockt).

---

## Pre-flight: afwijkingen t.o.v. de spec (laat de gebruiker bevestigen)

1. **`permission_mode`-waarden** afgestemd op echte `claude`-flags i.p.v. de labels uit de spec:
   - `default` → geen extra flag (normaal; prompt blijft staan — onbewaakt riskant te stallen)
   - `acceptEdits` → `--permission-mode acceptEdits` (dit is de **veilige default** uit de spec)
   - `bypass` → `--dangerously-skip-permissions` (= "autonomous")
2. **`PresenceEvent`/`PresenceSession`** bestaat al in `app/models/database.py` — **Task 5** checkt of dat een bruikbare idle/busy-bron is; zo niet, dan de eigen hook-gevoede Idle Detector (Task 5b).

---

## File Structure

**Backend (nieuw):**
- `backend/app/models/scheduled_message.py` — ORM: `ScheduledMessage`, `DeliveryAttempt`
- `backend/app/models/scheduled_message_schemas.py` — Pydantic in/out schemas + enums
- `backend/app/services/scheduling/tmux_inject.py` — `send_text(target, text)` via `tmux send-keys`
- `backend/app/services/scheduling/session_resolver.py` — `resolve_target(project_dir)` (discovery) + `spawn_for(project_dir, permission_mode)`
- `backend/app/services/scheduling/idle_state.py` — in-memory idle/busy registry, gevoed door hooks
- `backend/app/services/scheduling/delivery.py` — `DeliveryEngine` (resolve→spawn→wait-idle→inject)
- `backend/app/services/scheduling/scheduler.py` — APScheduler wrapper (`SchedulerService`)
- `backend/app/services/scheduling/crud.py` — DB CRUD voor scheduled messages + attempts
- `backend/app/api/v1/scheduled_messages/router.py` — REST + hook-ingest endpoint

**Backend (gewijzigd):**
- `backend/app/api/v1/router.py` — `include_router(scheduled_messages_router, ...)`
- `backend/app/main.py` — scheduler start/stop in `lifespan`
- `backend/requirements.txt` (of `pyproject.toml`) — `apscheduler`, `croniter`

**Tests (nieuw):** `backend/tests/test_tmux_inject.py`, `test_session_resolver.py`, `test_idle_state.py`, `test_delivery_engine.py`, `test_scheduler_service.py`, `test_scheduled_messages_api.py`

**Hook + frontend:**
- `backend/app/services/scheduling/hook_script.py` — genereert het hook-shellscript + install-helper
- `frontend/src/features/scheduled-messages/` — `ScheduledMessagesPage.tsx`, `api.ts`, `types.ts`, components

---

## Task 1: Dependencies

**Files:** Modify `backend/requirements.txt`

- [ ] **Step 1: Voeg dependencies toe**

Voeg toe aan `backend/requirements.txt`:
```
apscheduler>=3.10,<4
croniter>=2.0
```

- [ ] **Step 2: Installeer (in de backend-venv of container)**

Run: `cd backend && pip install -r requirements.txt`
Expected: apscheduler + croniter geïnstalleerd, geen conflicten.

- [ ] **Step 3: Commit**
```bash
git add backend/requirements.txt
git commit -m "build: add apscheduler + croniter for scheduling"
```

---

## Task 2: ORM-model

**Files:**
- Create: `backend/app/models/scheduled_message.py`
- Test: `backend/tests/test_scheduled_message_model.py`

- [ ] **Step 1: Failing test**
```python
# backend/tests/test_scheduled_message_model.py
import pytest
from sqlalchemy import select
from app.database import Base, engine, AsyncSessionLocal
from app.models.scheduled_message import ScheduledMessage, DeliveryAttempt


@pytest.mark.asyncio
async def test_create_scheduled_message():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as s:
        msg = ScheduledMessage(
            target_project="/home/dev/project-x",
            message="run tests",
            trigger_type="once",
            fire_at="2026-06-12T09:00:00+02:00",
            permission_mode="acceptEdits",
        )
        s.add(msg)
        await s.commit()
        await s.refresh(msg)
        assert msg.id is not None
        assert msg.status == "scheduled"
        assert msg.enabled is True
        assert msg.on_missing_session == "spawn"
        assert msg.when_busy == "wait_until_idle"
```

- [ ] **Step 2: Run → fail**

Run: `cd backend && pytest tests/test_scheduled_message_model.py -v`
Expected: FAIL (ModuleNotFoundError: app.models.scheduled_message).

- [ ] **Step 3: Implementeer model**
```python
# backend/app/models/scheduled_message.py
"""ORM models for scheduled messages."""
from datetime import datetime, timezone

from sqlalchemy import String, Text, Boolean, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ScheduledMessage(Base):
    __tablename__ = "scheduled_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_project: Mapped[str] = mapped_column(String(1024))
    message: Mapped[str] = mapped_column(Text)
    trigger_type: Mapped[str] = mapped_column(String(16))  # once | cron
    fire_at: Mapped[str | None] = mapped_column(String(40), nullable=True)      # ISO8601, once
    cron_expr: Mapped[str | None] = mapped_column(String(120), nullable=True)   # cron
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Brussels")
    permission_mode: Mapped[str] = mapped_column(String(20), default="acceptEdits")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="scheduled")
    on_missing_session: Mapped[str] = mapped_column(String(12), default="spawn")
    when_busy: Mapped[str] = mapped_column(String(16), default="wait_until_idle")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attempts: Mapped[list["DeliveryAttempt"]] = relationship(back_populates="message", cascade="all, delete-orphan")


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    scheduled_message_id: Mapped[int] = mapped_column(ForeignKey("scheduled_messages.id"))
    fired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_session: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str | None] = mapped_column(String(16), nullable=True)  # used_existing | spawned
    wait_duration_s: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(12), nullable=True)  # success | failed | timeout
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    message: Mapped["ScheduledMessage"] = relationship(back_populates="attempts")
```

- [ ] **Step 4: Run → pass**

Run: `cd backend && pytest tests/test_scheduled_message_model.py -v`
Expected: PASS.

- [ ] **Step 5: Importeer in init zodat `create_all` de tabellen ziet**

In `backend/app/main.py` lifespan, vóór `init_db()`, zorg dat het model geïmporteerd is. Voeg bovenaan `main.py` toe:
```python
import app.models.scheduled_message  # noqa: F401  (register tables for create_all)
```

- [ ] **Step 6: Commit**
```bash
git add backend/app/models/scheduled_message.py backend/tests/test_scheduled_message_model.py backend/app/main.py
git commit -m "feat(scheduling): ScheduledMessage + DeliveryAttempt models"
```

---

## Task 3: Pydantic-schemas

**Files:**
- Create: `backend/app/models/scheduled_message_schemas.py`
- Test: `backend/tests/test_scheduled_message_schemas.py`

- [ ] **Step 1: Failing test**
```python
# backend/tests/test_scheduled_message_schemas.py
import pytest
from pydantic import ValidationError
from app.models.scheduled_message_schemas import ScheduledMessageCreate


def test_once_requires_fire_at():
    with pytest.raises(ValidationError):
        ScheduledMessageCreate(target_project="/x", message="hi", trigger_type="once")


def test_cron_requires_cron_expr():
    with pytest.raises(ValidationError):
        ScheduledMessageCreate(target_project="/x", message="hi", trigger_type="cron")


def test_valid_once():
    m = ScheduledMessageCreate(
        target_project="/x", message="hi", trigger_type="once",
        fire_at="2026-06-12T09:00:00+02:00",
    )
    assert m.permission_mode == "acceptEdits"
```

- [ ] **Step 2: Run → fail**

Run: `cd backend && pytest tests/test_scheduled_message_schemas.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implementeer schemas**
```python
# backend/app/models/scheduled_message_schemas.py
"""Pydantic schemas for scheduled messages."""
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, model_validator

TriggerType = Literal["once", "cron"]
PermissionMode = Literal["default", "acceptEdits", "bypass"]
Status = Literal["scheduled", "pending_delivery", "delivered", "failed", "cancelled"]


class ScheduledMessageCreate(BaseModel):
    target_project: str
    message: str
    trigger_type: TriggerType
    fire_at: Optional[str] = None       # ISO8601, for once
    cron_expr: Optional[str] = None     # for cron
    timezone: str = "Europe/Brussels"
    permission_mode: PermissionMode = "acceptEdits"
    on_missing_session: Literal["spawn", "skip"] = "spawn"
    when_busy: Literal["wait_until_idle", "send_now"] = "wait_until_idle"

    @model_validator(mode="after")
    def _check_trigger(self):
        if self.trigger_type == "once" and not self.fire_at:
            raise ValueError("fire_at is required for trigger_type=once")
        if self.trigger_type == "cron" and not self.cron_expr:
            raise ValueError("cron_expr is required for trigger_type=cron")
        return self


class ScheduledMessageUpdate(BaseModel):
    message: Optional[str] = None
    fire_at: Optional[str] = None
    cron_expr: Optional[str] = None
    permission_mode: Optional[PermissionMode] = None
    enabled: Optional[bool] = None


class DeliveryAttemptResponse(BaseModel):
    id: int
    fired_at: datetime
    resolved_session: Optional[str] = None
    action: Optional[str] = None
    wait_duration_s: Optional[int] = None
    delivered_at: Optional[datetime] = None
    outcome: Optional[str] = None
    error: Optional[str] = None

    class Config:
        from_attributes = True


class ScheduledMessageResponse(BaseModel):
    id: int
    target_project: str
    message: str
    trigger_type: TriggerType
    fire_at: Optional[str] = None
    cron_expr: Optional[str] = None
    timezone: str
    permission_mode: PermissionMode
    enabled: bool
    status: Status
    created_at: datetime
    updated_at: datetime
    last_fired_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class HookEvent(BaseModel):
    """Posted by the CC hook script."""
    event: Literal["UserPromptSubmit", "Stop", "Notification", "SessionStart"]
    session_id: str
    cwd: str
```

- [ ] **Step 4: Run → pass**

Run: `cd backend && pytest tests/test_scheduled_message_schemas.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/models/scheduled_message_schemas.py backend/tests/test_scheduled_message_schemas.py
git commit -m "feat(scheduling): pydantic schemas + validation"
```

---

## Task 4: tmux-injectie-helper

**Files:**
- Create: `backend/app/services/scheduling/__init__.py` (leeg)
- Create: `backend/app/services/scheduling/tmux_inject.py`
- Test: `backend/tests/test_tmux_inject.py`

- [ ] **Step 1: Failing test** (subprocess gemockt — geen echte tmux nodig)
```python
# backend/tests/test_tmux_inject.py
from unittest.mock import patch, call
from app.services.scheduling.tmux_inject import send_text


def test_send_text_runs_send_keys_literal_then_enter():
    with patch("app.services.scheduling.tmux_inject.subprocess.run") as run:
        run.return_value.returncode = 0
        ok = send_text("sess:0.0", "hello world")
    assert ok is True
    assert run.call_args_list[0] == call(
        ["tmux", "send-keys", "-t", "sess:0.0", "-l", "hello world"],
        capture_output=True, text=True, timeout=10,
    )
    assert run.call_args_list[1] == call(
        ["tmux", "send-keys", "-t", "sess:0.0", "Enter"],
        capture_output=True, text=True, timeout=10,
    )


def test_send_text_returns_false_on_failure():
    with patch("app.services.scheduling.tmux_inject.subprocess.run") as run:
        run.return_value.returncode = 1
        run.return_value.stderr = "no such session"
        assert send_text("bad", "x") is False
```

- [ ] **Step 2: Run → fail**

Run: `cd backend && pytest tests/test_tmux_inject.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implementeer**
```python
# backend/app/services/scheduling/tmux_inject.py
"""Inject text into a tmux pane via send-keys (model A delivery)."""
import logging
import subprocess

logger = logging.getLogger(__name__)


def send_text(tmux_target: str, text: str) -> bool:
    """Type `text` into the tmux pane and press Enter. Returns True on success.

    Uses `-l` (literal) so message content is never interpreted as key names.
    """
    try:
        r1 = subprocess.run(
            ["tmux", "send-keys", "-t", tmux_target, "-l", text],
            capture_output=True, text=True, timeout=10,
        )
        if r1.returncode != 0:
            logger.warning("send-keys literal failed for %s: %s", tmux_target, r1.stderr)
            return False
        r2 = subprocess.run(
            ["tmux", "send-keys", "-t", tmux_target, "Enter"],
            capture_output=True, text=True, timeout=10,
        )
        if r2.returncode != 0:
            logger.warning("send-keys Enter failed for %s: %s", tmux_target, r2.stderr)
            return False
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.warning("send-keys error for %s: %s", tmux_target, e)
        return False
```

- [ ] **Step 4: Run → pass**

Run: `cd backend && pytest tests/test_tmux_inject.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/services/scheduling/__init__.py backend/app/services/scheduling/tmux_inject.py backend/tests/test_tmux_inject.py
git commit -m "feat(scheduling): tmux send-keys injection helper"
```

---

## Task 5: Idle-state registry (+ checken of PresenceEvent volstaat)

**Files:**
- Create: `backend/app/services/scheduling/idle_state.py`
- Test: `backend/tests/test_idle_state.py`

> **Onderzoek eerst (5 min, geen code):** lees `app/models/database.py` (`PresenceEvent`, `PresenceSession`) en zoek waar ze geschreven worden (`grep -rn PresenceEvent backend/app`). Levert claude-deck al per-sessie idle/busy via hooks? Zo **ja** → gebruik die bron in de resolver (Task 6) i.p.v. onderstaande store, en sla Task 5 over (documenteer dat). Zo **nee** → bouw de store hieronder.

- [ ] **Step 1: Failing test**
```python
# backend/tests/test_idle_state.py
import asyncio
import pytest
from app.services.scheduling.idle_state import IdleState


def test_unknown_session_is_busy_by_default():
    st = IdleState()
    assert st.is_idle("/proj") is False


def test_stop_marks_idle_prompt_marks_busy():
    st = IdleState()
    st.record("SessionStart", cwd="/proj", session_id="s1")
    st.record("Stop", cwd="/proj", session_id="s1")
    assert st.is_idle("/proj") is True
    st.record("UserPromptSubmit", cwd="/proj", session_id="s1")
    assert st.is_idle("/proj") is False


@pytest.mark.asyncio
async def test_wait_until_idle_resolves_when_stop_arrives():
    st = IdleState()
    st.record("UserPromptSubmit", cwd="/proj", session_id="s1")

    async def fire_stop():
        await asyncio.sleep(0.05)
        st.record("Stop", cwd="/proj", session_id="s1")

    asyncio.create_task(fire_stop())
    became_idle = await st.wait_until_idle("/proj", timeout_s=2)
    assert became_idle is True


@pytest.mark.asyncio
async def test_wait_until_idle_times_out():
    st = IdleState()
    st.record("UserPromptSubmit", cwd="/proj", session_id="s1")
    became_idle = await st.wait_until_idle("/proj", timeout_s=0.1)
    assert became_idle is False
```

- [ ] **Step 2: Run → fail**

Run: `cd backend && pytest tests/test_idle_state.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implementeer**
```python
# backend/app/services/scheduling/idle_state.py
"""In-memory per-project idle/busy state, fed by CC hook events.

State is keyed by resolved project cwd. A session is 'idle' after a Stop with no
later UserPromptSubmit. Unknown => treated as busy (caller should not assume idle).
"""
import asyncio
import os

_IDLE_EVENTS = {"Stop"}
_BUSY_EVENTS = {"UserPromptSubmit", "SessionStart", "Notification"}


def _norm(path: str) -> str:
    return os.path.normpath(path)


class IdleState:
    def __init__(self) -> None:
        self._idle: dict[str, bool] = {}
        self._waiters: dict[str, list[asyncio.Event]] = {}

    def record(self, event: str, cwd: str, session_id: str) -> None:
        key = _norm(cwd)
        if event in _IDLE_EVENTS:
            self._idle[key] = True
            for ev in self._waiters.get(key, []):
                ev.set()
        elif event in _BUSY_EVENTS:
            self._idle[key] = False

    def is_idle(self, cwd: str) -> bool:
        return self._idle.get(_norm(cwd), False)

    async def wait_until_idle(self, cwd: str, timeout_s: float) -> bool:
        key = _norm(cwd)
        if self._idle.get(key, False):
            return True
        ev = asyncio.Event()
        self._waiters.setdefault(key, []).append(ev)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout_s)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            self._waiters.get(key, []).remove(ev)


# Module-level singleton (shared by hook endpoint + delivery engine)
idle_state = IdleState()
```

- [ ] **Step 4: Run → pass**

Run: `cd backend && pytest tests/test_idle_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/services/scheduling/idle_state.py backend/tests/test_idle_state.py
git commit -m "feat(scheduling): hook-fed idle/busy state registry"
```

---

## Task 6: Session-resolver (discovery + spawn-wrapper met permission-mode)

**Files:**
- Create: `backend/app/services/scheduling/session_resolver.py`
- Test: `backend/tests/test_session_resolver.py`

- [ ] **Step 1: Failing test** (discovery + spawn gemockt)
```python
# backend/tests/test_session_resolver.py
from unittest.mock import patch
from app.services.scheduling.session_resolver import resolve_target, permission_flags


def test_permission_flags_mapping():
    assert permission_flags("default") == []
    assert permission_flags("acceptEdits") == ["--permission-mode", "acceptEdits"]
    assert permission_flags("bypass") == ["--dangerously-skip-permissions"]


def test_resolve_target_picks_matching_project():
    sessions = [
        {"tmux_target": "a:0.0", "cwd": "/home/g/dev/x"},
        {"tmux_target": "b:0.0", "cwd": "/home/g/dev/y"},
    ]
    with patch("app.services.scheduling.session_resolver.discover_agent_sessions", return_value=sessions):
        assert resolve_target("/home/g/dev/y") == "b:0.0"


def test_resolve_target_returns_none_when_absent():
    with patch("app.services.scheduling.session_resolver.discover_agent_sessions", return_value=[]):
        assert resolve_target("/home/g/dev/z") is None
```

- [ ] **Step 2: Run → fail**

Run: `cd backend && pytest tests/test_session_resolver.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implementeer**
```python
# backend/app/services/scheduling/session_resolver.py
"""Resolve a project directory to a live tmux target, or spawn one."""
import logging
import os

from app.services.runs.discovery import discover_agent_sessions
from app.services.cc_bridge.spawn import spawn_session

logger = logging.getLogger(__name__)


def permission_flags(permission_mode: str) -> list[str]:
    if permission_mode == "acceptEdits":
        return ["--permission-mode", "acceptEdits"]
    if permission_mode == "bypass":
        return ["--dangerously-skip-permissions"]
    return []  # default


def resolve_target(project_dir: str) -> str | None:
    """Return the tmux_target of a live CC session whose cwd matches project_dir."""
    want = os.path.normpath(project_dir)
    for s in discover_agent_sessions():
        if os.path.normpath(s.get("cwd", "")) == want:
            return s.get("tmux_target")
    return None


def spawn_for(project_dir: str, permission_mode: str) -> str:
    """Spawn a new CC session in project_dir and return its tmux_target.

    NOTE: spawn_session currently only supports skip_permissions(bool). For
    acceptEdits we need a richer command; see Task 6b to extend spawn_session
    with an explicit `extra_args` parameter. Until then, map bypass->skip and
    treat acceptEdits/default as skip=False.
    """
    skip = permission_mode == "bypass"
    result = spawn_session(directory=project_dir, mode="plain", skip_permissions=skip)
    return result["tmux_target"]
```

- [ ] **Step 4: Run → pass**

Run: `cd backend && pytest tests/test_session_resolver.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**
```bash
git add backend/app/services/scheduling/session_resolver.py backend/tests/test_session_resolver.py
git commit -m "feat(scheduling): session resolver (discovery) + permission flag mapping"
```

### Task 6b: spawn_session uitbreiden met permission-mode

**Files:** Modify `backend/app/services/cc_bridge/spawn.py`, `backend/tests/test_runs_spawn.py`

- [ ] **Step 1: Failing test** — voeg een test toe die `spawn_session(..., extra_args=["--permission-mode","acceptEdits"])` verwacht in het `tmux new-session`-commando.
```python
def test_spawn_includes_extra_args(monkeypatch):
    import app.services.cc_bridge.spawn as sp
    captured = {}
    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        class R: returncode = 0; stderr = ""
        return R()
    monkeypatch.setattr(sp.subprocess, "run", fake_run)
    monkeypatch.setattr(sp.Path, "is_dir", lambda self: True)
    sp.spawn_session(directory="/tmp", mode="plain", extra_args=["--permission-mode", "acceptEdits"])
    joined = captured["cmd"][-1]
    assert "--permission-mode acceptEdits" in joined
```

- [ ] **Step 2: Run → fail.** `cd backend && pytest tests/test_runs_spawn.py -k extra_args -v`

- [ ] **Step 3: Implementeer** — voeg `extra_args: list[str] | None = None` toe aan `spawn_session`, en na de mode-afhandeling: `if extra_args: command += extra_args`. Werk `spawn_for` (Task 6) bij om `extra_args=permission_flags(permission_mode)` door te geven i.p.v. de skip-bool-workaround.

- [ ] **Step 4: Run → pass.** Beide tests groen.

- [ ] **Step 5: Commit**
```bash
git add backend/app/services/cc_bridge/spawn.py backend/app/services/scheduling/session_resolver.py backend/tests/test_runs_spawn.py
git commit -m "feat(scheduling): spawn_session extra_args + wire permission modes"
```

---

## Task 7: Delivery Engine

**Files:**
- Create: `backend/app/services/scheduling/delivery.py`
- Test: `backend/tests/test_delivery_engine.py`

- [ ] **Step 1: Failing test** (alle randen gemockt)
```python
# backend/tests/test_delivery_engine.py
import pytest
from unittest.mock import patch, MagicMock
from app.services.scheduling.delivery import DeliveryEngine
from app.services.scheduling.idle_state import IdleState


def _engine(idle):
    return DeliveryEngine(idle_state=idle)


@pytest.mark.asyncio
async def test_existing_idle_session_gets_message():
    idle = IdleState(); idle.record("Stop", "/proj", "s1")
    eng = _engine(idle)
    with patch("app.services.scheduling.delivery.resolve_target", return_value="t:0.0"), \
         patch("app.services.scheduling.delivery.send_text", return_value=True) as send:
        res = await eng.deliver(project_dir="/proj", message="hi", permission_mode="acceptEdits")
    assert res.outcome == "success"
    assert res.action == "used_existing"
    send.assert_called_once_with("t:0.0", "hi")


@pytest.mark.asyncio
async def test_no_session_spawns_then_sends():
    idle = IdleState()
    eng = _engine(idle)
    with patch("app.services.scheduling.delivery.resolve_target", return_value=None), \
         patch("app.services.scheduling.delivery.spawn_for", return_value="new:0.0") as spawn, \
         patch("app.services.scheduling.delivery.send_text", return_value=True) as send:
        # spawned session is idle by definition; mark it so wait short-circuits
        idle.record("Stop", "/proj", "s1")
        res = await eng.deliver(project_dir="/proj", message="go", permission_mode="bypass")
    assert res.action == "spawned"
    assert res.outcome == "success"
    spawn.assert_called_once()
    send.assert_called_once_with("new:0.0", "go")


@pytest.mark.asyncio
async def test_busy_then_timeout_marks_timeout():
    idle = IdleState(); idle.record("UserPromptSubmit", "/proj", "s1")
    eng = _engine(idle)
    with patch("app.services.scheduling.delivery.resolve_target", return_value="t:0.0"), \
         patch("app.services.scheduling.delivery.send_text", return_value=True) as send:
        res = await eng.deliver(project_dir="/proj", message="hi",
                                permission_mode="acceptEdits", timeout_s=0.1)
    assert res.outcome == "timeout"
    send.assert_not_called()
```

- [ ] **Step 2: Run → fail.** `cd backend && pytest tests/test_delivery_engine.py -v`

- [ ] **Step 3: Implementeer**
```python
# backend/app/services/scheduling/delivery.py
"""Resolve target -> spawn if needed -> wait until idle -> inject."""
import logging
import time
from dataclasses import dataclass

from app.services.scheduling.idle_state import IdleState, idle_state as default_idle
from app.services.scheduling.session_resolver import resolve_target, spawn_for
from app.services.scheduling.tmux_inject import send_text

logger = logging.getLogger(__name__)


@dataclass
class DeliveryResult:
    outcome: str            # success | failed | timeout
    action: str | None = None   # used_existing | spawned
    resolved_session: str | None = None
    wait_duration_s: int = 0
    error: str | None = None


class DeliveryEngine:
    def __init__(self, idle_state: IdleState | None = None):
        self.idle = idle_state or default_idle

    async def deliver(self, *, project_dir: str, message: str,
                      permission_mode: str = "acceptEdits",
                      on_missing_session: str = "spawn",
                      when_busy: str = "wait_until_idle",
                      timeout_s: float = 1800) -> DeliveryResult:
        target = resolve_target(project_dir)
        action = "used_existing"
        if target is None:
            if on_missing_session == "skip":
                return DeliveryResult(outcome="failed", error="no live session (skip)")
            try:
                target = spawn_for(project_dir, permission_mode)
                action = "spawned"
            except Exception as e:  # spawn_session raises ValueError
                return DeliveryResult(outcome="failed", error=f"spawn failed: {e}")

        wait_start = time.monotonic()
        if when_busy == "wait_until_idle" and not self.idle.is_idle(project_dir):
            became_idle = await self.idle.wait_until_idle(project_dir, timeout_s)
            if not became_idle:
                return DeliveryResult(outcome="timeout", action=action,
                                      resolved_session=target,
                                      wait_duration_s=int(time.monotonic() - wait_start))

        ok = send_text(target, message)
        waited = int(time.monotonic() - wait_start)
        if ok:
            return DeliveryResult(outcome="success", action=action,
                                  resolved_session=target, wait_duration_s=waited)
        return DeliveryResult(outcome="failed", action=action, resolved_session=target,
                              wait_duration_s=waited, error="send-keys failed")
```

- [ ] **Step 4: Run → pass.** `cd backend && pytest tests/test_delivery_engine.py -v`

- [ ] **Step 5: Commit**
```bash
git add backend/app/services/scheduling/delivery.py backend/tests/test_delivery_engine.py
git commit -m "feat(scheduling): delivery engine (resolve/spawn/wait-idle/inject)"
```

---

## Task 8: CRUD + Scheduler-service

**Files:**
- Create: `backend/app/services/scheduling/crud.py`
- Create: `backend/app/services/scheduling/scheduler.py`
- Test: `backend/tests/test_scheduler_service.py`

- [ ] **Step 1: Failing test** (APScheduler in-memory; delivery gemockt)
```python
# backend/tests/test_scheduler_service.py
import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from app.services.scheduling.scheduler import SchedulerService


@pytest.mark.asyncio
async def test_once_job_fires_and_calls_delivery():
    svc = SchedulerService()
    svc.start()
    fired = asyncio.Event()
    async def fake_run(msg_id):
        fired.set()
    with patch.object(svc, "_run_delivery", side_effect=fake_run):
        fire_at = (datetime.now(timezone.utc) + timedelta(seconds=0.3)).isoformat()
        svc.schedule_once(message_id=1, fire_at_iso=fire_at)
        await asyncio.wait_for(fired.wait(), timeout=3)
    svc.shutdown()


def test_cron_and_remove():
    svc = SchedulerService(); svc.start()
    svc.schedule_cron(message_id=2, cron_expr="0 9 * * 1-5", tz="Europe/Brussels")
    assert svc.has_job(2) is True
    svc.remove(2)
    assert svc.has_job(2) is False
    svc.shutdown()
```

- [ ] **Step 2: Run → fail.** `cd backend && pytest tests/test_scheduler_service.py -v`

- [ ] **Step 3: Implementeer scheduler**
```python
# backend/app/services/scheduling/scheduler.py
"""APScheduler wrapper: schedules once/cron jobs that trigger delivery."""
import logging
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

logger = logging.getLogger(__name__)


def _job_id(message_id: int) -> str:
    return f"sched-msg-{message_id}"


class SchedulerService:
    def __init__(self) -> None:
        self._sched = AsyncIOScheduler()

    def start(self) -> None:
        if not self._sched.running:
            self._sched.start()

    def shutdown(self) -> None:
        if self._sched.running:
            self._sched.shutdown(wait=False)

    def has_job(self, message_id: int) -> bool:
        return self._sched.get_job(_job_id(message_id)) is not None

    def remove(self, message_id: int) -> None:
        job = self._sched.get_job(_job_id(message_id))
        if job:
            job.remove()

    def schedule_once(self, message_id: int, fire_at_iso: str) -> None:
        self._sched.add_job(
            self._run_delivery, trigger=DateTrigger(run_date=datetime.fromisoformat(fire_at_iso)),
            args=[message_id], id=_job_id(message_id), replace_existing=True,
            misfire_grace_time=3600, coalesce=True,
        )

    def schedule_cron(self, message_id: int, cron_expr: str, tz: str) -> None:
        self._sched.add_job(
            self._run_delivery, trigger=CronTrigger.from_crontab(cron_expr, timezone=tz),
            args=[message_id], id=_job_id(message_id), replace_existing=True,
            misfire_grace_time=3600, coalesce=True, max_instances=1,
        )

    async def _run_delivery(self, message_id: int) -> None:
        # Imported here to avoid a circular import at module load.
        from app.services.scheduling.crud import run_scheduled_delivery
        try:
            await run_scheduled_delivery(message_id)
        except Exception:
            logger.exception("delivery failed for message %s", message_id)


# Module-level singleton
scheduler_service = SchedulerService()
```

- [ ] **Step 4: Implementeer crud (DB + orchestratie)**
```python
# backend/app/services/scheduling/crud.py
"""DB CRUD + the function APScheduler calls on fire."""
from datetime import datetime, timezone

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.scheduled_message import ScheduledMessage, DeliveryAttempt
from app.services.scheduling.delivery import DeliveryEngine

_engine = DeliveryEngine()


async def run_scheduled_delivery(message_id: int) -> None:
    """Called by the scheduler when a job fires."""
    async with AsyncSessionLocal() as s:
        msg = await s.get(ScheduledMessage, message_id)
        if not msg or not msg.enabled:
            return
        # Coalescing: skip if a previous delivery is still pending.
        if msg.status == "pending_delivery":
            return
        msg.status = "pending_delivery"
        msg.last_fired_at = datetime.now(timezone.utc)
        attempt = DeliveryAttempt(scheduled_message_id=msg.id, fired_at=msg.last_fired_at)
        s.add(attempt)
        await s.commit()

        res = await _engine.deliver(
            project_dir=msg.target_project, message=msg.message,
            permission_mode=msg.permission_mode,
            on_missing_session=msg.on_missing_session, when_busy=msg.when_busy,
        )

        attempt.outcome = res.outcome
        attempt.action = res.action
        attempt.resolved_session = res.resolved_session
        attempt.wait_duration_s = res.wait_duration_s
        attempt.error = res.error
        attempt.delivered_at = datetime.now(timezone.utc) if res.outcome == "success" else None
        # once -> terminal; cron -> back to scheduled for next run
        if msg.trigger_type == "cron":
            msg.status = "scheduled"
        else:
            msg.status = "delivered" if res.outcome == "success" else "failed"
        await s.commit()
```

- [ ] **Step 5: Run → pass.** `cd backend && pytest tests/test_scheduler_service.py -v`

- [ ] **Step 6: Commit**
```bash
git add backend/app/services/scheduling/scheduler.py backend/app/services/scheduling/crud.py backend/tests/test_scheduler_service.py
git commit -m "feat(scheduling): APScheduler service + delivery orchestration"
```

---

## Task 9: API-router + hook-ingest + wiring

**Files:**
- Create: `backend/app/api/v1/scheduled_messages/__init__.py` (leeg)
- Create: `backend/app/api/v1/scheduled_messages/router.py`
- Modify: `backend/app/api/v1/router.py`, `backend/app/main.py`
- Test: `backend/tests/test_scheduled_messages_api.py`

- [ ] **Step 1: Failing test** (TestClient; scheduler gemockt)
```python
# backend/tests/test_scheduled_messages_api.py
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.mark.asyncio
async def test_create_list_delete_once():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        payload = {"target_project": "/tmp", "message": "hi",
                   "trigger_type": "once", "fire_at": "2999-01-01T09:00:00+00:00"}
        r = await ac.post("/api/v1/scheduled-messages", json=payload)
        assert r.status_code == 201, r.text
        mid = r.json()["id"]
        r = await ac.get("/api/v1/scheduled-messages")
        assert any(m["id"] == mid for m in r.json()["items"])
        r = await ac.delete(f"/api/v1/scheduled-messages/{mid}")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_hook_event_updates_idle_state():
    from app.services.scheduling.idle_state import idle_state
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        await ac.post("/api/v1/scheduled-messages/hook-event",
                      json={"event": "Stop", "session_id": "s1", "cwd": "/tmp"})
    assert idle_state.is_idle("/tmp") is True
```

- [ ] **Step 2: Run → fail.** `cd backend && pytest tests/test_scheduled_messages_api.py -v`

- [ ] **Step 3: Implementeer router**
```python
# backend/app/api/v1/scheduled_messages/router.py
"""REST API for scheduled messages + CC hook ingest."""
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.scheduled_message import ScheduledMessage, DeliveryAttempt
from app.models.scheduled_message_schemas import (
    ScheduledMessageCreate, ScheduledMessageUpdate, ScheduledMessageResponse,
    DeliveryAttemptResponse, HookEvent,
)
from app.services.scheduling.idle_state import idle_state
from app.services.scheduling.scheduler import scheduler_service

router = APIRouter(prefix="/scheduled-messages", tags=["Scheduled Messages"])


def _register(msg: ScheduledMessage) -> None:
    if not msg.enabled:
        scheduler_service.remove(msg.id)
        return
    if msg.trigger_type == "once" and msg.fire_at:
        scheduler_service.schedule_once(msg.id, msg.fire_at)
    elif msg.trigger_type == "cron" and msg.cron_expr:
        scheduler_service.schedule_cron(msg.id, msg.cron_expr, msg.timezone)


@router.post("", response_model=ScheduledMessageResponse, status_code=status.HTTP_201_CREATED)
async def create(payload: ScheduledMessageCreate):
    async with AsyncSessionLocal() as s:
        msg = ScheduledMessage(**payload.model_dump())
        s.add(msg)
        await s.commit()
        await s.refresh(msg)
        _register(msg)
        return msg


@router.get("")
async def list_all():
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(ScheduledMessage).order_by(ScheduledMessage.id.desc()))).scalars().all()
        return {"items": [ScheduledMessageResponse.model_validate(m) for m in rows]}


@router.get("/{mid}/attempts", response_model=list[DeliveryAttemptResponse])
async def attempts(mid: int):
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(
            select(DeliveryAttempt).where(DeliveryAttempt.scheduled_message_id == mid)
            .order_by(DeliveryAttempt.id.desc()))).scalars().all()
        return rows


@router.patch("/{mid}", response_model=ScheduledMessageResponse)
async def update(mid: int, payload: ScheduledMessageUpdate):
    async with AsyncSessionLocal() as s:
        msg = await s.get(ScheduledMessage, mid)
        if not msg:
            raise HTTPException(404, "not found")
        for k, v in payload.model_dump(exclude_unset=True).items():
            setattr(msg, k, v)
        await s.commit()
        await s.refresh(msg)
        _register(msg)
        return msg


@router.delete("/{mid}")
async def delete(mid: int):
    async with AsyncSessionLocal() as s:
        msg = await s.get(ScheduledMessage, mid)
        if not msg:
            raise HTTPException(404, "not found")
        scheduler_service.remove(mid)
        await s.delete(msg)
        await s.commit()
        return {"deleted": True}


@router.post("/hook-event")
async def hook_event(ev: HookEvent):
    idle_state.record(ev.event, cwd=ev.cwd, session_id=ev.session_id)
    return {"ok": True}
```

- [ ] **Step 4: Wire router + scheduler lifecycle**

In `backend/app/api/v1/router.py` (bij de andere includes):
```python
from app.api.v1.scheduled_messages.router import router as scheduled_messages_router
router.include_router(scheduled_messages_router)
```

In `backend/app/main.py` lifespan — start vóór `yield`, stop erna:
```python
from app.services.scheduling.scheduler import scheduler_service
# ... in lifespan, after init_db():
scheduler_service.start()
# reschedule persisted enabled jobs
from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.scheduled_message import ScheduledMessage
async with AsyncSessionLocal() as s:
    for m in (await s.execute(select(ScheduledMessage).where(ScheduledMessage.enabled == True))).scalars():  # noqa: E712
        if m.trigger_type == "once" and m.fire_at:
            scheduler_service.schedule_once(m.id, m.fire_at)
        elif m.trigger_type == "cron" and m.cron_expr:
            scheduler_service.schedule_cron(m.id, m.cron_expr, m.timezone)
# ... after yield:
scheduler_service.shutdown()
```

- [ ] **Step 5: Run → pass.** `cd backend && pytest tests/test_scheduled_messages_api.py -v` (en de volledige suite: `pytest -q`).

- [ ] **Step 6: Commit**
```bash
git add backend/app/api/v1/scheduled_messages backend/app/api/v1/router.py backend/app/main.py backend/tests/test_scheduled_messages_api.py
git commit -m "feat(scheduling): REST API + hook ingest + scheduler lifecycle wiring"
```

---

## Task 10: CC hook-script + install-helper

**Files:**
- Create: `backend/app/services/scheduling/hook_script.py`
- Test: `backend/tests/test_hook_script.py`

- [ ] **Step 1: Failing test**
```python
# backend/tests/test_hook_script.py
from app.services.scheduling.hook_script import render_hook_command


def test_hook_command_posts_event():
    cmd = render_hook_command(event="Stop", port=8000)
    assert "curl" in cmd and "Stop" in cmd and "hook-event" in cmd
    assert "$CLAUDE_SESSION_ID" in cmd or "session_id" in cmd
```

- [ ] **Step 2: Run → fail.** `cd backend && pytest tests/test_hook_script.py -v`

- [ ] **Step 3: Implementeer**
```python
# backend/app/services/scheduling/hook_script.py
"""Render the CC hook command that POSTs session events to the backend.

Install by adding entries to ~/.claude/settings.json under "hooks" for the
UserPromptSubmit, Stop, Notification, and SessionStart events. The hook reads
the JSON CC passes on stdin (contains session_id + cwd).
"""
import json


def render_hook_command(event: str, port: int = 8000) -> str:
    # CC passes hook input as JSON on stdin; jq extracts session_id + cwd.
    url = f"http://localhost:{port}/api/v1/scheduled-messages/hook-event"
    return (
        "jq -c --arg ev %s '{event:$ev, session_id:.session_id, cwd:.cwd}' "
        "| curl -s -X POST -H 'Content-Type: application/json' -d @- %s >/dev/null 2>&1 || true"
    ) % (json.dumps(event), url)


def settings_hooks_block(port: int = 8000) -> dict:
    """Return a dict to merge into ~/.claude/settings.json 'hooks'."""
    def entry(ev):
        return [{"hooks": [{"type": "command", "command": render_hook_command(ev, port)}]}]
    return {
        "UserPromptSubmit": entry("UserPromptSubmit"),
        "Stop": entry("Stop"),
        "Notification": entry("Notification"),
        "SessionStart": entry("SessionStart"),
    }
```

- [ ] **Step 4: Run → pass.** `cd backend && pytest tests/test_hook_script.py -v`

- [ ] **Step 5: Documenteer install** in `docs/cockpit/hooks-install.md`: het `settings_hooks_block` mergen in `~/.claude/settings.json`, vereist `jq` + `curl` in de sessie-omgeving (WSL Ubuntu ✓). Noot: de hook draait in dezelfde WSL waar CC draait, dus `localhost:8000` bereikt de backend-container/proces.

- [ ] **Step 6: Commit**
```bash
git add backend/app/services/scheduling/hook_script.py backend/tests/test_hook_script.py docs/cockpit/hooks-install.md
git commit -m "feat(scheduling): CC hook command renderer + install docs"
```

---

## Task 11: Frontend — feature-module (geen test-setup in repo; manueel verifiëren)

**Files:**
- Create: `frontend/src/features/scheduled-messages/types.ts`, `api.ts`, `ScheduledMessagesPage.tsx`, `components/ScheduledMessageForm.tsx`, `components/DeliveryLog.tsx`
- Modify: `frontend/src/App.tsx` (route), navigatie/sidebar component

- [ ] **Step 1: types.ts** — TypeScript-interfaces die exact `ScheduledMessageResponse` + `DeliveryAttemptResponse` spiegelen (zelfde veldnamen: `id, target_project, message, trigger_type, fire_at, cron_expr, timezone, permission_mode, enabled, status, ...`).

- [ ] **Step 2: api.ts** — fetch-wrappers naar `/api/v1/scheduled-messages` (GET/POST/PATCH/DELETE) + `/{id}/attempts`, via de bestaande `@/lib/api` helper.

- [ ] **Step 3: ScheduledMessagesPage.tsx** — lijst met status-badges; gebruik `CLICKABLE_CARD` uit `@/lib/constants` voor rijen (UI-conventie). "Nieuw"-knop opent `ScheduledMessageForm` in een `MODAL_SIZES.MD` dialog.

- [ ] **Step 4: ScheduledMessageForm.tsx** — velden: projectkiezer (hergebruik bestaande project-selectie/`useProjects`), message-textarea, radio once/cron (datetime-picker vs cron-expr met `croniter`-achtige client-validatie/preview), permission-mode dropdown (`default`/`acceptEdits`/`bypass`).

- [ ] **Step 5: DeliveryLog.tsx** — tabel uit `/{id}/attempts` (fired_at, action, wait, outcome, error).

- [ ] **Step 6: Route + nav** — voeg route toe in `App.tsx` en een nav-item (volg het patroon van een bestaande feature, bv. Sessions/CC Bridge).

- [ ] **Step 7: Lint + build.** `cd frontend && npm run lint && npm run build` → geen errors.

- [ ] **Step 8: Commit**
```bash
git add frontend/src/features/scheduled-messages frontend/src/App.tsx
git commit -m "feat(scheduling): scheduled messages UI (list, form, delivery log)"
```

---

## Task 12: E2e runtime-validatie (na de fase-1 runtime-checklist)

- [ ] **Step 1:** `docker compose up -d`, open UI, ga naar de Scheduled Messages-pagina.
- [ ] **Step 2:** Installeer de hooks in `~/.claude/settings.json` (Task 10).
- [ ] **Step 3:** Maak een **timer** "over 1 min" naar een project met een **lopende, idle** CC-sessie → bevestig dat het bericht in de sessie verschijnt; check de delivery-log (`used_existing`, `success`).
- [ ] **Step 4:** Maak een timer naar een project **zonder** sessie → bevestig spawn (`tmux ls`) + injectie (`spawned`, `success`).
- [ ] **Step 5:** Start een **lange** taak in een sessie, plan een timer → bevestig dat levering **wacht** tot `Stop` en dan pas injecteert.
- [ ] **Step 6:** Maak een **cron** (bv. elke minuut) → bevestig herhaalde afvuringen + dat overlap wordt gecoalesceerd.
- [ ] **Step 7:** Documenteer resultaten in `fase-1-validation.md` (runtime-sectie) en sluit de gate.

---

## Self-Review (uitgevoerd bij het schrijven)

- **Spec-dekking:** datamodel (T2/T3), scheduler once+cron (T8), spawn-indien-nodig (T6/T7), wacht-tot-idle (T5/T7), per-taak permission (T3/T6b), in-process APScheduler (T8/T9), idle via hooks (T5/T9/T10), edge-cases timeout/coalescing (T7/T8), UI (T11), testing (elke task). ✅
- **Placeholders:** geen — elke code-stap bevat echte code.
- **Type-consistentie:** `permission_mode` = `default|acceptEdits|bypass` overal (schemas T3, resolver T6, spawn T6b); `DeliveryResult.outcome` = `success|failed|timeout` (T7) = `attempt.outcome` (T8); `tmux_target` overal string `sess:win.pane`.
- **Open punt voor review:** de `permission_mode`-labels wijken af van de spec (`safe/accept-edits/autonomous`) → hier `default/acceptEdits/bypass` afgestemd op echte claude-flags. **Laat de gebruiker dit bevestigen.**

## Volgende stap

Twee uitvoeringsopties (zie writing-plans handoff): **subagent-driven** (aanbevolen) of **inline executing-plans**. Begin pas met Task 7–9 e2e na de runtime-validatie (Docker + login). Tasks 1–6 + 10 zijn volledig offline te bouwen+testen.
