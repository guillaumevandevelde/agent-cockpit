"""Inbound webhook endpoints for event-triggered automations.

External events (a GitHub issue being opened, a PR being opened) create a
kanban Backlog card, which the existing auto-dispatcher then claims and spawns.
See ``app.services.webhook_triggers`` for the event→action mapping and for the
HMAC verification this module enforces.

This was originally phrased as an extension of the scheduled-messages
layer; that feature (tmux injection) was retired — see
``docs/cockpit/scheduled-trigger-consolidatie-decision.md`` §5.2. The
recurring-triggers feature covers the time-based half of the
"external→kaart" pattern.

Setup, threat model and the curl recipe live in
``docs/cockpit/github-issue-webhook.md``.
"""
import json

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.services.webhook_triggers import (
    GITHUB_SIGNATURE_HEADER,
    GITHUB_WEBHOOK_SECRET_NAME,
    ParentCardNotFound,
    dispatch_webhook_event,
    resolve_webhook_secret,
    signature_matches,
)

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


@router.post("/github")
async def github_webhook(
    request: Request,
    project_key: str,
    parent_card_id: str | None = None,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str = Header(default=""),
):
    """Receive a GitHub webhook and map it onto the kanban-dispatch layer.

    ``project_key`` (query param) selects the board — configure it in the
    GitHub webhook URL, e.g. ``/api/v1/webhooks/github?project_key=<key>``.
    ``X-GitHub-Event`` carries the event name (``issues``, ``pull_request``);
    the payload's ``action`` field disambiguates it (``opened``), giving the
    event key ``issues.opened`` looked up in the handler registry.

    ``parent_card_id`` (optional query param) files the resulting card as a
    child of an existing card, with its ``plan_ref`` wired in the same
    transaction. A parent id that does not resolve is refused (422) rather than
    accepted: the child would be held out of dispatch under ``missing_parent``
    with nothing visible at this boundary.

    **Authentication.** This endpoint is an unauthenticated trust boundary, so
    every delivery must carry ``X-Hub-Signature-256`` — GitHub's HMAC-SHA256
    over the raw body under the project's ``GITHUB_WEBHOOK_SECRET`` SecretStore
    entry. Failure modes are fail-closed and distinguishable: 503 when the
    project has no readable secret (nothing can be verified), 401 when the
    signature is missing or wrong. The body is parsed only after the signature
    verifies.
    """
    raw = await request.body()

    secret = await resolve_webhook_secret(project_key)
    if not secret:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "webhook_secret_not_configured",
                "credential_name": GITHUB_WEBHOOK_SECRET_NAME,
                "project_key": project_key,
            },
        )
    if not signature_matches(secret, raw, x_hub_signature_256):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_signature", "header": GITHUB_SIGNATURE_HEADER},
        )

    try:
        payload = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail={"error": "invalid_json"},
        ) from None
    if not isinstance(payload, dict):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail={"error": "payload_not_an_object"},
        )

    action = payload.get("action")
    event_type = f"{x_github_event}.{action}" if action else x_github_event
    try:
        return await dispatch_webhook_event(
            source="github",
            event_type=event_type,
            payload=payload,
            project_key=project_key,
            parent_card_id=parent_card_id,
        )
    except ParentCardNotFound as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "parent_card_not_found", "parent_card_id": str(exc)},
        ) from None
