# Scheduled Session Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a scheduled message target one specific Claude Code session (picked at schedule time) and, when it fires, inject the message into that session's live tmux pane or relaunch the session with `claude --resume <id>` and then inject — handling the "usage-limit stop" case where the pane may or may not have survived.

**Architecture:** Extends the existing fase-2 scheduled-messages engine. Adds session-level granularity on top of the current cwd-keyed engine: a new in-memory `SessionRegistry` (fed by the CC hook, which now also reports `$TMUX_PANE`) maps `session_id → pane_id` + per-session idle. The delivery engine gains a session branch: alive → wait-idle + inject into that pane; exited → `claude --resume <id>` (reusing `cc_bridge.spawn.spawn_session(mode="resume")`) + settle delay + inject. Backend stays Python/FastAPI/async SQLAlchemy + pytest; frontend React/TS (no FE test harness — verify via `npm run build` + lint).

**Tech Stack:** FastAPI, async SQLAlchemy + aiosqlite, APScheduler, pytest/pytest-asyncio (backend); React 19 + Vite + shadcn/ui (frontend); tmux for delivery.

**Spec:** `docs/superpowers/specs/2026-06-13-scheduled-session-resume-design.md`

---

## File Structure

**Backend — create:**
- `backend/app/services/scheduling/session_registry.py` — in-memory `session_id → pane_id` + per-session idle, fed by hooks.
- `backend/app/services/scheduling/schema_guard.py` — idempotent `ALTER TABLE` to add new columns without wiping the DB.
- `backend/tests/test_session_registry.py`
- `backend/tests/test_schema_guard.py`
- `backend/tests/test_session_resolver_session.py`
- `backend/tests/test_delivery_session.py`

**Backend — modify:**
- `backend/app/models/scheduled_message.py` — new columns on `ScheduledMessage`.
- `backend/app/models/scheduled_message_schemas.py` — new fields + validation; `HookEvent.tmux_pane`.
- `backend/app/services/scheduling/hook_script.py` — emit `tmux_pane`.
- `backend/app/services/scheduling/session_resolver.py` — `resolve_session_target`, `resume_spawn_for`, `AMBIGUOUS`.
- `backend/app/services/scheduling/delivery.py` — session branch.
- `backend/app/services/scheduling/crud.py` — pass new fields through.
- `backend/app/api/v1/scheduled_messages/router.py` — hook endpoint feeds the registry.
- `backend/app/main.py` — call `ensure_scheduled_message_columns` at startup.
- `backend/tests/test_scheduled_message_schemas.py`, `test_scheduled_messages_api.py`, `test_delivery_engine.py` — extend where noted.

**Frontend — modify:**
- `frontend/src/features/scheduled-messages/types.ts` — new fields + resumable-session types.
- `frontend/src/features/scheduled-messages/api.ts` — fetch resumable sessions.
- `frontend/src/features/scheduled-messages/components/ScheduledMessageForm.tsx` — target-type toggle + session picker.
- `frontend/src/features/scheduled-messages/ScheduledMessagesPage.tsx` — "resume" badge.
- `frontend/src/features/scheduled-messages/components/DeliveryLog.tsx` — render `resumed` action (no code change needed beyond confirming generic action rendering; verify in Task 12).

---

## Task 1: Data model columns

**Files:**
- Modify: `backend/app/models/scheduled_message.py:14-31`
- Test: `backend/tests/test_scheduled_message_model.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_scheduled_message_model.py`:

```python
@pytest.mark.asyncio
async def test_session_target_columns_default_to_project(db_session):
    from app.models.scheduled_message import ScheduledMessage
    msg = ScheduledMessage(
        target_project="/proj", message="hi", trigger_type="once", fire_at="2026-01-01T00:00:00",
    )
    db_session.add(msg)
    await db_session.commit()
    await db_session.refresh(msg)
    assert msg.target_kind == "project"
    assert msg.target_session_id is None
    assert msg.project_folder is None
    assert msg.session_preview is None
```

(Reuse the existing `db_session` fixture in that test file. If the file has no fixture import pattern, mirror the top of the existing test module.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_scheduled_message_model.py::test_session_target_columns_default_to_project -v`
Expected: FAIL — `AttributeError: ... 'target_kind'` or column missing.

- [ ] **Step 3: Add the columns**

In `backend/app/models/scheduled_message.py`, inside `class ScheduledMessage`, after the `when_busy` column (line ~28) add:

```python
    target_kind: Mapped[str] = mapped_column(String(16), default="project")  # project | session
    target_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    project_folder: Mapped[str | None] = mapped_column(String(255), nullable=True)
    session_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_scheduled_message_model.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/scheduled_message.py backend/tests/test_scheduled_message_model.py
git commit -m "feat(scheduling): add session-target columns to scheduled_messages"
```

---

## Task 2: Defensive column migration

**Files:**
- Create: `backend/app/services/scheduling/schema_guard.py`
- Modify: `backend/app/main.py:17-19` (lifespan, after `init_db`)
- Test: `backend/tests/test_schema_guard.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_schema_guard.py`:

```python
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from app.services.scheduling.schema_guard import ensure_scheduled_message_columns

NEW = {"target_kind", "target_session_id", "project_folder", "session_preview"}


@pytest.mark.asyncio
async def test_adds_missing_columns_idempotently(tmp_path):
    db = tmp_path / "old.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    # Simulate an old DB whose table predates the new columns.
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE scheduled_messages (id INTEGER PRIMARY KEY, message TEXT)"
        )
    await ensure_scheduled_message_columns(engine)
    await ensure_scheduled_message_columns(engine)  # second run must not error
    async with engine.begin() as conn:
        result = await conn.exec_driver_sql("PRAGMA table_info(scheduled_messages)")
        cols = {row[1] for row in result.fetchall()}
    await engine.dispose()
    assert NEW <= cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_schema_guard.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.scheduling.schema_guard`.

- [ ] **Step 3: Implement the guard**

Create `backend/app/services/scheduling/schema_guard.py`:

```python
"""Add new scheduled_messages columns in place, without dropping the DB.

The project has no migration framework (schema is created via create_all). When
we add columns to an existing install, we ALTER the table at startup so the
user's existing data survives. SQLite supports ADD COLUMN with a default.
"""
from sqlalchemy.ext.asyncio import AsyncEngine

_NEW_COLUMNS = {
    "target_kind": "VARCHAR(16) DEFAULT 'project'",
    "target_session_id": "VARCHAR(128)",
    "project_folder": "VARCHAR(255)",
    "session_preview": "TEXT",
}


async def ensure_scheduled_message_columns(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        result = await conn.exec_driver_sql("PRAGMA table_info(scheduled_messages)")
        existing = {row[1] for row in result.fetchall()}
        for column, ddl in _NEW_COLUMNS.items():
            if column not in existing:
                await conn.exec_driver_sql(
                    f"ALTER TABLE scheduled_messages ADD COLUMN {column} {ddl}"
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_schema_guard.py -v`
Expected: PASS.

- [ ] **Step 5: Wire into startup**

In `backend/app/main.py`, inside `lifespan`, immediately after `await init_db()` (line 18) add:

```python
    from app.services.scheduling.schema_guard import ensure_scheduled_message_columns
    from app.database import engine
    await ensure_scheduled_message_columns(engine)
```

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && source venv/bin/activate && pytest tests/ -q`
Expected: PASS (no regressions).

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/scheduling/schema_guard.py backend/tests/test_schema_guard.py backend/app/main.py
git commit -m "feat(scheduling): defensively add new columns on startup"
```

---

## Task 3: Schema fields + validation

**Files:**
- Modify: `backend/app/models/scheduled_message_schemas.py`
- Test: `backend/tests/test_scheduled_message_schemas.py`

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_scheduled_message_schemas.py`:

```python
import pytest
from pydantic import ValidationError
from app.models.scheduled_message_schemas import ScheduledMessageCreate


def test_session_target_requires_session_id_and_folder():
    with pytest.raises(ValidationError):
        ScheduledMessageCreate(
            target_project="/proj", message="hi", trigger_type="once",
            fire_at="2026-01-01T00:00:00", target_kind="session",
        )


def test_session_target_valid():
    m = ScheduledMessageCreate(
        target_project="/proj", message="hi", trigger_type="once",
        fire_at="2026-01-01T00:00:00", target_kind="session",
        target_session_id="abc-123", project_folder="-home-guillaume-proj",
    )
    assert m.target_kind == "session"
    assert m.target_session_id == "abc-123"


def test_project_target_defaults():
    m = ScheduledMessageCreate(
        target_project="/proj", message="hi", trigger_type="once",
        fire_at="2026-01-01T00:00:00",
    )
    assert m.target_kind == "project"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_scheduled_message_schemas.py -v`
Expected: FAIL — `target_kind` not a valid field / no validation.

- [ ] **Step 3: Implement schema changes**

In `backend/app/models/scheduled_message_schemas.py`:

Add a type alias near the top (after line 8):

```python
TargetKind = Literal["project", "session"]
```

In `ScheduledMessageCreate`, add fields (after `when_busy`, line ~20):

```python
    target_kind: TargetKind = "project"
    target_session_id: Optional[str] = None
    project_folder: Optional[str] = None
    session_preview: Optional[str] = None
```

Extend the existing `_check_trigger` validator (or add a second `model_validator(mode="after")`) with:

```python
        if self.target_kind == "session" and (
            not self.target_session_id or not self.project_folder
        ):
            raise ValueError(
                "target_session_id and project_folder are required for target_kind=session"
            )
        return self
```

In `ScheduledMessageResponse`, add (after `status`, line ~64):

```python
    target_kind: TargetKind = "project"
    target_session_id: Optional[str] = None
    project_folder: Optional[str] = None
    session_preview: Optional[str] = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_scheduled_message_schemas.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/scheduled_message_schemas.py backend/tests/test_scheduled_message_schemas.py
git commit -m "feat(scheduling): session-target schema fields + validation"
```

---

## Task 4: Hook reports tmux pane

**Files:**
- Modify: `backend/app/services/scheduling/hook_script.py:11-16`
- Modify: `backend/app/models/scheduled_message_schemas.py` (`HookEvent`)
- Test: `backend/tests/test_hook_script.py` (create if absent)

- [ ] **Step 1: Write the failing tests**

Create or extend `backend/tests/test_hook_script.py`:

```python
from app.services.scheduling.hook_script import render_hook_command
from app.models.scheduled_message_schemas import HookEvent


def test_render_includes_tmux_pane():
    cmd = render_hook_command("Stop", port=8000)
    assert "env.TMUX_PANE" in cmd
    assert "tmux_pane" in cmd


def test_hook_event_accepts_optional_pane():
    ev = HookEvent(event="Stop", session_id="s1", cwd="/proj")
    assert ev.tmux_pane is None
    ev2 = HookEvent(event="Stop", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert ev2.tmux_pane == "%3"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_hook_script.py -v`
Expected: FAIL — `env.TMUX_PANE` not present / `HookEvent` rejects `tmux_pane`.

- [ ] **Step 3: Implement**

In `backend/app/services/scheduling/hook_script.py`, change `render_hook_command`'s jq filter to include the pane from the environment:

```python
def render_hook_command(event: str, port: int = 8000) -> str:
    url = f"http://localhost:{port}/api/v1/scheduled-messages/hook-event"
    return (
        "jq -c --arg ev %s '{event:$ev, session_id:.session_id, cwd:.cwd, tmux_pane:env.TMUX_PANE}' "
        "| curl -s -X POST -H 'Content-Type: application/json' -d @- %s >/dev/null 2>&1 || true"
    ) % (json.dumps(event), url)
```

In `backend/app/models/scheduled_message_schemas.py`, add to `HookEvent` (after `cwd`):

```python
    tmux_pane: Optional[str] = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_hook_script.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scheduling/hook_script.py backend/app/models/scheduled_message_schemas.py backend/tests/test_hook_script.py
git commit -m "feat(scheduling): hook reports TMUX_PANE for session mapping"
```

---

## Task 5: SessionRegistry

**Files:**
- Create: `backend/app/services/scheduling/session_registry.py`
- Test: `backend/tests/test_session_registry.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_session_registry.py`:

```python
import pytest
from app.services.scheduling.session_registry import SessionRegistry


def test_pane_mapping_and_idle_transitions():
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert reg.pane_for("s1") == "%3"
    assert reg.is_idle("s1") is False          # SessionStart => busy
    reg.record("Stop", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert reg.is_idle("s1") is True
    reg.record("UserPromptSubmit", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert reg.is_idle("s1") is False


def test_pane_kept_when_event_has_no_pane():
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%3")
    reg.record("Stop", session_id="s1", cwd="/proj", tmux_pane=None)
    assert reg.pane_for("s1") == "%3"


def test_unknown_session_is_not_idle():
    reg = SessionRegistry()
    assert reg.is_idle("nope") is False
    assert reg.pane_for("nope") is None


@pytest.mark.asyncio
async def test_wait_until_idle_returns_immediately_when_idle():
    reg = SessionRegistry()
    reg.record("Stop", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert await reg.wait_until_idle("s1", timeout_s=0.1) is True


@pytest.mark.asyncio
async def test_wait_until_idle_times_out_when_busy():
    reg = SessionRegistry()
    reg.record("UserPromptSubmit", session_id="s1", cwd="/proj", tmux_pane="%3")
    assert await reg.wait_until_idle("s1", timeout_s=0.1) is False


@pytest.mark.asyncio
async def test_wait_until_idle_wakes_on_stop():
    import asyncio
    reg = SessionRegistry()
    reg.record("UserPromptSubmit", session_id="s1", cwd="/proj", tmux_pane="%3")

    async def fire_stop():
        await asyncio.sleep(0.02)
        reg.record("Stop", session_id="s1", cwd="/proj", tmux_pane="%3")

    asyncio.create_task(fire_stop())
    assert await reg.wait_until_idle("s1", timeout_s=1.0) is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_session_registry.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

Create `backend/app/services/scheduling/session_registry.py`:

```python
"""In-memory per-session pane map + idle state, fed by CC hook events.

Keyed by Claude session_id (not cwd), so concurrent sessions in one working
copy are tracked independently. Idle == a Stop with no later busy event.
"""
import asyncio

_IDLE_EVENTS = {"Stop"}
_BUSY_EVENTS = {"UserPromptSubmit", "SessionStart", "Notification"}


class SessionRegistry:
    def __init__(self) -> None:
        self._panes: dict[str, str] = {}
        self._idle: dict[str, bool] = {}
        self._waiters: dict[str, list[asyncio.Event]] = {}

    def record(self, event: str, session_id: str, cwd: str,
               tmux_pane: str | None = None) -> None:
        if tmux_pane:
            self._panes[session_id] = tmux_pane
        if event in _IDLE_EVENTS:
            self._idle[session_id] = True
            for ev in self._waiters.get(session_id, []):
                ev.set()
        elif event in _BUSY_EVENTS:
            self._idle[session_id] = False

    def pane_for(self, session_id: str) -> str | None:
        return self._panes.get(session_id)

    def is_idle(self, session_id: str) -> bool:
        return self._idle.get(session_id, False)

    async def wait_until_idle(self, session_id: str, timeout_s: float) -> bool:
        if self._idle.get(session_id, False):
            return True
        ev = asyncio.Event()
        self._waiters.setdefault(session_id, []).append(ev)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout_s)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            self._waiters.get(session_id, []).remove(ev)


# Module-level singleton (shared by hook endpoint + delivery engine)
session_registry = SessionRegistry()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_session_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scheduling/session_registry.py backend/tests/test_session_registry.py
git commit -m "feat(scheduling): SessionRegistry (session_id -> pane + idle)"
```

---

## Task 6: Hook endpoint feeds the registry

**Files:**
- Modify: `backend/app/api/v1/scheduled_messages/router.py:84-87`
- Test: `backend/tests/test_scheduled_messages_api.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_scheduled_messages_api.py` (uses the existing FastAPI test client fixture in that file — mirror its import/fixture style):

```python
def test_hook_event_populates_session_registry(client):
    from app.services.scheduling.session_registry import session_registry
    resp = client.post("/api/v1/scheduled-messages/hook-event", json={
        "event": "SessionStart", "session_id": "sX", "cwd": "/proj", "tmux_pane": "%7",
    })
    assert resp.status_code == 200
    assert session_registry.pane_for("sX") == "%7"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_scheduled_messages_api.py::test_hook_event_populates_session_registry -v`
Expected: FAIL — `pane_for("sX")` is `None`.

- [ ] **Step 3: Implement**

In `backend/app/api/v1/scheduled_messages/router.py`, add the import near the others (line ~11):

```python
from app.services.scheduling.session_registry import session_registry
```

Replace the `hook_event` body (lines 84-87) with:

```python
@router.post("/hook-event")
async def hook_event(ev: HookEvent):
    idle_state.record(ev.event, cwd=ev.cwd, session_id=ev.session_id)
    session_registry.record(ev.event, session_id=ev.session_id, cwd=ev.cwd,
                            tmux_pane=ev.tmux_pane)
    return {"ok": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_scheduled_messages_api.py::test_hook_event_populates_session_registry -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/scheduled_messages/router.py backend/tests/test_scheduled_messages_api.py
git commit -m "feat(scheduling): hook endpoint feeds SessionRegistry"
```

---

## Task 7: resolve_session_target + resume_spawn_for

**Files:**
- Modify: `backend/app/services/scheduling/session_resolver.py`
- Test: `backend/tests/test_session_resolver_session.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_session_resolver_session.py`:

```python
from unittest.mock import patch
from app.services.scheduling import session_resolver as sr
from app.services.scheduling.session_resolver import resolve_session_target, AMBIGUOUS
from app.services.scheduling.session_registry import SessionRegistry


def _panes(*items):
    # items: (pane_id, cwd, target)
    return [{"pane_id": p, "cwd": c, "tmux_target": t} for p, c, t in items]


def test_known_pane_alive_returns_target():
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%3")
    with patch.object(sr, "session_registry", reg), \
         patch.object(sr, "discover_agent_sessions",
                      return_value=_panes(("%3", "/proj", "win:0.0"))):
        assert resolve_session_target("s1", "/proj") == "win:0.0"


def test_known_pane_gone_returns_none():
    reg = SessionRegistry()
    reg.record("SessionStart", session_id="s1", cwd="/proj", tmux_pane="%3")
    with patch.object(sr, "session_registry", reg), \
         patch.object(sr, "discover_agent_sessions", return_value=_panes()):
        assert resolve_session_target("s1", "/proj") is None


def test_cold_registry_zero_panes_returns_none():
    reg = SessionRegistry()
    with patch.object(sr, "session_registry", reg), \
         patch.object(sr, "discover_agent_sessions", return_value=_panes()):
        assert resolve_session_target("s1", "/proj") is None


def test_cold_registry_single_pane_returns_target():
    reg = SessionRegistry()
    with patch.object(sr, "session_registry", reg), \
         patch.object(sr, "discover_agent_sessions",
                      return_value=_panes(("%9", "/proj", "win:0.0"))):
        assert resolve_session_target("s1", "/proj") == "win:0.0"


def test_cold_registry_multiple_panes_returns_ambiguous():
    reg = SessionRegistry()
    with patch.object(sr, "session_registry", reg), \
         patch.object(sr, "discover_agent_sessions",
                      return_value=_panes(("%1", "/proj", "a:0.0"),
                                          ("%2", "/proj", "b:0.0"))):
        assert resolve_session_target("s1", "/proj") is AMBIGUOUS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_session_resolver_session.py -v`
Expected: FAIL — `resolve_session_target` / `AMBIGUOUS` undefined.

- [ ] **Step 3: Implement**

In `backend/app/services/scheduling/session_resolver.py`, add the import (top) and new symbols:

```python
from app.services.scheduling.session_registry import session_registry

# Sentinel: registry is cold and >1 live claude pane shares the cwd, so we
# cannot safely tell which one is the target — refuse rather than risk a fork.
AMBIGUOUS = object()


def resolve_session_target(session_id: str, cwd: str):
    """Return the tmux_target of the session's live pane, None if it has exited,
    or AMBIGUOUS when the registry is cold and the cwd has >1 live claude pane."""
    sessions = discover_agent_sessions()
    pane_id = session_registry.pane_for(session_id)
    if pane_id:
        for s in sessions:
            if s.get("pane_id") == pane_id:
                return s.get("tmux_target")
        return None  # we knew the pane; it's gone -> exited
    want = os.path.normpath(cwd)
    matches = [s for s in sessions if os.path.normpath(s.get("cwd", "")) == want]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0].get("tmux_target")
    return AMBIGUOUS


def resume_spawn_for(session_id: str, project_folder: str, cwd: str,
                     permission_mode: str) -> str:
    """Relaunch a specific session with `claude --resume <id>` and return its target."""
    result = spawn_session(
        directory=cwd,
        mode="resume",
        session_id=session_id,
        project_folder=project_folder,
        extra_args=permission_flags(permission_mode),
    )
    return result["tmux_target"]
```

(`discover_agent_sessions`, `spawn_session`, `permission_flags`, and `os` are already imported in this module.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_session_resolver_session.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scheduling/session_resolver.py backend/tests/test_session_resolver_session.py
git commit -m "feat(scheduling): resolve_session_target + resume_spawn_for"
```

---

## Task 8: Delivery engine — session branch

**Files:**
- Modify: `backend/app/services/scheduling/delivery.py`
- Test: `backend/tests/test_delivery_session.py`

> **Readiness note (supersedes spec §4 wording):** a freshly `--resume`d session
> loads the conversation and waits for input — it does **not** fire a `Stop`
> hook, so we cannot wait-until-idle on it. Instead, after a resume spawn we
> sleep `resume_settle_s` (default 3.0s; pass 0 in tests) to let the TUI load,
> then inject. The **alive** path still waits-until-idle via the registry,
> because a limit-stop fires `Stop` and leaves the session idle.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_delivery_session.py`:

```python
import pytest
from unittest.mock import patch
from app.services.scheduling.delivery import DeliveryEngine
from app.services.scheduling.session_resolver import AMBIGUOUS
from app.services.scheduling.session_registry import SessionRegistry


def _engine(reg):
    return DeliveryEngine(registry=reg)


async def _deliver(eng, **kw):
    base = dict(
        project_dir="/proj", message="go", permission_mode="acceptEdits",
        target_kind="session", target_session_id="s1",
        project_folder="-home-guillaume-proj", resume_settle_s=0,
    )
    base.update(kw)
    return await eng.deliver(**base)


@pytest.mark.asyncio
async def test_alive_idle_session_gets_injected():
    reg = SessionRegistry(); reg.record("Stop", "s1", "/proj", "%3")
    eng = _engine(reg)
    with patch("app.services.scheduling.delivery.resolve_session_target", return_value="win:0.0"), \
         patch("app.services.scheduling.delivery.send_text", return_value=True) as send:
        res = await _deliver(eng)
    assert res.outcome == "success"
    assert res.action == "used_existing"
    send.assert_called_once_with("win:0.0", "go")


@pytest.mark.asyncio
async def test_exited_session_is_resume_spawned_then_injected():
    reg = SessionRegistry()
    eng = _engine(reg)
    with patch("app.services.scheduling.delivery.resolve_session_target", return_value=None), \
         patch("app.services.scheduling.delivery.resume_spawn_for", return_value="new:0.0") as spawn, \
         patch("app.services.scheduling.delivery.send_text", return_value=True) as send:
        res = await _deliver(eng)
    assert res.outcome == "success"
    assert res.action == "resumed"
    spawn.assert_called_once_with("s1", "-home-guillaume-proj", "/proj", "acceptEdits")
    send.assert_called_once_with("new:0.0", "go")


@pytest.mark.asyncio
async def test_ambiguous_cold_registry_fails():
    reg = SessionRegistry()
    eng = _engine(reg)
    with patch("app.services.scheduling.delivery.resolve_session_target", return_value=AMBIGUOUS), \
         patch("app.services.scheduling.delivery.send_text") as send:
        res = await _deliver(eng)
    assert res.outcome == "failed"
    assert "ambiguous" in (res.error or "").lower()
    send.assert_not_called()


@pytest.mark.asyncio
async def test_alive_but_busy_times_out():
    reg = SessionRegistry(); reg.record("UserPromptSubmit", "s1", "/proj", "%3")
    eng = _engine(reg)
    with patch("app.services.scheduling.delivery.resolve_session_target", return_value="win:0.0"), \
         patch("app.services.scheduling.delivery.send_text") as send:
        res = await _deliver(eng, timeout_s=0.1)
    assert res.outcome == "timeout"
    send.assert_not_called()


@pytest.mark.asyncio
async def test_resume_spawn_failure_marks_failed():
    reg = SessionRegistry()
    eng = _engine(reg)
    with patch("app.services.scheduling.delivery.resolve_session_target", return_value=None), \
         patch("app.services.scheduling.delivery.resume_spawn_for", side_effect=ValueError("boom")), \
         patch("app.services.scheduling.delivery.send_text") as send:
        res = await _deliver(eng)
    assert res.outcome == "failed"
    assert "boom" in (res.error or "")
    send.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_delivery_session.py -v`
Expected: FAIL — `deliver` rejects `target_kind` / `registry` kwargs.

- [ ] **Step 3: Implement**

In `backend/app/services/scheduling/delivery.py`:

Update imports at the top:

```python
import asyncio
import logging
import time
from dataclasses import dataclass

from app.services.scheduling.idle_state import IdleState, idle_state as default_idle
from app.services.scheduling.session_registry import SessionRegistry, session_registry
from app.services.scheduling.session_resolver import (
    resolve_target, spawn_for, resolve_session_target, resume_spawn_for, AMBIGUOUS,
)
from app.services.scheduling.tmux_inject import send_text
```

Update `__init__`:

```python
    def __init__(self, idle_state: IdleState | None = None,
                 registry: SessionRegistry | None = None):
        self.idle = idle_state or default_idle
        self.registry = registry or session_registry
```

Add the session params to `deliver` and branch at the top of its body:

```python
    async def deliver(self, *, project_dir: str, message: str,
                      permission_mode: str = "acceptEdits",
                      on_missing_session: str = "spawn",
                      when_busy: str = "wait_until_idle",
                      timeout_s: float = 1800,
                      target_kind: str = "project",
                      target_session_id: str | None = None,
                      project_folder: str | None = None,
                      resume_settle_s: float = 3.0) -> DeliveryResult:
        if target_kind == "session":
            return await self._deliver_session(
                session_id=target_session_id, project_folder=project_folder,
                cwd=project_dir, message=message, permission_mode=permission_mode,
                when_busy=when_busy, timeout_s=timeout_s, resume_settle_s=resume_settle_s,
            )
        # ----- existing project path unchanged below -----
        target = resolve_target(project_dir)
        # ... (leave the rest of the current method body as-is) ...
```

Add the new method (after `deliver`):

```python
    async def _deliver_session(self, *, session_id: str, project_folder: str | None,
                               cwd: str, message: str, permission_mode: str,
                               when_busy: str, timeout_s: float,
                               resume_settle_s: float) -> DeliveryResult:
        target = resolve_session_target(session_id, cwd)
        if target is AMBIGUOUS:
            return DeliveryResult(
                outcome="failed",
                error="ambiguous live sessions in cwd; cannot safely resume",
            )

        wait_start = time.monotonic()
        if target is not None:
            action = "used_existing"
            if when_busy == "wait_until_idle" and not self.registry.is_idle(session_id):
                became_idle = await self.registry.wait_until_idle(session_id, timeout_s)
                if not became_idle:
                    return DeliveryResult(
                        outcome="timeout", action=action, resolved_session=target,
                        wait_duration_s=int(time.monotonic() - wait_start),
                    )
        else:
            try:
                target = resume_spawn_for(session_id, project_folder, cwd, permission_mode)
            except Exception as e:  # resume spawn raises ValueError
                return DeliveryResult(outcome="failed", error=f"resume spawn failed: {e}")
            action = "resumed"
            if resume_settle_s:
                await asyncio.sleep(resume_settle_s)

        ok = send_text(target, message)
        waited = int(time.monotonic() - wait_start)
        if ok:
            return DeliveryResult(outcome="success", action=action,
                                  resolved_session=target, wait_duration_s=waited)
        return DeliveryResult(outcome="failed", action=action, resolved_session=target,
                              wait_duration_s=waited, error="send-keys failed")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_delivery_session.py tests/test_delivery_engine.py -v`
Expected: PASS (both the new session tests and the existing project tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scheduling/delivery.py backend/tests/test_delivery_session.py
git commit -m "feat(scheduling): delivery engine session-resume branch"
```

---

## Task 9: Wire session fields through crud

**Files:**
- Modify: `backend/app/services/scheduling/crud.py:27-31`
- Test: `backend/tests/test_delivery_engine.py` (add a crud-glue test) or extend `test_scheduled_messages_api.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_delivery_session.py`:

```python
@pytest.mark.asyncio
async def test_crud_passes_session_fields_to_engine(db_session):
    from app.models.scheduled_message import ScheduledMessage
    from app.services.scheduling import crud
    from app.services.scheduling.delivery import DeliveryResult

    msg = ScheduledMessage(
        target_project="/proj", message="go", trigger_type="once",
        fire_at="2026-01-01T00:00:00", target_kind="session",
        target_session_id="s1", project_folder="-home-guillaume-proj",
    )
    db_session.add(msg)
    await db_session.commit()
    await db_session.refresh(msg)

    captured = {}

    async def fake_deliver(**kwargs):
        captured.update(kwargs)
        return DeliveryResult(outcome="success", action="resumed", resolved_session="new:0.0")

    with patch.object(crud._engine, "deliver", side_effect=fake_deliver):
        await crud.run_scheduled_delivery(msg.id)

    assert captured["target_kind"] == "session"
    assert captured["target_session_id"] == "s1"
    assert captured["project_folder"] == "-home-guillaume-proj"
```

(Use the same `db_session` fixture the other model/crud tests use. `run_scheduled_delivery` opens its own `AsyncSessionLocal`, so this test relies on the message being committed to the shared test DB — mirror how existing crud/api tests set up `AsyncSessionLocal` against the test database. If those tests use a dedicated fixture/conftest to point `AsyncSessionLocal` at the test DB, reuse it here.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_delivery_session.py::test_crud_passes_session_fields_to_engine -v`
Expected: FAIL — `captured` has no `target_kind` (crud doesn't pass it yet).

- [ ] **Step 3: Implement**

In `backend/app/services/scheduling/crud.py`, replace the `_engine.deliver(...)` call (lines 27-31) with:

```python
        res = await _engine.deliver(
            project_dir=msg.target_project, message=msg.message,
            permission_mode=msg.permission_mode,
            on_missing_session=msg.on_missing_session, when_busy=msg.when_busy,
            target_kind=msg.target_kind or "project",
            target_session_id=msg.target_session_id,
            project_folder=msg.project_folder,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_delivery_session.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full backend suite**

Run: `cd backend && source venv/bin/activate && pytest tests/ -q`
Expected: PASS (139 prior + new tests, no regressions).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/scheduling/crud.py backend/tests/test_delivery_session.py
git commit -m "feat(scheduling): pass session-target fields through to delivery"
```

---

## Task 10: Frontend types + resumable-session API

**Files:**
- Modify: `frontend/src/features/scheduled-messages/types.ts`
- Modify: `frontend/src/features/scheduled-messages/api.ts`

- [ ] **Step 1: Extend types**

In `frontend/src/features/scheduled-messages/types.ts`:

Add `export type TargetKind = 'project' | 'session'` near the top type aliases.

Add to `ScheduledMessage` interface:

```typescript
  target_kind: TargetKind
  target_session_id: string | null
  project_folder: string | null
  session_preview: string | null
```

Add to `ScheduledMessageCreate` interface:

```typescript
  target_kind?: TargetKind
  target_session_id?: string
  project_folder?: string
  session_preview?: string
```

Append a resumable-session type:

```typescript
export interface ResumableSession {
  id: string
  project_folder: string
  project_name: string
  summary: string
  modified_at: string
  worktree_label: string
}
```

- [ ] **Step 2: Add the API call**

In `frontend/src/features/scheduled-messages/api.ts`, add the import and function:

```typescript
import type {
  ScheduledMessage,
  ScheduledMessageCreate,
  ScheduledMessageUpdate,
  ScheduledMessageListResponse,
  DeliveryAttempt,
  ResumableSession,
} from './types'

export async function listResumableSessions(directory: string): Promise<ResumableSession[]> {
  const res = await apiClient<{ sessions: ResumableSession[] }>(
    `agent-bridge/resumable-sessions?directory=${encodeURIComponent(directory)}&limit=20`,
  )
  return res.sessions
}
```

- [ ] **Step 3: Verify build**

Run: `cd frontend && npm run build`
Expected: build succeeds, 0 TypeScript errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/scheduled-messages/types.ts frontend/src/features/scheduled-messages/api.ts
git commit -m "feat(ui): scheduled-message session-target types + resumable API"
```

---

## Task 11: Form — target-type toggle + session picker

**Files:**
- Modify: `frontend/src/features/scheduled-messages/components/ScheduledMessageForm.tsx`

- [ ] **Step 1: Add state + session loading**

At the top of `ScheduledMessageForm`, add imports and state:

```typescript
import { useState, useEffect } from 'react'
import { createScheduledMessage, listResumableSessions } from '../api'
import type { ScheduledMessageCreate, TriggerType, PermissionMode, ResumableSession } from '../types'
```

Add state (after `targetProject`):

```typescript
  const [targetKind, setTargetKind] = useState<'project' | 'session'>('project')
  const [sessions, setSessions] = useState<ResumableSession[]>([])
  const [sessionId, setSessionId] = useState('')

  useEffect(() => {
    if (targetKind !== 'session' || !targetProject) { setSessions([]); return }
    listResumableSessions(targetProject)
      .then(setSessions)
      .catch(() => setSessions([]))
  }, [targetKind, targetProject])
```

- [ ] **Step 2: Add the target-kind toggle + picker to the JSX**

Directly after the Project `<div>` block (before the Message block, ~line 84) insert:

```tsx
      <div className="space-y-1.5">
        <Label>Target</Label>
        <div className="flex gap-4">
          {(['project', 'session'] as const).map((k) => (
            <label key={k} className="flex items-center gap-2 cursor-pointer text-sm">
              <input
                type="radio"
                name="targetKind"
                value={k}
                checked={targetKind === k}
                onChange={() => setTargetKind(k)}
              />
              {k === 'project' ? 'Project message' : 'Resume a specific session'}
            </label>
          ))}
        </div>
      </div>

      {targetKind === 'session' && (
        <div className="space-y-1.5">
          <Label>Session to resume</Label>
          <Select value={sessionId} onValueChange={setSessionId}>
            <SelectTrigger>
              <SelectValue placeholder={sessions.length ? 'Select a session…' : 'No resumable sessions found'} />
            </SelectTrigger>
            <SelectContent>
              {sessions.map((s) => (
                <SelectItem key={s.id} value={s.id}>
                  [{s.worktree_label}] {s.summary || s.id.slice(0, 8)} · {new Date(s.modified_at).toLocaleString()}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
```

- [ ] **Step 3: Include session fields in submit + validate**

In `handleSubmit`, after the existing message check, add:

```typescript
    if (targetKind === 'session' && !sessionId) { setError('Select a session to resume'); return }
```

When building `payload`, add the session fields:

```typescript
    const payload: ScheduledMessageCreate = {
      target_project: targetProject,
      message: message.trim(),
      trigger_type: triggerType,
      timezone,
      permission_mode: permissionMode,
      on_missing_session: onMissing,
      when_busy: whenBusy,
      target_kind: targetKind,
    }
    if (targetKind === 'session') {
      const picked = sessions.find((s) => s.id === sessionId)
      payload.target_session_id = sessionId
      payload.project_folder = picked?.project_folder
      payload.session_preview = picked?.summary
    }
```

- [ ] **Step 4: Verify build + lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: build succeeds (0 TS errors), lint clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/scheduled-messages/components/ScheduledMessageForm.tsx
git commit -m "feat(ui): resume-session target picker in scheduled-message form"
```

---

## Task 12: List badge + delivery log + manual e2e

**Files:**
- Modify: `frontend/src/features/scheduled-messages/ScheduledMessagesPage.tsx`
- Verify: `frontend/src/features/scheduled-messages/components/DeliveryLog.tsx`

- [ ] **Step 1: Add a "resume" badge to the list**

In `ScheduledMessagesPage.tsx`, where each message row renders its trigger/status badges, add (using the existing badge component in that file — match the import already present):

```tsx
{m.target_kind === 'session' && (
  <Badge variant="secondary">resume</Badge>
)}
```

If the row shows a subtitle, append the session preview when present:

```tsx
{m.target_kind === 'session' && m.session_preview && (
  <span className="text-xs text-muted-foreground truncate">↻ {m.session_preview}</span>
)}
```

- [ ] **Step 2: Confirm DeliveryLog renders the new action**

Open `frontend/src/features/scheduled-messages/components/DeliveryLog.tsx`. `action` is rendered as a free-form string (`used_existing` / `spawned`), so `resumed` renders automatically. If there is a hardcoded action→label map, add `resumed: 'Resumed'`. No change otherwise.

- [ ] **Step 3: Verify build + lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: build succeeds, lint clean.

- [ ] **Step 4: Rebuild frontend so the running backend serves it**

Run: `cd frontend && npm run build`
(The backend serves `frontend/dist`; a server restart alone won't show UI changes.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/scheduled-messages/ScheduledMessagesPage.tsx frontend/src/features/scheduled-messages/components/DeliveryLog.tsx
git commit -m "feat(ui): resume badge + delivery-log resumed action"
```

- [ ] **Step 6: Manual WSL e2e (record results)**

Prereqs: `claude` logged in; the scheduling hooks installed in `~/.claude/settings.json` (the app exposes `settings_hooks_block`; confirm they POST to `/api/v1/scheduled-messages/hook-event`). Backend running (`uvicorn app.main:app --reload --port 8000`).

1. **Exited-session resume:** Start a `claude` session in a test project via the UI/tmux; note its session id (from the resume picker). Kill its tmux pane (`tmux kill-session -t <name>`). In the UI, create a scheduled message → target "Resume a specific session" → pick that session → fire-at = now + 1 min → message "continue". Observe: a new tmux session spawns via `claude --resume <id>`, and after the settle delay the message is injected. Delivery log shows `action=resumed`, `outcome=success`.
2. **Alive-pane inject:** Start a session, let it go idle (a `Stop`). Schedule a resume targeting it for now + 1 min. Observe: no new spawn; the message is injected into the existing pane. Log shows `action=used_existing`.
3. Note any timing issues with `resume_settle_s` (tune the default in `delivery.py` if 3s is too short for conversation load).

---

## Self-review notes

- Spec §1 (defensive columns) → Task 1 + Task 2. §2 (registry + hook pane) → Task 4 + Task 5 + Task 6. §3 (resolve + cold-fallback) → Task 7. §4 (delivery branch) → Task 8 (with the readiness refinement noted). §5 (crud glue) → Task 9. §6 (UI) → Tasks 10-12. Testing → per-task + Task 12 manual e2e. All spec sections covered.
- Method/field names consistent across tasks: `target_kind`, `target_session_id`, `project_folder`, `session_preview`; `resolve_session_target`, `resume_spawn_for`, `AMBIGUOUS`; `session_registry` / `SessionRegistry`; `DeliveryResult.action == "resumed"`.
- Readiness for resumed sessions uses `resume_settle_s` (not idle-wait); documented in Task 8 as superseding the spec's idle wording for that path.
