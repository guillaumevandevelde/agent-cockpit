"""Run group grouping — auto-detect lead+member runs by cwd and manage manual groups."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import RunGroup, RunMembership

logger = logging.getLogger(__name__)


def discover_groups(
    runs: list[dict[str, Any]],
    manual_groups: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Group discovered runs.

    Uses a two-pass approach:
    1. Auto-detect groups by grouping runs that share the same ``cwd``
       and ``cli`` — the most reliable signal that runs are related
       (lead + members spawned via ``task()`` all run in the same directory).
    2. Merge in any manually-created groups from the DB, matching by run_name.

    A "group" is a dict with:
      - group_id: unique string ("auto-<hash>" or "manual-<db-id>")
      - name: display name (directory basename or user-given name)
      - cli: shared CLI id
      - cwd: shared working directory
      - is_auto_detected: True for auto groups, False for manual
      - lead: the first run in the group (or explicit lead for manual groups)
      - runs: all runs in the group
    """
    groups: list[dict[str, Any]] = []
    used_runs: set[str] = set()

    # Pass 1: manual groups from DB (take precedence over auto-detect)
    manual_run_names: set[str] = set()
    if manual_groups:
        for mg in manual_groups:
            member_names = {
                m.get("run_name", m.get("session_name", ""))
                for m in mg.get("memberships", mg.get("members", []))
            }
            group_runs = [
                r for r in runs
                if r.get("session_name", r.get("run_name", "")) in member_names
            ]
            if group_runs:
                for r in group_runs:
                    name = r.get("session_name", r.get("run_name", ""))
                    used_runs.add(name)
                    manual_run_names.add(name)
                groups.append({
                    "group_id": mg["group_id"],
                    "name": mg.get("name", "Group"),
                    "cli": mg.get("cli", ""),
                    "cli_display_name": mg.get("cli_display_name", group_runs[0].get("cli_display_name", "")),
                    "cwd": mg.get("cwd", group_runs[0].get("cwd", "")),
                    "is_auto_detected": False,
                    "lead": group_runs[0],
                    "runs": group_runs,
                })

    # Pass 2: auto-detect by cwd + cli (skipping manual group members)
    from collections import defaultdict

    auto_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        name = run.get("session_name", run.get("run_name", ""))
        if name in manual_run_names:
            continue
        key = (run.get("cwd", ""), run.get("cli", ""))
        if key[0]:  # Only group runs with a known cwd
            auto_groups[key].append(run)

    for (cwd, cli), group in auto_groups.items():
        if len(group) < 2:
            continue  # Only form groups of 2+
        for r in group:
            used_runs.add(r.get("session_name", r.get("run_name", "")))
        from pathlib import Path

        name = Path(cwd).name or cwd
        import hashlib

        group_id = "auto-" + hashlib.md5(cwd.encode(), usedforsecurity=False).hexdigest()[:8]
        groups.append({
            "group_id": group_id,
            "name": name,
            "cli": cli,
            "cli_display_name": group[0].get("cli_display_name", cli),
            "cwd": cwd,
            "is_auto_detected": True,
            "lead": group[0],
            "runs": group,
        })

    return groups


def get_ungrouped_runs(
    runs: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return runs that are not part of any group."""
    group_run_names: set[str] = set()
    for group in groups:
        for member in group["runs"]:
            group_run_names.add(member.get("session_name", member.get("run_name", "")))
    return [r for r in runs if r.get("session_name", r.get("run_name", "")) not in group_run_names]


async def get_manual_groups(db: AsyncSession) -> list[dict[str, Any]]:
    """Load all manually-created groups from the database."""
    result = await db.execute(select(RunGroup))
    groups = result.scalars().all()
    output: list[dict[str, Any]] = []
    for group in groups:
        memberships_result = await db.execute(
            select(RunMembership).where(RunMembership.group_id == group.id)
        )
        memberships = memberships_result.scalars().all()
        output.append({
            "group_id": f"manual-{group.id}",
            "name": group.name,
            "cli": group.cli,
            "cwd": group.cwd,
            "is_auto_detected": False,
            "lead_run_name": group.lead_run_name,
            "memberships": [
                {
                    "run_name": m.run_name,
                    "pane_id": m.pane_id,
                    "tmux_target": m.tmux_target,
                }
                for m in memberships
            ],
        })
    return output


async def create_manual_group(
    db: AsyncSession,
    name: str,
    cli: str,
    cwd: str,
    lead_run_name: str | None = None,
    member_runs: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Create a new manual group in the database."""
    group = RunGroup(
        name=name,
        cli=cli,
        cwd=cwd,
        lead_run_name=lead_run_name,
        is_auto_detected=False,
    )
    db.add(group)
    await db.flush()  # Get group.id

    if member_runs:
        for mr in member_runs:
            membership = RunMembership(
                group_id=group.id,
                run_name=mr.get("run_name", mr.get("session_name", "")),
                pane_id=mr.get("pane_id"),
                tmux_target=mr.get("tmux_target", ""),
            )
            db.add(membership)

    await db.commit()
    await db.refresh(group)

    return {
        "group_id": f"manual-{group.id}",
        "name": group.name,
        "cli": group.cli,
        "cwd": group.cwd,
        "is_auto_detected": False,
        "lead_run_name": group.lead_run_name,
        "memberships": member_runs or [],
    }


async def delete_manual_group(db: AsyncSession, group_id: int) -> bool:
    """Delete a manual group by its database ID."""
    result = await db.execute(select(RunGroup).where(RunGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        return False
    await db.delete(group)
    await db.commit()
    return True


async def add_group_membership(
    db: AsyncSession,
    group_id: int,
    run_name: str,
    pane_id: str | None = None,
    tmux_target: str | None = None,
) -> bool:
    """Add a membership to an existing manual group."""
    result = await db.execute(select(RunGroup).where(RunGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        return False
    membership = RunMembership(
        group_id=group_id,
        run_name=run_name,
        pane_id=pane_id,
        tmux_target=tmux_target or "",
    )
    db.add(membership)
    await db.commit()
    return True


async def remove_group_membership(db: AsyncSession, membership_id: int) -> bool:
    """Remove a membership from a manual group."""
    result = await db.execute(
        select(RunMembership).where(RunMembership.id == membership_id)
    )
    membership = result.scalar_one_or_none()
    if not membership:
        return False
    await db.delete(membership)
    await db.commit()
    return True