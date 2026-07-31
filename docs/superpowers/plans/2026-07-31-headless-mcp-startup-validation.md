# Headless MCP Startup Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make headless dispatch wait for Claude Code's init event and reject skipped MCP configuration entries before recording the spawn as successful.

**Architecture:** `headless_runner` owns a one-shot startup future completed by the raw `system/init` event. A non-empty `mcp_server_errors` array raises `McpServerConfigError`; a clean init marks readiness. `dispatch._run_card` supports both existing synchronous transport results and the headless transport's awaitable result so the established claim-compensation and board-visible dispatch-failure path remains the single error-reporting mechanism.

**Tech Stack:** Python 3.11+, asyncio subprocesses/futures, pytest + pytest-asyncio, async SQLAlchemy kanban operations.

## Global Constraints

- Do not classify ordinary `mcp_servers[].status == "failed"` connection failures; this card covers only non-empty `mcp_server_errors`.
- Preserve each error entry's `name`, `type`, and `message` in the propagated exception text.
- Do not run the full backend pytest suite locally; use `scripts/run-single-test.sh` for targeted tests and CI for the canonical backend gate.
- Do not change frontend code, database schema, or MCP configuration.

---

### Task 1: Reject skipped MCP entries during headless startup

**Files:**
- Modify: `backend/app/kanban/headless_runner.py:631-757,834-1061,1138-1302`
- Test: `backend/tests/test_headless_transport.py`

**Interfaces:**
- Produces: `class McpServerConfigError(RuntimeError)` containing formatted skipped-entry details.
- Produces: `async def headless_transport(...) -> dict` that returns only after clean init.
- Extends: `run_headless(..., startup_future: asyncio.Future[None] | None = None) -> dict`.
- Extends: `_consume_log_file(..., startup_future: asyncio.Future[None] | None = None) -> int` and `_dispatch_log_line(..., startup_future: asyncio.Future[None] | None = None) -> None`.

- [ ] **Step 1: Write the failing real-shape fixture test**

Add to `backend/tests/test_headless_transport.py` a fake CLI that emits the verified event and then sleeps:

```python
@pytest.mark.asyncio
async def test_headless_transport_rejects_init_mcp_server_errors(monkeypatch, tmp_path):
    import os
    import sys as stdlib_sys

    pidfile = tmp_path / "fake_cli.pid"
    fake_cli = tmp_path / "fake_claude.py"
    fake_cli.write_text(
        "import json, os, sys, time\n"
        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
        "payload = {'type':'system','subtype':'init','session_id':'sess-mcp-bad',"
        "'cwd':'.','model':'claude-opus-4-8','permissionMode':'acceptEdits',"
        "'mcp_server_errors':[{'name':'cockpit-kanban','type':'invalid_config',"
        "'message':'Skipped — invalid MCP server config for \\\"cockpit-kanban\\\": command: expected string, received undefined'}]}\n"
        "sys.stdout.write(json.dumps(payload) + '\\n'); sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    wrapper = tmp_path / "fake_claude.sh"
    wrapper.write_text(f"#!/bin/sh\nexec {stdlib_sys.executable} '{fake_cli}' \"$@\"\n")
    wrapper.chmod(0o755)
    monkeypatch.setattr(hr, "resolve_cli_executable", lambda cli_id: str(wrapper))

    worktree = tmp_path / ".claude" / "worktrees" / "k-mcp-bad"
    worktree.mkdir(parents=True)
    monkeypatch.setattr(hr, "_spawn_headless_worktree", lambda *args, **kwargs: None)

    with pytest.raises(hr.McpServerConfigError) as exc_info:
        await hr.headless_transport(
            directory=str(tmp_path), prompt="ok", session_name="k-mcp-bad",
        )

    message = str(exc_info.value)
    assert "cockpit-kanban" in message
    assert "invalid_config" in message
    assert "expected string, received undefined" in message
    pid = int(pidfile.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert "k-mcp-bad" not in hr._headless_processes
```

Adapt the worktree setup to the final helper boundary rather than monkeypatching an undefined name if the existing inline `_spawn_git_worktree` remains local.

- [ ] **Step 2: Run the test to verify RED**

Run:

```bash
bash scripts/run-single-test.sh tests/test_headless_transport.py::test_headless_transport_rejects_init_mcp_server_errors
```

Expected: FAIL because `headless_transport` returns a dict immediately and `McpServerConfigError` does not exist.

- [ ] **Step 3: Add raw init validation and startup signaling**

In `headless_runner.py`, add the typed error and formatter:

```python
class McpServerConfigError(RuntimeError):
    pass


def _mcp_server_config_error(payload: Mapping[str, Any]) -> McpServerConfigError | None:
    errors = payload.get("mcp_server_errors")
    if not errors:
        return None
    details = "; ".join(
        f"{item.get('name', '<unknown>')} ({item.get('type', 'unknown')}): "
        f"{item.get('message', 'no message')}"
        for item in errors
        if isinstance(item, Mapping)
    )
    return McpServerConfigError(
        f"Claude Code skipped MCP server configuration: {details or errors!r}"
    )
```

Before `map_stream_event(payload)` in `_dispatch_log_line`, when the payload is `type == "system"` and `subtype == "init"`, set the future exception for a non-empty error list or set its result for a clean init. Pass the optional future from `run_headless` through `_consume_log_file` into `_dispatch_log_line`. If validation raises, raise the same exception after setting the future so `run_headless`'s existing tailer-first path kills and reaps the child.

Convert `headless_transport` to `async def`. Keep worktree creation and reservation behavior, create `startup_future = loop.create_future()`, launch `run_headless(..., startup_future=startup_future)` as the existing strong-referenced task, and wait for either readiness or task completion:

```python
done, _ = await asyncio.wait(
    {startup_future, task}, return_when=asyncio.FIRST_COMPLETED,
)
if startup_future in done:
    startup_future.result()
else:
    await task
    raise RuntimeError(f"headless {session_name} exited before session init")
return {"session_name": session_name, "transport": "headless", "status": "started"}
```

Retain the task in `_headless_start_tasks` after a clean init so it owns the full run lifecycle.

- [ ] **Step 4: Run the targeted fixture test to verify GREEN**

Run the same single-test command. Expected: PASS; subprocess and registry cleanup assertions succeed.

- [ ] **Step 5: Run adjacent headless lifecycle tests**

Run:

```bash
bash scripts/run-single-test.sh tests/test_headless_transport.py -k 'run_headless or headless_transport or full_dispatch_cycle'
```

Expected: all selected tests pass. Fix only readiness-signature call sites or fixture assumptions caused by this change.

- [ ] **Step 6: Commit the runner change**

```bash
git add backend/app/kanban/headless_runner.py backend/tests/test_headless_transport.py
git commit -m "fix(headless): reject skipped MCP config at init"
```

---

### Task 2: Route awaitable startup failures through dispatch compensation

**Files:**
- Modify: `backend/app/kanban/dispatch.py:12-23,3597-3605,5949-5990`
- Test: `backend/tests/test_kanban_dispatch.py:6697-6762`

**Interfaces:**
- Consumes: an async headless transport returning `dict` or raising `McpServerConfigError` before readiness.
- Produces: `SpawnTransport.__call__` return type `dict | Awaitable[dict]`.
- Preserves: all synchronous transports and the existing `_run_card` failure compensation.

- [ ] **Step 1: Write a failing awaitable-transport dispatch test**

Add next to synchronous spawn-failure tests:

```python
@pytest.mark.asyncio
async def test_awaitable_spawn_failure_uses_dispatch_failure_path():
    class AsyncFailingTransport(RecordingTransport):
        def __call__(self, **kwargs):
            self.calls.append(kwargs)

            async def fail():
                raise RuntimeError(
                    "Claude Code skipped MCP server configuration: "
                    "cockpit-kanban (invalid_config): expected string"
                )

            return fail()

    transport = AsyncFailingTransport()
    async with KanbanSessionLocal() as s:
        cid = await _make_card(s, title="async-mcp-fails", column="To Resume")
        await s.commit()

    for _ in range(dispatch.MAX_DISPATCH_FAILURES):
        async with KanbanSessionLocal() as s:
            with pytest.raises(RuntimeError, match="cockpit-kanban"):
                await dispatch.dispatch_project(
                    s, project_key=PK, project_path="/p", transport=transport,
                )
            await s.commit()

    async with KanbanSessionLocal() as s:
        card = await get_card(s, cid)
        activity = await service.card_activity(s, cid)
    assert card.column == "Impediment"
    assert card.claimed_by is None
    comments = [
        op.payload["text"] for op in activity
        if op.op_type == "comment" and op.payload["text"].startswith("[dispatch-failure]")
    ]
    assert comments
    assert "cockpit-kanban" in comments[-1]
```

- [ ] **Step 2: Run the test to verify RED**

Run:

```bash
bash scripts/run-single-test.sh tests/test_kanban_dispatch.py::test_awaitable_spawn_failure_uses_dispatch_failure_path
```

Expected: FAIL because `_run_card` treats the coroutine object as a successful spawn; pytest may also report an un-awaited coroutine.

- [ ] **Step 3: Await only awaitable transport results**

Import `inspect`, widen the protocol return type, and change the spawn call:

```python
spawned = card_transport(...)
if inspect.isawaitable(spawned):
    spawned = await spawned
```

Keep both statements inside the existing `try` so awaited exceptions enter the unchanged release/failure/move/comment branch.

- [ ] **Step 4: Run focused dispatch tests to verify GREEN**

Run:

```bash
bash scripts/run-single-test.sh tests/test_kanban_dispatch.py -k 'awaitable_spawn_failure or synchronous_spawn_failure'
```

Expected: the new awaitable test and existing synchronous failure tests pass.

- [ ] **Step 5: Commit the dispatch integration**

```bash
git add backend/app/kanban/dispatch.py backend/tests/test_kanban_dispatch.py
git commit -m "fix(dispatch): await headless startup readiness"
```

---

### Task 3: Verify the complete scoped change

**Files:**
- Verify: `backend/app/kanban/headless_runner.py`
- Verify: `backend/app/kanban/dispatch.py`
- Verify: `backend/tests/test_headless_transport.py`
- Verify: `backend/tests/test_kanban_dispatch.py`

**Interfaces:**
- Consumes the completed runner and dispatcher changes.
- Produces verification evidence for the host card and CI-ready commits.

- [ ] **Step 1: Run all directly changed test files through targeted wrappers**

```bash
bash scripts/run-single-test.sh tests/test_headless_transport.py
bash scripts/run-single-test.sh tests/test_kanban_dispatch.py -k 'spawn_failure or headless'
```

Expected: both commands pass. Do not run full local pytest.

- [ ] **Step 2: Run ruff on changed backend files**

```bash
/home/vdvgu/claude-cockpit/backend/venv/bin/ruff check \
  backend/app/kanban/headless_runner.py \
  backend/app/kanban/dispatch.py \
  backend/tests/test_headless_transport.py \
  backend/tests/test_kanban_dispatch.py
```

Expected: `All checks passed!`

- [ ] **Step 3: Confirm the implementation diff is scoped**

```bash
git status --short
git diff origin/master...HEAD --stat
git diff origin/master...HEAD -- backend/app/kanban/headless_runner.py backend/app/kanban/dispatch.py backend/tests/test_headless_transport.py backend/tests/test_kanban_dispatch.py
```

Expected: only the design/plan docs, runner, dispatch integration, and their tests changed; no frontend, schema, or config changes.

- [ ] **Step 4: Commit any verification-only corrections**

If targeted verification required corrections, commit only those exact files:

```bash
git add backend/app/kanban/headless_runner.py backend/app/kanban/dispatch.py backend/tests/test_headless_transport.py backend/tests/test_kanban_dispatch.py
git commit -m "test(headless): harden MCP startup failure coverage"
```
