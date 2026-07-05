"""Agent performance stats derived from the kanban op-log.

The op-log (KanbanOp) is the source of truth: every move/claim/comment carries a
real wall-clock `created_at`. From it we derive, per agent:
  - task throughput (closed agent-column segments) + in-progress count
  - success rate (segments that advanced vs. ones that hit Impediment)
  - average / median time per task
  - the most common failure reasons (impediment comments)

Token usage is enriched best-effort by joining Claude Code's usage logs:
a dispatched session runs in a worktree `<repo>/.claude/worktrees/<session>`, so
its usage folder name ends in `-worktrees-<session>`, and `<session>` is exactly
the `agent:<session>` claimant recorded on the card. See gather_token_usage.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable

from app.kanban.schemas import COLUMNS

AGENT_CLAIM_PREFIX = "agent:"
IMPEDIMENT_PREFIX = "**Impediment:**"
WORKTREE_MARKER = "-worktrees-"


def is_agent_column(name: str | None, agent_columns: set[str] | None = None) -> bool:
    """Whether a column name denotes an agent (its name IS the agent).

    When `agent_columns` is given (the project's current agent columns), only those
    count — this excludes legacy/renamed workflow columns that no longer exist.
    Without it, falls back to "anything not in the fixed set".
    """
    if not name:
        return False
    if agent_columns is not None:
        return name in agent_columns
    return name not in COLUMNS


def _seconds(start, end) -> float | None:
    if start is None or end is None:
        return None
    delta = (end - start).total_seconds()
    return delta if delta >= 0 else None


def _session_of(claimed_by: str | None) -> str | None:
    if claimed_by and claimed_by.startswith(AGENT_CLAIM_PREFIX):
        return claimed_by[len(AGENT_CLAIM_PREFIX):]
    return None


def session_of_folder(folder: str) -> str | None:
    """Recover the dispatch session name from a usage project-folder name."""
    if WORKTREE_MARKER in folder:
        return folder.split(WORKTREE_MARKER, 1)[1] or None
    return None


def _walk_card(card, ops, agent_columns: set[str] | None) -> dict:
    """Single pass over one card's ops (HLC order) → segments, failures, claims.

    A *segment* is a contiguous period the card spent in one agent column.
    It closes when the card moves out: leaving for Impediment counts as failed,
    any other forward move counts as completed. A segment still open at the end
    (card parked on an agent column) is reported as in-progress.
    """
    current: str | None = None
    entered = None
    segments: list[dict] = []
    failures: list[dict] = []
    session_to_agent: dict[str, str] = {}

    def agent_now() -> str | None:
        return current if is_agent_column(current, agent_columns) else card.agent

    for op in ops:
        if op.op_type == "create":
            current = (op.payload or {}).get("column", "Backlog")
            entered = op.created_at
        elif op.op_type == "move":
            new_col = (op.payload or {}).get("column")
            if not new_col or new_col == current:
                continue
            if is_agent_column(current, agent_columns):
                outcome = "failed" if new_col == "Impediment" else "completed"
                segments.append({
                    "agent": current,
                    "outcome": outcome,
                    "duration": _seconds(entered, op.created_at),
                })
            current = new_col
            entered = op.created_at
        elif op.op_type == "claim":
            session = _session_of((op.payload or {}).get("claimed_by"))
            agent = agent_now()
            if session and agent:
                session_to_agent[session] = agent
        elif op.op_type == "comment":
            text = (op.payload or {}).get("text", "") or ""
            if text.startswith(IMPEDIMENT_PREFIX):
                failures.append({
                    "agent": agent_now(),
                    "reason": text[len(IMPEDIMENT_PREFIX):].strip() or "(no reason given)",
                })

    open_agent = current if is_agent_column(current, agent_columns) else None
    return {
        "segments": segments,
        "failures": failures,
        "session_to_agent": session_to_agent,
        "open_agent": open_agent,
    }


def _round(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


def compute_core_stats(
    cards: Iterable, ops: Iterable, agent_columns: set[str] | None = None,
) -> dict:
    """Pure aggregation over project cards and their op-log entries.

    `ops` is every op for these cards (any order); they are grouped per card and
    re-sorted by HLC here so callers can pass a flat list. `agent_columns` is the
    project's current agent-column names; when given, segments through legacy or
    renamed columns are ignored so only real agents are reported.
    """
    ops_by_card: dict[str, list] = defaultdict(list)
    for op in ops:
        ops_by_card[op.entity_id].append(op)
    for card_ops in ops_by_card.values():
        card_ops.sort(key=lambda o: o.hlc)

    per_agent: dict[str, dict] = defaultdict(lambda: {
        "tasks": 0, "completed": 0, "failed": 0,
        "in_progress": 0, "_durations": [],
    })
    failure_counts: dict[tuple[str | None, str], int] = defaultdict(int)
    session_to_agent: dict[str, str] = {}

    for card in cards:
        walked = _walk_card(card, ops_by_card.get(card.id, []), agent_columns)
        session_to_agent.update(walked["session_to_agent"])
        for seg in walked["segments"]:
            a = per_agent[seg["agent"]]
            a["tasks"] += 1
            a["completed" if seg["outcome"] == "completed" else "failed"] += 1
            if seg["duration"] is not None:
                a["_durations"].append(seg["duration"])
        if walked["open_agent"]:
            per_agent[walked["open_agent"]]["in_progress"] += 1
        for f in walked["failures"]:
            failure_counts[(f["agent"], f["reason"])] += 1

    agents = []
    tot_tasks = tot_completed = tot_failed = tot_in_progress = 0
    all_durations: list[float] = []
    for name in sorted(per_agent):
        a = per_agent[name]
        durations = a["_durations"]
        all_durations.extend(durations)
        closed = a["completed"] + a["failed"]
        agents.append({
            "agent": name,
            "tasks": a["tasks"],
            "completed": a["completed"],
            "failed": a["failed"],
            "in_progress": a["in_progress"],
            "success_rate": round(a["completed"] / closed, 3) if closed else None,
            "avg_duration_seconds": _round(statistics.fmean(durations)) if durations else None,
            "median_duration_seconds": _round(statistics.median(durations)) if durations else None,
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_tokens": 0, "cache_read_tokens": 0, "total_tokens": 0,
        })
        tot_tasks += a["tasks"]
        tot_completed += a["completed"]
        tot_failed += a["failed"]
        tot_in_progress += a["in_progress"]

    closed_total = tot_completed + tot_failed
    common_failures = [
        {"agent": agent, "reason": reason, "count": count}
        for (agent, reason), count in sorted(
            failure_counts.items(), key=lambda kv: (-kv[1], kv[0][1])
        )
    ]

    return {
        "totals": {
            "total_tasks": tot_tasks,
            "completed": tot_completed,
            "failed": tot_failed,
            "in_progress": tot_in_progress,
            "success_rate": round(tot_completed / closed_total, 3) if closed_total else None,
            "avg_duration_seconds": _round(statistics.fmean(all_durations)) if all_durations else None,
        },
        "agents": agents,
        "common_failures": common_failures,
        "session_to_agent": session_to_agent,
    }


def apply_token_usage(agents: list[dict], usage_by_agent: dict[str, dict]) -> bool:
    """Fold per-agent token sums into the agent rows. Returns True if any matched."""
    matched = False
    by_name = {a["agent"]: a for a in agents}
    for agent, tokens in usage_by_agent.items():
        row = by_name.get(agent)
        if row is None:
            continue
        for k in ("input_tokens", "output_tokens",
                  "cache_creation_tokens", "cache_read_tokens"):
            row[k] = tokens.get(k, 0)
        row["total_tokens"] = sum(
            tokens.get(k, 0) for k in (
                "input_tokens", "output_tokens",
                "cache_creation_tokens", "cache_read_tokens",
            )
        )
        if row["total_tokens"]:
            matched = True
    return matched


async def gather_token_usage(session_to_agent: dict[str, str]) -> dict[str, dict]:
    """Best-effort: sum Claude Code token usage per agent via worktree-folder join.

    Never raises — returns {} on any failure so the dashboard degrades gracefully.
    """
    if not session_to_agent:
        return {}
    try:
        from app.services.usage_service import UsageService

        entries = await UsageService().get_all_usage_entries()
    except Exception:
        return {}

    totals: dict[str, dict] = defaultdict(lambda: {
        "input_tokens": 0, "output_tokens": 0,
        "cache_creation_tokens": 0, "cache_read_tokens": 0,
    })
    for e in entries:
        session = session_of_folder(e.project_path or "")
        agent = session_to_agent.get(session) if session else None
        if not agent:
            continue
        t = totals[agent]
        t["input_tokens"] += e.input_tokens
        t["output_tokens"] += e.output_tokens
        t["cache_creation_tokens"] += e.cache_creation_tokens
        t["cache_read_tokens"] += e.cache_read_tokens
    return dict(totals)
