"""Agent team grouping — auto-detect lead+member teams by cwd and manage manual teams."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import AgentTeam, AgentTeamMember

logger = logging.getLogger(__name__)


def discover_teams(
    sessions: list[dict[str, Any]],
    manual_teams: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Group discovered sessions into teams.

    Uses a two-pass approach:
    1. Auto-detect teams by grouping sessions that share the same ``cwd``
       and ``provider`` — the most reliable signal that sessions are related
       (lead + members spawned via ``task()`` all run in the same directory).
    2. Merge in any manually-created teams from the DB, matching by session_name.

    A "team" is a dict with:
      - team_id: unique string ("auto-<hash>" or "manual-<db-id>")
      - name: display name (directory basename or user-given name)
      - provider: shared provider id
      - cwd: shared working directory
      - is_auto_detected: True for auto groups, False for manual
      - lead: the first session in the group (or explicit lead for manual teams)
      - members: all sessions in the group
    """
    teams: list[dict[str, Any]] = []
    used_sessions: set[str] = set()

    # Pass 1: manual teams from DB (take precedence over auto-detect)
    manual_member_names: set[str] = set()
    if manual_teams:
        for mt in manual_teams:
            member_names = {m.get("session_name", "") for m in mt.get("members", [])}
            team_sessions = [
                s for s in sessions
                if s.get("session_name", "") in member_names
            ]
            if team_sessions:
                for s in team_sessions:
                    used_sessions.add(s.get("session_name", ""))
                    manual_member_names.add(s.get("session_name", ""))
                teams.append({
                    "team_id": mt["team_id"],
                    "name": mt.get("name", "Team"),
                    "provider": mt.get("provider", ""),
                    "provider_display_name": mt.get("provider_display_name", team_sessions[0].get("provider_display_name", "")),
                    "cwd": mt.get("cwd", team_sessions[0].get("cwd", "")),
                    "is_auto_detected": False,
                    "lead": team_sessions[0],
                    "members": team_sessions,
                })

    # Pass 2: auto-detect by cwd + provider (skipping manual team members)
    from collections import defaultdict

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for session in sessions:
        if session.get("session_name", "") in manual_member_names:
            continue
        key = (session.get("cwd", ""), session.get("provider", ""))
        if key[0]:  # Only group sessions with a known cwd
            groups[key].append(session)

    for (cwd, provider), group in groups.items():
        if len(group) < 2:
            continue  # Only form teams of 2+
        for s in group:
            used_sessions.add(s.get("session_name", ""))
        from pathlib import Path

        name = Path(cwd).name or cwd
        import hashlib

        team_id = "auto-" + hashlib.md5(cwd.encode(), usedforsecurity=False).hexdigest()[:8]
        teams.append({
            "team_id": team_id,
            "name": name,
            "provider": provider,
            "provider_display_name": group[0].get("provider_display_name", provider),
            "cwd": cwd,
            "is_auto_detected": True,
            "lead": group[0],
            "members": group,
        })

    return teams


def get_ungrouped_sessions(
    sessions: list[dict[str, Any]],
    teams: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return sessions that are not part of any team."""
    team_session_names: set[str] = set()
    for team in teams:
        for member in team["members"]:
            team_session_names.add(member.get("session_name", ""))
    return [s for s in sessions if s.get("session_name", "") not in team_session_names]


async def get_manual_teams(db: AsyncSession) -> list[dict[str, Any]]:
    """Load all manually-created teams from the database."""
    result = await db.execute(select(AgentTeam))
    teams = result.scalars().all()
    output: list[dict[str, Any]] = []
    for team in teams:
        members_result = await db.execute(
            select(AgentTeamMember).where(AgentTeamMember.team_id == team.id)
        )
        members = members_result.scalars().all()
        output.append({
            "team_id": f"manual-{team.id}",
            "name": team.name,
            "provider": team.provider,
            "cwd": team.cwd,
            "is_auto_detected": False,
            "lead_session_name": team.lead_session_name,
            "members": [
                {
                    "session_name": m.session_name,
                    "pane_id": m.pane_id,
                    "tmux_target": m.tmux_target,
                }
                for m in members
            ],
        })
    return output


async def create_manual_team(
    db: AsyncSession,
    name: str,
    provider: str,
    cwd: str,
    lead_session_name: str | None = None,
    member_sessions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Create a new manual team in the database."""
    team = AgentTeam(
        name=name,
        provider=provider,
        cwd=cwd,
        lead_session_name=lead_session_name,
        is_auto_detected=False,
    )
    db.add(team)
    await db.flush()  # Get team.id

    if member_sessions:
        for ms in member_sessions:
            member = AgentTeamMember(
                team_id=team.id,
                session_name=ms.get("session_name", ""),
                pane_id=ms.get("pane_id"),
                tmux_target=ms.get("tmux_target", ""),
            )
            db.add(member)

    await db.commit()
    await db.refresh(team)

    return {
        "team_id": f"manual-{team.id}",
        "name": team.name,
        "provider": team.provider,
        "cwd": team.cwd,
        "is_auto_detected": False,
        "lead_session_name": team.lead_session_name,
        "members": member_sessions or [],
    }


async def delete_manual_team(db: AsyncSession, team_id: int) -> bool:
    """Delete a manual team by its database ID."""
    result = await db.execute(select(AgentTeam).where(AgentTeam.id == team_id))
    team = result.scalar_one_or_none()
    if not team:
        return False
    await db.delete(team)
    await db.commit()
    return True


async def add_team_member(
    db: AsyncSession,
    team_id: int,
    session_name: str,
    pane_id: str | None = None,
    tmux_target: str | None = None,
) -> bool:
    """Add a member to an existing manual team."""
    result = await db.execute(select(AgentTeam).where(AgentTeam.id == team_id))
    team = result.scalar_one_or_none()
    if not team:
        return False
    member = AgentTeamMember(
        team_id=team_id,
        session_name=session_name,
        pane_id=pane_id,
        tmux_target=tmux_target or "",
    )
    db.add(member)
    await db.commit()
    return True


async def remove_team_member(db: AsyncSession, member_id: int) -> bool:
    """Remove a member from a manual team."""
    result = await db.execute(
        select(AgentTeamMember).where(AgentTeamMember.id == member_id)
    )
    member = result.scalar_one_or_none()
    if not member:
        return False
    await db.delete(member)
    await db.commit()
    return True
