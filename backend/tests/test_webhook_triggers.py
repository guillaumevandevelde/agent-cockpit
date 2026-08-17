"""Event-/webhook-triggered automations → kanban-dispatch layer.

The GitHub webhook is an unauthenticated trust boundary, so every test posts a
*signed* body: the raw bytes are HMAC-SHA256'd under the project's
``GITHUB_WEBHOOK_SECRET`` SecretStore entry, exactly as GitHub does. The
``_webhook_secret`` fixture points the store at a tmp_path and patches the
factory **on the consumer module** (``webhook_triggers._store``) — patching
``secrets_store`` itself would not reach the binding the handler uses (see
``docs/cockpit/test-doubles-convention.md``).
"""
import hashlib
import hmac
import json
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services import webhook_triggers
from app.services.secrets_store import AGESecretStore
from app.services.webhook_triggers import dispatch_webhook_event
from tests.kanban_test_db import reset_test_tables

PROJECT = "P"
SECRET = "hook-test-secret"


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.fixture(autouse=True)
def _webhook_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the webhook's SecretStore at a tmp_path holding the test secret."""
    root = tmp_path / "secrets-store"
    root.mkdir()

    def _factory(root=root):
        # scrypt_log_n=14 (format minimum) instead of the production 20: the
        # cost is recorded per file and honoured on read, so this only makes
        # the fixture cheap. Same trade-off as tests/test_api_secrets.py.
        return AGESecretStore(root=root, passphrase="hook-test-pass",
                              scrypt_log_n=14)

    monkeypatch.setattr(webhook_triggers, "_store", _factory)
    _factory().put(PROJECT, webhook_triggers.GITHUB_WEBHOOK_SECRET_NAME, SECRET)
    return root


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256,
    ).hexdigest()


def _headers(body: bytes, event: str, *, secret: str = SECRET) -> dict:
    return {
        "X-GitHub-Event": event,
        "X-Hub-Signature-256": _sign(body, secret),
        "Content-Type": "application/json",
    }


def _issue_payload(action: str = "opened", number: int = 7) -> dict:
    return {
        "action": action,
        "issue": {
            "number": number,
            "title": "Board never loads on cold start",
            "html_url": f"https://github.com/acme/repo/issues/{number}",
            "body": "Steps to reproduce: ...",
            "labels": [{"name": "bug"}, {"name": "backend"}],
        },
        "repository": {"full_name": "acme/repo"},
    }


def _pr_payload(action: str = "opened", number: int = 42) -> dict:
    return {
        "action": action,
        "pull_request": {
            "number": number,
            "title": "Add feature X",
            "html_url": f"https://github.com/acme/repo/pull/{number}",
            "body": "This PR adds X.",
        },
        "repository": {"full_name": "acme/repo"},
    }


async def _post(ac: AsyncClient, payload: dict, event: str, **params):
    """POST a signed webhook delivery. Signs the exact bytes that are sent."""
    body = json.dumps(payload).encode("utf-8")
    return await ac.post(
        "/api/v1/webhooks/github",
        params={"project_key": PROJECT, **params},
        headers=_headers(body, event),
        content=body,
    )


@pytest_asyncio.fixture
async def ac():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        yield client


# -- GitHub issue → dispatch unit (end-to-end) ------------------------------


@pytest.mark.asyncio
async def test_github_issue_opened_creates_backlog_card(ac: AsyncClient):
    """A faked `issues.opened` delivery travels the whole routing: signature
    verification → handler → op-log → a Backlog card the dispatcher can claim.
    """
    r = await _post(ac, _issue_payload(), "issues")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["triggered"] is True
    assert data["action"] == "card_created"
    cid = data["card_id"]
    assert cid

    r = await ac.get(
        "/api/v1/kanban/cards",
        params={"project_key": PROJECT, "column": "Backlog"},
    )
    cards = r.json()["items"]
    card = next((c for c in cards if c["id"] == cid), None)
    assert card is not None
    assert "#7" in card["title"]
    assert "Board never loads" in card["title"]
    assert card["column"] == "Backlog"
    # Issue body, URL and GitHub labels survive into the card the agent reads.
    assert "Steps to reproduce" in card["description"]
    assert "github.com/acme/repo/issues/7" in card["description"]
    assert "bug" in card["description"]
    assert card["metadata"]["issue_number"] == 7
    assert card["metadata"]["repo"] == "acme/repo"


@pytest.mark.asyncio
async def test_github_issue_redelivery_does_not_duplicate(ac: AsyncClient):
    """GitHub retries deliveries; the second one must not file a second card."""
    first = await _post(ac, _issue_payload(), "issues")
    second = await _post(ac, _issue_payload(action="reopened"), "issues")
    assert second.status_code == 200, second.text
    assert second.json()["action"] == "card_exists"
    assert second.json()["card_id"] == first.json()["card_id"]

    r = await ac.get(
        "/api/v1/kanban/cards",
        params={"project_key": PROJECT, "column": "Backlog"},
    )
    issue_cards = [
        c for c in r.json()["items"]
        if (c.get("metadata") or {}).get("issue_number") == 7
    ]
    assert len(issue_cards) == 1


@pytest.mark.asyncio
async def test_issue_card_under_parent_gets_plan_ref(ac: AsyncClient):
    """A child card without a `plan_ref` is held out of dispatch silently
    (`dispatch._awaiting_plan_ref`), so the webhook must wire one when a
    `parent_card_id` is supplied."""
    r = await ac.post(
        "/api/v1/kanban/cards",
        json={
            "project_key": PROJECT, "title": "Upstream repo work",
            "description": "parent", "column": "Backlog",
            "confirm_new_project": True,
        },
    )
    assert r.status_code == 201, r.text
    parent_id = r.json()["id"]

    r = await _post(ac, _issue_payload(number=9), "issues",
                    parent_card_id=parent_id)
    assert r.status_code == 200, r.text
    child_id = r.json()["card_id"]

    r = await ac.get(f"/api/v1/kanban/cards/{child_id}")
    child = r.json()
    assert child["parent_card_id"] == parent_id
    kinds = [d["kind"] for d in child["deliverables"]]
    assert "plan_ref" in kinds, child["deliverables"]


@pytest.mark.asyncio
async def test_unknown_parent_card_id_is_refused(ac: AsyncClient):
    """Refusing beats filing a child that the dispatcher parks under
    `missing_parent` with nothing visible at this boundary."""
    r = await _post(ac, _issue_payload(), "issues", parent_card_id="deadbeef")
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error"] == "parent_card_not_found"


# -- HMAC verification (fail closed) ----------------------------------------


@pytest.mark.asyncio
async def test_missing_signature_is_rejected(ac: AsyncClient):
    body = json.dumps(_issue_payload()).encode("utf-8")
    r = await ac.post(
        "/api/v1/webhooks/github",
        params={"project_key": PROJECT},
        headers={"X-GitHub-Event": "issues", "Content-Type": "application/json"},
        content=body,
    )
    assert r.status_code == 401, r.text
    assert r.json()["detail"]["error"] == "invalid_signature"

    # Nothing was created: an unknown project_key 404s rather than reporting an
    # empty board (kanban card 91c85199), which is a stronger statement than
    # `items == []`.
    r = await ac.get(
        "/api/v1/kanban/cards",
        params={"project_key": PROJECT, "column": "Backlog"},
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_wrong_signature_is_rejected(ac: AsyncClient):
    body = json.dumps(_issue_payload()).encode("utf-8")
    r = await ac.post(
        "/api/v1/webhooks/github",
        params={"project_key": PROJECT},
        headers=_headers(body, "issues", secret="not-the-secret"),
        content=body,
    )
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_tampered_body_is_rejected(ac: AsyncClient):
    """The signature covers the raw body, so mutating the payload after signing
    must fail even though the secret is right."""
    signed = json.dumps(_issue_payload()).encode("utf-8")
    tampered = json.dumps(_issue_payload(number=99)).encode("utf-8")
    r = await ac.post(
        "/api/v1/webhooks/github",
        params={"project_key": PROJECT},
        headers=_headers(signed, "issues"),
        content=tampered,
    )
    assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_missing_project_secret_fails_closed(ac: AsyncClient):
    """No readable secret means nothing can be verified — refuse, don't accept."""
    webhook_triggers._store().delete(
        PROJECT, webhook_triggers.GITHUB_WEBHOOK_SECRET_NAME,
    )
    r = await _post(ac, _issue_payload(), "issues")
    assert r.status_code == 503, r.text
    detail = r.json()["detail"]
    assert detail["error"] == "webhook_secret_not_configured"
    assert detail["credential_name"] == "GITHUB_WEBHOOK_SECRET"


# -- existing PR trigger + ignore semantics ---------------------------------


@pytest.mark.asyncio
async def test_github_pr_opened_creates_backlog_card(ac: AsyncClient):
    r = await _post(ac, _pr_payload(), "pull_request")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["triggered"] is True
    assert data["action"] == "card_created"
    cid = data["card_id"]
    assert cid

    # The card lands in Backlog: the existing auto-dispatcher claims and
    # spawns Backlog cards, so this IS the spawn trigger (no parallel path).
    r = await ac.get(
        "/api/v1/kanban/cards",
        params={"project_key": PROJECT, "column": "Backlog"},
    )
    cards = r.json()["items"]
    card = next((c for c in cards if c["id"] == cid), None)
    assert card is not None
    assert "#42" in card["title"]
    assert card["column"] == "Backlog"


@pytest.mark.asyncio
async def test_github_pr_closed_is_ignored(ac: AsyncClient):
    r = await _post(ac, _pr_payload(action="closed"), "pull_request")
    assert r.status_code == 200, r.text
    assert r.json()["triggered"] is False

    r = await ac.get(
        "/api/v1/kanban/cards",
        params={"project_key": PROJECT, "column": "Backlog"},
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_github_issue_closed_is_ignored(ac: AsyncClient):
    """Only `opened`/`reopened` map to a card; closed-issue reconciliation is
    deliberately out of scope (docs/cockpit/github-issue-webhook.md §6)."""
    r = await _post(ac, _issue_payload(action="closed"), "issues")
    assert r.status_code == 200, r.text
    assert r.json()["triggered"] is False


@pytest.mark.asyncio
async def test_non_issue_event_is_ignored(ac: AsyncClient):
    r = await _post(ac, {"zen": "Design for failure."}, "ping")
    assert r.status_code == 200, r.text
    assert r.json()["triggered"] is False


@pytest.mark.asyncio
async def test_dispatch_unknown_source_event_returns_not_triggered():
    result = await dispatch_webhook_event(
        source="gitlab", event_type="merge_request.open",
        payload={}, project_key=PROJECT,
    )
    assert result["triggered"] is False
    assert "no handler" in result["reason"]
