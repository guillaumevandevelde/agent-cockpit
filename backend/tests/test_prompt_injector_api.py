"""REST API tests for the per-project prompt-injector kill-switch.

Covers ``GET /api/v1/kanban/prompt-injector`` and
``POST /api/v1/kanban/prompt-injector`` (kaart d0446fd8…). The
dispatcher reads the kill-switch on every spawn tick via
``app.kanban.prompt_injectors.resolve_active_injectors``, so these
endpoints are the operator-facing override that switches Caveman +
Ponytail off project-wide without touching the per-lane column flags.

Mirrors the shape of ``tests/test_token_saver_api.py`` — same fixture
stack, same round-trip semantics — so a future reader comparing the
two kill-switches sees the same pattern in both places.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app.kanban.db import KanbanSessionLocal
from app.kanban.models import KanbanMeta
from app.main import app
from tests.kanban_test_db import reset_test_tables


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


def test_get_prompt_injector_returns_false_when_unset(client):
    """No row in ``KanbanMeta`` → ``enabled=False``."""
    r = client.get(
        "/api/v1/kanban/prompt-injector", params={"project_key": "PROJ"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"project_key": "PROJ", "enabled": False}


def test_post_prompt_injector_persists_value(client):
    """POST writes ``"1"`` to ``KanbanMeta`` and GET returns it."""
    r = client.post(
        "/api/v1/kanban/prompt-injector",
        json={"project_key": "PROJ", "enabled": True},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"project_key": "PROJ", "enabled": True}

    r = client.get(
        "/api/v1/kanban/prompt-injector", params={"project_key": "PROJ"},
    )
    assert r.json()["enabled"] is True


def test_post_prompt_injector_round_trips_false(client):
    """POST ``enabled=False`` after a true → GET returns ``False``.

    Operator path: kill-switch was on (e.g. for an incident), now
    flipped back off → GET reflects the cleared state immediately.
    """
    client.post(
        "/api/v1/kanban/prompt-injector",
        json={"project_key": "PROJ", "enabled": True},
    )
    client.post(
        "/api/v1/kanban/prompt-injector",
        json={"project_key": "PROJ", "enabled": False},
    )
    r = client.get(
        "/api/v1/kanban/prompt-injector", params={"project_key": "PROJ"},
    )
    assert r.json()["enabled"] is False


def test_post_prompt_injector_value_is_canonical(client):
    """The persisted value is the literal string ``"1"`` / ``"0"`` —
    matches the convention used by ``token_saver.set_board_enabled`` and
    ``dispatch.set_autodispatch`` so the kill-switch reads predictably
    from any other inspection path (e.g. an ad-hoc sqlite query or a
    new helper that joins the table).
    """
    client.post(
        "/api/v1/kanban/prompt-injector",
        json={"project_key": "PROJ", "enabled": True},
    )

    async def _read():
        async with KanbanSessionLocal() as s:
            return (await s.execute(
                __import__("sqlalchemy").select(KanbanMeta).where(
                    KanbanMeta.key == "prompt_injector:PROJ",
                )
            )).scalar_one_or_none()

    import asyncio
    row = asyncio.run(_read())
    assert row is not None
    assert row.value == "1"


def test_post_prompt_injector_requires_project_key(client):
    """Pydantic rejects the call when ``project_key`` is missing — the
    contract is explicit so a UI that forgets to send the project key
    fails fast instead of silently writing to a wrong project.
    """
    r = client.post(
        "/api/v1/kanban/prompt-injector",
        json={"enabled": True},
    )
    assert r.status_code == 422, r.text


# --- Column flag round-trip -----------------------------------------------


def test_column_patch_persists_caveman_and_ponytail_flags(client):
    """PATCH ``/columns/{id}`` with the two new flags must persist them
    and the GET response must reflect them. This is the operator's
    Save-path in the dialog — a regression that drops the fields on the
    floor would silently break the kill-switch-via-flag interaction.
    """
    # Seed a column via direct DB write (the dialog's create path).
    import uuid

    from app.kanban.models import KanbanColumn
    col_id = f"col-{uuid.uuid4().hex}"
    async def _seed():
        from app.kanban.db import KanbanSessionLocal
        async with KanbanSessionLocal() as s:
            s.add(KanbanColumn(
                id=col_id, project_key="PROJ", name="engineer", rank="0000",
                caveman_enabled=0, ponytail_enabled=0,
            ))
            await s.commit()
    import asyncio
    asyncio.run(_seed())

    # Flip both on.
    r = client.patch(
        f"/api/v1/kanban/columns/{col_id}",
        json={"caveman_enabled": True, "ponytail_enabled": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["caveman_enabled"] is True
    assert body["ponytail_enabled"] is True

    # Flip caveman back off; ponytail stays on — independent switches.
    r = client.patch(
        f"/api/v1/kanban/columns/{col_id}",
        json={"caveman_enabled": False},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["caveman_enabled"] is False
    assert body["ponytail_enabled"] is True


def test_column_response_default_off_on_fresh_column(client):
    """A freshly created column must report both flags as false. The
    acceptance criterion "never on by default" lives in this contract —
    a regression here would let the operator-less dispatcher fire the
    injector on the first dispatch into a new column.
    """
    import uuid

    from app.kanban.models import KanbanColumn
    col_id = f"col-{uuid.uuid4().hex}"
    async def _seed():
        from app.kanban.db import KanbanSessionLocal
        async with KanbanSessionLocal() as s:
            s.add(KanbanColumn(
                id=col_id, project_key="PROJ", name="researcher", rank="0000",
            ))
            await s.commit()
    import asyncio
    asyncio.run(_seed())

    r = client.patch(
        f"/api/v1/kanban/columns/{col_id}",
        json={"default_agent": "researcher"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["caveman_enabled"] is False
    assert body["ponytail_enabled"] is False
