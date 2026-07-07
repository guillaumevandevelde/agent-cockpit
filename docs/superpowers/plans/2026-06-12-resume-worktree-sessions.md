# Resume Worktree Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users resume Claude Code sessions that were started inside git worktrees, from the CC Bridge → New Session → Resume picker, with each session launching in its original working directory.

**Architecture:** A new agent-bridge backend endpoint aggregates resumable sessions across a project and all of its git worktrees (via `git worktree list`), tagging each with a `worktree_label`. The Claude Code provider's resume path is fixed to always launch in the session's recorded cwd. The frontend resume picker calls the new endpoint and renders a per-row badge.

**Tech Stack:** FastAPI + async SQLAlchemy (backend), pytest + pytest-asyncio (tests), React 19 + TypeScript + Vite (frontend).

**Spec:** `docs/superpowers/specs/2026-06-12-resume-worktree-sessions-design.md`

---

## File Structure

**Backend**
- Create: `backend/app/services/agent_bridge/resumable.py` — worktree enumeration + session aggregation.
- Modify: `backend/app/services/providers/claude_code.py` — `resolve_directory` resume fix.
- Modify: `backend/app/models/schemas.py` — `ResumableSession` + `ResumableSessionListResponse`.
- Modify: `backend/app/api/v1/agent_bridge/router.py` — new `GET /resumable-sessions` endpoint.
- Create: `backend/tests/test_agent_bridge_resumable.py` — aggregation + resolve_directory tests.

**Frontend**
- Modify: `frontend/src/types/sessions.ts` — `ResumableSession` type.
- Modify: `frontend/src/features/cc-bridge/api.ts` — `fetchResumableSessions`.
- Modify: `frontend/src/features/cc-bridge/NewSessionDialog.tsx` — use new endpoint + badge.

---

## Task 1: Backend — resume always launches in the session's recorded cwd

**Files:**
- Modify: `backend/app/services/providers/claude_code.py:77-80`
- Test: `backend/tests/test_agent_bridge_resumable.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_agent_bridge_resumable.py`:

```python
"""Tests for worktree-aware resume aggregation and directory resolution."""
import json
import os
import time
from pathlib import Path

import pytest


def test_resume_resolve_directory_prefers_transcript_cwd(monkeypatch, tmp_path):
    from app.services.cc_bridge import spawn as claude_spawn
    from app.services.providers.base import SpawnCommandOptions
    from app.services.providers.claude_code import ClaudeCodeProvider

    worktree_dir = tmp_path / "wt"
    worktree_dir.mkdir()
    project_folder = "-tmp-wt"
    session_id = "sess-1"
    tdir = tmp_path / ".claude" / "projects" / project_folder
    tdir.mkdir(parents=True)
    (tdir / f"{session_id}.jsonl").write_text(
        json.dumps({"cwd": str(worktree_dir)}) + "\n", encoding="utf-8"
    )

    monkeypatch.setattr(claude_spawn.Path, "home", classmethod(lambda cls: tmp_path))

    provider = ClaudeCodeProvider()
    resolved = provider.resolve_directory(
        SpawnCommandOptions(
            directory=str(tmp_path),  # non-empty, deliberately NOT the worktree
            mode="resume",
            session_id=session_id,
            project_folder=project_folder,
        )
    )

    assert resolved == str(worktree_dir)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_bridge_resumable.py::test_resume_resolve_directory_prefers_transcript_cwd -v`
Expected: FAIL — `resolved` equals `str(tmp_path)` (the passed directory), not the worktree cwd.

- [ ] **Step 3: Apply the fix**

In `backend/app/services/providers/claude_code.py`, replace the `resolve_directory` method:

```python
    def resolve_directory(self, options: SpawnCommandOptions) -> str:
        # For resume, the launch directory is fully determined by the session's
        # recorded cwd — never the directory the picker was browsing. This also
        # makes worktree sessions resume in their own worktree.
        if options.mode == "resume" and options.project_folder:
            return _resolve_project_directory(options.project_folder, options.session_id)
        return options.directory
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_bridge_resumable.py::test_resume_resolve_directory_prefers_transcript_cwd -v`
Expected: PASS

- [ ] **Step 5: Run the existing spawn tests to confirm no regression**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_bridge_spawn.py -v`
Expected: PASS (including `test_claude_resume_resolves_directory_from_transcript_cwd`, which passes `directory=""` and still resolves correctly).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/providers/claude_code.py backend/tests/test_agent_bridge_resumable.py
git commit -m "fix(agent-bridge): resume always launches in session's recorded cwd"
```

---

## Task 2: Backend — schemas for resumable sessions

**Files:**
- Modify: `backend/app/models/schemas.py:1093-1104` (after `SessionSummary`)

- [ ] **Step 1: Add the schema classes**

In `backend/app/models/schemas.py`, immediately after the `SessionSummary` class (ends at line 1104), add:

```python
class ResumableSession(SessionSummary):
    """A session summary tagged with the worktree it belongs to, for the resume picker."""

    worktree_label: str


class ResumableSessionListResponse(BaseModel):
    """Aggregated resumable sessions across a project and its worktrees."""

    sessions: List[ResumableSession]
```

`List` and `BaseModel` are already imported in this file (used by `SessionListResponse`); no new imports needed.

- [ ] **Step 2: Verify it imports cleanly**

Run: `cd backend && source venv/bin/activate && python -c "from app.models.schemas import ResumableSession, ResumableSessionListResponse; print('ok')"`
Expected: prints `ok`

- [ ] **Step 3: Commit**

```bash
git add backend/app/models/schemas.py
git commit -m "feat(agent-bridge): add ResumableSession schemas"
```

---

## Task 3: Backend — worktree enumeration + session aggregation service

**Files:**
- Create: `backend/app/services/agent_bridge/resumable.py`
- Test: `backend/tests/test_agent_bridge_resumable.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_agent_bridge_resumable.py`:

```python
def _write_session(folder: Path, session_id: str, text: str):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{session_id}.jsonl").write_text(
        json.dumps({"type": "user", "message": {"content": text}}) + "\n",
        encoding="utf-8",
    )


def test_encode_project_folder_matches_claude_layout():
    from app.services.agent_bridge.resumable import _encode_project_folder

    encoded = _encode_project_folder(
        "/home/user/dev/claude-cockpit/.claude/worktrees/Kanban-plan"
    )
    assert encoded == "-home-user-dev-claude-cockpit--claude-worktrees-Kanban-plan"


@pytest.mark.asyncio
async def test_aggregates_main_and_worktree_sessions(monkeypatch, tmp_path):
    from app.services.agent_bridge import resumable

    main_dir = tmp_path / "repo"
    main_dir.mkdir()
    wt_dir = tmp_path / "repo" / ".claude" / "worktrees" / "feat"
    wt_dir.mkdir(parents=True)

    projects_dir = tmp_path / "projects"
    main_folder = resumable._encode_project_folder(str(main_dir))
    wt_folder = resumable._encode_project_folder(str(wt_dir))
    _write_session(projects_dir / main_folder, "main-sess", "hello from main")
    _write_session(projects_dir / wt_folder, "wt-sess", "hello from worktree")

    # Make the worktree session newer so it sorts first.
    future = time.time() + 10
    os.utime(projects_dir / wt_folder / "wt-sess.jsonl", (future, future))

    monkeypatch.setattr(
        resumable,
        "_list_worktrees",
        lambda d: [(str(main_dir), True), (str(wt_dir), False)],
    )
    monkeypatch.setattr(
        "app.services.session_service.get_claude_projects_dir", lambda: projects_dir
    )

    sessions = await resumable.list_resumable_sessions(str(main_dir), limit=20, db=None)

    assert [s.id for s in sessions] == ["wt-sess", "main-sess"]
    assert {s.id: s.worktree_label for s in sessions} == {
        "wt-sess": "feat",
        "main-sess": "main",
    }


@pytest.mark.asyncio
async def test_non_git_directory_returns_only_its_own_sessions(monkeypatch, tmp_path):
    from app.services.agent_bridge import resumable

    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    projects_dir = tmp_path / "projects"
    folder = resumable._encode_project_folder(str(plain_dir))
    _write_session(projects_dir / folder, "only-sess", "hello")

    # Real git call on a non-repo returns non-zero -> fallback to [(dir, True)].
    monkeypatch.setattr(
        "app.services.session_service.get_claude_projects_dir", lambda: projects_dir
    )

    sessions = await resumable.list_resumable_sessions(str(plain_dir), limit=20, db=None)

    assert [s.id for s in sessions] == ["only-sess"]
    assert sessions[0].worktree_label == "main"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_bridge_resumable.py -v -k "encode or aggregates or non_git"`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.agent_bridge.resumable'`

- [ ] **Step 3: Create the service module**

Create `backend/app/services/agent_bridge/resumable.py`:

```python
"""Aggregate resumable Claude Code sessions across a project and its git worktrees."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import ResumableSession
from app.services.agent_bridge.spawn import _validate_directory
from app.services.session_service import SessionService


def _encode_project_folder(path: str) -> str:
    """Encode an absolute path to Claude's project folder name.

    Mirrors the frontend's claudeProjectFolderFromPath: '/' and '.' both map to
    '-'. Order matters ('/' first), so '/a/.claude' -> '-a--claude'.
    """
    return path.rstrip("/").replace("/", "-").replace(".", "-")


def _list_worktrees(directory: str) -> list[tuple[str, bool]]:
    """Return (worktree_path, is_main) tuples for the repo containing `directory`.

    The first entry from `git worktree list` is the main worktree. Falls back to
    a single (directory, True) entry when git is unavailable or the directory is
    not a git repository.
    """
    try:
        result = subprocess.run(
            ["git", "-C", directory, "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return [(directory, True)]
    if result.returncode != 0:
        return [(directory, True)]

    paths: list[str] = []
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            paths.append(line[len("worktree ") :].strip())
    if not paths:
        return [(directory, True)]
    return [(path, index == 0) for index, path in enumerate(paths)]


async def list_resumable_sessions(
    directory: str,
    limit: int,
    db: Optional[AsyncSession],
) -> list[ResumableSession]:
    """List resumable sessions across `directory`'s project and its worktrees."""
    main_dir = _validate_directory(directory)
    worktrees = _list_worktrees(main_dir)

    service = SessionService(db)
    aggregated: list[ResumableSession] = []
    for path, is_main in worktrees:
        folder = _encode_project_folder(path)
        label = "main" if is_main else (Path(path).name or "main")
        response = await service.list_sessions(
            project_folder=folder,
            limit=limit,
            sort_by="date",
            sort_order="desc",
        )
        for summary in response.sessions:
            aggregated.append(
                ResumableSession(**summary.model_dump(), worktree_label=label)
            )

    # ISO-8601 timestamps sort lexicographically in chronological order.
    aggregated.sort(key=lambda s: s.modified_at, reverse=True)
    return aggregated[:limit]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && source venv/bin/activate && pytest tests/test_agent_bridge_resumable.py -v -k "encode or aggregates or non_git"`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_bridge/resumable.py backend/tests/test_agent_bridge_resumable.py
git commit -m "feat(agent-bridge): aggregate resumable sessions across worktrees"
```

---

## Task 4: Backend — wire the `/resumable-sessions` endpoint

**Files:**
- Modify: `backend/app/api/v1/agent_bridge/router.py:10` (fastapi imports), `:13-17` (service imports), and add a new route after the existing GET `/sessions` handler (`:52-59`).

- [ ] **Step 1: Add imports**

In `backend/app/api/v1/agent_bridge/router.py`, change the fastapi import line:

```python
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket
```

Add these imports below the existing `from app.services...` block (after line 17):

```python
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.schemas import ResumableSessionListResponse
from app.services.agent_bridge.resumable import list_resumable_sessions
```

- [ ] **Step 2: Add the route**

In the same file, directly after the existing `list_sessions` GET handler (which ends at line 59, before `@router.get("/sessions/{target:path}/preview")`), insert:

```python
@router.get("/resumable-sessions", response_model=ResumableSessionListResponse)
async def list_resumable_sessions_endpoint(
    directory: str = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    try:
        sessions = await list_resumable_sessions(directory, limit, db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ResumableSessionListResponse(sessions=sessions)
```

- [ ] **Step 3: Smoke-test the endpoint against the real environment**

Start the backend if not running (`cd backend && source venv/bin/activate && uvicorn app.main:app --reload --port 8000`), then in another shell:

Run: `curl -s "http://localhost:8000/api/v1/agent-bridge/resumable-sessions?directory=/home/user/dev/claude-cockpit&limit=20" | python3 -m json.tool | head -40`
Expected: JSON `{"sessions": [...]}` that includes at least one session with `"worktree_label": "Kanban-plan"` (the real worktree session), alongside `"main"` sessions.

- [ ] **Step 4: Run the full backend test suite**

Run: `cd backend && source venv/bin/activate && pytest tests/ -q`
Expected: PASS (no regressions)

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/agent_bridge/router.py
git commit -m "feat(agent-bridge): expose GET /resumable-sessions endpoint"
```

---

## Task 5: Frontend — types + API client function

**Files:**
- Modify: `frontend/src/types/sessions.ts:33-42` (after `SessionSummary`)
- Modify: `frontend/src/features/cc-bridge/api.ts`

- [ ] **Step 1: Add the `ResumableSession` type**

In `frontend/src/types/sessions.ts`, after the `SessionSummary` interface (ends line 42), add:

```typescript
export interface ResumableSession extends SessionSummary {
  worktree_label: string
}

export interface ResumableSessionListResponse {
  sessions: ResumableSession[]
}
```

- [ ] **Step 2: Add the API function**

In `frontend/src/features/cc-bridge/api.ts`, add this import near the top (after the existing `./types` import line):

```typescript
import type { ResumableSessionListResponse } from '@/types/sessions'
```

Then add this function (e.g. right after `fetchCCSessions`):

```typescript
export async function fetchResumableSessions(
  directory: string,
  limit = 20,
): Promise<ResumableSessionListResponse> {
  return apiClient<ResumableSessionListResponse>(
    buildEndpoint(BASE + '/resumable-sessions', { directory, limit }),
  )
}
```

- [ ] **Step 3: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types/sessions.ts frontend/src/features/cc-bridge/api.ts
git commit -m "feat(cc-bridge): add fetchResumableSessions client + type"
```

---

## Task 6: Frontend — resume picker uses the aggregated endpoint + badge

**Files:**
- Modify: `frontend/src/features/cc-bridge/NewSessionDialog.tsx` (imports, state types, the resume fetch effect at `:142-162`, and the picker render at `:408-434`)

- [ ] **Step 1: Update imports**

In `frontend/src/features/cc-bridge/NewSessionDialog.tsx`:

- Add `ResumableSession` to the sessions-types import. Find the import that pulls `SessionSummary` from `@/types/sessions` and change it to include `ResumableSession`. If `SessionSummary` is no longer referenced after Step 2, drop it from the import.
- Add `fetchResumableSessions` to the cc-bridge api import (the line importing `spawnSession` etc. from `./api`).
- Remove the now-unused `claudeProjectFolderFromPath` from the `@/lib/utils` import (keep `cn`).
- Remove the `useSessionsApi` import (no longer used; verify with a search for `useSessionsApi`/`listSessions` after Step 3).

- [ ] **Step 2: Update state types**

Change the two state declarations (currently around `:109-110`):

```typescript
  const [recentSessions, setRecentSessions] = useState<ResumableSession[]>([])
  const [selectedSession, setSelectedSession] = useState<ResumableSession | null>(null)
```

Remove the `const { listSessions } = useSessionsApi()` line (around `:113`).

Remove the now-dead derived values (around `:120-123`):

```typescript
  const resumeProjectPath = directory.trim()
  const resumeProjectFolder = resumeProjectPath
    ? claudeProjectFolderFromPath(resumeProjectPath)
    : undefined
```

- [ ] **Step 3: Replace the resume fetch effect**

Replace the entire effect at `:142-162` (the `// Fetch sessions when switching to resume mode` block) with:

```typescript
  // Fetch sessions for the selected project AND its git worktrees in resume mode.
  useEffect(() => {
    if (mode !== 'resume' || isCodex) return
    let cancelled = false
    setSelectedSession(null)
    setRecentSessions([])
    const dir = directory.trim()
    if (!dir) {
      setLoadingSessions(false)
      return () => { cancelled = true }
    }
    setLoadingSessions(true)
    fetchResumableSessions(dir, 20)
      .then((data) => { if (!cancelled) setRecentSessions(data.sessions) })
      .catch(() => { if (!cancelled) setRecentSessions([]) })
      .finally(() => { if (!cancelled) setLoadingSessions(false) })
    return () => { cancelled = true }
  }, [mode, isCodex, directory])
```

- [ ] **Step 4: Render the worktree badge**

In the picker render, replace the project-name/timestamp row (currently `:420-427`) with a version that adds the badge:

```typescript
                      <div className="flex items-center justify-between gap-2 min-w-0">
                        <span className="flex items-center gap-1.5 min-w-0">
                          <span className="text-sm font-medium truncate min-w-0">
                            {session.project_name}
                          </span>
                          <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                            {session.worktree_label}
                          </span>
                        </span>
                        <span className="text-xs text-muted-foreground shrink-0">
                          {formatTimestamp(session.modified_at)}
                        </span>
                      </div>
```

- [ ] **Step 5: Type-check and lint**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: no errors (confirms no unused imports/vars from the removals).

- [ ] **Step 6: Build the frontend (app is served from dist)**

Run: `cd frontend && npm run build`
Expected: build succeeds, `frontend/dist` updated.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/cc-bridge/NewSessionDialog.tsx
git commit -m "feat(cc-bridge): resume picker lists worktree sessions with badges"
```

---

## Task 7: Manual end-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Restart/confirm the backend is serving the new build**

Ensure backend is running on :8000 (it serves `frontend/dist`). Open the app and go to CC Bridge.

- [ ] **Step 2: Open New Session → Resume**

- Provider: Claude Code. Mode: Resume. Directory: `/home/user/dev/claude-cockpit` (the main project).
- Expected: the picker lists sessions from the main repo **and** the worktrees, each with a badge: `main`, `Kanban-plan`, `containerized-option`, `notification-long-task`.

- [ ] **Step 3: Resume the real worktree session**

- Select the `Kanban-plan`-badged session and launch.
- Expected: a tmux session spawns and Claude resumes that transcript.

- [ ] **Step 4: Confirm it launched in the worktree directory**

Run: `tmux list-panes -a -F "#{session_name} #{pane_current_path}" | grep -i kanban`
Expected: the pane's current path is `/home/user/dev/claude-cockpit/.claude/worktrees/Kanban-plan` (not the main repo).

- [ ] **Step 5: Final commit if any verification tweaks were needed**

Only if changes were made during verification:

```bash
git add -A
git commit -m "fix(cc-bridge): address worktree resume verification findings"
```

---

## Self-Review Notes

- **Spec coverage:** Endpoint (Task 3+4), git worktree discovery + labels (Task 3), resume-in-cwd fix (Task 1), frontend picker + badge (Task 5+6), deleted-worktree edge (covered by Task 1's `_resolve_project_directory` ValueError → HTTP 400 / spawn error; deleted worktrees are absent from `git worktree list` so never listed), tests (Tasks 1, 3; full suite Task 4), manual resume (Task 7), `npm run build` reminder (Task 6 Step 6). All spec sections mapped.
- **Type consistency:** `ResumableSession` (extends `SessionSummary`, adds `worktree_label: str`) and `ResumableSessionListResponse` are named identically across backend schemas, frontend types, and the API. `_encode_project_folder` / `_list_worktrees` / `list_resumable_sessions` names are consistent between the service, its tests, and the router.
- **No placeholders:** every code/command step contains concrete content.
