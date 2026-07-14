"""Event-/webhook-triggered automations.

Maps an incoming external event onto the existing kanban-dispatch layer.
Creating a ``Backlog`` card *is* the trigger: the auto-dispatcher already
claims and spawns Backlog cards, so an event simply produces a card and the
same execution primitives take over — no parallel spawn mechanism.

The mapping is intentionally minimal and extensible. One concrete trigger is
registered (``github`` / ``pull_request.opened``); adding another source or
event is a single ``_HANDLERS`` entry pointing at an ``async`` handler with the
signature ``handler(payload: dict, project_key: str) -> dict``.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from app.kanban import service
from app.kanban.db import KanbanSessionLocal
from app.kanban.operations import apply_operation

logger = logging.getLogger(__name__)

Handler = Callable[[dict, str], Awaitable[dict]]


async def _create_backlog_card(
    *, project_key: str, title: str, description: str, metadata: dict | None = None,
) -> str:
    """Create a Backlog card via the same op-log flow the REST/MCP paths use.

    Returns the new card id. The card sits in Backlog so the existing
    auto-dispatcher picks it up on its next tick.
    """
    async with KanbanSessionLocal() as s:
        agent = await service.resolve_create_agent(
            s, project_key, work_type=None, explicit_agent=None,
        )
        cid = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=project_key, entity_id=None,
            payload={
                "title": title,
                "description": description,
                "column": "Backlog",
                "agent": agent,
                "metadata": metadata,
            },
        )
        await s.commit()
    return cid


async def _handle_github_pr_opened(payload: dict, project_key: str) -> dict:
    """GitHub ``pull_request.opened`` → a Backlog review card."""
    pr = payload.get("pull_request") or {}
    number = pr.get("number")
    pr_title = pr.get("title") or ""
    url = pr.get("html_url") or ""
    repo = (payload.get("repository") or {}).get("full_name") or ""
    body = pr.get("body") or ""

    title = f"[PR #{number}] Review: {pr_title}".strip()
    parts = [f"Automated from GitHub `pull_request.opened` on `{repo}`."]
    if url:
        parts.append(f"PR: {url}")
    if body:
        parts.extend(("", body))
    description = "\n".join(parts)

    cid = await _create_backlog_card(
        project_key=project_key,
        title=title,
        description=description,
        metadata={
            "source": "github",
            "event": "pull_request.opened",
            "repo": repo,
            "pr_number": number,
            "pr_url": url,
        },
    )
    logger.info(
        "webhook github pull_request.opened → card %s (%s#%s) on %s",
        cid, repo, number, project_key,
    )
    return {"triggered": True, "action": "card_created", "card_id": cid}


# (source, event_type) → handler. Add entries here to support more triggers.
_HANDLERS: dict[tuple[str, str], Handler] = {
    ("github", "pull_request.opened"): _handle_github_pr_opened,
}


async def dispatch_webhook_event(
    *, source: str, event_type: str, payload: dict, project_key: str,
) -> dict:
    """Route an incoming event to its handler, or ignore it.

    Unmapped events are a no-op (``triggered: False``) rather than an error:
    a source (e.g. GitHub) fires many event types at one endpoint and the
    caller should not treat "not interesting" as a failure.
    """
    handler = _HANDLERS.get((source, event_type))
    if handler is None:
        return {
            "triggered": False,
            "reason": f"no handler for {source}:{event_type}",
        }
    return await handler(payload, project_key)
