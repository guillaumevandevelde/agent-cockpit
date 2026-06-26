# Skill Stats — Design Spec

**Date:** 2026-06-27  
**Status:** Approved  
**Scope:** Per-project skill usage stats surfaced as a third tab on the Skills page

## Goal

Show how often each installed skill is invoked within the selected project's Claude sessions, so that barely-used skills can be removed (reducing context load).

## Architecture

### Data source

Claude session JSONL files under `~/.claude/projects/<project-folder>/<session>.jsonl`.

Each session file contains `type: "assistant"` records. When Claude invokes a skill, the `message.content[]` array contains an item with:

```json
{ "type": "tool_use", "name": "Skill", "input": { "skill": "brainstorming" } }
```

Scope: only the selected project's JSONL folder (no all-time cross-project scan).

### Backend

**New file:** `backend/app/services/skill_stats_service.py`

```
SkillStatsService
  async scan_project(project_path: str) -> list[SkillUsageStat]
    - discover_jsonl_files via same path utility as UsageService
    - async read each file, extract Skill tool_use events
    - aggregate count per skill name, sort descending
```

**New endpoint** added to `backend/app/api/v1/agents.py`:

```
GET /api/v1/agents/skills/stats?project_path=<path>
→ SkillStatsResponse { stats: [{ skill: str, count: int }] }
```

**New Pydantic schemas** added to `backend/app/models/schemas.py`:
- `SkillUsageStat(skill: str, count: int)`
- `SkillStatsResponse(stats: list[SkillUsageStat])`

No DB caching — JSONL files are local and per-project scans are fast.

### Frontend

**Modified file:** `frontend/src/features/skills/SkillsPage.tsx`

Add a third tab `"stats"` to `SkillsTab` union type alongside `"installed"` and `"discover"`.

**Tab UI:**
- Icon: `BarChart2` from lucide-react
- Label: `Stats`
- Fetch triggered on first render of the tab (lazy via `useEffect` gated on `activeTab === "stats"`)
- Endpoint call: `GET /api/v1/agents/skills/stats?project_path=…`

**Stats tab content:**

1. **Used skills** — ranked list sorted by count descending:
   - Rank number, skill name, invocation count badge, CSS width bar (no chart lib)
2. **Never used** — installed skills whose name doesn't appear in stats:
   - Amber callout listing skill names — candidates for uninstalling
   - Note: JSONL records the full qualified name (e.g. `superpowers:brainstorming`); installed skill names are short (e.g. `brainstorming`). The "never used" check strips the namespace prefix (`<ns>:`) before comparing so `superpowers:brainstorming` matches installed skill `brainstorming`.
3. **No project selected** — placeholder: "Select a project to see skill usage stats"
4. **No data** — "No skill invocations found for this project yet"

**State added to SkillsPage:**
- `skillStats: SkillUsageStat[] | null`
- `statsLoading: boolean`
- `statsError: string | null`
- `statsFetched: boolean` (prevents re-fetch on tab re-entry)

**Frontend types** added to `frontend/src/types/agents.ts`:
- `SkillUsageStat { skill: string; count: number }`
- `SkillStatsResponse { stats: SkillUsageStat[] }`

## Data Flow

```
User selects project → clicks "Stats" tab
  → frontend calls GET /api/v1/agents/skills/stats?project_path=/path/to/project
  → backend: convert path → ~/.claude/projects/<folder>/, glob *.jsonl
  → async read each file, collect Skill tool_use entries
  → aggregate counts, sort desc
  → return SkillStatsResponse
  → frontend renders ranked list + "never used" callout
```

## Error Handling

- Project path not found: return empty stats `{ stats: [] }`, frontend shows "No skill invocations found"
- JSONL parse error on individual line: skip line (same as UsageService)
- File read error: skip file, continue (same as UsageService)
- No project selected in frontend: show placeholder, do not call endpoint

## Testing

**Backend** (`backend/tests/test_skill_stats.py`):
- `test_scan_empty_project` — no JSONL files → empty stats
- `test_scan_single_session` — one file with 3 Skill calls → correct counts
- `test_scan_multiple_sessions` — two files, aggregated counts
- `test_scan_non_skill_tool_use_ignored` — Bash/Read tool_use entries not counted
- `test_endpoint_no_project` — missing `project_path` param → 400

**Frontend:** No dedicated test file (frontend tests not yet set up per CLAUDE.md). Manual verification: click Stats tab with project selected, confirm counts match session JSONL.

## Out of scope

- All-time / cross-project aggregation
- Time-windowed filtering (last 7/30 days)
- Per-session breakdown
- Trend charts
- Uninstall action from Stats tab
