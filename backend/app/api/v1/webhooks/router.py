"""Inbound webhook endpoints for event-triggered automations.

External events (e.g. a GitHub PR being opened) create a kanban Backlog
card, which the existing auto-dispatcher then claims and spawns. See
``app.services.webhook_triggers`` for the event→action mapping.

This was originally phrased as an extension of the scheduled-messages
layer; that feature (tmux injection) was retired — see
``docs/cockpit/scheduled-trigger-consolidatie-decision.md`` §5.2. The
recurring-triggers feature covers the time-based half of the
"external→kaart" pattern.
"""
from fastapi import APIRouter, Header, Request

from app.services.webhook_triggers import dispatch_webhook_event

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/github")
async def github_webhook(
    request: Request,
    project_key: str,
    x_github_event: str = Header(default=""),
):
    """Receive a GitHub webhook and map it onto the kanban-dispatch layer.

    ``project_key`` (query param) selects the board — configure it in the
    GitHub webhook URL, e.g. ``/api/v1/webhooks/github?project_key=<key>``.
    ``X-GitHub-Event`` carries the event name (``pull_request``); the payload's
    ``action`` field disambiguates it (``opened``), giving the event key
    ``pull_request.opened`` looked up in the handler registry.
    """
    payload = await request.json()
    action = payload.get("action")
    event_type = f"{x_github_event}.{action}" if action else x_github_event
    return await dispatch_webhook_event(
        source="github",
        event_type=event_type,
        payload=payload,
        project_key=project_key,
    )
