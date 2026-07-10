# Kanban Model Override Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a kanban card or column pick which Claude model a dispatched session runs with, falling back through column default → persona frontmatter → platform default, with the model list sourced from the installed `claude` CLI instead of hardcoded.

**Architecture:** Two new free-text DB columns (`KanbanCard.model`, `KanbanColumn.default_model`) resolved by a new precedence function in `dispatch.py`, threaded through the existing `SpawnTransport` abstraction into `SpawnCommandOptions.model`, which `ClaudeCodeProvider` now actually turns into a `--model` CLI flag (a currently-dead wiring gap). A manual refresh action shells out to `claude -p "/model"` and caches the parsed alias list in `KanbanMeta`.

**Tech Stack:** FastAPI + async SQLAlchemy + aiosqlite (backend), React 19 + TypeScript + shadcn/ui (frontend), pytest + pytest-asyncio (backend tests), vitest + testing-library (frontend tests).

## Global Constraints

- No enum/validation on `model` values anywhere (card, column, API) — free text, matching the existing `card.agent`/`labels` precedent (spec Non-goals).
- Precedence order is exactly: `card.model` > `column.default_model` > persona frontmatter `model:` > nothing (no `--model` flag passed).
- No change to the MiniMax `ANTHROPIC_MODEL` env-var mechanism in `platform_env.py`.
- Model-options cache is kanban-scoped (`KanbanMeta`, not the generic provider layer) and is machine-wide, not per-project (the installed CLI is a device property).
- Every SQL migration is additive (`ALTER TABLE ... ADD COLUMN`), following the exact pattern already in `backend/app/kanban/db.py`.

---

## Task 1: Data model — `card.model` + `column.default_model`

**Files:**
- Modify: `backend/app/kanban/models.py` (`KanbanCard`, `KanbanColumn`)
- Modify: `backend/app/kanban/db.py` (`_ensure_card_columns`, `_ensure_column_table`)
- Modify: `backend/app/kanban/schemas.py` (`CardResponse`, `CardCreate`, `CardUpdate`, `ColumnResponse`, `ColumnCreate`, `ColumnUpdate`)
- Modify: `backend/app/kanban/operations.py` (`_materialize`, create + update branches)
- Modify: `backend/app/kanban/service.py` (`create_column`, add `get_column_default_model`)
- Modify: `backend/app/api/v1/kanban/router.py` (`create_column`, `update_column` handlers)
- Test: `backend/tests/test_kanban_operations.py`, `backend/tests/test_kanban_service.py`

**Interfaces:**
- Produces: `KanbanCard.model: str | None`, `KanbanColumn.default_model: str | None`, `service.get_column_default_model(session, project_key, column_name) -> str | None`, `service.create_column(..., default_model: str | None = None)`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_kanban_operations.py`:

```python
@pytest.mark.asyncio
async def test_create_card_persists_model():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "x", "column": "Backlog", "model": "opus"},
        )
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.model == "opus"


@pytest.mark.asyncio
async def test_update_card_persists_model():
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key="git:example", entity_id=None,
            payload={"title": "x", "column": "Backlog"},
        )
        await apply_operation(
            s, op_type="update", entity_type="card",
            project_key="git:example", entity_id=cid,
            payload={"model": "sonnet"},
        )
        await s.commit()
        card = await s.get(KanbanCard, cid)
        assert card.model == "sonnet"
```

Append to `backend/tests/test_kanban_service.py`:

```python
@pytest.mark.asyncio
async def test_column_default_model_roundtrip():
    async with KanbanSessionLocal() as s:
        col = await service.create_column(
            s, project_key="A", name="engineer", default_agent="engineer",
            default_model="opus",
        )
        await s.commit()
        assert col.default_model == "opus"
        assert await service.get_column_default_model(s, "A", "engineer") == "opus"


@pytest.mark.asyncio
async def test_column_default_model_missing_column_returns_none():
    async with KanbanSessionLocal() as s:
        assert await service.get_column_default_model(s, "A", "no-such-column") is None


@pytest.mark.asyncio
async def test_update_column_can_set_default_model():
    async with KanbanSessionLocal() as s:
        col = await service.create_column(s, project_key="A", name="engineer")
        await s.commit()
        updated = await service.update_column(s, col.id, default_model="haiku")
        await s.commit()
        assert updated.default_model == "haiku"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_operations.py::test_create_card_persists_model tests/test_kanban_operations.py::test_update_card_persists_model tests/test_kanban_service.py::test_column_default_model_roundtrip tests/test_kanban_service.py::test_column_default_model_missing_column_returns_none tests/test_kanban_service.py::test_update_column_can_set_default_model -v`

Expected: FAIL — `AttributeError`/`TypeError: create_column() got an unexpected keyword argument 'default_model'`.

- [ ] **Step 3: Add the columns to the ORM models**

In `backend/app/kanban/models.py`, in `KanbanCard` (right after the `agent` field, line 48):

```python
    agent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Explicit model override for the spawned session (e.g. "opus", "sonnet",
    # a full model id). Precedence: card.model > column.default_model >
    # persona frontmatter `model:` > no --model flag (platform default). See
    # docs/superpowers/specs/2026-07-10-kanban-model-override-design.md.
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
```

In `KanbanColumn`, right after `default_platform` (line 117):

```python
    default_platform: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Column-wide model default, one precedence level below a card's own
    # `model` and above the persona frontmatter default. Same free-text,
    # no-validation contract as default_agent/default_platform.
    default_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
```

- [ ] **Step 4: Add the boot-time migration**

In `backend/app/kanban/db.py`, in `_ensure_card_columns`, after the `metadata` block (line 133):

```python
    if "metadata" not in cols:
        await conn.exec_driver_sql("ALTER TABLE kanban_cards ADD COLUMN metadata JSON")
    if "model" not in cols:
        await conn.exec_driver_sql("ALTER TABLE kanban_cards ADD COLUMN model VARCHAR(64)")
```

In `_ensure_column_table`, after the `max_sessions` block (line 159):

```python
    if "max_sessions" not in cols:
        await conn.exec_driver_sql("ALTER TABLE kanban_columns ADD COLUMN max_sessions INTEGER")
    if "default_model" not in cols:
        await conn.exec_driver_sql("ALTER TABLE kanban_columns ADD COLUMN default_model VARCHAR(64)")
```

- [ ] **Step 5: Add the fields to pydantic schemas**

In `backend/app/kanban/schemas.py`:

`CardResponse` — after `agent: str | None = None` (line 46):
```python
    agent: str | None = None
    model: str | None = None
```

`CardCreate` — after `agent: str | None = None` (line 85):
```python
    agent: str | None = None
    model: str | None = None
```

`CardUpdate` — after `agent: str | None = None` (line 104):
```python
    agent: str | None = None
    model: str | None = None
```

`ColumnResponse` — after `default_platform: str | None = None` (line 200):
```python
    default_platform: str | None = None
    default_model: str | None = None
```

`ColumnCreate` — after `default_platform: str | None = None` (line 211):
```python
    default_platform: str | None = None
    default_model: str | None = None
```

`ColumnUpdate` — after `default_platform: str | None = None` (line 219):
```python
    default_platform: str | None = None
    default_model: str | None = None
```

- [ ] **Step 6: Wire `model` into the op-log materialization**

In `backend/app/kanban/operations.py`, `_materialize`'s create branch — add `model=payload.get("model"),` right after `agent=payload.get("agent"),` (line 129):

```python
                agent=payload.get("agent"),
                model=payload.get("model"),
```

In the update branch's field tuple (lines 180-184), add `"model"`:

```python
            for f in ("priority", "labels", "work_type", "agent", "model", "transport",
                      "resume_session_id", "resume_project_folder", "scheduled_at",
                      "dispatch_failures",
                      "analyst_agent_id", "executor_agent_id", "parent_card_id",
                      "analyst_run_id", "depends_on"):
```

- [ ] **Step 7: Add `default_model` to `service.create_column` and `get_column_default_model`**

In `backend/app/kanban/service.py`, update `create_column` (lines 158-173):

```python
async def create_column(session, project_key: str, name: str,
                        rank: str | None = None, default_agent: str | None = None,
                        default_platform: str | None = None,
                        default_model: str | None = None,
                        max_sessions: int | None = None):
    col = KanbanColumn(
        id=uuid.uuid4().hex,
        project_key=project_key,
        name=name,
        rank=rank or uuid.uuid4().hex,
        default_agent=default_agent,
        default_platform=default_platform,
        default_model=default_model,
        max_sessions=max_sessions,
    )
    session.add(col)
    await session.flush()
    return col
```

Add a new function right after `get_column_default_platform` (line 219):

```python
async def get_column_default_model(session, project_key: str, column_name: str) -> str | None:
    """Look up the default model for a column name within a project. None means
    no override -- resolution falls through to the persona frontmatter's
    `model:` field, then to no --model flag at all (platform default)."""
    stmt = (
        select(KanbanColumn)
        .where(KanbanColumn.project_key == project_key)
        .where(KanbanColumn.name == column_name)
    )
    col = (await session.execute(stmt)).scalar_one_or_none()
    return col.default_model if col else None
```

(`update_column` already applies arbitrary `**kwargs` via `setattr`, so it needs no change — router.py just needs to start passing `default_model` through.)

- [ ] **Step 8: Wire `default_model` through the column REST endpoints**

In `backend/app/api/v1/kanban/router.py`, `create_column` handler (lines 78-88):

```python
@router.post("/columns", response_model=ColumnResponse, status_code=status.HTTP_201_CREATED)
async def create_column(payload: ColumnCreate):
    async with KanbanSessionLocal() as s:
        col = await service.create_column(
            s, project_key=payload.project_key, name=payload.name,
            rank=payload.rank, default_agent=payload.default_agent,
            default_platform=payload.default_platform,
            default_model=payload.default_model,
            max_sessions=payload.max_sessions,
        )
        await s.commit()
        return ColumnResponse.model_validate(col)
```

`update_column` handler (lines 91-104):

```python
@router.patch("/columns/{column_id}", response_model=ColumnResponse)
async def update_column(column_id: str, payload: ColumnUpdate):
    async with KanbanSessionLocal() as s:
        col = await service.update_column(
            s, column_id,
            name=payload.name, rank=payload.rank,
            default_agent=payload.default_agent,
            default_platform=payload.default_platform,
            default_model=payload.default_model,
            max_sessions=payload.max_sessions,
        )
        if col is None:
            raise HTTPException(404, "column not found")
        await s.commit()
        return ColumnResponse.model_validate(col)
```

(Card create/update REST handlers need **no** change — `CardCreate`/`CardUpdate` are serialized generically via `payload.model_dump(...)`, confirmed by reading `router.py:231-317`; the new `model` field flows through automatically once the schema has it.)

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_operations.py tests/test_kanban_service.py -v`

Expected: PASS (all, including the 5 new tests).

- [ ] **Step 10: Commit**

```bash
git add backend/app/kanban/models.py backend/app/kanban/db.py backend/app/kanban/schemas.py backend/app/kanban/operations.py backend/app/kanban/service.py backend/app/api/v1/kanban/router.py backend/tests/test_kanban_operations.py backend/tests/test_kanban_service.py
git commit -m "feat(kanban): add card.model + column.default_model fields"
```

---

## Task 2: Persona frontmatter `model:` reader

**Files:**
- Modify: `backend/app/kanban/dispatch.py` (add `_read_persona_model`, add `import yaml`)
- Test: `backend/tests/test_kanban_personas.py`

**Interfaces:**
- Consumes: nothing new (reads `.claude/agents/<filename>` from disk, same as `_read_persona_file`).
- Produces: `_read_persona_model(project_path: str, filename: str) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_kanban_personas.py`:

```python
def test_read_persona_model_returns_frontmatter_model(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "engineer.md").write_text(
        "---\nname: 'engineer'\nmodel: 'claude-opus-4-8'\n---\nBe an engineer.\n"
    )
    assert dispatch._read_persona_model(str(tmp_path), "engineer.md") == "claude-opus-4-8"


def test_read_persona_model_returns_none_when_field_absent(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "analyst.md").write_text("---\nname: 'analyst'\n---\nBe an analyst.\n")
    assert dispatch._read_persona_model(str(tmp_path), "analyst.md") is None


def test_read_persona_model_returns_none_when_no_frontmatter(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "plain.md").write_text("Just a body, no frontmatter.\n")
    assert dispatch._read_persona_model(str(tmp_path), "plain.md") is None


def test_read_persona_model_returns_none_for_missing_file(tmp_path):
    assert dispatch._read_persona_model(str(tmp_path), "missing.md") is None


def test_read_persona_model_returns_none_for_malformed_yaml(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "broken.md").write_text("---\nmodel: [unclosed\n---\nBody.\n")
    assert dispatch._read_persona_model(str(tmp_path), "broken.md") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_personas.py -v -k read_persona_model`

Expected: FAIL — `AttributeError: module 'app.kanban.dispatch' has no attribute '_read_persona_model'`.

- [ ] **Step 3: Implement `_read_persona_model`**

In `backend/app/kanban/dispatch.py`, add `import yaml` to the top-level imports (after `import uuid`, line 15):

```python
import uuid
import yaml
```

Add the function right after `_read_persona_file` (after line 371):

```python
def _read_persona_model(project_path: str, filename: str) -> str | None:
    """Read the `model:` field from a persona file's YAML frontmatter, if any.

    Complements `_read_persona_file`, which strips this exact frontmatter
    block before the persona body reaches the prompt (see `_strip_frontmatter`)
    -- today that `model:` field (already present in engineer.md/analyst.md)
    is silently discarded. This is the read that makes it a real fallback in
    the model-resolution precedence. Never raises: a missing file, absent
    frontmatter, missing `model` key, or malformed YAML all resolve to None,
    which falls through to the next precedence level.
    """
    path = Path(project_path) / ".claude" / "agents" / filename
    try:
        text = path.read_text()
    except OSError:
        return None
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    try:
        frontmatter = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return None
    if not isinstance(frontmatter, dict):
        return None
    model = frontmatter.get("model")
    return model if isinstance(model, str) and model else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_personas.py -v`

Expected: PASS (all, including the 5 new tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/kanban/dispatch.py backend/tests/test_kanban_personas.py
git commit -m "feat(kanban): read model: from persona frontmatter (was silently dropped)"
```

---

## Task 3: Effective-model precedence + transport wiring

**Files:**
- Modify: `backend/app/kanban/dispatch.py` (`SpawnTransport`, `make_worktree_transport`, `make_resume_transport`, `sandcastle_transport`, `_run_card`, add `_effective_model`)
- Modify: `backend/tests/test_kanban_dispatch.py` (fix 7 existing transport stubs + add precedence tests)
- Modify: `backend/tests/test_kanban_maturity.py` (fix 1 existing transport stub)
- Modify: `backend/tests/integration/test_multi_agent_kanban.py` (fix 1 existing transport stub)

**Interfaces:**
- Consumes: `service.get_column_default_model` (Task 1), `_read_persona_model` (Task 2).
- Produces: `_effective_model(card_model, column_default_model, persona_model) -> str | None`. `SpawnTransport.__call__` and all its implementations now accept `model: str | None = None`.

**Why this task touches three test files that don't obviously relate to "model":** `_run_card` will start calling `card_transport(...)` with an unconditional `model=effective_model` keyword. Every hand-written transport stub in the test suite has an explicit keyword-only signature (no `**kwargs`), so without updating them, this call raises `TypeError: unexpected keyword argument 'model'` in every dispatch test that spawns through a stub — this is the same ripple that happened when `platform` was added to the transport signature.

- [ ] **Step 1: Write the failing precedence tests**

Append to `backend/tests/test_kanban_dispatch.py` (after `test_dispatch_uses_column_default_platform`, line 204). First, update `RecordingTransport.__call__` (line 43-47) to accept and record `model`, since these new tests need to observe it:

```python
class RecordingTransport:
    """A real (non-mock) transport that records calls and returns a session."""
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, *, directory, prompt, session_name, provider_id="claude-code",
                 platform="anthropic", model=None):
        self.calls.append({"directory": directory, "prompt": prompt,
                           "session_name": session_name, "provider_id": provider_id,
                           "platform": platform, "model": model})
        if self.fail:
            raise RuntimeError("tmux exploded")
        return {"session_name": session_name, "tmux_target": f"{session_name}:0.0"}
```

Then add the new tests:

```python
@pytest.mark.asyncio
async def test_dispatch_no_model_by_default():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] is None


@pytest.mark.asyncio
async def test_dispatch_uses_card_model_over_everything():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_model="sonnet",
        )
        cid = await _make_card(s)
        await apply_operation(s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid, payload={"model": "opus"})
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] == "opus"


@pytest.mark.asyncio
async def test_dispatch_uses_column_default_model_when_card_model_unset():
    transport = RecordingTransport()
    async with KanbanSessionLocal() as s:
        await service.create_column(
            s, project_key=PK, name="engineer", default_agent="engineer",
            default_model="sonnet",
        )
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(s, card_id=cid, project_path="/p", transport=transport)
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] == "sonnet"


@pytest.mark.asyncio
async def test_dispatch_falls_back_to_persona_frontmatter_model(tmp_path):
    transport = RecordingTransport()
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "engineer.md").write_text(
        "---\nname: 'engineer'\nmodel: 'claude-opus-4-8'\n---\nBe an engineer.\n"
    )
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s)
        await s.commit()
        await dispatch.dispatch_card(
            s, card_id=cid, project_path=str(tmp_path), transport=transport,
        )
        await s.commit()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] == "claude-opus-4-8"


def test_effective_model_precedence():
    assert dispatch._effective_model("opus", "sonnet", "haiku") == "opus"
    assert dispatch._effective_model(None, "sonnet", "haiku") == "sonnet"
    assert dispatch._effective_model(None, None, "haiku") == "haiku"
    assert dispatch._effective_model(None, None, None) is None
    assert dispatch._effective_model("", "", "") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py -v -k "model"`

Expected: FAIL — `AttributeError: module 'app.kanban.dispatch' has no attribute '_effective_model'`, and the `RecordingTransport` calls dict has no `"model"` key yet (KeyError once the attribute error above is fixed first).

- [ ] **Step 3: Add `_effective_model` and thread `model` through the transports**

In `backend/app/kanban/dispatch.py`, update the `SpawnTransport` protocol (lines 669-671):

```python
class SpawnTransport(Protocol):
    def __call__(self, *, directory: str, prompt: str, session_name: str,
                 provider_id: str = "claude-code", platform: str = "anthropic",
                 model: str | None = None) -> dict: ...
```

Update `make_worktree_transport`'s inner `_transport` (lines 685-686 and the `SpawnCommandOptions` construction at line 713):

```python
    def _transport(*, directory: str, prompt: str, session_name: str,
                   provider_id: str = "claude-code", platform: str = "anthropic",
                   model: str | None = None) -> dict:
```

```python
        options = SpawnCommandOptions(
            directory=worktree_path, mode="plain", prompt=prompt,
            skip_permissions=skip_permissions, worktree_path=worktree_path, repo_path=repo,
            platform=platform, model=model,
        )
```

Update `sandcastle_transport`'s signature (lines 744-745) and docstring (lines 748-750):

```python
def sandcastle_transport(*, directory: str, prompt: str, session_name: str,
                         provider_id: str = "claude-code", platform: str = "anthropic",
                         model: str | None = None) -> dict:
    """Sandcastle transport: run the agent in an isolated sandbox via sandcastle.

    `provider_id`, `platform` and `model` are accepted for transport-signature
    parity but ignored: sandcastle runs use the per-project sandcastle config's
    `agent_provider`, not the card's, column's, or persona's.
```

Update `make_resume_transport`'s inner `_transport` (lines 1922-1923 and the `SpawnCommandOptions` construction at line 1935):

```python
    def _transport(*, directory: str, prompt: str, session_name: str,
                   provider_id: str = "claude-code", platform: str = "anthropic",
                   model: str | None = None) -> dict:
```

```python
        options = SpawnCommandOptions(
            directory=directory,
            mode="resume",
            session_id=session_id,
            project_folder=project_folder,
            prompt=prompt,
            skip_permissions=skip_permissions,
            platform=platform,
            model=model,
        )
```

Add `_effective_model` right after `_read_persona_model` (Task 2):

```python
def _effective_model(card_model: str | None, column_default_model: str | None,
                     persona_model: str | None) -> str | None:
    """Precedence: card.model > column.default_model > persona frontmatter
    `model:` > None (no --model flag, platform default applies). Empty
    strings are treated as unset, same as None."""
    return card_model or column_default_model or persona_model or None
```

In `_run_card`, right after the `platform` line (line 1115), add the model resolution, and pass it into the transport call (line 1133):

```python
    platform = await get_column_default_platform(session, project_key, target_agent) or PLATFORM_ANTHROPIC
    persona_model = _read_persona_model(project_path, f"{target_agent}.md")
    column_default_model = await get_column_default_model(session, project_key, target_agent)
    effective_model = _effective_model(card.model, column_default_model, persona_model)
```

```python
        spawned = card_transport(directory=project_path, prompt=prompt, session_name=name,
                                 provider_id=provider_id, platform=platform, model=effective_model)
```

Add `get_column_default_model` to the import from `app.kanban.service` (line 23):

```python
from app.kanban.service import get_card, get_column_default_model, get_column_default_platform, list_cards
```

- [ ] **Step 4: Fix the remaining transport stubs so existing tests still pass**

In `backend/tests/test_kanban_dispatch.py`, 6 inline stub closures each declare the pre-`model` signature. Find each by its function name + parameter list (line numbers are approximate — locate by content, since earlier edits in this task shift them) and add `, model=None` before the closing `):`. The bodies are unchanged; only the signature line changes, at each of these 6 sites:

1. `resume_transport` (~line 1236):
   `def resume_transport(*, directory, prompt, session_name, provider_id="claude-code", platform="anthropic"):`
   → `def resume_transport(*, directory, prompt, session_name, provider_id="claude-code", platform="anthropic", model=None):`

2. `resume_transport` (~line 1484), identical before/after to site 1.

3. `fake_sandcastle` (~line 1688):
   `def fake_sandcastle(*, directory, prompt, session_name, provider_id="claude-code", platform="anthropic"):`
   → `def fake_sandcastle(*, directory, prompt, session_name, provider_id="claude-code", platform="anthropic", model=None):`

4. `fake_sandcastle` (~line 1719), identical before/after to site 3.

5. `fake_worktree` (~line 1723):
   `def fake_worktree(*, directory, prompt, session_name, provider_id="claude-code", platform="anthropic"):`
   → `def fake_worktree(*, directory, prompt, session_name, provider_id="claude-code", platform="anthropic", model=None):`

6. `resume_transport` (~line 1801), identical before/after to site 1.

A quick way to confirm all 6 are caught: after editing, `grep -n 'def .*directory, prompt, session_name' backend/tests/test_kanban_dispatch.py | grep -v 'model=None'` should return nothing except the `RecordingTransport.__call__` signature already fixed in Step 1 (search for it too — it should also show `model=None`).

In `backend/tests/test_kanban_maturity.py`, update `RecordingTransport.__call__` (line 42-43):

```python
    def __call__(self, *, directory, prompt, session_name, provider_id="claude-code",
                 platform="anthropic", model=None):
```

In `backend/tests/integration/test_multi_agent_kanban.py`, update `RecordingTransport.__call__` (line 42-43) identically:

```python
    def __call__(self, *, directory, prompt, session_name, provider_id="claude-code",
                 platform="anthropic", model=None):
```

- [ ] **Step 5: Run the full dispatch test suite to verify nothing broke and new tests pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py tests/test_kanban_maturity.py tests/test_kanban_personas.py tests/integration/test_multi_agent_kanban.py -v`

Expected: PASS — all existing tests plus the 5 new ones from Step 1.

- [ ] **Step 6: Commit**

```bash
git add backend/app/kanban/dispatch.py backend/tests/test_kanban_dispatch.py backend/tests/test_kanban_maturity.py backend/tests/integration/test_multi_agent_kanban.py
git commit -m "feat(kanban): resolve effective model and thread it through dispatch transports"
```

---

## Task 4: `ClaudeCodeProvider` — actually emit `--model`

**Files:**
- Modify: `backend/app/services/providers/claude_code.py`
- Test: `backend/tests/test_providers.py`

**Interfaces:**
- Consumes: `SpawnCommandOptions.model` (already exists, `base.py:33`).
- Produces: no new public interface — fixes `build_spawn_command`'s existing contract.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_providers.py`:

```python
def test_claude_code_spawn_command_includes_model_flag_when_set():
    from app.services.providers import get_provider
    from app.services.providers.base import SpawnCommandOptions

    provider = get_provider("claude-code")
    command = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="plain", model="opus", prompt="do the thing")
    )

    assert command == ["claude", "--model", "opus", "do the thing"]


def test_claude_code_spawn_command_omits_model_flag_when_unset():
    from app.services.providers import get_provider
    from app.services.providers.base import SpawnCommandOptions

    provider = get_provider("claude-code")
    command = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="plain", prompt="do the thing")
    )

    assert "--model" not in command
    assert command == ["claude", "do the thing"]


def test_claude_code_spawn_command_includes_model_flag_across_modes():
    from app.services.providers import get_provider
    from app.services.providers.base import SpawnCommandOptions

    provider = get_provider("claude-code")

    worktree_command = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="worktree",
                            worktree_name="k-feature-a1b2", model="sonnet")
    )
    assert worktree_command == ["claude", "--worktree", "k-feature-a1b2", "--model", "sonnet"]

    resume_command = provider.build_spawn_command(
        SpawnCommandOptions(directory="/tmp/project", mode="resume",
                            session_id="sess-123", model="haiku")
    )
    assert resume_command == ["claude", "--resume", "sess-123", "--model", "haiku"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_providers.py -v -k claude_code_spawn_command`

Expected: FAIL — `assert ["claude", "do the thing"] == ["claude", "--model", "opus", "do the thing"]` (the `--model` flag is missing).

- [ ] **Step 3: Wire `--model` into `build_spawn_command`**

In `backend/app/services/providers/claude_code.py`, update `build_spawn_command` (lines 57-77):

```python
    def build_spawn_command(self, options: SpawnCommandOptions) -> list[str]:
        command = ["claude"]

        if options.mode == "plain":
            pass
        elif options.mode == "worktree":
            if not options.worktree_name:
                raise ValueError("worktree_name is required for Claude Code worktree mode")
            command += ["--worktree", options.worktree_name]
        elif options.mode == "resume":
            if not options.session_id:
                raise ValueError("session_id is required for Claude Code resume mode")
            command += ["--resume", options.session_id]
        else:
            raise ValueError(f"Unsupported Claude Code mode: {options.mode}")

        if options.skip_permissions:
            command.append("--dangerously-skip-permissions")
        if options.model:
            command += ["--model", options.model]
        if options.prompt:
            command.append(options.prompt)
        return command
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_providers.py -v`

Expected: PASS (all, including the 3 new tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/providers/claude_code.py backend/tests/test_providers.py
git commit -m "fix(providers): ClaudeCodeProvider now passes --model (was silently ignored)"
```

---

## Task 5: Model-options refresh (kanban-scoped, `KanbanMeta`-backed)

**Files:**
- Modify: `backend/app/kanban/dispatch.py` (add parse fn, refresh fn, cache getters/setters)
- Modify: `backend/app/api/v1/kanban/router.py` (add `GET /model-options`, `POST /model-options/refresh`)
- Test: `backend/tests/test_kanban_model_options.py` (new file)

**Interfaces:**
- Produces: `dispatch._parse_model_options(output: str) -> list[str]`, `dispatch.refresh_claude_model_options_sync() -> list[str]`, `dispatch.refresh_claude_model_options(session) -> list[str]`, `dispatch.get_cached_model_options(session) -> list[str]`.
- API: `GET /api/v1/kanban/model-options` → `{"provider": "claude-code", "options": [...]}`; `POST /api/v1/kanban/model-options/refresh` → same shape (502 on subprocess failure).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_kanban_model_options.py`:

```python
# backend/tests/test_kanban_model_options.py
from unittest.mock import patch

import pytest
import pytest_asyncio

from app.kanban import dispatch
from tests.kanban_test_db import TestSessionLocal, reset_test_tables

KanbanSessionLocal = TestSessionLocal()

# Captured verbatim from `claude -p "/model"` (Claude Code 2.1.206, 2026-07-10).
SAMPLE_CLI_OUTPUT = (
    "Current model: Sonnet 5 (default)\n"
    "Usage: /model <name>. Available: sonnet, opus, haiku, fable, best, "
    "sonnet[1m], opus[1m], fable[1m], opusplan, default, or a full model ID.\n"
)


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


def test_parse_model_options_extracts_available_list():
    options = dispatch._parse_model_options(SAMPLE_CLI_OUTPUT)
    assert options == [
        "sonnet", "opus", "haiku", "fable", "best",
        "sonnet[1m]", "opus[1m]", "fable[1m]", "opusplan", "default",
    ]


def test_parse_model_options_returns_empty_list_when_marker_absent():
    assert dispatch._parse_model_options("something unexpected\n") == []


@pytest.mark.asyncio
async def test_get_cached_model_options_returns_seed_when_never_refreshed():
    async with KanbanSessionLocal() as s:
        assert await dispatch.get_cached_model_options(s) == list(dispatch.MODEL_OPTIONS_SEED)


@pytest.mark.asyncio
async def test_refresh_claude_model_options_caches_parsed_list():
    async with KanbanSessionLocal() as s:
        with patch.object(dispatch, "refresh_claude_model_options_sync",
                          return_value=["sonnet", "opus"]):
            options = await dispatch.refresh_claude_model_options(s)
            await s.commit()
        assert options == ["sonnet", "opus"]
        assert await dispatch.get_cached_model_options(s) == ["sonnet", "opus"]


@pytest.mark.asyncio
async def test_refresh_with_empty_result_does_not_clobber_cache():
    async with KanbanSessionLocal() as s:
        with patch.object(dispatch, "refresh_claude_model_options_sync",
                          return_value=["sonnet", "opus"]):
            await dispatch.refresh_claude_model_options(s)
            await s.commit()
        with patch.object(dispatch, "refresh_claude_model_options_sync",
                          return_value=[]):
            options = await dispatch.refresh_claude_model_options(s)
            await s.commit()
        assert options == []
        # Cache is untouched -- still the last good list, not wiped.
        assert await dispatch.get_cached_model_options(s) == ["sonnet", "opus"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_model_options.py -v`

Expected: FAIL — `AttributeError: module 'app.kanban.dispatch' has no attribute '_parse_model_options'`.

- [ ] **Step 3: Implement the parse + refresh + cache functions**

In `backend/app/kanban/dispatch.py`, add near the other `KanbanMeta`-backed settings (after `set_default_transport`, i.e. after the block ending around line 245 — find it by searching for `TRANSPORT_PREFIX` usage and insert after that function):

```python
# ---- model options: device-local cache of `claude -p "/model"`'s alias list ----

MODEL_OPTIONS_KEY = "model_options:claude-code"
MODEL_OPTIONS_SEED = ("sonnet", "opus", "haiku")


def _parse_model_options(output: str) -> list[str]:
    """Parse `claude -p "/model"` stdout into the list of available aliases.

    Real output (Claude Code 2.1.206, verified 2026-07-10):
        Current model: Sonnet 5 (default)
        Usage: /model <name>. Available: sonnet, opus, haiku, fable, best,
        sonnet[1m], opus[1m], fable[1m], opusplan, default, or a full model ID.

    The trailing "or a full model ID" clause is dropped -- it isn't an alias,
    it's a note that any string is accepted. Returns [] if the "Available: "
    marker isn't found (unexpected CLI output shape) rather than raising --
    callers fall back to the cached/seed list.
    """
    marker = "Available: "
    idx = output.find(marker)
    if idx == -1:
        return []
    tail = " ".join(output[idx + len(marker):].split())
    items = [s.strip() for s in tail.split(",")]
    return [s for s in items if s and "full model ID" not in s]


def refresh_claude_model_options_sync() -> list[str]:
    """Run `claude -p "/model"` and parse the available model aliases.

    Synchronous subprocess.run: a short-lived, one-shot CLI query, not a
    spawned session -- no worktree, no tmux. Raises subprocess.SubprocessError
    or OSError (e.g. `claude` not on PATH) on failure; callers decide whether
    to surface that or fall back to the cache.
    """
    result = subprocess.run(
        ["claude", "-p", "/model"],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return _parse_model_options(result.stdout)


async def refresh_claude_model_options(session) -> list[str]:
    """Refresh and cache the model-options list. An empty parse result is
    returned as-is but does NOT overwrite a previously cached non-empty list
    -- a transient CLI output-shape hiccup shouldn't wipe out a known-good
    cache."""
    import asyncio
    options = await asyncio.to_thread(refresh_claude_model_options_sync)
    if options:
        await _set_model_options_cache(session, options)
    return options


async def get_cached_model_options(session) -> list[str]:
    row = await session.get(KanbanMeta, MODEL_OPTIONS_KEY)
    if row is None:
        return list(MODEL_OPTIONS_SEED)
    try:
        options = json.loads(row.value)
    except (TypeError, ValueError):
        return list(MODEL_OPTIONS_SEED)
    return options if options else list(MODEL_OPTIONS_SEED)


async def _set_model_options_cache(session, options: list[str]) -> None:
    value = json.dumps(options)
    row = await session.get(KanbanMeta, MODEL_OPTIONS_KEY)
    if row is None:
        session.add(KanbanMeta(key=MODEL_OPTIONS_KEY, value=value))
    else:
        row.value = value
    await session.flush()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_model_options.py -v`

Expected: PASS (all 5 tests).

- [ ] **Step 5: Add the REST endpoints**

In `backend/app/api/v1/kanban/router.py`, add `import subprocess` to the top-level imports (after `import logging`, line 3):

```python
import json
import logging
import subprocess
```

Add the two endpoints after the `delete_column` endpoint (after line 112, before the work-type-mappings section):

```python
@router.get("/model-options")
async def model_options():
    """Cached list of Claude model aliases (sonnet/opus/haiku/...), refreshed
    on demand via POST .../model-options/refresh. Seed defaults until the
    first refresh."""
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        return {"provider": "claude-code",
                "options": await dispatch.get_cached_model_options(s)}


@router.post("/model-options/refresh")
async def refresh_model_options():
    """Re-query the installed `claude` CLI for its current model alias list
    and cache it. 502 if the CLI isn't installed/reachable -- the cached list
    from the last successful refresh (or the seed) is left untouched."""
    from app.kanban import dispatch
    async with KanbanSessionLocal() as s:
        try:
            options = await dispatch.refresh_claude_model_options(s)
        except (OSError, subprocess.SubprocessError) as e:
            raise HTTPException(502, f"failed to query claude CLI: {e}") from e
        await s.commit()
        return {"provider": "claude-code", "options": options}
```

- [ ] **Step 6: Write a router test**

Create `backend/tests/test_kanban_model_options_api.py`:

```python
# backend/tests/test_kanban_model_options_api.py
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.kanban import dispatch
from app.main import app
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


async def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_get_model_options_returns_seed_before_any_refresh():
    async with await _client() as c:
        r = await c.get("/api/v1/kanban/model-options")
    assert r.status_code == 200
    assert r.json() == {"provider": "claude-code", "options": list(dispatch.MODEL_OPTIONS_SEED)}


@pytest.mark.asyncio
async def test_refresh_model_options_updates_cache():
    with patch.object(dispatch, "refresh_claude_model_options_sync",
                      return_value=["sonnet", "opus", "haiku", "fable"]):
        async with await _client() as c:
            r = await c.post("/api/v1/kanban/model-options/refresh")
    assert r.status_code == 200
    assert r.json() == {"provider": "claude-code",
                        "options": ["sonnet", "opus", "haiku", "fable"]}


@pytest.mark.asyncio
async def test_refresh_model_options_502_when_cli_unavailable():
    with patch.object(dispatch, "refresh_claude_model_options_sync",
                      side_effect=FileNotFoundError("claude not found")):
        async with await _client() as c:
            r = await c.post("/api/v1/kanban/model-options/refresh")
    assert r.status_code == 502
```

- [ ] **Step 7: Run all model-options tests**

Run: `cd backend && source venv/bin/activate && pytest tests/test_kanban_model_options.py tests/test_kanban_model_options_api.py -v`

Expected: PASS (all 8 tests).

- [ ] **Step 8: Commit**

```bash
git add backend/app/kanban/dispatch.py backend/app/api/v1/kanban/router.py backend/tests/test_kanban_model_options.py backend/tests/test_kanban_model_options_api.py
git commit -m "feat(kanban): model-options refresh via claude -p /model, KanbanMeta cache"
```

---

## Task 6: Frontend types + api plumbing

**Files:**
- Modify: `frontend/src/features/kanban/types.ts`
- Modify: `frontend/src/features/kanban/api.ts`

**Interfaces:**
- Produces: `Card.model`, `KanbanColumn.default_model`, `DEFAULT_MODEL_SUGGESTIONS`, `kanbanApi.getModelOptions()`, `kanbanApi.refreshModelOptions()`, updated `createCard`/`updateCard`/`createColumn`/`updateColumn` body types.

This task is pure plumbing (types + fetch wrappers) with no independently testable behavior of its own — it's exercised by the component tests in Tasks 7 and 8. No standalone test step.

- [ ] **Step 1: Add types**

In `frontend/src/features/kanban/types.ts`, after `PLATFORMS`/`Platform` (line 25):

```python
export const PLATFORMS = ["anthropic", "bedrock", "minimax"] as const;
export type Platform = (typeof PLATFORMS)[number];

// Seed suggestions shown in the model free-text field before the list has
// ever been refreshed from the installed CLI. Mirrors backend/app/kanban/
// dispatch.py MODEL_OPTIONS_SEED. Not an enum -- any string is accepted (see
// docs/superpowers/specs/2026-07-10-kanban-model-override-design.md).
export const DEFAULT_MODEL_SUGGESTIONS = ["sonnet", "opus", "haiku"] as const;
```

In `KanbanColumn` (line 47-57), add `default_model` after `default_platform`:

```python
export interface KanbanColumn {
  id: string;
  project_key: string;
  name: string;
  rank: string;
  default_agent: string | null;
  default_platform: string | null;
  default_model: string | null;
  max_sessions: number | null;
  created_at: string;
  updated_at: string;
}
```

In `Card` (line 71-102), add `model` after `agent`:

```python
  work_type?: string | null;
  agent?: string | null;
  model?: string | null;
  transport?: string | null;  // worktree | sandcastle | auto (null)
```

- [ ] **Step 2: Add api.ts fields + endpoints**

In `frontend/src/features/kanban/api.ts`, `createColumn` body type (lines 28-35):

```python
  createColumn: (body: {
    project_key: string;
    name: string;
    rank?: string;
    default_agent?: string | null;
    default_platform?: string | null;
    default_model?: string | null;
    max_sessions?: number | null;
  }): Promise<KanbanColumn> =>
    apiClient<KanbanColumn>(`${BASE}/columns`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
```

`updateColumn` (lines 41-48):

```python
  updateColumn: (
    id: string,
    body: { name?: string; rank?: string; default_agent?: string | null; default_platform?: string | null; default_model?: string | null; max_sessions?: number | null }
  ): Promise<KanbanColumn> =>
    apiClient<KanbanColumn>(`${BASE}/columns/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
```

`createCard` body type (lines 65-84), add `model` after `agent`:

```python
    work_type?: string | null;
    agent?: string | null;
    model?: string | null;
    transport?: string | null;
```

`updateCard` body type (lines 86-107), add `model` after `agent`:

```python
      agent?: string | null;
      model?: string | null;
      priority?: string | null;
```

Add two new API functions after `deleteWorkTypeMapping` (after line 294, before the closing `};` of the `kanbanApi` object):

```python
  getModelOptions: (): Promise<{ provider: string; options: string[] }> =>
    apiClient<{ provider: string; options: string[] }>(`${BASE}/model-options`),

  refreshModelOptions: (): Promise<{ provider: string; options: string[] }> =>
    apiClient<{ provider: string; options: string[] }>(`${BASE}/model-options/refresh`, {
      method: "POST",
    }),
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && npm run build`

Expected: builds clean (no TypeScript errors). This is the verification step for this task — the new fields/functions are additive and unused until Tasks 7-8 wire them into components, so `tsc` passing is the signal that nothing existing broke.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/features/kanban/types.ts frontend/src/features/kanban/api.ts
git commit -m "feat(kanban): frontend types + api client for model override"
```

---

## Task 7: `ColumnSettingsDialog` — Default model field + Refresh

**Files:**
- Modify: `frontend/src/features/kanban/components/ColumnSettingsDialog.tsx`
- Test: `frontend/src/features/kanban/components/ColumnSettingsDialog.test.tsx` (new file)

**Interfaces:**
- Consumes: `kanbanApi.getModelOptions`, `kanbanApi.refreshModelOptions`, `KanbanColumn.default_model`, `DEFAULT_MODEL_SUGGESTIONS` (Task 6).

- [ ] **Step 1: Write the failing test**

Create `frontend/src/features/kanban/components/ColumnSettingsDialog.test.tsx`:

```tsx
// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const getModelOptions = vi.fn(async () => ({ provider: "claude-code", options: ["sonnet", "opus", "haiku"] }));
const refreshModelOptions = vi.fn(async () => ({ provider: "claude-code", options: ["sonnet", "opus", "haiku", "fable"] }));
const updateColumn = vi.fn(async (id: string, body: Record<string, unknown>) => ({
  id, project_key: "P", name: "engineer", rank: "0",
  default_agent: "engineer", default_platform: null, default_model: null,
  max_sessions: null, created_at: "", updated_at: "",
  ...body,
}));

vi.mock("../api", () => ({
  kanbanApi: {
    agents: vi.fn(async () => ({ agents: ["engineer", "analyst"] })),
    getModelOptions,
    refreshModelOptions,
    updateColumn,
  },
}));

import { ColumnSettingsDialog } from "./ColumnSettingsDialog";

const COLUMN = {
  id: "c1", project_key: "P", name: "engineer", rank: "0",
  default_agent: "engineer", default_platform: null, default_model: null,
  max_sessions: null, created_at: "", updated_at: "",
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ColumnSettingsDialog model field", () => {
  it("fetches model suggestions on open", async () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[COLUMN]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await waitFor(() => expect(getModelOptions).toHaveBeenCalled());
  });

  it("submits the typed model as default_model on Save", async () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[COLUMN]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    fireEvent.change(screen.getByLabelText(/default model/i), { target: { value: "opus" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(updateColumn).toHaveBeenCalledWith(
      "c1",
      expect.objectContaining({ default_model: "opus" }),
    ));
  });

  it("refreshes the suggestion list when Refresh is clicked", async () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[COLUMN]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    await waitFor(() => expect(refreshModelOptions).toHaveBeenCalled());
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/kanban/components/ColumnSettingsDialog.test.tsx`

Expected: FAIL — no "Default model" label / no "Refresh" button exist yet.

- [ ] **Step 3: Add the Default model field + Refresh button**

In `frontend/src/features/kanban/components/ColumnSettingsDialog.tsx`:

Add imports (after line 21, `import { PLATFORMS } from "../types";`):

```tsx
import { PLATFORMS, DEFAULT_MODEL_SUGGESTIONS } from "../types";
```

Add state (after `editMaxSessions`, line 53):

```tsx
  const [editMaxSessions, setEditMaxSessions] = useState<number | null>(null);
  const [editModel, setEditModel] = useState<string>("");
  const [modelOptions, setModelOptions] = useState<string[]>([...DEFAULT_MODEL_SUGGESTIONS]);
```

Fetch suggestions on open (add a new `useEffect` after the `availableAgents` effect, line 62):

```tsx
  useEffect(() => {
    if (!open) return;
    kanbanApi.getModelOptions().then((r) => setModelOptions(r.options)).catch(() => {});
  }, [open]);

  const handleRefreshModels = async () => {
    try {
      const r = await kanbanApi.refreshModelOptions();
      setModelOptions(r.options);
    } catch {
      toast.error("Failed to refresh model list");
    }
  };
```

Include `default_model` in the update payload (`handleUpdate`, lines 88-103):

```tsx
  const handleUpdate = async (id: string) => {
    const agent = editAgent.trim() || null;
    const platform = editPlatform === DEFAULT_PLATFORM_SENTINEL ? null : editPlatform;
    const model = editModel.trim() || null;
    try {
      const col = await kanbanApi.updateColumn(id, {
        default_agent: agent,
        default_platform: platform,
        default_model: model,
        max_sessions: editMaxSessions,
      });
      setItems((prev) => prev.map((c) => (c.id === id ? col : c)));
      setEditingId(null);
      onChanged();
    } catch {
      toast.error("Failed to update column");
    }
  };
```

Add the field to the editing row (right after the Platform `<Select>` block, before the max-sessions `<div>`, around line 175):

```tsx
                  <div className="flex flex-col gap-1">
                    <label htmlFor={`default-model-${col.id}`} className="sr-only">
                      Default model
                    </label>
                    <input
                      id={`default-model-${col.id}`}
                      list={`model-suggestions-${col.id}`}
                      className="h-8 w-32 rounded border bg-background px-2 text-sm"
                      placeholder="Default model"
                      value={editModel}
                      onChange={(e) => setEditModel(e.target.value)}
                    />
                    <datalist id={`model-suggestions-${col.id}`}>
                      {modelOptions.map((m) => (
                        <option key={m} value={m} />
                      ))}
                    </datalist>
                    <button
                      type="button"
                      className="text-[10px] text-muted-foreground hover:text-foreground text-left"
                      onClick={handleRefreshModels}
                    >
                      Refresh
                    </button>
                  </div>
```

Show the current value in the read-only row (right after the Platform display block, around line 217):

```tsx
                    {col.default_model && (
                      <div className="text-xs text-muted-foreground">
                        Model: {col.default_model}
                      </div>
                    )}
```

Seed `editModel` when entering edit mode (in the "Edit" button's `onClick`, alongside `setEditAgent`/`setEditPlatform`, around line 227-232):

```tsx
                        onClick={() => {
                          setEditingId(col.id);
                          setEditAgent(col.default_agent ?? "");
                          setEditPlatform(col.default_platform ?? DEFAULT_PLATFORM_SENTINEL);
                          setEditModel(col.default_model ?? "");
                          setEditMaxSessions(col.max_sessions ?? 0);
                        }}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/kanban/components/ColumnSettingsDialog.test.tsx`

Expected: PASS (all 3 tests).

- [ ] **Step 5: Run the full frontend test suite + lint to check for regressions**

Run: `cd frontend && npm run lint && npx vitest run src/features/kanban`

Expected: PASS, no lint errors, no regressions in other kanban component tests.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/kanban/components/ColumnSettingsDialog.tsx frontend/src/features/kanban/components/ColumnSettingsDialog.test.tsx
git commit -m "feat(kanban): Default model field + refresh in ColumnSettingsDialog"
```

---

## Task 8: `CardEditDialog` — Model field + wiring through KanbanPage/CardDrawer

**Files:**
- Modify: `frontend/src/features/kanban/components/CardEditDialog.tsx`
- Modify: `frontend/src/features/kanban/KanbanPage.tsx`
- Modify: `frontend/src/features/kanban/components/CardDrawer.tsx`
- Test: `frontend/src/features/kanban/components/CardEditDialog.test.tsx`

**Interfaces:**
- Consumes: `kanbanApi.getModelOptions` (Task 6), `DEFAULT_MODEL_SUGGESTIONS` (Task 6).
- Produces: `CardEditDialog`'s `onSubmit` payload now includes `model: string | null`.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/features/kanban/components/CardEditDialog.test.tsx` (inside the existing `describe("CardEditDialog", ...)` block, after the `work_type` test):

```tsx
  it("forwards the chosen model in the onSubmit payload", () => {
    const onSubmit = vi.fn();
    render(
      <CardEditDialog
        open
        initial={{ title: "T", description: "" }}
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    );
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "opus" } });
    screen.getByRole("button", { name: /update/i }).click();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const payload = onSubmit.mock.calls[0][0];
    expect(payload).toHaveProperty("model", "opus");
  });

  it("submits model: null when the field is left empty", () => {
    const onSubmit = vi.fn();
    render(
      <CardEditDialog
        open
        initial={{ title: "T", description: "" }}
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    );
    screen.getByRole("button", { name: /update/i }).click();
    const payload = onSubmit.mock.calls[0][0];
    expect(payload).toHaveProperty("model", null);
  });
```

Also add `getModelOptions` to the mocked API module at the top of the file (extend the existing `vi.mock` block; there isn't one for `../api` yet in this file, so add it):

```tsx
vi.mock("../api", () => ({
  kanbanApi: {
    getModelOptions: vi.fn(async () => ({ provider: "claude-code", options: ["sonnet", "opus", "haiku"] })),
  },
}));
```

(Place this `vi.mock` call alongside the existing `vi.mock("@/contexts/ProviderContext", ...)` and `vi.mock("@/features/cc-bridge/api", ...)` calls near the top of the file, before the `import { CardEditDialog } ...` line — vitest hoists `vi.mock` calls automatically regardless of position, but keeping them grouped matches the file's existing style.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/features/kanban/components/CardEditDialog.test.tsx`

Expected: FAIL — no element with label "Model" exists yet.

- [ ] **Step 3: Add the Model field to `CardEditDialog`**

In `frontend/src/features/kanban/components/CardEditDialog.tsx`:

Update the import (line 27):

```tsx
import { PRIORITIES, WORK_TYPES, DEFAULT_MODEL_SUGGESTIONS, type Priority, type WorkType } from "../types";
import { kanbanApi } from "../api";
```

Extend the `initial` and `onSubmit` prop types (lines 57-69 and 73-86) — add `model` next to `work_type`:

```tsx
  initial?: {
    title: string;
    description: string;
    priority?: string | null;
    labels?: string[] | null;
    work_type?: string | null;
    model?: string | null;
    transport?: string | null;
    resume_session_id?: string | null;
    resume_project_folder?: string | null;
    scheduled_at?: string | null;
    analyst_agent_id?: string | null;
    executor_agent_id?: string | null;
  };
  defaultAgent?: string | null;
  projectPath?: string;
  onClose: () => void;
  onSubmit: (data: {
    title: string;
    description: string;
    priority: string | null;
    labels: string[];
    work_type: string | null;
    agent: string | null;
    model: string | null;
    transport: string | null;
    resume_session_id: string | null;
    resume_project_folder: string | null;
    scheduled_at: string | null;
    analyst_agent_id: string | null;
    executor_agent_id: string | null;
  }) => void;
```

Add state (after `workType`, line 98):

```tsx
  const [workType, setWorkType] = useState<WorkType | "">(
    (initial?.work_type as WorkType) ?? ""
  );
  const [model, setModel] = useState<string>(initial?.model ?? "");
  const [modelOptions, setModelOptions] = useState<string[]>([...DEFAULT_MODEL_SUGGESTIONS]);

  useEffect(() => {
    if (!open) return;
    kanbanApi.getModelOptions().then((r) => setModelOptions(r.options)).catch(() => {});
  }, [open]);
```

Add the field to the form (right after the Provider `<Select>` block, before the Analyst-agent section, around line 233):

```tsx
          <div className="space-y-2">
            <Label htmlFor="card-model">Model</Label>
            <input
              id="card-model"
              list="card-model-suggestions"
              className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm"
              placeholder="(unset — falls back to column/persona default)"
              value={model}
              onChange={(e) => setModel(e.target.value)}
            />
            <datalist id="card-model-suggestions">
              {modelOptions.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
            <p className="text-xs text-muted-foreground">
              Overrides the column default and persona frontmatter for this card only.
            </p>
          </div>
```

Include `model` in the submit payload (the `onSubmit` call, lines 441-456):

```tsx
              onSubmit({
                title,
                description,
                priority: priority === "none" ? null : priority,
                labels,
                work_type: workType || null,
                agent: agent === AUTO ? null : agent,
                model: model.trim() || null,
                transport: transport === "auto" ? null : transport,
                resume_session_id,
                resume_project_folder,
                scheduled_at: scheduledAt ? new Date(scheduledAt).toISOString() : null,
                analyst_agent_id: analystAgentId === AUTO ? null : analystAgentId,
                executor_agent_id: executorAgentId === AUTO ? null : executorAgentId,
              })
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/features/kanban/components/CardEditDialog.test.tsx`

Expected: PASS (all tests, including the 2 new ones).

- [ ] **Step 5: Wire `model` through `KanbanPage.tsx` and `CardDrawer.tsx`**

In `frontend/src/features/kanban/KanbanPage.tsx`, the `CardEditDialog` `onSubmit` handler (around line 349-368) — add `model` to the destructured params and the `createCard` call:

```tsx
          onSubmit={async ({ title, description, priority, labels, work_type, agent, model, transport, resume_session_id, resume_project_folder, scheduled_at, analyst_agent_id, executor_agent_id }) => {
            try {
              await kanbanApi.createCard({
                project_key: projectKey,
                title,
                description,
                priority,
                labels: labels.length ? labels : null,
                work_type,
                agent,
                model,
                transport,
                resume_session_id,
                resume_project_folder,
                scheduled_at,
                analyst_agent_id,
                executor_agent_id,
              });
              setCreating(false);
              void reload();
            } catch {
              toast.error("Failed to create card");
            }
          }}
```

In `frontend/src/features/kanban/components/CardDrawer.tsx`, the `CardEditDialog` `initial` prop (around line 556-568) — add `model: card.model`:

```tsx
            initial={{
              title: card.title,
              description: card.description,
              priority: card.priority,
              labels: card.labels,
              work_type: card.work_type,
              model: card.model,
              transport: card.transport,
              resume_session_id: card.resume_session_id,
              resume_project_folder: card.resume_project_folder,
              scheduled_at: card.scheduled_at,
              analyst_agent_id: card.analyst_agent_id,
              executor_agent_id: card.executor_agent_id,
            }}
```

And its `onSubmit` handler (around line 572-590) — add `model` to the destructured params and the `updateCard` call:

```tsx
            onSubmit={async ({ title, description, priority, labels, work_type, agent, model, transport, resume_session_id, resume_project_folder, scheduled_at, analyst_agent_id, executor_agent_id }) => {
              try {
                await kanbanApi.updateCard(card.id, {
                  title,
                  description,
                  priority,
                  labels: labels.length ? labels : null,
                  work_type,
                  agent,
                  model,
                  transport,
                  resume_session_id,
                  resume_project_folder,
                  scheduled_at,
                  analyst_agent_id,
                  executor_agent_id,
                });
                setEditing(false);
                onChanged();
              } catch {
                toast.error("Failed to update card");
              }
            }}
```

- [ ] **Step 6: Run the full frontend suite + build**

Run: `cd frontend && npm run lint && npx vitest run src/features/kanban && npm run build`

Expected: PASS — lint clean, all kanban tests green, production build succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/kanban/components/CardEditDialog.tsx frontend/src/features/kanban/components/CardEditDialog.test.tsx frontend/src/features/kanban/KanbanPage.tsx frontend/src/features/kanban/components/CardDrawer.tsx
git commit -m "feat(kanban): Model field in CardEditDialog, wired through create/update card"
```

---

## Final verification

- [ ] Run the complete backend suite: `cd backend && source venv/bin/activate && pytest tests/ -v -k kanban`
- [ ] Run the complete frontend suite: `cd frontend && npm run lint && npx vitest run && npm run build`
- [ ] Manual smoke (per the design doc's "Manual smoke" section): enable autodispatch on a test project, set `column.default_model="opus"` via the UI, dispatch a card, confirm the spawned tmux pane's `claude` invocation includes `--model opus` (`tmux capture-pane` or inspect the pane's process command line).
