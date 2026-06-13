# Kanban Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A per-project kanban board where Claude Code agents claim cards and bind deliverables through MCP tools, built local-first on an append-only operation log so cross-device sync can be switched on later.

**Architecture:** All mutations (from the MCP server and the REST/UI layer) flow through one `apply_operation` pipeline that (1) assigns an HLC, (2) appends to an append-only `kanban_ops` table, (3) updates derived materialized tables (`kanban_cards`, `kanban_deliverables`) using per-field last-write-wins and conditional claims. The board lives in its own configurable SQLAlchemy store, separate from Cockpit's device-local DB, so the materialized state is always rebuildable from the op-log (`rematerialize()`), which is also the basis for future sync.

**Tech Stack:** FastAPI · async SQLAlchemy 2.0 + aiosqlite · `mcp` SDK (`FastMCP`, SSE transport mounted on the FastAPI app) · pytest + pytest-asyncio · React 19 + Vite + TypeScript + shadcn/ui.

---

## Scope & deviations from spec

- **Alembic deferred (not v1).** The spec's decision table lists "Alembic from v1". This plan instead uses `create_all` for the kanban store (consistent with the rest of the codebase, which has no migration system) and relies on `rematerialize()` to rebuild the disposable materialized tables from the append-only op-log. The op-log is the only schema that must stay stable, and it is simple and append-only. Introduce Alembic in the later **sync-activation** phase, when a non-wipeable remote primary actually exists. The `rematerialize()` function built here is the safety net and is required for sync replay regardless.
- **Frontend has no test harness** (per `CLAUDE.md`: "Frontend tests not yet set up"). Frontend tasks are verified with `cd frontend && npm run build` (must be 0 errors) and `npm run lint`, plus manual checks. Backend tasks are full TDD.
- **Sync is scaffolded, not activated.** Phase K builds the repository/transport seam and a local no-op transport. No remote primary is run in v1.

## File structure

**Backend (new):**
- `backend/app/kanban/__init__.py`
- `backend/app/kanban/db.py` — separate `KanbanBase`, `kanban_engine`, `KanbanSessionLocal`, `init_kanban_db()`.
- `backend/app/kanban/hlc.py` — `HLC` hybrid logical clock.
- `backend/app/kanban/models.py` — `KanbanOp`, `KanbanCard`, `KanbanDeliverable`, `KanbanMeta`.
- `backend/app/kanban/schemas.py` — Pydantic request/response models + `COLUMNS` constant.
- `backend/app/kanban/project_key.py` — `resolve_project_key()`.
- `backend/app/kanban/operations.py` — `apply_operation()` (the conflict engine) + `rematerialize()`.
- `backend/app/kanban/service.py` — read queries + thin wrappers used by REST and MCP.
- `backend/app/kanban/mcp_server.py` — `FastMCP` server exposing the 9 tools.
- `backend/app/kanban/sync.py` — `SyncTransport` protocol + `LocalNoopTransport` (Phase K).
- `backend/app/api/v1/kanban/__init__.py`, `backend/app/api/v1/kanban/router.py` — REST routes.

**Backend (modified):**
- `backend/app/config.py` — add `kanban_database_url`.
- `backend/app/api/v1/router.py` — register the kanban router.
- `backend/app/main.py` — call `init_kanban_db()` in lifespan + mount the MCP SSE app.

**Backend (tests):**
- `backend/tests/test_kanban_hlc.py`
- `backend/tests/test_kanban_project_key.py`
- `backend/tests/test_kanban_operations.py`
- `backend/tests/test_kanban_rematerialize.py`
- `backend/tests/test_kanban_service.py`
- `backend/tests/test_kanban_api.py`
- `backend/tests/test_kanban_mcp.py`

**Frontend (new):** `frontend/src/features/kanban/` — `KanbanPage.tsx`, `api.ts`, `types.ts`, `components/Board.tsx`, `components/Column.tsx`, `components/CardItem.tsx`, `components/CardDrawer.tsx`, `components/CardEditDialog.tsx`, `components/EnableKanbanToggle.tsx`.

**Frontend (modified):** `frontend/src/App.tsx` (route), navigation/sidebar (menu entry).

---

## Phase A — Kanban store foundation

### Task A1: Config setting for the kanban store

**Files:**
- Modify: `backend/app/config.py:29`

- [ ] **Step 1: Add the setting**

In `class Settings`, directly after the existing `database_url` line, add:

```python
    # Separate store for the kanban board domain (portable, sync-able).
    # Kept apart from database_url, which holds device-local data.
    kanban_database_url: str = "sqlite+aiosqlite:///./kanban.db"
```

- [ ] **Step 2: Verify it imports**

Run: `cd backend && source venv/bin/activate && python -c "from app.config import settings; print(settings.kanban_database_url)"`
Expected: `sqlite+aiosqlite:///./kanban.db`

- [ ] **Step 3: Commit**

```bash
git add backend/app/config.py
git commit -m "feat(kanban): add kanban_database_url setting"
```

### Task A2: Separate kanban engine, Base, session factory

**Files:**
- Create: `backend/app/kanban/__init__.py` (empty)
- Create: `backend/app/kanban/db.py`

- [ ] **Step 1: Create the package marker**

Create `backend/app/kanban/__init__.py` as an empty file.

- [ ] **Step 2: Write `db.py`**

```python
"""Separate SQLAlchemy store for the kanban board domain.

Intentionally independent from app.database: the board is portable and
sync-able, whereas app.database holds device-local data (tmux targets,
absolute paths, scheduled deliveries).
"""
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession, create_async_engine, async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class KanbanBase(DeclarativeBase):
    """Base for all kanban-domain models."""
    pass


kanban_engine = create_async_engine(settings.kanban_database_url, future=True)

if settings.kanban_database_url.startswith("sqlite"):
    @event.listens_for(kanban_engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

KanbanSessionLocal = async_sessionmaker(
    kanban_engine, class_=AsyncSession, expire_on_commit=False,
    autocommit=False, autoflush=False,
)


async def init_kanban_db() -> None:
    """Create kanban tables. Import models so they register on KanbanBase."""
    from app.kanban import models  # noqa: F401
    async with kanban_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.create_all)
```

- [ ] **Step 3: Verify import (models module is created in Task C, so import only the engine here)**

Run: `cd backend && source venv/bin/activate && python -c "from app.kanban.db import KanbanBase, kanban_engine; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/app/kanban/__init__.py backend/app/kanban/db.py
git commit -m "feat(kanban): separate SQLAlchemy store (engine, base, session)"
```

---

## Phase B — Hybrid Logical Clock

### Task B1: HLC

**Files:**
- Create: `backend/app/kanban/hlc.py`
- Test: `backend/tests/test_kanban_hlc.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_kanban_hlc.py
from app.kanban.hlc import HLC, hlc_max


def test_ticks_are_monotonic_and_sortable():
    clock = HLC(node_id="dev-a")
    a = clock.tick()
    b = clock.tick()
    assert a < b  # lexicographic string ordering == causal ordering


def test_same_physical_ms_increments_logical():
    clock = HLC(node_id="dev-a", _now_ms=lambda: 1000)
    a = clock.tick()
    b = clock.tick()
    assert a < b
    assert a.split(":")[0] == b.split(":")[0]  # same physical component


def test_update_pushes_clock_past_remote():
    clock = HLC(node_id="dev-a", _now_ms=lambda: 1000)
    remote = "9999999999999:00042:dev-b"
    clock.update(remote)
    nxt = clock.tick()
    assert nxt > remote


def test_hlc_max_returns_later():
    assert hlc_max("1:0:a", "2:0:a") == "2:0:a"
    assert hlc_max("2:0:a", None) == "2:0:a"
    assert hlc_max(None, "2:0:a") == "2:0:a"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_hlc.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.kanban.hlc'`

- [ ] **Step 3: Implement `hlc.py`**

```python
"""Hybrid Logical Clock: total, deterministic ordering across devices
despite wall-clock drift. Tick strings sort lexicographically by causal order.

Format: "<physical_ms:013d>:<logical:05d>:<node_id>".
"""
import time
from typing import Callable, Optional


def _default_now_ms() -> int:
    return int(time.time() * 1000)


def _format(physical: int, logical: int, node_id: str) -> str:
    return f"{physical:013d}:{logical:05d}:{node_id}"


def _physical(hlc: str) -> int:
    return int(hlc.split(":")[0])


def _logical(hlc: str) -> int:
    return int(hlc.split(":")[1])


class HLC:
    def __init__(self, node_id: str, _now_ms: Callable[[], int] = _default_now_ms):
        self.node_id = node_id
        self._now_ms = _now_ms
        self._last_physical = 0
        self._last_logical = 0

    def tick(self) -> str:
        """Generate the next local HLC (call when creating an op)."""
        pt = self._now_ms()
        if pt > self._last_physical:
            self._last_physical, self._last_logical = pt, 0
        else:
            self._last_logical += 1
        return _format(self._last_physical, self._last_logical, self.node_id)

    def update(self, remote_hlc: str) -> None:
        """Advance the clock to dominate a received remote HLC."""
        rp, rl = _physical(remote_hlc), _logical(remote_hlc)
        pt = self._now_ms()
        new_physical = max(self._last_physical, rp, pt)
        if new_physical == self._last_physical == rp:
            new_logical = max(self._last_logical, rl) + 1
        elif new_physical == self._last_physical:
            new_logical = self._last_logical + 1
        elif new_physical == rp:
            new_logical = rl + 1
        else:
            new_logical = 0
        self._last_physical, self._last_logical = new_physical, new_logical


def hlc_max(a: Optional[str], b: Optional[str]) -> Optional[str]:
    """Return the later of two HLCs (None-safe)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if a >= b else b
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_hlc.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/hlc.py backend/tests/test_kanban_hlc.py
git commit -m "feat(kanban): hybrid logical clock with tests"
```

---

## Phase C — Data model

### Task C1: ORM models (op-log + materialized + meta)

**Files:**
- Create: `backend/app/kanban/models.py`
- Test: `backend/tests/test_kanban_operations.py` (created here with a model smoke test; expanded in Phase E)

- [ ] **Step 1: Write a failing model smoke test**

```python
# backend/tests/test_kanban_operations.py
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.kanban.db import KanbanBase, kanban_engine, KanbanSessionLocal
from app.kanban import models


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with kanban_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.drop_all)
        await conn.run_sync(KanbanBase.metadata.create_all)
    yield


@pytest.mark.asyncio
async def test_can_persist_an_op_row():
    async with KanbanSessionLocal() as s:
        s.add(models.KanbanOp(
            op_id="dev-a:1", device_id="dev-a", seq=1, hlc="1:0:dev-a",
            project_key="git:example", entity_type="card", entity_id="c1",
            op_type="create", payload={"title": "x", "column": "Backlog"},
        ))
        await s.commit()
        rows = (await s.execute(select(models.KanbanOp))).scalars().all()
        assert len(rows) == 1
        assert rows[0].payload["title"] == "x"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_operations.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.kanban.models'`

- [ ] **Step 3: Implement `models.py`**

```python
"""Kanban ORM models. Two layers:
- KanbanOp: append-only operation log (source of truth + activity feed).
- KanbanCard / KanbanDeliverable: materialized, derived state for fast reads.
- KanbanMeta: small key/value store (device_id, sync cursors).
"""
from datetime import datetime, timezone

from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.kanban.db import KanbanBase


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KanbanOp(KanbanBase):
    __tablename__ = "kanban_ops"

    op_id: Mapped[str] = mapped_column(String(128), primary_key=True)  # "<device>:<seq>"
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    hlc: Mapped[str] = mapped_column(String(48), index=True)
    project_key: Mapped[str] = mapped_column(String(512), index=True)
    entity_type: Mapped[str] = mapped_column(String(16))   # card | comment | deliverable
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    op_type: Mapped[str] = mapped_column(String(16))       # create|move|update|claim|release|comment|attach
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class KanbanCard(KanbanBase):
    __tablename__ = "kanban_cards"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(512), index=True)
    title: Mapped[str] = mapped_column(String(512), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    column: Mapped[str] = mapped_column(String(32), default="Backlog")
    rank: Mapped[str] = mapped_column(String(64), default="")
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    labels: Mapped[list | None] = mapped_column(JSON, nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    # Per-field HLCs powering last-write-wins.
    title_hlc: Mapped[str | None] = mapped_column(String(48), nullable=True)
    description_hlc: Mapped[str | None] = mapped_column(String(48), nullable=True)
    column_hlc: Mapped[str | None] = mapped_column(String(48), nullable=True)
    rank_hlc: Mapped[str | None] = mapped_column(String(48), nullable=True)
    claim_hlc: Mapped[str | None] = mapped_column(String(48), nullable=True)

    deliverables: Mapped[list["KanbanDeliverable"]] = relationship(
        back_populates="card", cascade="all, delete-orphan",
    )


class KanbanDeliverable(KanbanBase):
    __tablename__ = "kanban_deliverables"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    card_id: Mapped[str] = mapped_column(ForeignKey("kanban_cards.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))   # pr|branch|commit|link|note
    ref: Mapped[str] = mapped_column(Text)          # portable reference, never a local path
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    card: Mapped["KanbanCard"] = relationship(back_populates="deliverables")


class KanbanMeta(KanbanBase):
    __tablename__ = "kanban_meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_operations.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/models.py backend/tests/test_kanban_operations.py
git commit -m "feat(kanban): op-log + materialized + meta ORM models"
```

### Task C2: Schemas + COLUMNS constant

**Files:**
- Create: `backend/app/kanban/schemas.py`

- [ ] **Step 1: Write `schemas.py`**

```python
"""Pydantic schemas + the fixed column set."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

COLUMNS = ["Backlog", "Analysis", "Todo", "Doing", "Review", "Done"]
DELIVERABLE_KINDS = ["pr", "branch", "commit", "link", "note"]


class DeliverableResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    ref: str
    created_at: datetime


class CardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    project_key: str
    title: str
    description: str
    column: str
    rank: str
    priority: Optional[str] = None
    labels: Optional[list] = None
    claimed_by: Optional[str] = None
    claimed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    deliverables: list[DeliverableResponse] = []


class CardCreate(BaseModel):
    project_key: str
    title: str
    description: str = ""
    column: str = "Backlog"
    priority: Optional[str] = None
    labels: Optional[list] = None


class CardUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    labels: Optional[list] = None


class MoveRequest(BaseModel):
    column: str
    rank: Optional[str] = None


class ClaimRequest(BaseModel):
    claimed_by: str  # "<session-id>@<device>" or a human label


class CommentRequest(BaseModel):
    text: str


class AttachRequest(BaseModel):
    kind: str
    ref: str


class ActivityEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    hlc: str
    op_type: str
    entity_type: str
    payload: dict
    created_at: datetime
```

- [ ] **Step 2: Verify import**

Run: `cd backend && source venv/bin/activate && python -c "from app.kanban.schemas import COLUMNS; print(COLUMNS)"`
Expected: `['Backlog', 'Analysis', 'Todo', 'Doing', 'Review', 'Done']`

- [ ] **Step 3: Commit**

```bash
git add backend/app/kanban/schemas.py
git commit -m "feat(kanban): pydantic schemas + fixed columns"
```

---

## Phase D — Project key resolver

### Task D1: `resolve_project_key`

**Files:**
- Create: `backend/app/kanban/project_key.py`
- Test: `backend/tests/test_kanban_project_key.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_kanban_project_key.py
from app.kanban.project_key import normalize_remote, resolve_project_key


def test_normalize_strips_git_suffix_and_scheme():
    assert normalize_remote("https://github.com/u/repo.git") == "github.com/u/repo"
    assert normalize_remote("git@github.com:u/repo.git") == "github.com/u/repo"
    assert normalize_remote("ssh://git@host.com/u/repo") == "host.com/u/repo"


def test_resolve_uses_git_remote_when_present():
    key = resolve_project_key("/any/path", _remote_getter=lambda p: "git@github.com:u/repo.git")
    assert key == "git:github.com/u/repo"


def test_resolve_falls_back_to_slug_when_no_remote():
    key = resolve_project_key("/home/me/My Project", _remote_getter=lambda p: None)
    assert key == "slug:my-project"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_project_key.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `project_key.py`**

```python
"""Device-independent project identity for the board.

Primary key = normalized git remote ("git:<host>/<path>"); fallback =
"slug:<basename>" so repos without a remote still get a stable-ish key.
"""
import re
import subprocess
from pathlib import Path
from typing import Callable, Optional


def _git_remote(project_path: str) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", project_path, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        url = out.stdout.strip()
        return url or None
    except Exception:
        return None


def normalize_remote(url: str) -> str:
    url = url.strip()
    url = re.sub(r"\.git$", "", url)
    url = re.sub(r"^[a-z]+://", "", url)        # strip scheme (https://, ssh://)
    url = re.sub(r"^[^@/]+@", "", url)          # strip user@
    url = url.replace(":", "/", 1) if "/" not in url.split(":", 1)[0] else url
    url = url.replace(":", "/")                 # scp-style host:path -> host/path
    return re.sub(r"/+", "/", url)


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "project"


def resolve_project_key(
    project_path: str,
    _remote_getter: Callable[[str], Optional[str]] = _git_remote,
) -> str:
    remote = _remote_getter(project_path)
    if remote:
        return f"git:{normalize_remote(remote)}"
    return f"slug:{_slug(Path(project_path).name)}"
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_project_key.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/project_key.py backend/tests/test_kanban_project_key.py
git commit -m "feat(kanban): device-independent project key resolver"
```

---

## Phase E — The conflict engine (`apply_operation` + `rematerialize`)

This is the heart of the feature. `apply_operation` is the single mutation pipeline; `rematerialize` rebuilds materialized tables from the op-log (safety net + sync replay).

### Task E1: device_id helper + op writer skeleton

**Files:**
- Create: `backend/app/kanban/operations.py`
- Test: `backend/tests/test_kanban_operations.py` (append tests)

- [ ] **Step 1: Add failing tests for op creation + materialization of `create`**

Append to `backend/tests/test_kanban_operations.py`:

```python
from app.kanban.operations import apply_operation, get_device_id
from app.kanban.models import KanbanCard


@pytest.mark.asyncio
async def test_create_card_materializes_a_card_row():
    async with KanbanSessionLocal() as s:
        card_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "First", "description": "d", "column": "Backlog"},
        )
        await s.commit()
        card = await s.get(KanbanCard, card_id)
        assert card is not None
        assert card.title == "First"
        assert card.column == "Backlog"
        assert card.title_hlc is not None


@pytest.mark.asyncio
async def test_device_id_is_stable():
    async with KanbanSessionLocal() as s:
        a = await get_device_id(s)
        b = await get_device_id(s)
        await s.commit()
        assert a == b and len(a) > 0
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_operations.py -v`
Expected: FAIL — `ImportError: cannot import name 'apply_operation'`

- [ ] **Step 3: Implement `operations.py` (create path + helpers)**

```python
"""Single mutation pipeline + materialization.

apply_operation(): assign HLC -> append KanbanOp -> update materialized state.
All writes (REST and MCP) go through here. rematerialize() rebuilds the
materialized tables from the op-log (added in Task E5).
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func

from app.kanban.hlc import HLC, hlc_max
from app.kanban.models import KanbanCard, KanbanDeliverable, KanbanMeta, KanbanOp

# One in-process clock per backend. node_id is bound lazily to the device_id.
_clock: Optional[HLC] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def get_device_id(session) -> str:
    row = await session.get(KanbanMeta, "device_id")
    if row is None:
        row = KanbanMeta(key="device_id", value=uuid.uuid4().hex[:12])
        session.add(row)
        await session.flush()
    return row.value


async def _clock_for(session) -> HLC:
    global _clock
    device_id = await get_device_id(session)
    if _clock is None or _clock.node_id != device_id:
        _clock = HLC(node_id=device_id)
        # Seed past the highest HLC already stored so restarts stay monotonic.
        highest = (await session.execute(select(func.max(KanbanOp.hlc)))).scalar()
        if highest:
            _clock.update(highest)
    return _clock


async def _next_seq(session, device_id: str) -> int:
    n = (await session.execute(
        select(func.count()).select_from(KanbanOp).where(KanbanOp.device_id == device_id)
    )).scalar() or 0
    return n + 1


async def apply_operation(
    session, *, op_type: str, entity_type: str, project_key: str,
    entity_id: Optional[str], payload: dict,
) -> str:
    """Append an op and fold it into materialized state. Returns entity_id."""
    clock = await _clock_for(session)
    device_id = await get_device_id(session)
    hlc = clock.tick()
    entity_id = entity_id or uuid.uuid4().hex
    seq = await _next_seq(session, device_id)

    session.add(KanbanOp(
        op_id=f"{device_id}:{seq}", device_id=device_id, seq=seq, hlc=hlc,
        project_key=project_key, entity_type=entity_type, entity_id=entity_id,
        op_type=op_type, payload=payload,
    ))
    await session.flush()
    await _materialize(session, op_type=op_type, entity_type=entity_type,
                       project_key=project_key, entity_id=entity_id,
                       payload=payload, hlc=hlc)
    return entity_id


async def _materialize(session, *, op_type, entity_type, project_key,
                       entity_id, payload, hlc) -> None:
    if entity_type == "card" and op_type == "create":
        existing = await session.get(KanbanCard, entity_id)
        if existing is None:  # idempotent: re-applying create is a no-op
            session.add(KanbanCard(
                id=entity_id, project_key=project_key,
                title=payload.get("title", ""),
                description=payload.get("description", ""),
                column=payload.get("column", "Backlog"),
                rank=payload.get("rank", hlc),
                priority=payload.get("priority"), labels=payload.get("labels"),
                title_hlc=hlc, description_hlc=hlc, column_hlc=hlc, rank_hlc=hlc,
            ))
            await session.flush()
        return
    # other op types added in Tasks E2-E4
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_operations.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/operations.py backend/tests/test_kanban_operations.py
git commit -m "feat(kanban): apply_operation create path + device_id + clock"
```

### Task E2: move + update (per-field LWW)

**Files:**
- Modify: `backend/app/kanban/operations.py` (`_materialize`)
- Test: `backend/tests/test_kanban_operations.py`

- [ ] **Step 1: Add failing tests**

```python
@pytest.mark.asyncio
async def test_move_updates_column_with_lww():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None,
            payload={"title": "t", "column": "Backlog"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id=cid, payload={"column": "Doing"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.column == "Doing"


@pytest.mark.asyncio
async def test_stale_move_is_ignored_by_lww():
    # An op with an older HLC than the field's current HLC must not win.
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        card = await s.get(KanbanCard, cid)
        card.column = "Review"
        card.column_hlc = "9999999999999:00000:dev-z"  # far-future HLC
        await s.flush()
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id=cid, payload={"column": "Done"})
        await s.commit()
        refreshed = await s.get(KanbanCard, cid)
        assert refreshed.column == "Review"  # stale move rejected


@pytest.mark.asyncio
async def test_update_title_and_description():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "old"})
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="p", entity_id=cid,
            payload={"title": "new", "description": "desc"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.title == "new"
        assert card.description == "desc"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_operations.py -k "move or update" -v`
Expected: FAIL — moves/updates not materialized (column stays "Backlog")

- [ ] **Step 3: Extend `_materialize`**

Add a helper and branches. Insert this helper above `_materialize` in `operations.py`:

```python
def _lww_set(card, field: str, value, hlc: str) -> None:
    """Apply value to card.<field> only if hlc beats the field's current hlc."""
    hlc_attr = f"{field}_hlc"
    current = getattr(card, hlc_attr)
    if hlc_max(current, hlc) == hlc and hlc != current:
        setattr(card, field, value)
        setattr(card, hlc_attr, hlc)
```

Then, inside `_materialize`, replace the trailing comment `# other op types ...` with:

```python
    if entity_type == "card" and op_type in ("move", "update"):
        card = await session.get(KanbanCard, entity_id)
        if card is None:
            return
        if op_type == "move":
            if "column" in payload:
                _lww_set(card, "column", payload["column"], hlc)
            if payload.get("rank") is not None:
                _lww_set(card, "rank", payload["rank"], hlc)
        else:  # update
            for f in ("title", "description"):
                if f in payload and payload[f] is not None:
                    _lww_set(card, f, payload[f], hlc)
            for f in ("priority", "labels"):  # non-LWW scalar attrs
                if f in payload:
                    setattr(card, f, payload[f])
        card.updated_at = _utcnow()
        await session.flush()
        return
    # claim/release/comment/attach added in Tasks E3-E4
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_operations.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/operations.py backend/tests/test_kanban_operations.py
git commit -m "feat(kanban): move/update materialization with per-field LWW"
```

### Task E3: conditional claim + release

**Files:**
- Modify: `backend/app/kanban/operations.py`
- Test: `backend/tests/test_kanban_operations.py`

- [ ] **Step 1: Add failing tests**

```python
from app.kanban.operations import ClaimRejected


@pytest.mark.asyncio
async def test_claim_sets_owner():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="p", entity_id=cid, payload={"claimed_by": "sess1@devA"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.claimed_by == "sess1@devA"


@pytest.mark.asyncio
async def test_second_claim_is_rejected():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="p", entity_id=cid, payload={"claimed_by": "first@devA"})
        with pytest.raises(ClaimRejected):
            await apply_operation(s, op_type="claim", entity_type="card",
                project_key="p", entity_id=cid, payload={"claimed_by": "second@devB"})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.claimed_by == "first@devA"


@pytest.mark.asyncio
async def test_release_clears_owner():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="claim", entity_type="card",
            project_key="p", entity_id=cid, payload={"claimed_by": "a@d"})
        await apply_operation(s, op_type="release", entity_type="card",
            project_key="p", entity_id=cid, payload={})
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.claimed_by is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_operations.py -k "claim or release" -v`
Expected: FAIL — `ImportError: cannot import name 'ClaimRejected'`

- [ ] **Step 3: Implement claim/release**

At the top of `operations.py` (after imports) add:

```python
class ClaimRejected(Exception):
    """Raised when a claim loses to an existing earlier claim."""
    def __init__(self, current_owner: str):
        self.current_owner = current_owner
        super().__init__(f"already claimed by {current_owner}")
```

Inside `_materialize`, replace the trailing `# claim/release ...` comment with:

```python
    if entity_type == "card" and op_type == "claim":
        card = await session.get(KanbanCard, entity_id)
        if card is None:
            return
        # Conditional: a live claim with an equal/earlier claim_hlc wins.
        if card.claimed_by is not None and hlc_max(card.claim_hlc, hlc) != hlc:
            raise ClaimRejected(card.claimed_by)
        if card.claimed_by is not None and card.claim_hlc and card.claim_hlc < hlc:
            # An earlier claim already holds it; later claim is rejected.
            raise ClaimRejected(card.claimed_by)
        card.claimed_by = payload["claimed_by"]
        card.claimed_at = _utcnow()
        card.claim_hlc = hlc
        card.updated_at = _utcnow()
        await session.flush()
        return

    if entity_type == "card" and op_type == "release":
        card = await session.get(KanbanCard, entity_id)
        if card is None:
            return
        if hlc_max(card.claim_hlc, hlc) == hlc:  # release must be newer than the claim
            card.claimed_by = None
            card.claimed_at = None
            card.claim_hlc = hlc
            card.updated_at = _utcnow()
            await session.flush()
        return
    # comment/attach added in Task E4
```

> Note: during live use a second claim raises `ClaimRejected` (surfaced to the caller). During `rematerialize` replay (Task E5) the rejection is swallowed so an already-claimed card simply keeps its first owner.

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_operations.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/operations.py backend/tests/test_kanban_operations.py
git commit -m "feat(kanban): conditional claim (first-wins) + release"
```

### Task E4: comment + attach deliverable

**Files:**
- Modify: `backend/app/kanban/operations.py`
- Test: `backend/tests/test_kanban_operations.py`

- [ ] **Step 1: Add failing tests**

```python
from app.kanban.models import KanbanDeliverable
from sqlalchemy import select as _select


@pytest.mark.asyncio
async def test_attach_deliverable_creates_row():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="p", entity_id=cid,
            payload={"kind": "pr", "ref": "https://github.com/u/r/pull/7"})
        await s.commit()
        rows = (await s.execute(_select(KanbanDeliverable))).scalars().all()
        assert len(rows) == 1
        assert rows[0].kind == "pr"
        assert rows[0].card_id == cid


@pytest.mark.asyncio
async def test_comment_op_is_recorded_in_log():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t"})
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="p", entity_id=cid, payload={"text": "looks good"})
        await s.commit()
        ops = (await s.execute(
            _select(KanbanOp).where(KanbanOp.op_type == "comment"))).scalars().all()
        assert ops[0].payload["text"] == "looks good"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_operations.py -k "attach or comment" -v`
Expected: FAIL — deliverable row not created

- [ ] **Step 3: Implement attach (comment needs no materialization — it lives in the op-log)**

Inside `_materialize`, replace the trailing `# comment/attach ...` comment with:

```python
    if entity_type == "deliverable" and op_type == "attach":
        session.add(KanbanDeliverable(
            id=uuid.uuid4().hex, card_id=entity_id,
            kind=payload["kind"], ref=payload["ref"],
        ))
        await session.flush()
        return
    # comment ops are pure log entries; nothing to materialize.
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_operations.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/operations.py backend/tests/test_kanban_operations.py
git commit -m "feat(kanban): attach deliverable + comment ops"
```

### Task E5: `rematerialize` (rebuild materialized state from the op-log)

**Files:**
- Modify: `backend/app/kanban/operations.py`
- Test: `backend/tests/test_kanban_rematerialize.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_kanban_rematerialize.py
import pytest
import pytest_asyncio
from sqlalchemy import select, delete

from app.kanban.db import KanbanBase, kanban_engine, KanbanSessionLocal
from app.kanban.models import KanbanCard, KanbanDeliverable
from app.kanban.operations import apply_operation, rematerialize


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with kanban_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.drop_all)
        await conn.run_sync(KanbanBase.metadata.create_all)
    yield


@pytest.mark.asyncio
async def test_rematerialize_rebuilds_state_from_oplog():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "t", "column": "Backlog"})
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="p", entity_id=cid, payload={"column": "Doing"})
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="p", entity_id=cid, payload={"kind": "note", "ref": "x"})
        await s.commit()

    # Wipe ONLY the materialized tables, keep the op-log.
    async with KanbanSessionLocal() as s:
        await s.execute(delete(KanbanDeliverable))
        await s.execute(delete(KanbanCard))
        await s.commit()

    async with KanbanSessionLocal() as s:
        await rematerialize(s)
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card is not None and card.column == "Doing"
        delivs = (await s.execute(select(KanbanDeliverable))).scalars().all()
        assert len(delivs) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_rematerialize.py -v`
Expected: FAIL — `ImportError: cannot import name 'rematerialize'`

- [ ] **Step 3: Implement `rematerialize`**

Add to `operations.py`:

```python
async def rematerialize(session) -> None:
    """Rebuild materialized tables by replaying the op-log in HLC order.
    Safe to run anytime; also the basis for sync replay. ClaimRejected is
    swallowed here so an already-owned card keeps its first claimant.
    """
    from sqlalchemy import delete
    await session.execute(delete(KanbanDeliverable))
    await session.execute(delete(KanbanCard))
    await session.flush()
    ops = (await session.execute(
        select(KanbanOp).order_by(KanbanOp.hlc.asc())
    )).scalars().all()
    for op in ops:
        try:
            await _materialize(
                session, op_type=op.op_type, entity_type=op.entity_type,
                project_key=op.project_key, entity_id=op.entity_id,
                payload=op.payload, hlc=op.hlc,
            )
        except ClaimRejected:
            pass
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_rematerialize.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/operations.py backend/tests/test_kanban_rematerialize.py
git commit -m "feat(kanban): rematerialize state from op-log (sync/replay safety net)"
```

---

## Phase F — Read service

### Task F1: query helpers

**Files:**
- Create: `backend/app/kanban/service.py`
- Test: `backend/tests/test_kanban_service.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_kanban_service.py
import pytest
import pytest_asyncio

from app.kanban.db import KanbanBase, kanban_engine, KanbanSessionLocal
from app.kanban.operations import apply_operation
from app.kanban import service


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with kanban_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.drop_all)
        await conn.run_sync(KanbanBase.metadata.create_all)
    yield


@pytest.mark.asyncio
async def test_list_cards_filters_by_project_and_column():
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None, payload={"title": "a1", "column": "Todo"})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None, payload={"title": "a2", "column": "Done"})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="B", entity_id=None, payload={"title": "b1", "column": "Todo"})
        await s.commit()
        all_a = await service.list_cards(s, "A")
        assert {c.title for c in all_a} == {"a1", "a2"}
        todo_a = await service.list_cards(s, "A", column="Todo")
        assert {c.title for c in todo_a} == {"a1"}


@pytest.mark.asyncio
async def test_card_activity_returns_oplog_for_card():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key="A", entity_id=None, payload={"title": "a"})
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="A", entity_id=cid, payload={"text": "hi"})
        await s.commit()
        feed = await service.card_activity(s, cid)
        assert [e.op_type for e in feed] == ["create", "comment"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.kanban.service'`

- [ ] **Step 3: Implement `service.py`**

```python
"""Read-side queries over the materialized state + op-log activity feed."""
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.kanban.models import KanbanCard, KanbanOp


async def list_cards(session, project_key: str, column: str | None = None):
    stmt = (
        select(KanbanCard)
        .where(KanbanCard.project_key == project_key)
        .options(selectinload(KanbanCard.deliverables))
        .order_by(KanbanCard.rank.asc())
    )
    if column is not None:
        stmt = stmt.where(KanbanCard.column == column)
    return (await session.execute(stmt)).scalars().all()


async def get_card(session, card_id: str):
    stmt = (
        select(KanbanCard)
        .where(KanbanCard.id == card_id)
        .options(selectinload(KanbanCard.deliverables))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def card_activity(session, card_id: str):
    stmt = (
        select(KanbanOp)
        .where(KanbanOp.entity_id == card_id)
        .order_by(KanbanOp.hlc.asc())
    )
    return (await session.execute(stmt)).scalars().all()
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_service.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/service.py backend/tests/test_kanban_service.py
git commit -m "feat(kanban): read service (list/get cards, activity feed)"
```

---

## Phase G — REST API

### Task G1: router with CRUD + actions

**Files:**
- Create: `backend/app/api/v1/kanban/__init__.py` (empty)
- Create: `backend/app/api/v1/kanban/router.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `backend/tests/test_kanban_api.py`

- [ ] **Step 1: Write the failing API tests**

```python
# backend/tests/test_kanban_api.py
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.kanban.db import KanbanBase, kanban_engine


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with kanban_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.drop_all)
        await conn.run_sync(KanbanBase.metadata.create_all)
    yield


@pytest.mark.asyncio
async def test_create_list_move_card():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "Build X"})
        assert r.status_code == 201, r.text
        cid = r.json()["id"]

        r = await ac.get("/api/v1/kanban/cards", params={"project_key": "P"})
        assert any(c["id"] == cid for c in r.json()["items"])

        r = await ac.post(f"/api/v1/kanban/cards/{cid}/move", json={"column": "Doing"})
        assert r.status_code == 200
        assert r.json()["column"] == "Doing"


@pytest.mark.asyncio
async def test_claim_conflict_returns_409():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "t"})).json()["id"]
        r1 = await ac.post(f"/api/v1/kanban/cards/{cid}/claim",
            json={"claimed_by": "first@d"})
        assert r1.status_code == 200
        r2 = await ac.post(f"/api/v1/kanban/cards/{cid}/claim",
            json={"claimed_by": "second@d"})
        assert r2.status_code == 409, r2.text
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_api.py -v`
Expected: FAIL — 404 (routes not registered)

- [ ] **Step 3: Implement `backend/app/api/v1/kanban/router.py`**

```python
"""REST API for the kanban board. All mutations go through apply_operation."""
from fastapi import APIRouter, HTTPException, Query, status

from app.kanban.db import KanbanSessionLocal
from app.kanban import service
from app.kanban.operations import apply_operation, ClaimRejected
from app.kanban.schemas import (
    CardResponse, CardCreate, CardUpdate, MoveRequest, ClaimRequest,
    CommentRequest, AttachRequest, ActivityEntry, COLUMNS,
)

router = APIRouter(prefix="/kanban", tags=["Kanban"])


@router.get("/columns")
async def columns():
    return {"columns": COLUMNS}


@router.get("/cards")
async def list_cards(project_key: str = Query(...), column: str | None = None):
    async with KanbanSessionLocal() as s:
        rows = await service.list_cards(s, project_key, column)
        return {"items": [CardResponse.model_validate(c) for c in rows]}


async def _reload(s, cid: str) -> CardResponse:
    card = await service.get_card(s, cid)
    if card is None:
        raise HTTPException(404, "card not found")
    return CardResponse.model_validate(card)


@router.post("/cards", response_model=CardResponse, status_code=status.HTTP_201_CREATED)
async def create_card(payload: CardCreate):
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key=payload.project_key, entity_id=None,
            payload=payload.model_dump(exclude={"project_key"}))
        await s.commit()
        return await _reload(s, cid)


@router.get("/cards/{cid}", response_model=CardResponse)
async def get_card(cid: str):
    async with KanbanSessionLocal() as s:
        return await _reload(s, cid)


@router.get("/cards/{cid}/activity", response_model=list[ActivityEntry])
async def activity(cid: str):
    async with KanbanSessionLocal() as s:
        return await service.card_activity(s, cid)


@router.patch("/cards/{cid}", response_model=CardResponse)
async def update_card(cid: str, payload: CardUpdate):
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=cid,
            payload=payload.model_dump(exclude_unset=True))
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/move", response_model=CardResponse)
async def move_card(cid: str, payload: MoveRequest):
    if payload.column not in COLUMNS:
        raise HTTPException(422, f"unknown column: {payload.column}")
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=cid, payload=payload.model_dump())
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/claim", response_model=CardResponse)
async def claim_card(cid: str, payload: ClaimRequest):
    async with KanbanSessionLocal() as s:
        try:
            await apply_operation(s, op_type="claim", entity_type="card",
                project_key="", entity_id=cid, payload=payload.model_dump())
        except ClaimRejected as e:
            raise HTTPException(status.HTTP_409_CONFLICT, str(e))
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/release", response_model=CardResponse)
async def release_card(cid: str):
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="release", entity_type="card",
            project_key="", entity_id=cid, payload={})
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/comment", response_model=CardResponse)
async def comment(cid: str, payload: CommentRequest):
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=cid, payload=payload.model_dump())
        await s.commit()
        return await _reload(s, cid)


@router.post("/cards/{cid}/deliverables", response_model=CardResponse)
async def attach(cid: str, payload: AttachRequest):
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="", entity_id=cid, payload=payload.model_dump())
        await s.commit()
        return await _reload(s, cid)
```

Create empty `backend/app/api/v1/kanban/__init__.py`.

- [ ] **Step 4: Register the router**

In `backend/app/api/v1/router.py`, add near the other imports (around line 26):

```python
from .kanban.router import router as kanban_router
```

And after the `scheduled_messages_router` include (line ~66):

```python
router.include_router(kanban_router)
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_api.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Initialize the kanban DB on startup**

In `backend/app/main.py` lifespan, after the existing `init_db()` call, add:

```python
    from app.kanban.db import init_kanban_db
    await init_kanban_db()
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/v1/kanban backend/app/api/v1/router.py backend/app/main.py
git commit -m "feat(kanban): REST API (cards CRUD + move/claim/release/comment/deliverable)"
```

---

## Phase H — MCP server (the agent interface)

### Task H1: add the MCP SDK dependency

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Add `mcp` to dependencies**

In `backend/pyproject.toml`, add `"mcp>=1.2.0"` to the `dependencies` list.

- [ ] **Step 2: Install**

Run: `cd backend && source venv/bin/activate && pip install "mcp>=1.2.0"`
Expected: installs without error.

- [ ] **Step 3: Verify import**

Run: `cd backend && source venv/bin/activate && python -c "from mcp.server.fastmcp import FastMCP; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml
git commit -m "chore(kanban): add mcp SDK dependency"
```

### Task H2: FastMCP server exposing the 9 tools

**Files:**
- Create: `backend/app/kanban/mcp_server.py`
- Test: `backend/tests/test_kanban_mcp.py`

- [ ] **Step 1: Write the failing test (call tool functions directly)**

The tool callables are plain async functions wrapped by FastMCP; test them directly against a fresh DB.

```python
# backend/tests/test_kanban_mcp.py
import pytest
import pytest_asyncio

from app.kanban.db import KanbanBase, kanban_engine
from app.kanban import mcp_server as m


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with kanban_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.drop_all)
        await conn.run_sync(KanbanBase.metadata.create_all)
    yield


@pytest.mark.asyncio
async def test_create_then_list_then_claim():
    created = await m.create_card("P", "Do the thing", "details")
    cid = created["id"]
    listed = await m.list_cards("P")
    assert any(c["id"] == cid for c in listed)
    claimed = await m.claim_card(cid, "sess1@devA")
    assert claimed["claimed_by"] == "sess1@devA"


@pytest.mark.asyncio
async def test_claim_conflict_returns_error_dict():
    cid = (await m.create_card("P", "t", ""))["id"]
    await m.claim_card(cid, "first@d")
    result = await m.claim_card(cid, "second@d")
    assert result["error"] == "already_claimed"
    assert result["owner"] == "first@d"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_mcp.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.kanban.mcp_server'`

- [ ] **Step 3: Implement `mcp_server.py`**

```python
"""Kanban MCP server. The agent talks to this over localhost SSE; only the
backend reaches the store, so the agent never sees DB/sync credentials.

Each tool is a thin wrapper over apply_operation/service, returning plain
dicts (JSON-serializable) for the MCP layer.
"""
from mcp.server.fastmcp import FastMCP

from app.kanban.db import KanbanSessionLocal
from app.kanban import service
from app.kanban.operations import apply_operation, ClaimRejected
from app.kanban.schemas import CardResponse

mcp = FastMCP("cockpit-kanban")


def _card_dict(card) -> dict:
    return CardResponse.model_validate(card).model_dump(mode="json")


@mcp.tool()
async def list_cards(project: str, column: str | None = None) -> list[dict]:
    """List cards for a project, optionally filtered by column."""
    async with KanbanSessionLocal() as s:
        rows = await service.list_cards(s, project, column)
        return [_card_dict(c) for c in rows]


@mcp.tool()
async def get_card(card_id: str) -> dict:
    """Get a single card with its deliverables."""
    async with KanbanSessionLocal() as s:
        card = await service.get_card(s, card_id)
        return _card_dict(card) if card else {"error": "not_found"}


@mcp.tool()
async def create_card(project: str, title: str, description: str = "",
                      column: str = "Backlog") -> dict:
    """Create a new card (agents may decompose work into subtask cards)."""
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(s, op_type="create", entity_type="card",
            project_key=project, entity_id=None,
            payload={"title": title, "description": description, "column": column})
        await s.commit()
        return _card_dict(await service.get_card(s, cid))


@mcp.tool()
async def claim_card(card_id: str, claimed_by: str) -> dict:
    """Claim a card. Returns the card, or {error: already_claimed, owner}."""
    async with KanbanSessionLocal() as s:
        try:
            await apply_operation(s, op_type="claim", entity_type="card",
                project_key="", entity_id=card_id, payload={"claimed_by": claimed_by})
        except ClaimRejected as e:
            return {"error": "already_claimed", "owner": e.current_owner}
        await s.commit()
        return _card_dict(await service.get_card(s, card_id))


@mcp.tool()
async def move_card(card_id: str, column: str) -> dict:
    """Move a card to a different column."""
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="move", entity_type="card",
            project_key="", entity_id=card_id, payload={"column": column})
        await s.commit()
        return _card_dict(await service.get_card(s, card_id))


@mcp.tool()
async def update_card(card_id: str, title: str | None = None,
                      description: str | None = None) -> dict:
    """Update a card's title and/or description."""
    payload = {k: v for k, v in {"title": title, "description": description}.items()
               if v is not None}
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=card_id, payload=payload)
        await s.commit()
        return _card_dict(await service.get_card(s, card_id))


@mcp.tool()
async def comment(card_id: str, text: str) -> dict:
    """Add a comment to a card's activity feed."""
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="comment", entity_type="comment",
            project_key="", entity_id=card_id, payload={"text": text})
        await s.commit()
        return {"ok": True}


@mcp.tool()
async def attach_deliverable(card_id: str, kind: str, ref: str) -> dict:
    """Bind a deliverable (pr|branch|commit|link|note) as a portable reference."""
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="attach", entity_type="deliverable",
            project_key="", entity_id=card_id, payload={"kind": kind, "ref": ref})
        await s.commit()
        return _card_dict(await service.get_card(s, card_id))


@mcp.tool()
async def release_card(card_id: str) -> dict:
    """Release a claim on a card."""
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="release", entity_type="card",
            project_key="", entity_id=card_id, payload={})
        await s.commit()
        return _card_dict(await service.get_card(s, card_id))
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_mcp.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/mcp_server.py backend/tests/test_kanban_mcp.py
git commit -m "feat(kanban): FastMCP server with 9 board tools"
```

### Task H3: mount the MCP SSE app on FastAPI

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: Mount the SSE app**

In `backend/app/main.py`, after the FastAPI `app` is created and routers are included, add:

```python
    # Mount the kanban MCP server (SSE) at /kanban-mcp. Agents point their
    # .mcp.json at http://localhost:8000/kanban-mcp/sse.
    from app.kanban.mcp_server import mcp as kanban_mcp
    app.mount("/kanban-mcp", kanban_mcp.sse_app())
```

> If `FastMCP.sse_app()` is unavailable in the installed `mcp` version, use `kanban_mcp.streamable_http_app()` and document the `/mcp` path instead. Verify with `python -c "from app.kanban.mcp_server import mcp; print([a for a in dir(mcp) if 'app' in a])"`.

- [ ] **Step 2: Verify the app still boots**

Run: `cd backend && source venv/bin/activate && python -c "from app.main import app; print([r.path for r in app.routes if 'kanban' in r.path])"`
Expected: includes `/kanban-mcp`.

- [ ] **Step 3: Run the full backend suite (no regressions)**

Run: `cd backend && source venv/bin/activate && pytest tests/ -q`
Expected: all pass (existing 139 + new kanban tests).

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py
git commit -m "feat(kanban): mount MCP SSE app on FastAPI"
```

---

## Phase I — Opt-in: enable kanban + register MCP server per project

The board exists for any project, but "kanban mode" means registering the
`cockpit-kanban` MCP server in that project's `.mcp.json` so its agents can reach it.

### Task I1: enable/disable endpoints

**Files:**
- Modify: `backend/app/api/v1/kanban/router.py`
- Test: `backend/tests/test_kanban_api.py`

- [ ] **Step 1: Add failing test**

```python
@pytest.mark.asyncio
async def test_enable_writes_mcp_entry(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/enable",
            json={"project_path": str(tmp_path)})
        assert r.status_code == 200, r.text
        assert r.json()["project_key"]
        mcp_file = tmp_path / ".mcp.json"
        assert mcp_file.exists()
        assert "cockpit-kanban" in mcp_file.read_text()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_api.py -k enable -v`
Expected: FAIL — 404

- [ ] **Step 3: Implement the endpoints**

Add to `backend/app/kanban/schemas.py`:

```python
class EnableRequest(BaseModel):
    project_path: str
    slug: Optional[str] = None  # override when no git remote
```

Add to `backend/app/api/v1/kanban/router.py` (imports + routes):

```python
import json
from pathlib import Path
from app.kanban.project_key import resolve_project_key
from app.kanban.schemas import EnableRequest

MCP_SSE_URL = "http://localhost:8000/kanban-mcp/sse"


@router.post("/enable")
async def enable(payload: EnableRequest):
    path = Path(payload.project_path)
    if not path.is_dir():
        raise HTTPException(422, "project_path is not a directory")
    key = f"slug:{payload.slug}" if payload.slug else resolve_project_key(str(path))
    mcp_file = path / ".mcp.json"
    data = {}
    if mcp_file.exists():
        try:
            data = json.loads(mcp_file.read_text())
        except json.JSONDecodeError:
            data = {}
    data.setdefault("mcpServers", {})["cockpit-kanban"] = {
        "type": "sse", "url": MCP_SSE_URL,
    }
    mcp_file.write_text(json.dumps(data, indent=2))
    return {"project_key": key, "enabled": True}


@router.post("/disable")
async def disable(payload: EnableRequest):
    path = Path(payload.project_path)
    mcp_file = path / ".mcp.json"
    if mcp_file.exists():
        try:
            data = json.loads(mcp_file.read_text())
            data.get("mcpServers", {}).pop("cockpit-kanban", None)
            mcp_file.write_text(json.dumps(data, indent=2))
        except json.JSONDecodeError:
            pass
    return {"enabled": False}


@router.get("/project-key")
async def project_key(project_path: str = Query(...)):
    return {"project_key": resolve_project_key(project_path)}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_api.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/kanban/router.py backend/app/kanban/schemas.py backend/tests/test_kanban_api.py
git commit -m "feat(kanban): enable/disable opt-in (writes .mcp.json) + project-key endpoint"
```

---

## Phase J — Frontend feature module

> No frontend test harness exists. Verify every frontend task with
> `cd frontend && npm run lint && npm run build` (0 errors) plus the manual check noted.
> After building, the dist is served at :8000 (per project memory).

### Task J1: types + API client

**Files:**
- Create: `frontend/src/features/kanban/types.ts`
- Create: `frontend/src/features/kanban/api.ts`

- [ ] **Step 1: Write `types.ts`**

```typescript
export const COLUMNS = ["Backlog", "Analysis", "Todo", "Doing", "Review", "Done"] as const;
export type Column = (typeof COLUMNS)[number];

export interface Deliverable {
  id: string;
  kind: "pr" | "branch" | "commit" | "link" | "note";
  ref: string;
  created_at: string;
}

export interface Card {
  id: string;
  project_key: string;
  title: string;
  description: string;
  column: Column;
  rank: string;
  priority?: string | null;
  labels?: string[] | null;
  claimed_by?: string | null;
  claimed_at?: string | null;
  created_at: string;
  updated_at: string;
  deliverables: Deliverable[];
}

export interface ActivityEntry {
  hlc: string;
  op_type: string;
  entity_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}
```

- [ ] **Step 2: Write `api.ts` (follow the existing `@/lib/api` pattern)**

First confirm the helper name: `grep -n "export" frontend/src/lib/api.ts | head`. Use that client (below assumes a default `api` with `.get/.post/.patch` returning parsed JSON; adapt to the real signature).

```typescript
import { api } from "@/lib/api";
import type { Card, ActivityEntry } from "./types";

export const kanbanApi = {
  listCards: (projectKey: string, column?: string) =>
    api.get<{ items: Card[] }>(
      `/api/v1/kanban/cards?project_key=${encodeURIComponent(projectKey)}` +
        (column ? `&column=${encodeURIComponent(column)}` : ""),
    ),
  getCard: (id: string) => api.get<Card>(`/api/v1/kanban/cards/${id}`),
  activity: (id: string) => api.get<ActivityEntry[]>(`/api/v1/kanban/cards/${id}/activity`),
  createCard: (body: { project_key: string; title: string; description?: string; column?: string }) =>
    api.post<Card>(`/api/v1/kanban/cards`, body),
  updateCard: (id: string, body: { title?: string; description?: string }) =>
    api.patch<Card>(`/api/v1/kanban/cards/${id}`, body),
  move: (id: string, column: string) =>
    api.post<Card>(`/api/v1/kanban/cards/${id}/move`, { column }),
  claim: (id: string, claimedBy: string) =>
    api.post<Card>(`/api/v1/kanban/cards/${id}/claim`, { claimed_by: claimedBy }),
  release: (id: string) => api.post<Card>(`/api/v1/kanban/cards/${id}/release`, {}),
  comment: (id: string, text: string) =>
    api.post<Card>(`/api/v1/kanban/cards/${id}/comment`, { text }),
  attach: (id: string, kind: string, ref: string) =>
    api.post<Card>(`/api/v1/kanban/cards/${id}/deliverables`, { kind, ref }),
  projectKey: (projectPath: string) =>
    api.get<{ project_key: string }>(
      `/api/v1/kanban/project-key?project_path=${encodeURIComponent(projectPath)}`),
  enable: (projectPath: string, slug?: string) =>
    api.post<{ project_key: string }>(`/api/v1/kanban/enable`, { project_path: projectPath, slug }),
  disable: (projectPath: string) =>
    api.post<{ enabled: boolean }>(`/api/v1/kanban/disable`, { project_path: projectPath }),
};
```

- [ ] **Step 3: Build check**

Run: `cd frontend && npm run build`
Expected: 0 errors (unused exports are fine; types resolve).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/kanban/types.ts frontend/src/features/kanban/api.ts
git commit -m "feat(kanban): frontend types + API client"
```

### Task J2: Board, Column, CardItem components

**Files:**
- Create: `frontend/src/features/kanban/components/CardItem.tsx`
- Create: `frontend/src/features/kanban/components/Column.tsx`
- Create: `frontend/src/features/kanban/components/Board.tsx`

- [ ] **Step 1: `CardItem.tsx` (clickable card using the project convention)**

```tsx
import { Card as UiCard } from "@/components/ui/card";
import { CLICKABLE_CARD } from "@/lib/constants";
import type { Card } from "../types";

export function CardItem({ card, onOpen }: { card: Card; onOpen: (c: Card) => void }) {
  return (
    <UiCard
      className={`${CLICKABLE_CARD} p-3 mb-2`}
      role="button"
      tabIndex={0}
      onClick={() => onOpen(card)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(card); }
      }}
    >
      <div className="font-medium text-sm">{card.title}</div>
      <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
        {card.claimed_by && <span>👤 {card.claimed_by}</span>}
        {card.deliverables.length > 0 && <span>📎 {card.deliverables.length}</span>}
      </div>
    </UiCard>
  );
}
```

- [ ] **Step 2: `Column.tsx` (drop target)**

```tsx
import type { Card, Column as Col } from "../types";
import { CardItem } from "./CardItem";

export function Column({
  column, cards, onOpen, onDropCard,
}: {
  column: Col;
  cards: Card[];
  onOpen: (c: Card) => void;
  onDropCard: (cardId: string, column: Col) => void;
}) {
  return (
    <div
      className="flex-1 min-w-56 bg-muted/40 rounded-lg p-2"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => onDropCard(e.dataTransfer.getData("text/card-id"), column)}
    >
      <div className="px-1 pb-2 text-xs font-semibold uppercase text-muted-foreground">
        {column} <span className="ml-1">({cards.length})</span>
      </div>
      {cards.map((c) => (
        <div
          key={c.id}
          draggable
          onDragStart={(e) => e.dataTransfer.setData("text/card-id", c.id)}
        >
          <CardItem card={c} onOpen={onOpen} />
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: `Board.tsx`**

```tsx
import { COLUMNS, type Card, type Column as Col } from "../types";
import { Column } from "./Column";

export function Board({
  cards, onOpen, onMove,
}: {
  cards: Card[];
  onOpen: (c: Card) => void;
  onMove: (cardId: string, column: Col) => void;
}) {
  return (
    <div className="flex gap-3 overflow-x-auto pb-4">
      {COLUMNS.map((col) => (
        <Column
          key={col}
          column={col}
          cards={cards.filter((c) => c.column === col)}
          onOpen={onOpen}
          onDropCard={onMove}
        />
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Build check**

Run: `cd frontend && npm run build`
Expected: 0 errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/kanban/components/Board.tsx frontend/src/features/kanban/components/Column.tsx frontend/src/features/kanban/components/CardItem.tsx
git commit -m "feat(kanban): Board/Column/CardItem with drag-to-move"
```

### Task J3: CardDrawer (detail + activity + deliverables) and CardEditDialog

**Files:**
- Create: `frontend/src/features/kanban/components/CardDrawer.tsx`
- Create: `frontend/src/features/kanban/components/CardEditDialog.tsx`

- [ ] **Step 1: `CardEditDialog.tsx` (create/edit, markdown description)**

```tsx
import { useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { MarkdownPreviewToggle } from "@/components/shared/MarkdownPreviewToggle";
import { MODAL_SIZES } from "@/lib/constants";

export function CardEditDialog({
  open, initial, onClose, onSubmit,
}: {
  open: boolean;
  initial?: { title: string; description: string };
  onClose: () => void;
  onSubmit: (data: { title: string; description: string }) => void;
}) {
  const [title, setTitle] = useState(initial?.title ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader><DialogTitle>{initial ? "Edit card" : "New card"}</DialogTitle></DialogHeader>
        <Input placeholder="Title" value={title} onChange={(e) => setTitle(e.target.value)} />
        <MarkdownPreviewToggle value={description} onChange={setDescription} />
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button disabled={!title.trim()} onClick={() => onSubmit({ title, description })}>Save</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

> Confirm `MarkdownPreviewToggle`'s prop names with `grep -n "function MarkdownPreviewToggle\|props" frontend/src/components/shared/MarkdownPreviewToggle.tsx` and adjust `value`/`onChange` if they differ.

- [ ] **Step 2: `CardDrawer.tsx` (detail + activity + deliverables + claim/release)**

```tsx
import { useEffect, useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { MarkdownRenderer } from "@/components/shared/MarkdownRenderer";
import { MODAL_SIZES } from "@/lib/constants";
import { kanbanApi } from "../api";
import type { Card, ActivityEntry } from "../types";

export function CardDrawer({
  card, onClose, onChanged,
}: {
  card: Card;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [activity, setActivity] = useState<ActivityEntry[]>([]);
  useEffect(() => { kanbanApi.activity(card.id).then(setActivity); }, [card.id]);

  const act = async (fn: () => Promise<unknown>) => { await fn(); onChanged(); };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className={MODAL_SIZES.LG}>
        <DialogHeader><DialogTitle>{card.title}</DialogTitle></DialogHeader>

        <div className="text-sm"><MarkdownRenderer content={card.description || "_No description_"} /></div>

        <div className="flex items-center gap-2 text-xs">
          {card.claimed_by
            ? <Button size="sm" variant="outline" onClick={() => act(() => kanbanApi.release(card.id))}>
                Release ({card.claimed_by})
              </Button>
            : <Button size="sm" onClick={() => act(() => kanbanApi.claim(card.id, "me@ui"))}>Claim</Button>}
        </div>

        <div>
          <div className="text-xs font-semibold mb-1">Deliverables</div>
          {card.deliverables.length === 0 && <div className="text-xs text-muted-foreground">None</div>}
          {card.deliverables.map((d) => (
            <div key={d.id} className="text-xs">{d.kind}: {d.ref}</div>
          ))}
        </div>

        <div>
          <div className="text-xs font-semibold mb-1">Activity</div>
          {activity.map((e) => (
            <div key={e.hlc} className="text-xs text-muted-foreground">
              {e.op_type} — {new Date(e.created_at).toLocaleString()}
              {e.op_type === "comment" ? `: ${String(e.payload.text ?? "")}` : ""}
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
```

> Confirm `MarkdownRenderer`'s prop name (`content` vs `children`) with a quick grep and adjust.

- [ ] **Step 3: Build check**

Run: `cd frontend && npm run build`
Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/kanban/components/CardDrawer.tsx frontend/src/features/kanban/components/CardEditDialog.tsx
git commit -m "feat(kanban): card drawer (detail/activity/deliverables) + edit dialog"
```

### Task J4: KanbanPage (wires everything) + EnableKanbanToggle

**Files:**
- Create: `frontend/src/features/kanban/components/EnableKanbanToggle.tsx`
- Create: `frontend/src/features/kanban/KanbanPage.tsx`
- Modify: `frontend/src/App.tsx` (route) and the nav/sidebar

- [ ] **Step 1: `EnableKanbanToggle.tsx`**

```tsx
import { Button } from "@/components/ui/button";
import { kanbanApi } from "../api";

export function EnableKanbanToggle({ projectPath, onChanged }: { projectPath: string; onChanged: () => void }) {
  return (
    <div className="flex gap-2">
      <Button size="sm" variant="outline"
        onClick={async () => { await kanbanApi.enable(projectPath); onChanged(); }}>
        Enable kanban (register MCP)
      </Button>
      <Button size="sm" variant="ghost"
        onClick={async () => { await kanbanApi.disable(projectPath); onChanged(); }}>
        Disable
      </Button>
    </div>
  );
}
```

- [ ] **Step 2: `KanbanPage.tsx`**

Use the existing project context to get the selected project. Confirm its shape with
`grep -n "useProject\|ProjectContext\|currentProject\|selectedProject" frontend/src/contexts/ProjectContext.tsx` and adapt the hook/fields below.

```tsx
import { useCallback, useEffect, useState } from "react";
import { useProject } from "@/contexts/ProjectContext"; // adapt to real export
import { Button } from "@/components/ui/button";
import { Board } from "./components/Board";
import { CardDrawer } from "./components/CardDrawer";
import { CardEditDialog } from "./components/CardEditDialog";
import { EnableKanbanToggle } from "./components/EnableKanbanToggle";
import { kanbanApi } from "./api";
import type { Card, Column as Col } from "./types";

export default function KanbanPage() {
  const { currentProject } = useProject(); // adapt: needs project.path
  const projectPath = currentProject?.path ?? "";
  const [projectKey, setProjectKey] = useState<string>("");
  const [cards, setCards] = useState<Card[]>([]);
  const [open, setOpen] = useState<Card | null>(null);
  const [creating, setCreating] = useState(false);

  const reload = useCallback(async () => {
    if (!projectKey) return;
    const { items } = await kanbanApi.listCards(projectKey);
    setCards(items);
    if (open) setOpen(items.find((c) => c.id === open.id) ?? null);
  }, [projectKey, open]);

  useEffect(() => {
    if (!projectPath) return;
    kanbanApi.projectKey(projectPath).then((r) => setProjectKey(r.project_key));
  }, [projectPath]);
  useEffect(() => { void reload(); }, [projectKey]); // eslint-disable-line

  const onMove = async (cardId: string, column: Col) => {
    setCards((cs) => cs.map((c) => (c.id === cardId ? { ...c, column } : c))); // optimistic
    await kanbanApi.move(cardId, column);
    void reload();
  };

  if (!projectPath) return <div className="p-6">Select a project first.</div>;

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold">Kanban</h1>
          <div className="text-xs text-muted-foreground">{projectKey || "…"}</div>
        </div>
        <div className="flex gap-2">
          <EnableKanbanToggle projectPath={projectPath} onChanged={reload} />
          <Button size="sm" onClick={() => setCreating(true)}>New card</Button>
        </div>
      </div>

      <Board cards={cards} onOpen={setOpen} onMove={onMove} />

      {open && <CardDrawer card={open} onClose={() => setOpen(null)} onChanged={reload} />}
      {creating && (
        <CardEditDialog
          open
          onClose={() => setCreating(false)}
          onSubmit={async ({ title, description }) => {
            await kanbanApi.createCard({ project_key: projectKey, title, description });
            setCreating(false);
            void reload();
          }}
        />
      )}
    </div>
  );
}
```

- [ ] **Step 3: Add the route + nav entry**

In `frontend/src/App.tsx`, import and add a route (match the existing route style):

```tsx
import KanbanPage from "@/features/kanban/KanbanPage";
// ...inside the routes:
<Route path="/kanban" element={<KanbanPage />} />
```

Add a sidebar/menu link to `/kanban` next to the other feature links (find the nav with
`grep -rn "scheduled-messages\|/plans\|to=\"/" frontend/src/components/layout` and follow that pattern).

- [ ] **Step 4: Lint + build**

Run: `cd frontend && npm run lint && npm run build`
Expected: 0 lint errors, 0 build errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/kanban frontend/src/App.tsx frontend/src/components/layout
git commit -m "feat(kanban): KanbanPage, enable toggle, route + nav entry"
```

---

## Phase K — Sync scaffolding (seam only; not activated)

### Task K1: sync transport interface + local no-op + ops-since query

**Files:**
- Create: `backend/app/kanban/sync.py`
- Test: `backend/tests/test_kanban_service.py` (append)

- [ ] **Step 1: Add failing tests**

```python
from app.kanban import sync as sync_mod
from app.kanban.operations import apply_operation


@pytest.mark.asyncio
async def test_ops_since_returns_ops_after_cursor():
    async with KanbanSessionLocal() as s:
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "a"})
        await apply_operation(s, op_type="create", entity_type="card",
            project_key="p", entity_id=None, payload={"title": "b"})
        await s.commit()
        first_two = await sync_mod.ops_since(s, cursor=None)
        assert len(first_two) == 2
        after = await sync_mod.ops_since(s, cursor=first_two[0].hlc)
        assert len(after) == 1
        assert after[0].payload["title"] == "b"


@pytest.mark.asyncio
async def test_ingest_foreign_ops_then_rematerialize():
    foreign = {
        "op_id": "devB:1", "device_id": "devB", "seq": 1,
        "hlc": "9999999999999:00000:devB", "project_key": "p",
        "entity_type": "card", "entity_id": "extern1", "op_type": "create",
        "payload": {"title": "fromB", "column": "Backlog"},
    }
    async with KanbanSessionLocal() as s:
        await sync_mod.ingest_ops(s, [foreign])
        await s.commit()
        from app.kanban.models import KanbanCard
        card = await s.get(KanbanCard, "extern1")
        assert card is not None and card.title == "fromB"


@pytest.mark.asyncio
async def test_ingest_is_idempotent():
    foreign = {
        "op_id": "devB:1", "device_id": "devB", "seq": 1,
        "hlc": "9999999999999:00000:devB", "project_key": "p",
        "entity_type": "card", "entity_id": "extern1", "op_type": "create",
        "payload": {"title": "fromB"},
    }
    async with KanbanSessionLocal() as s:
        await sync_mod.ingest_ops(s, [foreign])
        await sync_mod.ingest_ops(s, [foreign])  # second time = no-op
        await s.commit()
        from sqlalchemy import select, func
        from app.kanban.models import KanbanOp
        n = (await s.execute(select(func.count()).select_from(KanbanOp))).scalar()
        assert n == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_service.py -k "ops_since or ingest" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.kanban.sync'`

- [ ] **Step 3: Implement `sync.py`**

```python
"""Sync seam (not activated in v1). The op-log is append-only, so a sync
transport only needs: pull foreign ops, push local ops. Conflict logic lives
in materialization (operations.py), not here.
"""
from typing import Protocol

from sqlalchemy import select

from app.kanban.hlc import HLC
from app.kanban.models import KanbanOp
from app.kanban.operations import _clock_for, rematerialize  # reuse the clock


async def ops_since(session, cursor: str | None):
    """Return ops with hlc strictly greater than cursor, in hlc order."""
    stmt = select(KanbanOp).order_by(KanbanOp.hlc.asc())
    if cursor is not None:
        stmt = stmt.where(KanbanOp.hlc > cursor)
    return (await session.execute(stmt)).scalars().all()


async def ingest_ops(session, ops: list[dict]) -> int:
    """Insert foreign ops idempotently (by op_id), advance the clock past
    them, then rebuild materialized state. Returns count of newly inserted.
    """
    clock: HLC = await _clock_for(session)
    inserted = 0
    for op in ops:
        if await session.get(KanbanOp, op["op_id"]) is not None:
            continue
        session.add(KanbanOp(
            op_id=op["op_id"], device_id=op["device_id"], seq=op["seq"],
            hlc=op["hlc"], project_key=op["project_key"],
            entity_type=op["entity_type"], entity_id=op["entity_id"],
            op_type=op["op_type"], payload=op["payload"],
        ))
        clock.update(op["hlc"])
        inserted += 1
    await session.flush()
    if inserted:
        await rematerialize(session)
    return inserted


class SyncTransport(Protocol):
    async def pull(self, cursor: str | None) -> list[dict]: ...
    async def push(self, ops: list[dict]) -> None: ...


class LocalNoopTransport:
    """Default transport in v1: no remote. pull returns nothing, push drops."""
    async def pull(self, cursor: str | None) -> list[dict]:
        return []

    async def push(self, ops: list[dict]) -> None:
        return None
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_service.py -v`
Expected: PASS (all)

- [ ] **Step 5: Full backend suite**

Run: `cd backend && source venv/bin/activate && pytest tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/kanban/sync.py backend/tests/test_kanban_service.py
git commit -m "feat(kanban): sync seam (ops_since, idempotent ingest, transport protocol)"
```

---

## Final verification

- [ ] **Backend:** `cd backend && source venv/bin/activate && pytest tests/ -q` → all pass.
- [ ] **Frontend:** `cd frontend && npm run lint && npm run build` → 0 errors.
- [ ] **Manual e2e (WSL):**
  1. Start the backend; open the UI, go to **Kanban**, select a project.
  2. Create a card, drag it across columns, open the drawer, claim/release, attach a deliverable (a PR URL).
  3. Click **Enable kanban** → confirm `.mcp.json` in the project now has `cockpit-kanban`.
  4. In a tmux `claude` session inside that project, confirm the `cockpit-kanban` MCP tools are listed; have the agent `list_cards`, `claim_card`, `attach_deliverable`, and `move_card` to Review; confirm the UI reflects it.
- [ ] Update `docs/cockpit/00-orientation.md` to point at this plan as the active work.

## Spec coverage check

| Spec section | Covered by |
|---|---|
| §1 Columns & lifecycle | schemas `COLUMNS` (C2), move validation (G1) |
| §2a op-log | `KanbanOp` (C1), `apply_operation` (E1–E4) |
| §2b materialized + per-field HLC | `KanbanCard`/`KanbanDeliverable` (C1), LWW (E2) |
| §3 single mutation pipeline | `apply_operation` (E1) used by REST (G1) + MCP (H2) |
| §4 HLC + conflict policy | HLC (B1); move/update LWW (E2); conditional claim (E3); comment/attach additive (E4); rematerialize (E5) |
| §5 MCP server + tools | `mcp_server.py` (H2), mount (H3); initiative-layer = docs/CLAUDE.md instruction (manual e2e) |
| §6 opt-in + project key | enable/disable + `.mcp.json` (I1); `resolve_project_key` (D1) |
| §7 UI feature module | Phase J |
| §8 sync-ready | Phase K (seam), `rematerialize` (E5) |
| §9 dependencies / testability | each unit independently tested |
| §10 testing | unit (B/D/E/F), convergence (K1 ingest), integration (G1/H2) |
| §11 non-goals | custom columns, push-on-idle, merge-UI, multi-user, running a remote primary — all excluded |
