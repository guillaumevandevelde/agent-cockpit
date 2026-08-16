---
name: session-problem-scan
description: Use when scanning Claude Code sessions for stuck or crashed work, repeated transcript errors, orphaned tmux sessions on kanban cards, or when a user asks "is anything broken?" / "check the sessions" / "any stuck work?" / "scan health".
---

# session-problem-scan

## Stap 0 — staat de zelfverbeteringsloop aan?

**Doe dit vóór alles.** Het bord kan de loop uitgezet hebben, en dan hoort deze
skill niets te produceren:

```bash
curl -s "http://localhost:8000/api/v1/kanban/self-improve?project_key=<PROJECT_KEY>"
```

Antwoordt dat met `"enabled": false`, stop dan direct. File geen kaart, plaats
geen comment. Noem je waarneming in je afsluitende samenvatting op de host-kaart
— daar leest een mens het, en die beslist of er een kaart van komt.

Waarom deze poort bestaat: drie skills produceerden 318 van de 855 kaarten in
zeven weken, en een limiet op dispatch-slots knijpt alleen de consumptie af, niet
de instroom. Zie `docs/cockpit/cockpit-richting-decision.md` §8. Is het endpoint
onbereikbaar (backend uit), ga dan gewoon door — fail-open.

---

Scan Claude Code sessions for problems and file each finding as a Backlog kanban
ticket. Two failure modes are in scope:

- **A. Crashed/stuck dispatched session** — an `agent:*` claim on a kanban card
  with no live tmux session, or a card stuck in an agent column with no
  `analyst_run_id` written.
- **B. Repeated transcript errors** — same error signature appearing ≥ 3× in a
  single Claude Code session transcript (`~/.claude/projects/**/*.jsonl`).

This skill is **on-demand** (an agent invokes it). For scheduled health checks,
write a separate `scripts/health-scan.sh` instead.

## When to use

- A user asks "are any sessions broken?", "check for stuck work", "scan health".
- You noticed something odd and want a systematic sweep.
- Before declaring a kanban board "clean" — run this to be sure.

## When NOT to use

- Card is being worked on and tmux is alive — normal.
- Card moved to `Done` and tmux is gone — **expected** (auto-cleanup). Do NOT file.
- Card moved to `Impediment` — already flagged, do not double-file.

## Detection path A — Crashed/stuck dispatched session

Apply the **3-criteria test**. A card is "stuck" only if **all three** hold:

1. **Column is `analyst`, `executor`, or `Doing`** (NOT `Backlog`, `Done`,
   `Impediment`, or `Review`).
2. **Time in that column is abnormal** — > 2× the median lifetime of recently-
   completed cards in the same column. Without that baseline, hard fallback:
   **> 4 hours** in any agent column.
3. **Tmux session is missing or dead.** `claimed_by` starts with `agent:` but
   `tmux has-session -t <claimed_by>` fails. For analyst-phase cards, also
   confirm `analyst_run_id` is unset — that is the stronger signal.

### How to check

Use `mcp__cockpit-kanban__list_cards` with column filter, then for each candidate:

```bash
tmux has-session -t "agent:<card_id>" 2>/dev/null && echo "alive" || echo "dead"
```

Adjust the session-name pattern to whatever `backend/app/services/scheduling/`
actually uses — verify against `session_registry.py` or `crud.py` before trusting
this. If you cannot determine criterion 3 with certainty, **do not file a ticket**
— the cost of a false positive is worse than a miss.

## Detection path B — Repeated transcript errors

Scan transcripts for **the same error signature repeated ≥ 3 times** in one
session, OR **≥ 1 stack trace** from the agent's own code.

### Where transcripts live

- This project: `~/.claude/projects/-home-vdvgu-claude-cockpit/memory/*.jsonl`
- Other projects: `~/.claude/projects/-<encoded-path>/**/*.jsonl`

### What counts as an error signature

- `Error:` / `Exception:` / `Traceback (most recent call last):` in assistant or
  tool output.
- Tool-use failures: `"Tool result missing due to internal error"`,
  `"Hook timed out"`, `"ENOTEMPTY"`, `"context window exceeded"`.
- Consecutive identical assistant messages containing the same error string.

Skip: user-side typos and single transient failures followed by recovery.

### How to scan

```bash
grep -c "Traceback (most recent call last):" \
  ~/.claude/projects/-home-vdvgu-claude-cockpit/memory/*.jsonl \
  | awk -F: '$2 >= 3 {print $1}'
```

Read the matches manually — `grep -c` lies on multi-line stack traces. Open the
file and confirm the signature is genuine before filing.

## Ticket template

Use `mcp__cockpit-kanban__create_card`:

- **project**: resolve it first, don't guess — call the `cockpit-kanban`
  MCP server's `resolve_project_key` tool (works without shell access), or
  `curl -s "http://localhost:8000/api/v1/kanban/project-key?project_path=$(git rev-parse --show-toplevel)"`
  if you only have shell access.
  The board is keyed by a free-form string with no validation, so a guessed
  or hand-typed key silently creates an invisible parallel board — see
  `flag-problem`'s Step 1 for the incident that surfaced this.
- **column**: `Backlog`
- **title**: `[session-issue] <one-line summary>` — e.g.
  `[session-issue] Card abc-123 stuck in analyst 6h, tmux dead`
  or `[session-issue] Transcript foo.jsonl: 5× Traceback in last hour`.
- **description**: structured for fast triage (use this exact shape):

```markdown
## Problem
<1-2 sentences — what is broken>

## Evidence
- Card ID: <id> (or transcript path)
- Column / phase: <analyst|executor|Doing>
- Time in column: <duration>
- claimed_by: <value>
- tmux has-session: <result>
- analyst_run_id: <set|null>
- Transcript errors: <signature, count, last occurrence>

## Suggested recovery
<concrete next step — e.g. "call mcp__cockpit-kanban__redispatch_card with
project_path=/home/vdvgu/claude-cockpit" OR "open the transcript and look
for the loop in turn N–M">
```

Keep the description under ~30 lines — a triage engineer should be able to
decide in 60 seconds whether to act.

## Common mistakes

| Excuse | Why it's wrong |
|--------|----------------|
| "Card is in `Doing` → must be stuck" | `Doing` is normal; check tmux first. |
| "Tmux session gone → must be crashed" | Could be auto-cleanup from `Done` — check column. |
| "I'll just say it in chat, no card needed" | Verbal reports vanish; cards are the queue of record. |
| "I'll batch all findings into one card" | One card per problem — easier to triage, track, and close. |
| "Found one error → file a ticket" | One error is noise; need ≥ 3× same signature or a stack trace. |
| "I guessed `agent:<card_id>` for tmux name" | Verify against `session_registry.py` first. |

## Red flags — STOP and re-check

- Card you want to file for is already in `Impediment` or `Done`.
- You can't satisfy criterion 3 (tmux check) — don't guess, don't file.
- Transcript match was a single line that happened to contain the keyword —
  re-read the file and confirm it's a genuine repeated signature.
- You're about to file under a project key you typed from memory instead
  of resolving via `GET /api/v1/kanban/project-key?project_path=...`.
  Session problems in this fork always belong on *this repo's* resolved
  key, and a guessed key silently orphans the card onto an invisible board.

## Quick reference

```text
Path A (crash):  list_cards(filter=agent_column) → for each: time_in_col > 2h
                 AND tmux dead AND (if analyst: analyst_run_id null)
                 → create_card(Backlog) with evidence

Path B (errors): grep transcripts for error sigs ≥ 3× or stack trace
                 → read matches manually → create_card(Backlog) with evidence
```