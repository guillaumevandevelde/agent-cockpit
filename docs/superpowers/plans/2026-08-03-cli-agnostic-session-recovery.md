# CLI-Agnostic Session Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover dead Codex, OpenCode, and MiMo sessions into `To Resume` with their original session id and worktree, while retaining Claude Code behavior and making Copilot's currently unresolvable state explicit.

**Architecture:** Session-store discovery becomes an optional `AgenticCli` capability. Each verified adapter owns its vendor layout; the Kanban recovery layer only chooses the effective CLI and delegates. A recovered non-Claude target persists its absolute original worktree in the existing `resume_project_folder` field, and the generic spawn layer validates and restores that directory before building the adapter's resume command.

**Tech Stack:** Python 3.11+, stdlib `json`/`sqlite3`/`pathlib`, FastAPI service layer, async SQLAlchemy Kanban operations, pytest/pytest-asyncio.

## Global Constraints

- Preserve Claude Code's existing `~/.claude/projects/<encoded-worktree>/<session>.jsonl` behavior and public helper signatures where tests/diagnostics already consume them.
- Read third-party state read-only; never migrate, mutate, or create Codex/OpenCode/MiMo stores.
- Match only an exact normalized worktree directory and select the most recently updated matching session.
- Copilot returns no target and emits `resume detection unsupported for cli=copilot-cli`; never fall back to Claude's layout.
- A missing/corrupt store is a normal `None` result and must not break startup recovery or the reaper.
- Run only targeted backend tests via `scripts/run-single-test.sh`; do not run the full local pytest suite.
- No frontend files are in scope.

## Verified Vendor Contracts

- Codex rollout layout, `session_meta` fields, and `sessions/YYYY/MM/DD/rollout-…-<id>.jsonl`: [`openai/codex@51d4aa9`](https://github.com/openai/codex/blob/51d4aa946ca30ae388a2d696c733ac9d1c6537bd/codex-rs/rollout/src/recorder.rs).
- OpenCode's SQLite `session` table (`id`, `directory`, parent/archive/update fields): [`sst/opencode@9535a8f`](https://github.com/sst/opencode/blob/9535a8f929eeeb4116f3d06d2a8391e0ec72cff5/packages/core/src/session/sql.ts), cross-checked against installed OpenCode 1.18.8's read-only schema and `opencode session list --format json`.
- MiMoCode's `MIMOCODE_HOME`/XDG data contract, `mimocode.db`, session table, and `--session`/`--prompt` flags: [`XiaomiMiMo/MiMo-Code@09d03d6`](https://github.com/XiaomiMiMo/MiMo-Code/tree/09d03d67bcfc0f56733344912319dd71d39da424).
- Copilot's official docs confirm `~/.copilot/session-store.db` and `session-state/`, but do not provide a stable cwd field; known cwd persistence regressions make automatic discovery intentionally unsupported: [About GitHub Copilot CLI session data](https://docs.github.com/en/copilot/concepts/agents/copilot-cli/chronicle).

---

### Task 1: Add Adapter-Owned Resume Discovery

**Files:**
- Modify: `backend/app/services/agentic_cli/base.py`
- Modify: `backend/app/services/agentic_cli/claude_code.py`
- Modify: `backend/app/services/agentic_cli/codex_cli.py`
- Modify: `backend/app/services/agentic_cli/open_code.py`
- Modify: `backend/app/services/agentic_cli/mimo_code.py`
- Test: `backend/tests/test_agentic_cli_resume_resolution.py`

**Interfaces:**
- Produces: `AgenticCli.supports_resume_resolution: bool`
- Produces: `AgenticCli.resolve_resume_target(worktree_path: Path, *, data_dir: Path | None = None) -> tuple[str, str | None] | None`
- Produces: `AgenticCli.resolve_directory(options: SpawnCommandOptions) -> str`
- Produces: `_resolve_sqlite_resume_target(database_path: Path, worktree_path: Path) -> tuple[str, str] | None`
- Consumes: vendor-specific home helpers (`get_codex_home`, `get_opencode_data_home`, `get_mimo_data_home`) and the existing Claude projects directory helper.

- [ ] **Step 1: Write failing adapter-resolution tests**

Create `backend/tests/test_agentic_cli_resume_resolution.py` with fixtures that never touch real user stores:

```python
import json
import os
import sqlite3
from pathlib import Path

from app.services.agentic_cli.claude_code import ClaudeCodeCli
from app.services.agentic_cli.codex_cli import CodexCli
from app.services.agentic_cli.copilot_cli import CopilotCli
from app.services.agentic_cli.mimo_code import MiMoCodeCli
from app.services.agentic_cli.open_code import OpenCodeCli


def _session_db(path: Path, rows: list[tuple[str, str, int]]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT NOT NULL, "
        "time_updated INTEGER NOT NULL, time_archived INTEGER)"
    )
    conn.executemany(
        "INSERT INTO session(id, directory, time_updated) VALUES (?, ?, ?)", rows
    )
    conn.commit()
    conn.close()


def test_codex_resolver_selects_newest_exact_worktree(tmp_path):
    worktree = tmp_path / "repo" / ".claude" / "worktrees" / "k-codex"
    worktree.mkdir(parents=True)
    sessions = tmp_path / "codex" / "sessions" / "2026" / "08" / "03"
    sessions.mkdir(parents=True)
    for sid, cwd, mtime in [
        ("11111111-1111-1111-1111-111111111111", str(worktree), 1000),
        ("22222222-2222-2222-2222-222222222222", str(worktree), 2000),
        ("33333333-3333-3333-3333-333333333333", str(tmp_path / "other"), 3000),
    ]:
        path = sessions / f"rollout-2026-08-03T00-00-00-{sid}.jsonl"
        path.write_text(json.dumps({
            "timestamp": "2026-08-03T00:00:00Z",
            "type": "session_meta",
            "payload": {"id": sid, "session_id": sid, "cwd": cwd},
        }) + "\n", encoding="utf-8")
        os.utime(path, (mtime, mtime))

    assert CodexCli().resolve_resume_target(worktree, data_dir=tmp_path / "codex") == (
        "22222222-2222-2222-2222-222222222222", str(worktree.resolve())
    )


def test_opencode_and_mimo_resolvers_query_exact_directory(tmp_path):
    worktree = tmp_path / "repo" / ".claude" / "worktrees" / "k-sqlite"
    worktree.mkdir(parents=True)
    for cli, filename in [(OpenCodeCli(), "opencode.db"), (MiMoCodeCli(), "mimocode.db")]:
        data = tmp_path / cli.id
        data.mkdir()
        _session_db(data / filename, [
            ("ses_old", str(worktree), 1000),
            ("ses_new", str(worktree), 2000),
            ("ses_other", str(tmp_path / "other"), 3000),
        ])
        assert cli.resolve_resume_target(worktree, data_dir=data) == (
            "ses_new", str(worktree.resolve())
        )


def test_copilot_resume_discovery_is_explicitly_unsupported(tmp_path):
    cli = CopilotCli()
    assert cli.supports_resume_resolution is False
    assert cli.resolve_resume_target(tmp_path) is None
```

Also add a Claude regression using two fake JSONL files and assert the newer stem plus encoded folder are unchanged.

- [ ] **Step 2: Run tests and confirm the capability is absent**

Run:

```bash
bash scripts/run-single-test.sh tests/test_agentic_cli_resume_resolution.py
```

Expected: import/attribute failures for `supports_resume_resolution`, `resolve_resume_target`, or `get_mimo_data_home`.

- [ ] **Step 3: Implement the base capability and SQLite helper**

In `base.py`, add:

```python
import sqlite3

ResumeTarget = tuple[str, str | None]


def _resolve_sqlite_resume_target(
    database_path: Path, worktree_path: Path,
) -> tuple[str, str] | None:
    if not database_path.is_file() or not worktree_path.is_dir():
        return None
    resolved = worktree_path.resolve()
    try:
        connection = sqlite3.connect(
            f"file:{database_path}?mode=ro", uri=True, timeout=1,
        )
        try:
            row = connection.execute(
                "SELECT id FROM session WHERE directory = ? "
                "AND time_archived IS NULL ORDER BY time_updated DESC LIMIT 1",
                (str(resolved),),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.warning("could not read resume session store %s", database_path, exc_info=True)
        return None
    return (str(row[0]), str(resolved)) if row else None
```

Add default adapter behavior:

```python
supports_resume_resolution = False

def resolve_resume_target(
    self, worktree_path: Path, *, data_dir: Path | None = None,
) -> ResumeTarget | None:
    return None

def resolve_directory(self, options: SpawnCommandOptions) -> str:
    if options.mode == "resume" and options.project_folder:
        candidate = Path(options.project_folder).expanduser()
        if candidate.is_absolute():
            resolved = candidate.resolve()
            if not resolved.is_dir():
                raise ValueError(f"Resume directory does not exist: '{candidate}'")
            return str(resolved)
    return options.directory
```

- [ ] **Step 4: Implement verified adapter resolvers**

- `ClaudeCodeCli`: set support true; move the current newest-JSONL logic into adapter methods, using `data_dir or get_claude_projects_dir()`, and retain its encoded folder return value.
- `CodexCli`: set support true; scan `(data_dir or get_codex_home()) / "sessions"` for `rollout-*.jsonl`; inspect only the initial metadata lines; accept `type == "session_meta"`, exact normalized `payload.cwd`, and `payload.id` (fall back to `payload.session_id`); choose newest mtime; return the absolute worktree as the opaque second value.
- `OpenCodeCli`: set support true; call `_resolve_sqlite_resume_target((data_dir or get_opencode_data_home()) / "opencode.db", worktree_path)`.
- `MiMoCodeCli`: set support true; add official XDG/MIMOCODE_HOME resolution and query `mimocode.db` with the same helper.
- `CopilotCli`: inherit the unsupported default.

- [ ] **Step 5: Run the adapter tests until green**

Run:

```bash
bash scripts/run-single-test.sh tests/test_agentic_cli_resume_resolution.py
```

Expected: all tests pass; corrupt/missing files return `None` without raising.

---

### Task 2: Resume Every CLI in Its Original Worktree

**Files:**
- Modify: `backend/app/services/runs/spawn.py:190-196`
- Modify: `backend/app/services/agentic_cli/mimo_code.py:90-105`
- Test: `backend/tests/test_runs_spawn.py`
- Test: `backend/tests/test_providers.py`

**Interfaces:**
- Consumes: `AgenticCli.resolve_directory(options)` from Task 1.
- Produces: all resume spawns validate/use the adapter-resolved directory before building argv.
- Produces: MiMo resume argv `mimo --session <id> --prompt <text>` matching the current upstream CLI.

- [ ] **Step 1: Write failing non-Claude resume-directory test**

Append to `backend/tests/test_runs_spawn.py`:

```python
def test_codex_resume_uses_recorded_worktree_directory(monkeypatch, tmp_path):
    from app.services.agentic_cli.base import SpawnCommandOptions
    from app.services.runs import spawn

    repo = tmp_path / "repo"
    repo.mkdir()
    worktree = tmp_path / "repo" / ".claude" / "worktrees" / "k-codex"
    worktree.mkdir(parents=True)
    calls = []

    def fake_run(args, capture_output=True, text=True, timeout=10):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(spawn, "_session_name_for", lambda directory, preferred=None: "k-resume")
    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    spawn.spawn_session("codex-cli", SpawnCommandOptions(
        directory=str(repo), mode="resume", session_id="codex-session",
        project_folder=str(worktree),
    ))

    assert calls[0][:7] == [
        "tmux", "new-session", "-d", "-s", "k-resume", "-c", str(worktree)
    ]
    assert f"--cd {worktree}" in calls[0][-1]
```

Update the MiMo provider test to expect `--session`, not `--resume`, and `--prompt` before prompt text.

- [ ] **Step 2: Run the two focused tests and confirm failure**

Run:

```bash
bash scripts/run-single-test.sh tests/test_runs_spawn.py::test_codex_resume_uses_recorded_worktree_directory
bash scripts/run-single-test.sh tests/test_providers.py -k mimo
```

Expected: Codex starts in the repo root and/or MiMo emits the stale `--resume` shape.

- [ ] **Step 3: Generalize spawn directory resolution and correct MiMo argv**

In `spawn.py`, replace the Claude-only `isinstance` branch with:

```python
cli = get_agentic_cli(cli_id)
directory = cli.resolve_directory(options)
options = SpawnCommandOptions(**{**options.__dict__, "directory": directory})
```

Remove the now-unused `ClaudeCodeCli` import. In `mimo_code.py`, emit `--session <id>` for resume and `--prompt <prompt>` for prompt input.

- [ ] **Step 4: Run the focused tests until green**

Run the two commands from Step 2 plus:

```bash
bash scripts/run-single-test.sh tests/test_runs_spawn.py::test_claude_resume_resolves_directory_from_transcript_cwd
```

Expected: Claude behavior remains green and Codex/MiMo use their correct worktree/flags.

---

### Task 3: Route Kanban Recovery Through the Effective CLI

**Files:**
- Modify: `backend/app/kanban/session_recovery.py`
- Modify: `backend/app/kanban/dispatch.py`
- Modify: `backend/app/kanban/takeover.py`
- Test: `backend/tests/test_kanban_session_recovery.py`
- Test: `backend/tests/test_kanban_dispatch.py`
- Test: `backend/tests/test_kanban_takeover.py`

**Interfaces:**
- Produces: `_effective_resume_cli_id(card) -> str` in `dispatch.py`.
- Produces: `_resolve_resume_target(project_path: str, session_name: str, *, cli_id: str = "claude-code", projects_dir: Path | None = None) -> tuple[str, str | None] | None`.
- Changes: injected `ResolveFn` callbacks accept keyword-only `cli_id`.
- Consumes: adapter `supports_resume_resolution` and `resolve_resume_target` from Task 1.

- [ ] **Step 1: Write failing CLI-routing tests**

Add a session-recovery unit that monkeypatches `get_agentic_cli` with a recording fake and calls `_resolve_resume_target(..., cli_id="codex-cli")`; assert the fake receives the exact worktree path and no Claude directory is read.

Add/update `recover_project` test callbacks:

```python
def fake_resolve(project_path, session_name, *, cli_id):
    assert cli_id == "open-code"
    return ("ses_open", "/p/.claude/worktrees/k-dead-open")
```

Create the card with `executor_agent_id="open-code"` and assert the resume fields are persisted before redispatch.

Add a dispatch test around `_move_to_resume` with a `codex-cli` card and patched resolver; assert `cli_id="codex-cli"` was passed and the card moves to `To Resume` rather than taking the plain-release fallback.

- [ ] **Step 2: Run focused tests and confirm current Claude-only calls fail**

Run:

```bash
bash scripts/run-single-test.sh tests/test_kanban_session_recovery.py
bash scripts/run-single-test.sh tests/test_kanban_dispatch.py -k "move_to_resume and cli"
```

Expected: callback signature/assertion failures because callers do not pass `cli_id`.

- [ ] **Step 3: Implement effective CLI resolution and delegate stores**

In `dispatch.py`, add a pure helper using the existing dispatch rules:

```python
def _effective_resume_cli_id(card) -> str:
    known_clis = _known_cli_ids()
    phase = resolve_phase(card)
    cli_id = _phase_cli_id(card, phase=phase, known_clis=known_clis)
    explicit = any(
        value in known_clis
        for value in (
            getattr(card, "analyst_agent_id" if phase == "analyst" else "executor_agent_id", None),
            getattr(card, "agent", None),
        )
    )
    return _cli_id_for_opencode_provider(
        cli_id, getattr(card, "dispatch_provider", None), explicit_cli_chosen=explicit,
    )
```

In `session_recovery.py`, preserve `_resolve_transcript_file` as a Claude-compatible wrapper for rate-limit consumers, but make `_resolve_resume_target`:

```python
worktree = Path(project_path) / ".claude" / "worktrees" / session_name
cli = get_agentic_cli(cli_id)
if not cli.supports_resume_resolution:
    logger.info("resume detection unsupported for cli=%s", cli_id)
    return None
return cli.resolve_resume_target(worktree, data_dir=projects_dir)
```

Catch unknown CLI ids and store read failures as logged `None` results.

- [ ] **Step 4: Thread `cli_id` through every recovery caller**

Pass `_effective_resume_cli_id(card)` in:

- `recover_project`
- `_stamp_resume_target`
- `_move_to_resume`
- `redispatch_card`'s inline target lookup
- `takeover.promote_to_tmux`

Update `ResolveFn` aliases and test doubles to accept `*, cli_id`.

- [ ] **Step 5: Run all touched recovery test files**

Run:

```bash
bash scripts/run-single-test.sh tests/test_kanban_session_recovery.py
bash scripts/run-single-test.sh tests/test_kanban_takeover.py
bash scripts/run-single-test.sh tests/test_kanban_dispatch.py -k "resume or redispatch or reaper"
```

Expected: all selected tests pass; existing Claude tests remain unchanged except callback keyword support.

---

### Task 4: Preserve Cleanup and Verify the Complete Fix

**Files:**
- Modify: `backend/app/kanban/session_cleanup.py:155-181`
- Test: `backend/tests/test_kanban_session_cleanup.py` (or the existing cleanup test file that owns `_worktree_path_for_card`)
- Modify: `docs/superpowers/plans/2026-08-03-cli-agnostic-session-recovery.md` only to check completed boxes if desired.

**Interfaces:**
- Consumes: internally persisted absolute non-Claude `resume_project_folder` values.
- Produces: cleanup accepts such a path only when it is an existing descendant of the card project's `.claude/worktrees` directory; Claude's encoded-folder path remains unchanged.

- [ ] **Step 1: Write a failing cleanup safety test**

Create a card-like object whose `resume_project_folder` is an absolute worktree under a fake resolved project and whose claim is absent. Patch `resolve_project_path` to the fake project. Assert `_worktree_path_for_card` returns that worktree. Add a second case pointing outside `.claude/worktrees` and assert `None`.

- [ ] **Step 2: Run the focused cleanup test and confirm failure**

Run the exact discovered test path with `scripts/run-single-test.sh`; expected failure is the current Claude-only `_resolve_project_directory` rejecting an absolute path.

- [ ] **Step 3: Implement contained absolute-path handling**

Before the Claude folder decoder branch:

```python
candidate = Path(resume_folder).expanduser()
if candidate.is_absolute():
    project_path = await resolve_project_path(card.project_key)
    if not project_path:
        return None
    allowed = (Path(project_path) / ".claude" / "worktrees").resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(allowed) or not resolved.is_dir():
        return None
    return resolved
```

Keep `_resolve_project_directory` for non-absolute Claude folder names.

- [ ] **Step 4: Run targeted verification**

Run:

```bash
bash scripts/run-single-test.sh tests/test_agentic_cli_resume_resolution.py
bash scripts/run-single-test.sh tests/test_runs_spawn.py -k "resume"
bash scripts/run-single-test.sh tests/test_providers.py -k "resume or mimo"
bash scripts/run-single-test.sh tests/test_kanban_session_recovery.py
bash scripts/run-single-test.sh tests/test_kanban_takeover.py
bash scripts/run-single-test.sh tests/test_kanban_dispatch.py -k "resume or redispatch or reaper"
/home/vdvgu/claude-cockpit/backend/venv/bin/ruff check backend/app/services/agentic_cli backend/app/services/runs/spawn.py backend/app/kanban/session_recovery.py backend/app/kanban/session_cleanup.py backend/app/kanban/takeover.py
```

Expected: every targeted test passes and Ruff reports no new errors.

- [ ] **Step 5: Run the end-of-card `iteration-loop verify` gate**

The branch touches `backend/app/`, so use the `verify` preset. It must emit `<loop-complete>` before shipping; backend pytest remains CI-only outside the targeted runs above.

- [ ] **Step 6: Review, commit, FCR, and ship**

Review `git diff`, commit all implementation/tests/plan in one implementation commit so the FCR commit scope is complete:

```bash
git add backend/app/services/agentic_cli backend/app/services/runs/spawn.py \
  backend/app/kanban/session_recovery.py backend/app/kanban/dispatch.py \
  backend/app/kanban/takeover.py backend/app/kanban/session_cleanup.py \
  backend/tests docs/superpowers/plans/2026-08-03-cli-agnostic-session-recovery.md
git commit -m "fix(dispatch): recover sessions with their original CLI"
```

Run the required cleared-context FCR against that exact commit, then follow the direct-mode `git-ship` recipe, attach the branch deliverable, run `session-retro`, and move host card `f829630df8494d3a8bf9f7e526dacb9f` to `Done` with a product-led summary.
