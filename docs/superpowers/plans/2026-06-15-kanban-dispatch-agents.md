# Kanban Dispatch Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a card the dispatcher picks up run autonomously end-to-end with a column-matched persona (Analyst for `Analysis`, Developer for `Todo`), shipping its result via a per-project mode — direct merge to master, or a draft pull request.

**Architecture:** Personas live as editable markdown files under `.claude/`; the dispatcher reads the matching file and injects it (plus the card and the project's ship mode) into the spawn prompt. Sessions spawn with permissions skipped, in a worktree the dispatcher itself creates from `origin/master`. A per-project `shipmode` flag (device-local in `KanbanMeta`, like the existing `autodispatch` flag) selects the developer's terminal ship step, encoded in a `git-ship` skill.

**Tech Stack:** FastAPI + async SQLAlchemy + aiosqlite (backend); pytest/pytest-asyncio; React 19 + TypeScript + shadcn/ui (frontend); git worktrees + `gh` CLI; tmux transport.

---

## File Structure

**Backend**
- `backend/app/kanban/dispatch.py` (modify) — ship-mode storage, persona selection + file reading, prompt builder v2, poll `Analysis`+`Todo`, worktree-from-`origin/master` transport.
- `backend/app/kanban/schemas.py` (modify) — `ShipModeRequest`.
- `backend/app/api/v1/kanban/router.py` (modify) — `GET/POST /kanban/shipmode`.
- `backend/app/services/providers/base.py` (modify) — `SpawnCommandOptions.worktree_path` + `repo_path`.
- `backend/app/services/agent_bridge/spawn.py` (modify) — record worktree/repo path in metadata; clean up dispatcher-owned worktrees in `kill_session`.
- `backend/tests/test_kanban_dispatch.py` (modify) — update for new signatures + new behavior.
- `backend/tests/test_kanban_shipmode.py` (create) — ship-mode storage + API.
- `backend/tests/test_spawn_worktree_cleanup.py` (create) — metadata + cleanup.

**Frontend**
- `frontend/src/features/kanban/api.ts` (modify) — `getShipMode` / `setShipMode`.
- `frontend/src/features/kanban/components/ShipModeToggle.tsx` (create) — selector.
- `frontend/src/features/kanban/KanbanPage.tsx` (modify) — render selector.

**Repo persona/skill files (committed so they exist in `origin/master` worktrees)**
- `.claude/agents/kanban-analyst.md` (create)
- `.claude/agents/kanban-developer.md` (create)
- `.claude/skills/git-ship/SKILL.md` (create)

---

## Task 1: Ship-mode storage in dispatch.py

**Files:**
- Modify: `backend/app/kanban/dispatch.py`
- Test: `backend/tests/test_kanban_shipmode.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_kanban_shipmode.py`:

```python
# backend/tests/test_kanban_shipmode.py
import pytest
import pytest_asyncio

from app.kanban.db import KanbanBase, kanban_engine, KanbanSessionLocal
from app.kanban import dispatch

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    async with kanban_engine.begin() as conn:
        await conn.run_sync(KanbanBase.metadata.drop_all)
        await conn.run_sync(KanbanBase.metadata.create_all)
    yield


PK = "git:example.com/me/repo"


async def test_ship_mode_defaults_to_pull_request():
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_ship_mode(s, PK) == "pull-request"


async def test_set_and_get_ship_mode():
    async with KanbanSessionLocal() as s:
        await dispatch.set_ship_mode(s, PK, "direct")
        await s.commit()
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_ship_mode(s, PK) == "direct"


async def test_set_ship_mode_rejects_unknown():
    async with KanbanSessionLocal() as s:
        with pytest.raises(ValueError):
            await dispatch.set_ship_mode(s, PK, "yolo")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_shipmode.py -v`
Expected: FAIL — `AttributeError: module 'app.kanban.dispatch' has no attribute 'get_ship_mode'`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/kanban/dispatch.py`, add near the other `META_PREFIX` constant:

```python
SHIPMODE_PREFIX = "shipmode:"
SHIP_MODES = ("pull-request", "direct")
DEFAULT_SHIP_MODE = "pull-request"
```

Add these functions after `list_autodispatch_projects`:

```python
async def get_ship_mode(session, project_key: str) -> str:
    row = await session.get(KanbanMeta, SHIPMODE_PREFIX + project_key)
    if row and row.value in SHIP_MODES:
        return row.value
    return DEFAULT_SHIP_MODE


async def set_ship_mode(session, project_key: str, mode: str) -> None:
    if mode not in SHIP_MODES:
        raise ValueError(f"unknown ship mode: {mode}")
    key = SHIPMODE_PREFIX + project_key
    row = await session.get(KanbanMeta, key)
    if row is None:
        session.add(KanbanMeta(key=key, value=mode))
    else:
        row.value = mode
    await session.flush()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_shipmode.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/dispatch.py backend/tests/test_kanban_shipmode.py
git commit -m "feat(kanban): per-project ship mode storage in KanbanMeta"
```

---

## Task 2: Ship-mode API endpoints

**Files:**
- Modify: `backend/app/kanban/schemas.py`
- Modify: `backend/app/api/v1/kanban/router.py`
- Test: `backend/tests/test_kanban_shipmode.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_kanban_shipmode.py`:

```python
from httpx import ASGITransport, AsyncClient

from app.main import app


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_get_shipmode_endpoint_defaults():
    async with await _client() as c:
        r = await c.get("/api/v1/kanban/shipmode", params={"project_key": PK})
    assert r.status_code == 200
    assert r.json() == {"project_key": PK, "mode": "pull-request"}


async def test_post_shipmode_endpoint_sets_value():
    async with await _client() as c:
        r = await c.post("/api/v1/kanban/shipmode", json={"project_key": PK, "mode": "direct"})
        assert r.status_code == 200
        assert r.json() == {"project_key": PK, "mode": "direct"}
        r2 = await c.get("/api/v1/kanban/shipmode", params={"project_key": PK})
    assert r2.json()["mode"] == "direct"


async def test_post_shipmode_rejects_unknown():
    async with await _client() as c:
        r = await c.post("/api/v1/kanban/shipmode", json={"project_key": PK, "mode": "yolo"})
    assert r.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_shipmode.py -k shipmode_endpoint -v`
Expected: FAIL — 404 Not Found (route does not exist)

- [ ] **Step 3: Write minimal implementation**

In `backend/app/kanban/schemas.py`, after `AutodispatchRequest`:

```python
class ShipModeRequest(BaseModel):
    project_key: str
    mode: str
```

In `backend/app/api/v1/kanban/router.py`, add `ShipModeRequest` to the schema import list, then append:

```python
@router.get("/shipmode")
async def get_shipmode(project_key: str = Query(...)):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {"project_key": project_key,
                "mode": await dispatch.get_ship_mode(s, project_key)}


@router.post("/shipmode")
async def set_shipmode(payload: ShipModeRequest):
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            await dispatch.set_ship_mode(s, payload.project_key, payload.mode)
        except ValueError as e:
            raise HTTPException(422, str(e))
        await s.commit()
    return {"project_key": payload.project_key, "mode": payload.mode}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_shipmode.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/schemas.py backend/app/api/v1/kanban/router.py backend/tests/test_kanban_shipmode.py
git commit -m "feat(kanban): GET/POST /kanban/shipmode endpoints"
```

---

## Task 3: Persona file reading + column→persona mapping

**Files:**
- Modify: `backend/app/kanban/dispatch.py`
- Test: `backend/tests/test_kanban_personas.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_kanban_personas.py`:

```python
# backend/tests/test_kanban_personas.py
from pathlib import Path

from app.kanban import dispatch


def test_strip_frontmatter_removes_yaml_block():
    body = dispatch._strip_frontmatter("---\nname: x\n---\nHello\nWorld\n")
    assert body == "Hello\nWorld\n"


def test_strip_frontmatter_passthrough_when_absent():
    assert dispatch._strip_frontmatter("Just text") == "Just text"


def test_persona_filename_for_column():
    assert dispatch._persona_filename("Analysis") == "kanban-analyst.md"
    assert dispatch._persona_filename("Todo") == "kanban-developer.md"
    assert dispatch._persona_filename("Backlog") is None


def test_read_persona_returns_body(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "kanban-developer.md").write_text("---\nname: dev\n---\nBe a developer.\n")
    assert dispatch._read_persona(str(tmp_path), "Todo") == "Be a developer."


def test_read_persona_missing_file_returns_none(tmp_path):
    assert dispatch._read_persona(str(tmp_path), "Todo") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_personas.py -v`
Expected: FAIL — `AttributeError: module 'app.kanban.dispatch' has no attribute '_strip_frontmatter'`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/kanban/dispatch.py`, add after the prompt section's imports (top already has `from pathlib import Path`):

```python
_PERSONA_BY_COLUMN = {
    "Analysis": "kanban-analyst.md",
    "Todo": "kanban-developer.md",
}


def _persona_filename(column: str) -> Optional[str]:
    return _PERSONA_BY_COLUMN.get(column)


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + len("\n---\n"):]
    return text


def _read_persona(project_path: str, column: str) -> Optional[str]:
    filename = _persona_filename(column)
    if not filename:
        return None
    path = Path(project_path) / ".claude" / "agents" / filename
    try:
        return _strip_frontmatter(path.read_text()).strip()
    except OSError:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_personas.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/dispatch.py backend/tests/test_kanban_personas.py
git commit -m "feat(kanban): read column-matched persona files for dispatch prompts"
```

---

## Task 4: Prompt builder v2 (persona + ship mode + terminal conditions)

**Files:**
- Modify: `backend/app/kanban/dispatch.py`
- Test: `backend/tests/test_kanban_dispatch.py` (modify existing prompt test)

- [ ] **Step 1: Write the failing test**

In `backend/tests/test_kanban_dispatch.py`, replace the existing `build_card_prompt` test with:

```python
async def test_card_prompt_includes_persona_card_and_shipmode():
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="Build widget")
        await apply_operation(s, op_type="update", entity_type="card",
            project_key="", entity_id=cid, payload={"description": "Make it blue"})
        await s.flush()
        card = await get_card(s, cid)
    prompt = dispatch.build_card_prompt(
        card, persona="You are the Developer agent.", ship_mode="direct",
    )
    assert "You are the Developer agent." in prompt
    assert "Build widget" in prompt
    assert "Make it blue" in prompt
    assert "Ship mode: direct" in prompt
    assert "git-ship" in prompt
    assert "cockpit-kanban" in prompt


def test_card_prompt_without_persona_still_works():
    class _C:
        title = "T"
        description = ""
    prompt = dispatch.build_card_prompt(_C(), persona=None, ship_mode="pull-request")
    assert "Ship mode: pull-request" in prompt
    assert "# T" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py -k prompt -v`
Expected: FAIL — `TypeError: build_card_prompt() got an unexpected keyword argument 'persona'`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/kanban/dispatch.py`, replace the whole `build_card_prompt` function with:

```python
def build_card_prompt(card, *, persona: Optional[str], ship_mode: str) -> str:
    preamble = (persona.strip() + "\n\n") if persona else ""
    return (
        f"{preamble}"
        "You are picking up a Kanban card from the Claude Cockpit board. "
        'It is already claimed by you and moved to "Doing".\n\n'
        f"# {card.title}\n"
        f"{getattr(card, 'description', '') or ''}\n\n"
        f"Ship mode: {ship_mode}\n\n"
        "Work autonomously to completion. When the code is ready, invoke the "
        "`git-ship` skill, which runs the tests and — only if they pass — ships per "
        "the ship mode above (direct merge to master, or a draft pull request).\n"
        "Then use the `cockpit-kanban` MCP tools to move the card to \"Review\" "
        "(`move_card`) and attach your result with `attach_deliverable` (branch or PR "
        "URL). If you cannot finish or the tests fail, leave a `comment` explaining why "
        "and leave the card in \"Doing\"."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py -k prompt -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/dispatch.py backend/tests/test_kanban_dispatch.py
git commit -m "feat(kanban): prompt builder injects persona + ship mode + terminal conditions"
```

---

## Task 5: dispatch_project polls Analysis+Todo and wires persona + ship mode

**Files:**
- Modify: `backend/app/kanban/dispatch.py`
- Test: `backend/tests/test_kanban_dispatch.py` (modify)

- [ ] **Step 1: Write the failing test**

In `backend/tests/test_kanban_dispatch.py`, first update `RecordingTransport.__call__` to accept the unchanged signature (it already is `*, directory, prompt, session_name`). Then add:

```python
async def test_dispatch_picks_analysis_with_analyst_persona(tmp_path, monkeypatch):
    # persona file present so the prompt carries the analyst body
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "kanban-analyst.md").write_text("You are the Analyst.")
    t = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="Investigate", column="Analysis")
        await s.commit()
    async with KanbanSessionLocal() as s:
        res = await dispatch.dispatch_project(
            s, project_key=PK, project_path=str(tmp_path), transport=t)
        await s.commit()
    assert res is not None
    assert "You are the Analyst." in t.calls[0]["prompt"]


async def test_dispatch_prefers_todo_over_analysis(tmp_path):
    t = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await _make_card(s, title="A-card", column="Analysis")
        await _make_card(s, title="T-card", column="Todo")
        await s.commit()
    async with KanbanSessionLocal() as s:
        res = await dispatch.dispatch_project(
            s, project_key=PK, project_path=str(tmp_path), transport=t)
        await s.commit()
    assert res is not None
    assert "T-card" in t.calls[0]["prompt"]


async def test_dispatch_injects_ship_mode(tmp_path):
    t = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await dispatch.set_ship_mode(s, PK, "direct")
        await _make_card(s, title="T-card", column="Todo")
        await s.commit()
    async with KanbanSessionLocal() as s:
        await dispatch.dispatch_project(
            s, project_key=PK, project_path=str(tmp_path), transport=t)
        await s.commit()
    assert "Ship mode: direct" in t.calls[0]["prompt"]
```

Also update any existing test that referenced only-Todo behavior so the no-op/busy assertions still hold (those use `column="Todo"` and remain valid).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py -k "analysis or prefers or ship_mode" -v`
Expected: FAIL — current `dispatch_project` only looks at `Todo` and calls `build_card_prompt(card)` (old signature)

- [ ] **Step 3: Write minimal implementation**

In `backend/app/kanban/dispatch.py`, add a candidate selector above `dispatch_project`:

```python
_DISPATCH_COLUMNS = ("Todo", "Analysis")  # Todo drains first, then Analysis


def _next_card(cards: Iterable[KanbanCard]) -> Optional[KanbanCard]:
    cards = list(cards)
    for col in _DISPATCH_COLUMNS:
        col_cards = [c for c in cards if c.column == col and not c.claimed_by]
        if col_cards:
            return col_cards[0]  # list_cards is ordered by rank
    return None
```

Then replace the body of `dispatch_project` from the `todo = ...` selection through the `build_card_prompt` call:

```python
async def dispatch_project(
    session, *, project_key: str, project_path: str, transport: SpawnTransport,
) -> Optional[dict]:
    """Claim+move+spawn the next Analysis/Todo card for one project. Returns a result
    dict or None when there is nothing to do (no candidate card, or project is busy)."""
    cards = await list_cards(session, project_key)
    if _project_is_busy(cards):
        return None

    card = _next_card(cards)
    if card is None:
        return None
    source_column = card.column

    name = _mint_session_name(project_path)
    claimant = CLAIMANT_PREFIX + name

    try:
        await apply_operation(
            session, op_type="claim", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={"claimed_by": claimant},
        )
    except ClaimRejected:
        return None  # lost the race; another tick/device took it

    await apply_operation(
        session, op_type="move", entity_type="card", project_key=project_key,
        entity_id=card.id, payload={"column": "Doing"},
    )

    persona = _read_persona(project_path, source_column)
    ship_mode = await get_ship_mode(session, project_key)
    prompt = build_card_prompt(card, persona=persona, ship_mode=ship_mode)
    try:
        spawned = transport(directory=project_path, prompt=prompt, session_name=name)
    except Exception:
        await apply_operation(
            session, op_type="release", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={},
        )
        await apply_operation(
            session, op_type="move", entity_type="card", project_key=project_key,
            entity_id=card.id, payload={"column": source_column},
        )
        logger.exception("spawn failed for card %s in %s", card.id, project_key)
        raise

    logger.info("dispatched card %s (%s) -> session %s", card.id, source_column, name)
    return {"card_id": card.id, "session_name": name, "claimant": claimant,
            "source_column": source_column, "spawned": spawned}
```

Note: the compensation now returns the card to its **source column** (Analysis or Todo), not hardcoded Todo.

- [ ] **Step 4: Run full dispatch test file**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py -v`
Expected: PASS (all tests, including the updated busy/no-op/claim-race cases)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/dispatch.py backend/tests/test_kanban_dispatch.py
git commit -m "feat(kanban): dispatch Analysis+Todo with persona and ship mode"
```

---

## Task 6: SpawnCommandOptions worktree/repo path + spawn metadata

**Files:**
- Modify: `backend/app/services/providers/base.py`
- Modify: `backend/app/services/agent_bridge/spawn.py`
- Test: `backend/tests/test_spawn_worktree_cleanup.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_spawn_worktree_cleanup.py`:

```python
# backend/tests/test_spawn_worktree_cleanup.py
import subprocess

from app.services.agent_bridge import spawn as spawnmod
from app.services.providers.base import SpawnCommandOptions


def test_kill_session_removes_dispatcher_worktree(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(cmd)
        class R:
            returncode = 0
            stderr = ""
        return R()

    monkeypatch.setattr(spawnmod.subprocess, "run", fake_run)
    # Seed metadata as if a dispatcher worktree session was spawned
    spawnmod._spawned_sessions["k-test-1234"] = {
        "provider": "claude-code",
        "mode": "plain",
        "directory": str(tmp_path / "wt"),
        "worktree_name": None,
        "worktree_path": str(tmp_path / "wt"),
        "repo_path": str(tmp_path / "repo"),
        "platform": "anthropic",
    }
    spawnmod.kill_session("k-test-1234", cleanup_worktree=True)
    removes = [c for c in calls if "worktree" in c and "remove" in c]
    assert removes, "expected a git worktree remove call"
    assert str(tmp_path / "wt") in removes[0]
    assert "-C" in removes[0] and str(tmp_path / "repo") in removes[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_spawn_worktree_cleanup.py -v`
Expected: FAIL — cleanup branch only handles `mode == "worktree"`, so no remove call recorded

- [ ] **Step 3: Write minimal implementation**

In `backend/app/services/providers/base.py`, add to the `SpawnCommandOptions` dataclass (after `worktree_name`):

```python
    worktree_path: str | None = None
    repo_path: str | None = None
```

In `backend/app/services/agent_bridge/spawn.py`, in `spawn_session` where `_spawned_sessions[name]` is built, add the two fields:

```python
    _spawned_sessions[name] = {
        "provider": provider.id,
        "mode": options.mode,
        "directory": directory,
        "worktree_name": options.worktree_name or (name if options.mode == "worktree" else None),
        "worktree_path": options.worktree_path,
        "repo_path": options.repo_path,
        "platform": options.platform,
    }
```

Still in `spawn.py`, extend the cleanup block in `kill_session`. After the existing `mode == "worktree"` block, add:

```python
    if (
        cleanup_worktree
        and metadata
        and metadata.get("worktree_path")
        and metadata.get("repo_path")
    ):
        try:
            subprocess.run(
                ["git", "-C", metadata["repo_path"], "worktree", "remove",
                 metadata["worktree_path"], "--force"],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            logger.warning("Failed to remove dispatcher worktree %s",
                           metadata["worktree_path"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_spawn_worktree_cleanup.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/providers/base.py backend/app/services/agent_bridge/spawn.py backend/tests/test_spawn_worktree_cleanup.py
git commit -m "feat(agent-bridge): track + clean up dispatcher-owned worktrees"
```

---

## Task 7: Worktree-from-origin/master transport

**Files:**
- Modify: `backend/app/kanban/dispatch.py`
- Test: `backend/tests/test_kanban_dispatch.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_kanban_dispatch.py`:

```python
def test_worktree_transport_creates_from_origin_master(monkeypatch, tmp_path):
    ran = []

    def fake_run(cmd, *a, **k):
        ran.append(cmd)
        class R:
            returncode = 0
            stderr = ""
        return R()

    captured = {}

    def fake_spawn(provider_id, options, session_name=None):
        captured["provider"] = provider_id
        captured["options"] = options
        captured["session_name"] = session_name
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}

    import app.kanban.dispatch as d
    monkeypatch.setattr(d.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "app.services.agent_bridge.spawn.spawn_session", fake_spawn)

    res = d.worktree_transport(
        directory=str(tmp_path), prompt="hi", session_name="k-proj-abcd")

    fetches = [c for c in ran if "fetch" in c]
    adds = [c for c in ran if "worktree" in c and "add" in c]
    assert fetches and adds
    assert "origin/master" in adds[0]
    opts = captured["options"]
    assert opts.mode == "plain"
    assert opts.skip_permissions is True
    assert opts.repo_path == str(tmp_path)
    assert opts.worktree_path == opts.directory
    assert res["session_name"] == "k-proj-abcd"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py -k worktree_transport -v`
Expected: FAIL — `AttributeError: module 'app.kanban.dispatch' has no attribute 'worktree_transport'`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/kanban/dispatch.py`, add `import subprocess` to the top imports, then add the new transport (and make it the default). Replace `tmux_transport` with:

```python
def worktree_transport(*, directory: str, prompt: str, session_name: str) -> dict:
    """Default transport: create a worktree off origin/master, then spawn an
    autonomous (permission-skipping) Claude Code session in it."""
    from app.services.agent_bridge.spawn import spawn_session
    from app.services.providers.base import SpawnCommandOptions

    repo = directory
    worktree_path = str(Path(repo) / ".claude" / "worktrees" / session_name)

    subprocess.run(["git", "-C", repo, "fetch", "origin"],
                   capture_output=True, text=True, timeout=60, check=True)
    subprocess.run(
        ["git", "-C", repo, "worktree", "add", "-b", session_name,
         worktree_path, "origin/master"],
        capture_output=True, text=True, timeout=60, check=True)

    options = SpawnCommandOptions(
        directory=worktree_path, mode="plain", prompt=prompt,
        skip_permissions=True, worktree_path=worktree_path, repo_path=repo,
    )
    return spawn_session("claude-code", options, session_name=session_name)
```

Then update `run_dispatch_tick`'s signature default and the `SpawnTransport` default:

```python
async def run_dispatch_tick(*, transport: SpawnTransport = worktree_transport) -> None:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py -k worktree_transport -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/dispatch.py backend/tests/test_kanban_dispatch.py
git commit -m "feat(kanban): spawn worktree from origin/master, autonomously"
```

---

## Task 8: Full backend dispatch suite green

**Files:**
- Test: `backend/tests/test_kanban_dispatch.py`, `test_kanban_shipmode.py`, `test_kanban_personas.py`, `test_spawn_worktree_cleanup.py`

- [ ] **Step 1: Run the dispatch + new suites together**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py tests/test_kanban_shipmode.py tests/test_kanban_personas.py tests/test_spawn_worktree_cleanup.py -v`
Expected: PASS (all). If a leftover test still calls `build_card_prompt(card)` or asserts `skip_permissions is False`, fix it to the new signature/behavior.

- [ ] **Step 2: Commit any test fixes**

```bash
git add backend/tests/
git commit -m "test(kanban): align dispatch tests with persona + worktree transport"
```

---

## Task 9: Frontend ship-mode selector

**Files:**
- Modify: `frontend/src/features/kanban/api.ts`
- Create: `frontend/src/features/kanban/components/ShipModeToggle.tsx`
- Modify: `frontend/src/features/kanban/KanbanPage.tsx`

- [ ] **Step 1: Add API methods**

In `frontend/src/features/kanban/api.ts`, add to `kanbanApi` after `setAutodispatch`:

```typescript
  getShipMode: (projectKey: string): Promise<{ mode: string }> =>
    apiClient<{ mode: string }>(
      `${BASE}/shipmode?project_key=${encodeURIComponent(projectKey)}`
    ),

  setShipMode: (projectKey: string, mode: string): Promise<{ mode: string }> =>
    apiClient<{ mode: string }>(`${BASE}/shipmode`, {
      method: "POST",
      body: JSON.stringify({ project_key: projectKey, mode }),
    }),
```

- [ ] **Step 2: Create the selector component**

Create `frontend/src/features/kanban/components/ShipModeToggle.tsx`:

```tsx
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { kanbanApi } from "../api";

export function ShipModeToggle({ projectKey }: { projectKey: string }) {
  const [mode, setMode] = useState<string | null>(null);

  useEffect(() => {
    if (!projectKey) return;
    kanbanApi
      .getShipMode(projectKey)
      .then((r) => setMode(r.mode))
      .catch(() => setMode("pull-request"));
  }, [projectKey]);

  if (!projectKey || mode === null) return null;

  const isDirect = mode === "direct";
  const toggle = async () => {
    const next = isDirect ? "pull-request" : "direct";
    try {
      await kanbanApi.setShipMode(projectKey, next);
      setMode(next);
      toast.success(
        next === "direct"
          ? "Ship: direct to master"
          : "Ship: draft pull request"
      );
    } catch {
      toast.error("Failed to change ship mode");
    }
  };

  return (
    <Button
      size="sm"
      variant={isDirect ? "destructive" : "outline"}
      onClick={toggle}
      title="How the developer agent ships: draft PR (safe) or direct merge+push to master"
    >
      {isDirect ? "Ship: direct to master" : "Ship: draft PR"}
    </Button>
  );
}
```

- [ ] **Step 3: Render it in the header**

In `frontend/src/features/kanban/KanbanPage.tsx`, add the import:

```tsx
import { ShipModeToggle } from "./components/ShipModeToggle";
```

And render it right after `<AutodispatchToggle ... />`:

```tsx
          <AutodispatchToggle projectKey={projectKey} />
          <ShipModeToggle projectKey={projectKey} />
```

- [ ] **Step 4: Lint + build**

Run: `cd frontend && npm run lint && npm run build`
Expected: no ESLint errors; build succeeds (backend serves `frontend/dist`, so a build is required to see the change).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/kanban/api.ts frontend/src/features/kanban/components/ShipModeToggle.tsx frontend/src/features/kanban/KanbanPage.tsx
git commit -m "feat(kanban): ship-mode selector in board header"
```

---

## Task 10: Analyst persona file

**Files:**
- Create: `.claude/agents/kanban-analyst.md`

- [ ] **Step 1: Write the persona file**

Create `.claude/agents/kanban-analyst.md`:

```markdown
---
name: kanban-analyst
description: Decomposes an Analysis card into well-scoped Todo cards on the Cockpit board.
---

You are the **Analyst** for this project's Kanban board. You were handed a card in the
`Analysis` column. Your deliverable is **decomposition**, not implementation.

Do this:

1. Read the card title and description. Investigate the codebase enough to understand the
   work — read files, search, but **write no production code**.
2. Break the work into small, independently shippable units. For each unit, create a new
   card in the `Todo` column with the `cockpit-kanban` MCP `create_card` tool. Each new
   card must document, in its description:
   - **Scope** — what is in and explicitly out.
   - **Approach** — the intended implementation path and key files.
   - **Acceptance** — how a developer agent will know it is done (tests, behavior).
3. When every unit is captured, `comment` on the source card listing the ids/titles of the
   cards you created, then `move_card` the source card to `Done`.

If the work is too vague to decompose, `comment` with the specific questions that block you
and leave the card in `Doing`. Do not invent requirements.
```

- [ ] **Step 2: Verify required anchors are present**

Run: `grep -E "create_card|move_card|Acceptance" .claude/agents/kanban-analyst.md`
Expected: all three strings present.

- [ ] **Step 3: Commit**

```bash
git add .claude/agents/kanban-analyst.md
git commit -m "feat(kanban): analyst persona — decompose Analysis cards into Todo cards"
```

---

## Task 11: Developer persona file

**Files:**
- Create: `.claude/agents/kanban-developer.md`

- [ ] **Step 1: Write the persona file**

Create `.claude/agents/kanban-developer.md`:

```markdown
---
name: kanban-developer
description: Implements a Todo card autonomously in a worktree and ships via the git-ship skill.
---

You are the **Developer** for this project's Kanban board. You were handed a card in the
`Todo` column and you are working in a fresh git worktree branched from `origin/master`.
Work **autonomously to completion** — do not stop to ask for confirmation.

Do this:

1. Read the card. Implement the change with tests, following the repo's existing patterns
   and `CLAUDE.md`. Keep commits focused.
2. When the code is ready, invoke the **`git-ship`** skill. It runs the test suite and, only
   if everything is green, ships according to this project's **ship mode** (stated in your
   opening prompt): a direct merge+push to master, or a draft pull request.
3. On success: `move_card` the card to `Review` and `attach_deliverable` with the branch name
   or PR URL via the `cockpit-kanban` MCP tools.
4. If tests fail or you are blocked: do **not** merge or open a PR. `comment` on the card with
   the failing output or the blocker, and leave the card in `Doing`.

Never push to any remote other than `origin`. Never force-push. Never merge red tests.
```

- [ ] **Step 2: Verify required anchors are present**

Run: `grep -E "git-ship|attach_deliverable|origin" .claude/agents/kanban-developer.md`
Expected: all three strings present.

- [ ] **Step 3: Commit**

```bash
git add .claude/agents/kanban-developer.md
git commit -m "feat(kanban): developer persona — autonomous Todo implementation + ship"
```

---

## Task 12: git-ship skill

**Files:**
- Create: `.claude/skills/git-ship/SKILL.md`

- [ ] **Step 1: Write the skill**

Create `.claude/skills/git-ship/SKILL.md`:

```markdown
---
name: git-ship
description: Use after finishing work in a worktree — runs tests and, only if green, merges to master or opens a draft PR per the project's ship mode.
---

# git-ship

Ship the work in the current worktree **safely and unattended**. Never ship red tests.
Your opening prompt states the **ship mode**: `direct` or `pull-request`. Follow the matching
path below.

## 1. Sync

```bash
git fetch origin
```

## 2. Run the tests — they gate everything

- Backend: activate the project's Python venv (in this repo it lives in the **main checkout**
  at `backend/venv`), then from the worktree's own backend dir:
  `pytest tests/`
- Frontend (if frontend files changed): `cd frontend && npm run lint && npm run build`

If anything fails: **stop**. Do not merge, do not open a PR. `comment` on the card with the
failing output and leave the card in `Doing`. You are done.

## 3a. Ship mode `direct`

Only when every test passed:

```bash
git push origin HEAD:refs/heads/<your-branch>      # back up the branch first
git fetch origin
git checkout -B ship-master origin/master
git merge --no-ff <your-branch>
git push origin HEAD:master
```

Then `move_card` to `Review` and `attach_deliverable` (kind `branch` or `commit`).

If the push is rejected (master moved / protected): fall back to the `pull-request` path.

## 3b. Ship mode `pull-request`

Only when every test passed. Requires the `gh` CLI authenticated:

```bash
gh auth status            # if this fails, see "gh unavailable" below
git push -u origin HEAD
gh pr create --draft --base master --fill
```

Capture the PR URL from `gh pr create` output, `attach_deliverable` (kind `pr`, the URL),
then `move_card` to `Review`.

**gh unavailable:** if `gh auth status` fails, do not merge. Push the branch
(`git push -u origin HEAD`), `comment` "gh unavailable — manual PR needed from <branch>",
and leave the card in `Doing`.

## Rules

- Push **only** to `origin`. Never to any other remote. Never `--force`.
- Never merge or open a PR when tests are red.
- A new worktree always branches from `origin/master`.
```

- [ ] **Step 2: Verify required anchors are present**

Run: `grep -E "ship mode|gh pr create|origin/master|never .*force|--force" .claude/skills/git-ship/SKILL.md -i`
Expected: ship mode, `gh pr create`, `origin/master`, and a force-push prohibition all present.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/git-ship/SKILL.md
git commit -m "feat(kanban): git-ship skill — gated merge-to-master or draft PR"
```

---

## Task 13: Final verification

**Files:** (none — verification only)

- [ ] **Step 1: Run the full backend suite**

Run: `cd backend && source venv/bin/activate && pytest tests/`
Expected: PASS. If the known DB-isolation flake appears (a couple of sqlalchemy tests failing by collection order), re-run the failing test in isolation to confirm it is the pre-existing flake, not a regression.

- [ ] **Step 2: Frontend lint + build**

Run: `cd frontend && npm run lint && npm run build`
Expected: clean lint, successful build.

- [ ] **Step 3: Sanity-check the new files exist in the tree**

Run: `ls .claude/agents/kanban-analyst.md .claude/agents/kanban-developer.md .claude/skills/git-ship/SKILL.md`
Expected: all three listed.

- [ ] **Step 4: Final commit (if anything outstanding) and stop**

```bash
git status
```

Leave merging to master + push to the user's standing "finish branch" flow (merge in a temp
worktree if the main checkout's master is dirty; push to `origin` only).

---

## Notes for the implementer

- **Run pytest from the worktree's own `backend/` dir**, but the venv lives in the **main
  checkout** — activate `…/<main-checkout>/backend/venv/bin/activate`, then `cd` to this
  worktree's `backend` before `pytest`.
- **Frontend changes require `npm run build`** — the backend serves `frontend/dist`; a dev
  reload alone won't reflect the change.
- **Do not commit** `backend/claude_registry.db-shm` / `-wal` — stage only the files each task
  names.
- **`KanbanMeta.key` is `String(64)`** — `shipmode:<project_key>` shares the column with the
  existing `autodispatch:` flag and the same length assumption; no schema change.
- The dispatcher already runs on a 10s APScheduler interval (`schedule_kanban_dispatch` in
  `scheduler.py`, started in `main.py`). No scheduler change is needed — it calls
  `run_dispatch_tick()` with the new default `worktree_transport`.
```
