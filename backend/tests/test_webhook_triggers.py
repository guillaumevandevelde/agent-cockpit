"""Event-/webhook-triggered automations → kanban-dispatch layer."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.webhook_triggers import dispatch_webhook_event
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


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


@pytest.mark.asyncio
async def test_github_pr_opened_creates_backlog_card():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post(
            "/api/v1/webhooks/github",
            params={"project_key": "P"},
            headers={"X-GitHub-Event": "pull_request"},
            json=_pr_payload(),
        )
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
            params={"project_key": "P", "column": "Backlog"},
        )
        cards = r.json()["items"]
        card = next((c for c in cards if c["id"] == cid), None)
        assert card is not None
        assert "#42" in card["title"]
        assert card["column"] == "Backlog"


@pytest.mark.asyncio
async def test_github_pr_closed_is_ignored():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post(
            "/api/v1/webhooks/github",
            params={"project_key": "P"},
            headers={"X-GitHub-Event": "pull_request"},
            json=_pr_payload(action="closed"),
        )
        assert r.status_code == 200, r.text
        assert r.json()["triggered"] is False

        # Nothing was created, so "P" is still an unknown project_key and the
        # list endpoint 404s rather than reporting an empty board -- that guard
        # is deliberate (kanban card 91c85199: an unknown key used to look
        # exactly like a valid, empty project). A 404 here is a stronger
        # statement than `items == []`: not just "no Backlog cards" but "this
        # project has no cards or columns at all".
        r = await ac.get(
            "/api/v1/kanban/cards", params={"project_key": "P", "column": "Backlog"}
        )
        assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_non_pull_request_event_is_ignored():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post(
            "/api/v1/webhooks/github",
            params={"project_key": "P"},
            headers={"X-GitHub-Event": "ping"},
            json={"zen": "Design for failure."},
        )
        assert r.status_code == 200, r.text
        assert r.json()["triggered"] is False


@pytest.mark.asyncio
async def test_dispatch_unknown_source_event_returns_not_triggered():
    result = await dispatch_webhook_event(
        source="gitlab", event_type="merge_request.open",
        payload={}, project_key="P",
    )
    assert result["triggered"] is False
    assert "no handler" in result["reason"]
