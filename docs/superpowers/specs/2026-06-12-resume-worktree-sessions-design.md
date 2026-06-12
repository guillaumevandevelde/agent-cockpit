# Resume worktree sessions via Agent Bridge

**Date:** 2026-06-12
**Status:** Approved (design)

## Problem

Sessions started inside a git **worktree** cannot be resumed from the UI. They
are visible on the read-only **Sessions** page (which lists every Claude project
folder), but resuming is only possible via **CC Bridge → New Session → Resume**,
and that flow never surfaces them.

Two root causes:

1. **Picker is scoped to one project folder.** The resume picker fetches sessions
   for `claudeProjectFolderFromPath(directory)`, where `directory` defaults to the
   active/main project (`/home/guillaume/dev/claude-cockpit`). Claude stores
   transcripts per working directory, so a worktree lives under a *separate*
   folder, e.g.:

   | Started in | Claude project folder |
   |---|---|
   | main repo | `-home-guillaume-dev-claude-cockpit` |
   | worktree `Kanban-plan` | `-home-guillaume-dev-claude-cockpit--claude-worktrees-Kanban-plan` |

   Worktree directories are not offered in the project dropdown either (project
   discovery ignores `.claude/worktrees/`), so they can only be reached by typing
   the exact hidden path.

2. **Resume launches in the wrong directory.** Even if a worktree session were
   selected, the frontend always sends `directory` = the main project path.
   `ClaudeCodeProvider.resolve_directory` returns that directory unchanged when it
   is non-empty, so `claude --resume <id>` would run in the main repo instead of
   the worktree the session belonged to.

The backend transcript-based resolver (`_resolve_project_directory`) already reads
the recorded `cwd` correctly — confirmed it returns
`/home/guillaume/dev/claude-cockpit/.claude/worktrees/Kanban-plan` for the real
`Kanban-plan` session. The gap is purely in surfacing and selecting the session,
plus the directory-resolution precedence for resume.

## Goal

In **CC Bridge → New Session → Resume**, list sessions from the selected project
**and all of its git worktrees** in one picker, and ensure resuming a session
launches `claude --resume` in that session's original working directory.

## Design

The dialog talks to the **agent-bridge** API (`BASE = 'agent-bridge'`,
`backend/app/api/v1/agent_bridge/router.py`). All new work lands there.

### 1. Backend — aggregated resumable-sessions endpoint

New endpoint: `GET /api/v1/agent-bridge/resumable-sessions?directory=<path>&limit=<n>`

Service logic (new helper in the `agent_bridge` service package):

1. Validate `directory` (absolute, exists, no traversal — reuse `_validate_directory`).
2. Run `git -C <dir> worktree list --porcelain` to enumerate worktree paths
   (the first entry is the main worktree). If git is missing or the directory is
   not a git repo, fall back to the single directory.
3. For each worktree path:
   - Encode to its Claude project folder name (same `/`→`-`, `.`→`-` mapping the
     frontend's `claudeProjectFolderFromPath` uses).
   - Compute a `worktree_label`: `"main"` for the repo's main worktree, otherwise
     the worktree directory's basename (e.g. `Kanban-plan`).
4. For each resolved folder, list sessions via the existing
   `SessionService.list_sessions(project_folder=…, limit=…)`.
5. Merge all sessions, attach `worktree_label` to each (keep each session's own
   `project_folder`), sort by `modified_at` desc, cap at `limit`.

Response shape: `{ "sessions": [ SessionSummary + { "worktree_label": str } ] }`.

A folder with no transcripts (worktree never ran Claude) simply contributes
nothing — no error.

### 2. Backend — resume launches in the session's own cwd

Change `ClaudeCodeProvider.resolve_directory` (`backend/app/services/providers/claude_code.py`):

For `mode == "resume"` **with** `project_folder` set, **always** derive the
directory from the transcript via `_resolve_project_directory(project_folder, session_id)`,
ignoring the passed `directory`. The resume target is fully determined by the
session, so the dropdown directory must not override it. This also makes ordinary
(non-worktree) resume more robust — it always lands in the recorded cwd.

```python
def resolve_directory(self, options):
    if options.mode == "resume" and options.project_folder:
        return _resolve_project_directory(options.project_folder, options.session_id)
    return options.directory
```

Edge case — deleted worktree: the recorded cwd no longer exists, so
`_resolve_project_directory` raises `ValueError`, surfaced to the user as a clear
spawn error. Such sessions also won't appear in the picker (git worktree list does
not report removed worktrees), so the behavior is consistent.

### 3. Frontend — picker uses the aggregated endpoint + badge

In `frontend/src/features/cc-bridge/`:

- Add `fetchResumableSessions(directory, limit)` to `api.ts` calling the new
  endpoint.
- In `NewSessionDialog.tsx` resume mode, replace the
  `listSessions({ project_folder })` effect with `fetchResumableSessions(directory)`.
  The fetch is keyed on the typed/dropdown `directory` (which now means "which
  project + its worktrees to browse").
- Render each session row with its `worktree_label` as a small badge
  (`[main]` / `[Kanban-plan]`), in one flat list sorted newest-first.
- On launch, keep sending `session_id` + `project_folder` from the selected
  session (already implemented at `NewSessionDialog.tsx:224`). The backend now
  derives the correct directory from those.

No change to `SpawnRequest`/`SpawnCommandOptions` — they already carry
`session_id` and `project_folder`.

## Testing

**Backend (pytest):**
- Aggregation: temp git repo with one added worktree, a temp Claude projects dir
  with fake transcripts in both folders → assert both folders' sessions appear,
  correct `worktree_label`s, sorted by date.
- Non-repo / no-git fallback: plain directory → only its own folder's sessions.
- `resolve_directory`: resume mode with a worktree `project_folder` returns the
  worktree cwd, not the passed directory.
- Deleted worktree: `_resolve_project_directory` raises `ValueError`.

**Manual:**
- Resume the real `Kanban-plan` session through the dialog; confirm the tmux
  session opens in `/home/guillaume/dev/claude-cockpit/.claude/worktrees/Kanban-plan`
  and Claude resumes the transcript.
- Rebuild frontend (`npm run build`) since the app is served from `frontend/dist`.

## Out of scope

- A Resume button on the read-only Sessions page.
- Surfacing worktree directories in the project dropdown.
- Resuming a worktree session in a *different* directory than its original cwd.
