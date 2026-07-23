"""Tests for the public PATCH /api/v1/kanban/columns/{id} contract.

Specifically: the endpoint must distinguish 'field not sent' from 'field set to
null', so a column-update PATCH carrying `max_sessions: null` actually clears
the existing cap (the column-pause UI's ∞ button). The same applies to the
nullable default_agent / default_provider / default_model fields.

The latently-broken shape lived at `service.update_column` doing
`if v is not None: setattr(...)` — every explicit null was silently dropped.
The new shape uses `payload.model_dump(exclude_unset=True)` (matches the rest
of the codebase, e.g. PATCH /cards/{cid}, scheduled_messages PATCH, security
PATCH, project_service.update).
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.kanban_test_db import reset_test_tables


@pytest_asyncio.fixture(autouse=True)
async def _tables():
    await reset_test_tables()
    yield


@pytest.mark.asyncio
async def test_patch_column_can_clear_max_sessions_with_null():
    """`∞` in the UI PATCHes {max_sessions: null} — that null must land."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
            "max_sessions": 2,
        })).json()["id"]
        assert (await ac.get("/api/v1/kanban/columns",
                             params={"project_key": "PROJ"})
                ).json()["columns"][0]["max_sessions"] == 2

        r = await ac.patch(f"/api/v1/kanban/columns/{cid}",
                           json={"max_sessions": None})
        assert r.status_code == 200, r.text
        assert r.json()["max_sessions"] is None

        # And the persisted value (re-GET) is null — not the old cap.
        listing = (await ac.get("/api/v1/kanban/columns",
                                params={"project_key": "PROJ"})
                   ).json()["columns"]
        assert listing[0]["max_sessions"] is None


@pytest.mark.asyncio
async def test_patch_column_can_clear_default_agent_with_null():
    """`null` for default_agent is honoured end-to-end (same exclude_unset gap)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
            "default_agent": "engineer",
        })).json()["id"]
        assert (await ac.get("/api/v1/kanban/columns",
                             params={"project_key": "PROJ"})
                ).json()["columns"][0]["default_agent"] == "engineer"

        r = await ac.patch(f"/api/v1/kanban/columns/{cid}",
                           json={"default_agent": None})
        assert r.status_code == 200, r.text
        assert r.json()["default_agent"] is None


@pytest.mark.asyncio
async def test_patch_column_can_clear_default_provider_with_null():
    """`null` for default_provider is honoured end-to-end."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
            "default_provider": "minimax",
        })).json()["id"]
        assert (await ac.get("/api/v1/kanban/columns",
                             params={"project_key": "PROJ"})
                ).json()["columns"][0]["default_provider"] == "minimax"

        r = await ac.patch(f"/api/v1/kanban/columns/{cid}",
                           json={"default_provider": None})
        assert r.status_code == 200, r.text
        assert r.json()["default_provider"] is None


@pytest.mark.asyncio
async def test_patch_column_can_clear_default_model_with_null():
    """`null` for default_model is honoured end-to-end."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
            "default_model": "opus",
        })).json()["id"]
        assert (await ac.get("/api/v1/kanban/columns",
                             params={"project_key": "PROJ"})
                ).json()["columns"][0]["default_model"] == "opus"

        r = await ac.patch(f"/api/v1/kanban/columns/{cid}",
                           json={"default_model": None})
        assert r.status_code == 200, r.text
        assert r.json()["default_model"] is None


# ---- default_provider validation (card 293d1faa…) ------------------------
#
# Today ``KanbanColumn.default_provider`` is unvalidated free-text —
# a typo (``"anthropc"``) silently loops the card through
# ``MAX_DISPATCH_FAILURES`` before it reaches Impediment. The card
# pins allow-list validation at the service boundary so the operator
# gets a 422 at save time instead.


@pytest.mark.asyncio
async def test_post_column_accepts_known_default_provider():
    """Known providers all pass."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
            "default_provider": "anthropic-compatible",
        })
    # POST /columns is registered with status_code=201 — the column was created.
    assert r.status_code == 201, r.text
    assert r.json()["default_provider"] == "anthropic-compatible"


@pytest.mark.asyncio
async def test_post_column_rejects_unknown_default_provider():
    """Unknown provider strings get a 422 — not a persisted typo."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
            "default_provider": "anthropc",
        })
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_patch_column_rejects_unknown_default_provider():
    """A typo PATCH is rejected the same way — no path past validation."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
        })).json()["id"]
        r = await ac.patch(
            f"/api/v1/kanban/columns/{cid}",
            json={"default_provider": "openai"},
        )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_patch_column_can_clear_default_provider_with_unknown_still_passes_after_validation():
    """A null PATCH still clears the column — distinct from the
    typo-rejection path."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
            "default_provider": "anthropic",
        })).json()["id"]
        r = await ac.patch(
            f"/api/v1/kanban/columns/{cid}",
            json={"default_provider": None},
        )
    assert r.status_code == 200, r.text
    assert r.json()["default_provider"] is None


@pytest.mark.asyncio
async def test_patch_column_omitted_fields_are_left_alone():
    """A PATCH that only mentions max_sessions must not touch default_agent."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
            "default_agent": "engineer",
            "default_model": "opus",
            "max_sessions": 3,
        })).json()["id"]

        # Only change max_sessions.
        r = await ac.patch(f"/api/v1/kanban/columns/{cid}",
                           json={"max_sessions": 7})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["max_sessions"] == 7
        assert body["default_agent"] == "engineer"
        assert body["default_model"] == "opus"


@pytest.mark.asyncio
async def test_patch_column_can_set_pause_via_zero():
    """`0` (Pause) and `null` (∞) are distinct values — both must round-trip.

    The column-pause UI sends `max_sessions: 0` to pause the column. The
    pause is interpreted by the dispatcher as 'no new sessions'; the value
    itself is persisted verbatim. This test guards against a regression where
    `0` is treated like `null` (or vice versa) at the API layer.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
        })).json()["id"]
        # Pause
        r = await ac.patch(f"/api/v1/kanban/columns/{cid}",
                           json={"max_sessions": 0})
        assert r.status_code == 200, r.text
        assert r.json()["max_sessions"] == 0

        # ∞ (clear the cap)
        r = await ac.patch(f"/api/v1/kanban/columns/{cid}",
                           json={"max_sessions": None})
        assert r.status_code == 200, r.text
        assert r.json()["max_sessions"] is None

        # A real cap (still works alongside the null-path)
        r = await ac.patch(f"/api/v1/kanban/columns/{cid}",
                           json={"max_sessions": 4})
        assert r.status_code == 200, r.text
        assert r.json()["max_sessions"] == 4


# --- (provider, model) validation (kaart 1782fa43…, follow-up) -------------
#
# A column's default_provider/default_model must agree: the bug behind the
# "minimax column stuck on opus" report was that the API would happily
# persist `(provider=minimax, model=opus)`. The product-owner decision is
# to refuse such combinations server-side so the inconsistency cannot
# sneak in even when a script (or a regression in the UI) sends it. The
# dispatcher also consults these defaults at spawn time, so the same rule
# prevents a silent fallback to an unrelated Anthropic model.
#
# Validation rule:
#   - effective provider = (patch.default_provider if set else existing)
#   - effective model    = (patch.default_model    if set else existing)
#   - if BOTH are non-null: model must be in the provider's known-options
#     list. Providers without a model-options cache (e.g. bedrock) skip
#     the check; a null provider skips the check.


@pytest.mark.asyncio
async def test_patch_column_rejects_model_unknown_to_minimax():
    """`opus` is a claude-code alias, not a minimax model — the API must
    reject `(provider=minimax, model=opus)` with 422 (kaart 1782fa43…)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
        })).json()["id"]
        r = await ac.patch(f"/api/v1/kanban/columns/{cid}", json={
            "default_provider": "minimax",
            "default_model": "opus",
        })
        assert r.status_code == 422, r.text
        assert "opus" in r.text and "minimax" in r.text


@pytest.mark.asyncio
async def test_patch_column_accepts_model_known_to_minimax():
    """`MiniMax-M3` is the seeded minimax model — save must succeed."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
        })).json()["id"]
        r = await ac.patch(f"/api/v1/kanban/columns/{cid}", json={
            "default_provider": "minimax",
            "default_model": "MiniMax-M3",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["default_provider"] == "minimax"
        assert body["default_model"] == "MiniMax-M3"


@pytest.mark.asyncio
async def test_patch_column_rejects_switching_provider_when_old_model_doesnt_fit():
    """Switching provider to minimax on a column that already has
    `default_model=opus` must be rejected. The frontend now clears the
    model field on provider-change, but a direct PATCH (or a regression)
    must NOT be able to land `(minimax, opus)`."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
            "default_provider": "anthropic",
            "default_model": "opus",
        })).json()["id"]
        r = await ac.patch(f"/api/v1/kanban/columns/{cid}", json={
            "default_provider": "minimax",
        })
        assert r.status_code == 422, r.text
        # The persisted state must be untouched — neither provider nor
        # model flipped silently to a partial invalid combo.
        body = (await ac.get("/api/v1/kanban/columns",
                              params={"project_key": "PROJ"})
                ).json()["columns"][0]
        assert body["default_provider"] == "anthropic"
        assert body["default_model"] == "opus"


@pytest.mark.asyncio
async def test_patch_column_rejects_model_unknown_to_anthropic():
    """`MiniMax-M3` is a minimax-only model — `(anthropic, MiniMax-M3)`
    must be rejected. Symmetric to the minimax guard above."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
        })).json()["id"]
        r = await ac.patch(f"/api/v1/kanban/columns/{cid}", json={
            "default_provider": "anthropic",
            "default_model": "MiniMax-M3",
        })
        assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_patch_column_accepts_null_provider_with_any_model():
    """`provider=null` skips the check — the column is letting the dispatch
    chain pick the provider, so any free-text model is acceptable (the
    chain validates it again at spawn time)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
        })).json()["id"]
        r = await ac.patch(f"/api/v1/kanban/columns/{cid}", json={
            "default_provider": None,
            "default_model": "opus",
        })
        assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_patch_column_accepts_null_model_with_any_provider():
    """A provider with `model=null` is also fine — it's the "set provider,
    let model fall through" combo, used when the column should pin a
    vendor but defer the model choice to the dispatch chain."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
        })).json()["id"]
        r = await ac.patch(f"/api/v1/kanban/columns/{cid}", json={
            "default_provider": "minimax",
            "default_model": None,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["default_provider"] == "minimax"
        assert body["default_model"] is None


@pytest.mark.asyncio
async def test_patch_column_accepts_bedrock_with_any_model():
    """Bedrock has no model-options cache (AWS model ids are ARN-shaped,
    not the bare aliases the cli returns). Free-form is acceptable; the
    CLI rejects unknown models at spawn time anyway."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
        })).json()["id"]
        r = await ac.patch(f"/api/v1/kanban/columns/{cid}", json={
            "default_provider": "bedrock",
            "default_model": "anthropic.claude-3-sonnet-20240229-v1:0",
        })
        assert r.status_code == 200, r.text


# --- shared ValueError → 422 helper (kaart cc113dbc…) ----------------------
#
# The three near-identical ``ValueError → HTTPException(422)`` sites in the
# column API (create_column, update_column, the (provider, model)
# co-validation) were centralised behind ``_column_validation_errors``. This
# test mounts a service call that raises an arbitrary ValueError and asserts
# it is surfaced as a 422 with the original message — one test covering the
# shared conversion for all three sites, independent of any specific
# validation rule.


@pytest.mark.asyncio
async def test_column_op_valueerror_becomes_422(monkeypatch):
    """Any ValueError from a column storage op becomes a 422 via the helper."""
    from app.kanban import service as kanban_service

    sentinel = "storage layer rejected this column (helper sentinel)"

    async def _boom(*args, **kwargs):
        raise ValueError(sentinel)

    # router.py does ``from app.kanban import service`` and calls
    # ``service.create_column`` by attribute — patching the module attribute
    # reaches that binding (test-doubles convention rule 2).
    monkeypatch.setattr(kanban_service, "create_column", _boom)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/columns", json={
            "project_key": "PROJ", "name": "engineer",
        })
    # The 422 + the sentinel message together prove the double fired and its
    # ValueError flowed through the shared conversion.
    assert r.status_code == 422, r.text
    assert sentinel in r.text