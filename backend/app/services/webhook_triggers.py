"""Event-/webhook-triggered automations.

Maps an incoming external event onto the existing kanban-dispatch layer.
Creating a ``Backlog`` card *is* the trigger: the auto-dispatcher already
claims and spawns Backlog cards, so an event simply produces a card and the
same execution primitives take over — no parallel spawn mechanism.

The mapping is intentionally minimal and extensible. Two concrete triggers are
registered (``github`` / ``issues.opened`` and ``github`` /
``pull_request.opened``); adding another source or event is a single
``_HANDLERS`` entry pointing at an ``async`` handler with the signature
``handler(payload: dict, project_key: str, parent_card_id: str | None) -> dict``.

Authentication lives here too (``resolve_webhook_secret`` +
``signature_matches``): the endpoint is an unauthenticated trust boundary
reachable from the public internet, so every delivery must carry a valid
``X-Hub-Signature-256`` HMAC over the raw body. The shared secret is a
per-project SecretStore entry named ``GITHUB_WEBHOOK_SECRET`` (see
``docs/cockpit/github-issue-webhook.md``); the store never hands out values
over REST, only names.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import select

from app.kanban import service
from app.kanban.db import KanbanSessionLocal
from app.kanban.models import KanbanCard
from app.kanban.operations import apply_operation, attach_plan
from app.services.secrets_store import AGESecretStore, SecretStoreError

logger = logging.getLogger(__name__)

Handler = Callable[[dict, str, "str | None"], Awaitable[dict]]

#: SecretStore entry holding the GitHub webhook's shared HMAC secret. One per
#: project_key — the same string configured in the GitHub webhook UI.
GITHUB_WEBHOOK_SECRET_NAME = "GITHUB_WEBHOOK_SECRET"

#: Header GitHub signs the raw request body into (HMAC-SHA256, hex-encoded).
GITHUB_SIGNATURE_HEADER = "X-Hub-Signature-256"

_SIGNATURE_PREFIX = "sha256="


class ParentCardNotFound(ValueError):
    """A ``parent_card_id`` was supplied but no such card exists.

    Creating a child against a missing parent is worse than refusing: the
    dispatcher holds it under ``missing_parent`` and the card never runs, with
    no visible error at the webhook boundary.
    """


# -- authentication ---------------------------------------------------------


def _store() -> AGESecretStore:
    """Per-call store. Patched in tests to point at a tmp_path.

    Cheap to construct — no I/O until the first CRUD call.
    """
    return AGESecretStore()


async def resolve_webhook_secret(
    project_key: str, name: str = GITHUB_WEBHOOK_SECRET_NAME,
) -> str | None:
    """Read the project's webhook secret, or ``None`` when unavailable.

    ``None`` covers both "never configured" and "store unreadable" (wrong
    passphrase, corrupt file). The caller must fail closed on it — an
    unverifiable delivery is refused, never accepted.

    The store is synchronous (in-process scrypt + ChaCha20), so the read runs
    in a thread to keep the event loop free.
    """
    def _read() -> str | None:
        return _store().get(project_key, name)

    try:
        return await asyncio.to_thread(_read)
    except SecretStoreError as exc:
        logger.warning(
            "webhook secret %s unavailable for project_key=%s: %s",
            name, project_key, exc,
        )
        return None


def signature_matches(secret: str, body: bytes, signature: str) -> bool:
    """True when ``signature`` is GitHub's HMAC-SHA256 of ``body`` under ``secret``.

    ``signature`` is the raw ``X-Hub-Signature-256`` header value, i.e.
    ``sha256=<hex>``. A missing, malformed or wrong signature is False; the
    comparison is constant-time.
    """
    if not signature or not signature.startswith(_SIGNATURE_PREFIX):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature[len(_SIGNATURE_PREFIX):].strip())


# -- card creation ----------------------------------------------------------


async def _create_backlog_card(
    *, project_key: str, title: str, description: str,
    metadata: dict | None = None, parent_card_id: str | None = None,
    plan_markdown: str | None = None,
) -> str:
    """Create a Backlog card via the same op-log flow the REST/MCP paths use.

    Returns the new card id. The card sits in Backlog so the existing
    auto-dispatcher picks it up on its next tick.

    When ``parent_card_id`` is given the card is created as a child *and*
    immediately gets its ``plan_ref`` deliverable via ``attach_plan``. Both
    halves are required: a child without a ``plan_ref`` is held out of dispatch
    silently (``dispatch._awaiting_plan_ref``), so creating one without wiring
    the plan would file a card that never runs.

    Raises ``ParentCardNotFound`` when ``parent_card_id`` does not resolve.
    """
    async with KanbanSessionLocal() as s:
        if parent_card_id is not None:
            parent = await s.get(KanbanCard, parent_card_id)
            if parent is None:
                raise ParentCardNotFound(parent_card_id)
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
                "parent_card_id": parent_card_id,
            },
        )
        if parent_card_id is not None:
            # depends_on_graph is empty on purpose: one webhook delivery carries
            # one issue, so there is nothing to derive a cross-issue dependency
            # from. A human or analyst re-runs add_plan_attachment with a real
            # graph when several issue-cards need ordering.
            await attach_plan(
                s, parent_card_id=parent_card_id,
                plan_markdown=plan_markdown or "",
                child_card_ids=[cid], depends_on_graph={},
            )
        await s.commit()
    return cid


async def _find_event_card(
    *, project_key: str, meta_match: dict,
) -> str | None:
    """Find an existing card whose ``metadata`` matches every key in ``meta_match``.

    Idempotency for redeliveries: GitHub retries a delivery it considers failed
    (and the operator can replay one by hand), which would otherwise file a
    second card for the same issue. The match is over the card's stored event
    metadata, so it also matches a card that has already moved past Backlog.

    ponytail: scans the project's cards in Python instead of querying into the
    JSON column. Boards here are hundreds of cards; swap in a JSON predicate or
    an index if that stops holding.
    """
    async with KanbanSessionLocal() as s:
        cards = (await s.execute(
            select(KanbanCard).where(KanbanCard.project_key == project_key)
        )).scalars().all()
    for card in cards:
        meta = card.meta or {}
        if all(meta.get(k) == v for k, v in meta_match.items()):
            return card.id
    return None


# -- handlers ---------------------------------------------------------------


async def _handle_github_issue_opened(
    payload: dict, project_key: str, parent_card_id: str | None = None,
) -> dict:
    """GitHub ``issues.opened`` / ``issues.reopened`` → a Backlog work card.

    The issue becomes a dispatch unit: title, body and labels land on the card,
    the auto-dispatcher claims it on the next tick. A redelivery of the same
    issue returns the existing card instead of a duplicate.
    """
    issue = payload.get("issue") or {}
    number = issue.get("number")
    issue_title = issue.get("title") or ""
    url = issue.get("html_url") or ""
    repo = (payload.get("repository") or {}).get("full_name") or ""
    body = issue.get("body") or ""
    labels = [
        name for name in (
            label.get("name") for label in (issue.get("labels") or [])
            if isinstance(label, dict)
        ) if name
    ]

    existing = await _find_event_card(
        project_key=project_key,
        meta_match={"source": "github", "repo": repo, "issue_number": number},
    )
    if existing is not None:
        logger.info(
            "webhook github issue %s#%s already has card %s on %s — no duplicate",
            repo, number, existing, project_key,
        )
        return {"triggered": True, "action": "card_exists", "card_id": existing}

    title = f"[issue #{number}] {issue_title}".strip()
    parts = [f"Automated from GitHub `issues` on `{repo}`."]
    if url:
        parts.append(f"Issue: {url}")
    if labels:
        parts.append(f"GitHub labels: {', '.join(labels)}")
    if body:
        parts.extend(("", body))
    description = "\n".join(parts)

    cid = await _create_backlog_card(
        project_key=project_key,
        title=title,
        description=description,
        metadata={
            "source": "github",
            "event": "issues.opened",
            "repo": repo,
            "issue_number": number,
            "issue_url": url,
            "github_labels": labels,
        },
        parent_card_id=parent_card_id,
        plan_markdown=(
            f"Auto-generated by the GitHub issue webhook for `{repo}` issue "
            f"#{number}. One delivery, one child card, no dependency graph — "
            f"re-run `add_plan_attachment` to order several issue cards."
        ),
    )
    logger.info(
        "webhook github issues → card %s (%s#%s) on %s (parent=%s)",
        cid, repo, number, project_key, parent_card_id,
    )
    return {"triggered": True, "action": "card_created", "card_id": cid}


async def _handle_github_pr_opened(
    payload: dict, project_key: str, parent_card_id: str | None = None,
) -> dict:
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
        parent_card_id=parent_card_id,
        plan_markdown=(
            f"Auto-generated by the GitHub webhook for `{repo}` PR #{number}."
        ),
    )
    logger.info(
        "webhook github pull_request.opened → card %s (%s#%s) on %s",
        cid, repo, number, project_key,
    )
    return {"triggered": True, "action": "card_created", "card_id": cid}


# (source, event_type) → handler. Add entries here to support more triggers.
_HANDLERS: dict[tuple[str, str], Handler] = {
    ("github", "issues.opened"): _handle_github_issue_opened,
    ("github", "issues.reopened"): _handle_github_issue_opened,
    ("github", "pull_request.opened"): _handle_github_pr_opened,
}


async def dispatch_webhook_event(
    *, source: str, event_type: str, payload: dict, project_key: str,
    parent_card_id: str | None = None,
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
    return await handler(payload, project_key, parent_card_id)
