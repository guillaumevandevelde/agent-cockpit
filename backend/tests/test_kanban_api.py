# backend/tests/test_kanban_api.py
import json

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
async def test_create_list_move_card():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "Build X", "confirm_new_project": True})
        assert r.status_code == 201, r.text
        cid = r.json()["id"]

        r = await ac.get("/api/v1/kanban/cards", params={"project_key": "P"})
        assert any(c["id"] == cid for c in r.json()["items"])

        r = await ac.post(f"/api/v1/kanban/cards/{cid}/move", json={"column": "Doing"})
        assert r.status_code == 200
        assert r.json()["column"] == "Doing"


@pytest.mark.asyncio
async def test_rest_move_card_to_impediment_is_rejected():
    """REST fallback mirror of the MCP gate (kaart b8e3ac8b… decision A).

    The dispatch fallback-instructie in dispatch.py points at this
    REST endpoint when the MCP handshake is broken. Without this gate
    a degraded dispatch path would still produce the 0-button
    Impediment screen, so the policy must be enforced server-side —
    not just in the MCP tool wrapper. The error message names
    `report_impediment` so the agent can self-correct without reading
    docs."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "T", "confirm_new_project": True})
        cid = r.json()["id"]

        r = await ac.post(f"/api/v1/kanban/cards/{cid}/move",
            json={"column": "Impediment"})
        assert r.status_code == 422, r.text
        # Message must steer the agent to the right tool.
        msg = r.text
        assert "report_impediment" in msg, (
            f"422 message must name report_impediment, got: {msg!r}"
        )
        # Card must stay put — the rejected move must not have applied.
        r2 = await ac.get("/api/v1/kanban/cards", params={"project_key": "P"})
        card = next(c for c in r2.json()["items"] if c["id"] == cid)
        assert card["column"] != "Impediment"


# --- REST /cards/{id}/move shares the summary/outcome gate with MCP -------
#
# Bug (kanban card efbb82e6…): the REST mirror of `mcp_server.move_card` did
# no gate at all — a `POST /move` to `Done` without a summary landed silently,
# and the dispatch fallback-instructie in dispatch.py told agents to "follow
# with a `comment` carrying your summary" precisely because the REST move had
# no `summary` field. The fix is one shared gate in `service.py` that both
# callers translate to their own wire shape (dict vs HTTPException 422).
# These tests pin the REST half of that contract. The MCP half stays in
# `tests/test_kanban_mcp.py`; that file already covers the surface and the
# refactor must not regress any of those cases.

@pytest.mark.asyncio
async def test_rest_move_card_to_impediment_without_summary_returns_use_report_impediment():
    """A REST move to Impediment WITHOUT a summary returns
    ``use_report_impediment`` (NOT ``summary_required``), mirroring the
    existing MCP contract (kaart b8e3ac8b… decision A): the Impediment
    gate fires before the summary check, and only ``report_impediment``
    may park a card there. This test pins the precedence explicitly so a
    future reviewer can't misread the AC ("Done of Impediment zonder
    samenvatting → summary_required") as applying to Impediment too —
    the AC says "same shape as MCP", and MCP returns
    ``use_report_impediment`` regardless of summary."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "T", "confirm_new_project": True})
        cid = r.json()["id"]

        # No `summary` field at all.
        r = await ac.post(f"/api/v1/kanban/cards/{cid}/move",
            json={"column": "Impediment"})
        assert r.status_code == 422, r.text
        body = r.json()
        assert body.get("error") == "use_report_impediment", (
            f"Impediment-without-summary must return use_report_impediment, "
            f"got {body!r}"
        )
        assert "report_impediment" in body.get("message", "")

        # Same error code when a summary IS supplied — the Impediment
        # gate fires first, ignoring summary (kaart b8e3ac8b… decision A).
        r = await ac.post(f"/api/v1/kanban/cards/{cid}/move",
            json={"column": "Impediment", "summary": "I am stuck."})
        assert r.status_code == 422, r.text
        assert r.json().get("error") == "use_report_impediment"


@pytest.mark.asyncio
async def test_rest_move_card_to_done_without_summary_is_rejected():
    """`POST /move` to Done without `summary` returns 422 `summary_required`,
    mirrors the MCP error code, and the card stays put."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "T", "confirm_new_project": True})
        cid = r.json()["id"]

        r = await ac.post(f"/api/v1/kanban/cards/{cid}/move",
            json={"column": "Done"})
        assert r.status_code == 422, r.text
        # Error code surface matches MCP — clients see the same shape
        # on the wire (top-level ``error`` key, not nested under
        # ``detail``).
        body = r.json()
        err = body.get("error")
        msg = body.get("message", "")
        assert err == "summary_required", (
            f"422 body must carry top-level error='summary_required', "
            f"got {body!r}"
        )
        assert "summary" in msg.lower(), (
            f"message must mention summary, got: {msg!r}"
        )
        # Card must stay put.
        r2 = await ac.get("/api/v1/kanban/cards", params={"project_key": "P"})
        card = next(c for c in r2.json()["items"] if c["id"] == cid)
        assert card["column"] != "Done"


@pytest.mark.asyncio
async def test_rest_move_card_to_done_with_blank_summary_is_rejected():
    """Blank/whitespace-only summary counts as missing (matches MCP)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "T", "confirm_new_project": True})
        cid = r.json()["id"]

        r = await ac.post(f"/api/v1/kanban/cards/{cid}/move",
            json={"column": "Done", "summary": "   \n  "})
        assert r.status_code == 422, r.text
        body = r.json()
        assert body.get("error") == "summary_required", body


@pytest.mark.asyncio
async def test_rest_move_card_to_done_with_summary_is_allowed_and_posts_comment():
    """Happy path: a Done move with `summary` lands the card in Done AND
    posts a `**Summary:** …` comment to the activity feed. Without the
    comment, the gate fires but the board stays empty — same shape of
    useless card the original bug described."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "T", "confirm_new_project": True})
        cid = r.json()["id"]

        r = await ac.post(f"/api/v1/kanban/cards/{cid}/move",
            json={"column": "Done",
                  "summary": "Implemented the REST summary gate."})
        assert r.status_code == 200, r.text
        assert r.json()["column"] == "Done"

        # The **Summary:** comment must have landed.
        r2 = await ac.get(f"/api/v1/kanban/cards/{cid}/activity")
        assert r2.status_code == 200, r2.text
        ops = r2.json()
        comment_ops = [o for o in ops if o["op_type"] == "comment"]
        assert any(
            "Implemented the REST summary gate." in (o["payload"].get("text") or "")
            and "**Summary:**" in (o["payload"].get("text") or "")
            for o in comment_ops
        ), f"Expected a **Summary:** comment, got ops: {ops!r}"


@pytest.mark.asyncio
async def test_rest_move_card_to_other_columns_does_not_require_summary():
    """Non-terminal moves (Backlog→Doing, etc.) keep working without
    `summary` — the gate fires only on Done/Impediment."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "T", "confirm_new_project": True})
        cid = r.json()["id"]

        r = await ac.post(f"/api/v1/kanban/cards/{cid}/move",
            json={"column": "Doing"})
        assert r.status_code == 200, r.text
        assert r.json()["column"] == "Doing"

        r = await ac.post(f"/api/v1/kanban/cards/{cid}/move",
            json={"column": "To Resume"})
        assert r.status_code == 200, r.text
        assert r.json()["column"] == "To Resume"


@pytest.mark.asyncio
async def test_rest_move_analysis_card_to_done_without_outcome_is_rejected():
    """Analysis cards (work_type='analysis') moving to Done without an
    explicit `outcome` from the closed enum are refused with the same
    `outcome_required` error as MCP. The card stays put."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "Analyse",
                  "work_type": "analysis", "confirm_new_project": True})
        cid = r.json()["id"]

        r = await ac.post(f"/api/v1/kanban/cards/{cid}/move",
            json={"column": "Done", "summary": "analysis done"})
        assert r.status_code == 422, r.text
        body = r.json()
        err = body.get("error")
        msg = body.get("message", "")
        assert err == "outcome_required", (
            f"expected outcome_required, got {r.json()!r}"
        )
        # Same three-way help as MCP — agent can self-correct.
        for value in ("decomposed", "not_feasible", "no_action_needed"):
            assert value in msg, (
                f"{value} missing from message: {msg!r}"
            )
        r2 = await ac.get("/api/v1/kanban/cards", params={"project_key": "P"})
        card = next(c for c in r2.json()["items"] if c["id"] == cid)
        assert card["column"] != "Done"


@pytest.mark.asyncio
async def test_rest_move_analysis_card_to_done_with_invalid_outcome_is_rejected():
    """`outcome` not in the closed enum → 422 `invalid_outcome` with the
    allowed list surfaced, mirroring MCP."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "Analyse",
                  "work_type": "analysis", "confirm_new_project": True})
        cid = r.json()["id"]

        r = await ac.post(f"/api/v1/kanban/cards/{cid}/move",
            json={"column": "Done", "summary": "x", "outcome": "bogus"})
        assert r.status_code == 422, r.text
        body = r.json()
        err = body.get("error")
        allowed = body.get("allowed")
        assert err == "invalid_outcome", (
            f"expected invalid_outcome, got {r.json()!r}"
        )
        assert allowed is not None, "invalid_outcome must carry allowed list"
        for value in ("decomposed", "not_feasible",
                      "no_action_needed", "filed_standalone"):
            assert value in allowed, (
                f"allowed list missing {value}: {allowed!r}"
            )


@pytest.mark.asyncio
async def test_reorder_cards_sets_rank_order():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        ids = []
        for title in ("A", "B", "C"):
            r = await ac.post("/api/v1/kanban/cards",
                json={"project_key": "P", "title": title, "column": "Backlog",
                      "confirm_new_project": True})
            ids.append(r.json()["id"])

        # Reverse the order: C, B, A
        reordered = list(reversed(ids))
        r = await ac.post("/api/v1/kanban/cards/reorder",
            json={"project_key": "P", "column": "Backlog", "ordered_ids": reordered})
        assert r.status_code == 200, r.text

        r = await ac.get("/api/v1/kanban/cards",
            params={"project_key": "P", "column": "Backlog"})
        got = [c["id"] for c in r.json()["items"]]
        assert got == reordered
        ranks = [c["rank"] for c in r.json()["items"]]
        assert ranks == sorted(ranks)


@pytest.mark.asyncio
async def test_reorder_ignores_unknown_ids_and_keeps_column():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        a = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "A", "column": "Backlog",
                  "confirm_new_project": True})).json()["id"]
        b = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "B", "column": "Backlog"})).json()["id"]

        r = await ac.post("/api/v1/kanban/cards/reorder",
            json={"project_key": "P", "column": "Backlog",
                  "ordered_ids": [b, "does-not-exist", a]})
        assert r.status_code == 200, r.text

        r = await ac.get("/api/v1/kanban/cards",
            params={"project_key": "P", "column": "Backlog"})
        items = r.json()["items"]
        assert [c["id"] for c in items] == [b, a]
        assert all(c["column"] == "Backlog" for c in items)


@pytest.mark.asyncio
async def test_claim_conflict_returns_409():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        cid = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "t", "confirm_new_project": True})).json()["id"]
        r1 = await ac.post(f"/api/v1/kanban/cards/{cid}/claim",
            json={"claimed_by": "first@d"})
        assert r1.status_code == 200
        r2 = await ac.post(f"/api/v1/kanban/cards/{cid}/claim",
            json={"claimed_by": "second@d"})
        assert r2.status_code == 409, r2.text


@pytest.mark.asyncio
async def test_enable_writes_mcp_entry(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/enable",
            json={"project_path": str(tmp_path)})
        assert r.status_code == 200, r.text
        assert r.json()["project_key"]
        mcp_file = tmp_path / ".mcp.json"
        assert mcp_file.exists()
        assert "cockpit-kanban" in mcp_file.read_text()


@pytest.mark.asyncio
async def test_enable_mcp_url_derives_from_request(tmp_path):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://example.test") as ac:
        r = await ac.post("/api/v1/kanban/enable",
            json={"project_path": str(tmp_path)})
        assert r.status_code == 200, r.text
    data = json.loads((tmp_path / ".mcp.json").read_text())
    url = data["mcpServers"]["cockpit-kanban"]["url"]
    assert url == "http://example.test/kanban-mcp/sse"


@pytest.mark.asyncio
async def test_enable_mcp_url_honours_public_base_url(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "public_base_url", "https://cockpit.example.com")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/enable",
            json={"project_path": str(tmp_path)})
        assert r.status_code == 200, r.text
    data = json.loads((tmp_path / ".mcp.json").read_text())
    url = data["mcpServers"]["cockpit-kanban"]["url"]
    assert url == "https://cockpit.example.com/kanban-mcp/sse"


@pytest.mark.asyncio
async def test_transport_defaults_worktree_and_roundtrips():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.get("/api/v1/kanban/transport", params={"project_key": "p2"})
        assert r.json()["transport"] == "worktree"
        s = await ac.post("/api/v1/kanban/transport",
                          json={"project_key": "p2", "transport": "sandcastle"})
        assert s.status_code == 200
        g = await ac.get("/api/v1/kanban/transport", params={"project_key": "p2"})
        assert g.json()["transport"] == "sandcastle"


@pytest.mark.asyncio
async def test_transport_rejects_unknown():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/transport",
                          json={"project_key": "p2", "transport": "podman"})
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_delete_card_without_worktree_succeeds():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "no worktree", "confirm_new_project": True})
        cid = r.json()["id"]

        r = await ac.delete(f"/api/v1/kanban/cards/{cid}")
        assert r.status_code == 204

        # The single-card GET is keyed by card_id (not project_key), so it
        # stays valid even after the project's only card was deleted — and
        # the project itself is now unknown to `list_cards` (no cards or
        # columns remain, see test_kanban_unknown_project_key_rest.py). The
        # new REST guard introduced by kanban card adffb537 turns that into
        # a structured 404; we use the single-card endpoint to assert the
        # card is gone without depending on the project-list semantics.
        r = await ac.get(f"/api/v1/kanban/cards/{cid}")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_card_warns_on_unmerged_worktree_then_force_deletes(monkeypatch):

    async def fake_warning(card):
        return {"worktree_path": "/tmp/fake", "branch": "feature",
                "default_branch": "master", "ahead": 2, "dirty": False}

    monkeypatch.setattr(
        "app.kanban.session_cleanup.find_worktree_unmerged_warning", fake_warning
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "P", "title": "has worktree", "confirm_new_project": True})
        cid = r.json()["id"]

        r = await ac.delete(f"/api/v1/kanban/cards/{cid}")
        assert r.status_code == 409
        assert "feature" in r.json()["detail"]
        assert "master" in r.json()["detail"]

        # card must still exist — the warning blocked the delete
        r = await ac.get("/api/v1/kanban/cards", params={"project_key": "P"})
        assert any(c["id"] == cid for c in r.json()["items"])

        r = await ac.delete(f"/api/v1/kanban/cards/{cid}", params={"force": "true"})
        assert r.status_code == 204

        # See test_delete_card_without_worktree_succeeds for why we use the
        # single-card GET here instead of the project-scoped list — the
        # project has no cards left after force-delete.
        r = await ac.get(f"/api/v1/kanban/cards/{cid}")
        assert r.status_code == 404


# ---- Fix B: auto-create analyst column on PATCH ------------------------------
# When a user enables multi-agent workflow by setting analyst_agent_id, the
# "analyst" kanban_columns row must exist so the dispatcher can move the card
# to it AND so the UI renders the column. Otherwise the card lands in a
# phantom column that doesn't show up in the board.


@pytest.mark.asyncio
async def test_patch_card_with_analyst_agent_id_creates_analyst_column():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        # Create a plain card first (no analyst config).
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "FIX-B-PROJ", "title": "Plain", "confirm_new_project": True})
        cid = r.json()["id"]

        # Confirm no "analyst" column exists yet.
        r = await ac.get("/api/v1/kanban/columns",
                          params={"project_key": "FIX-B-PROJ"})
        assert not any(c["name"] == "analyst" for c in r.json()["columns"]), (
            "precondition: no analyst column before PATCH"
        )

        # PATCH sets analyst_agent_id → must auto-create the analyst column.
        r = await ac.patch(f"/api/v1/kanban/cards/{cid}",
            json={"analyst_agent_id": "claude-code"})
        assert r.status_code == 200, r.text

        r = await ac.get("/api/v1/kanban/columns",
                          params={"project_key": "FIX-B-PROJ"})
        assert any(c["name"] == "analyst" for c in r.json()["columns"]), (
            "PATCH with analyst_agent_id must auto-create the analyst column"
        )


@pytest.mark.asyncio
async def test_patch_card_without_analyst_agent_id_does_not_create_column():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "FIX-B-NONE", "title": "Plain", "confirm_new_project": True})
        cid = r.json()["id"]

        # PATCH without analyst_agent_id → no analyst column.
        r = await ac.patch(f"/api/v1/kanban/cards/{cid}",
            json={"priority": "high"})
        assert r.status_code == 200, r.text

        r = await ac.get("/api/v1/kanban/columns",
                          params={"project_key": "FIX-B-NONE"})
        assert not any(c["name"] == "analyst" for c in r.json()["columns"]), (
            "PATCH without analyst_agent_id must NOT create the analyst column"
        )


@pytest.mark.asyncio
async def test_patch_card_idempotent_when_analyst_column_already_exists():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "FIX-B-IDEMP", "title": "Plain", "confirm_new_project": True})
        cid = r.json()["id"]

        # First PATCH creates the column.
        await ac.patch(f"/api/v1/kanban/cards/{cid}",
            json={"analyst_agent_id": "claude-code"})
        r = await ac.get("/api/v1/kanban/columns",
                          params={"project_key": "FIX-B-IDEMP"})
        analyst_cols = [c for c in r.json()["columns"] if c["name"] == "analyst"]
        assert len(analyst_cols) == 1

        # Second PATCH (different field) must NOT create a second column.
        r = await ac.patch(f"/api/v1/kanban/cards/{cid}",
            json={"priority": "high"})
        assert r.status_code == 200

        r = await ac.get("/api/v1/kanban/columns",
                          params={"project_key": "FIX-B-IDEMP"})
        analyst_cols = [c for c in r.json()["columns"] if c["name"] == "analyst"]
        assert len(analyst_cols) == 1, (
            f"second PATCH must not duplicate the analyst column, got "
            f"{len(analyst_cols)} rows"
        )


@pytest.mark.asyncio
async def test_create_card_with_analyst_agent_id_creates_analyst_column():
    """If a future caller / frontend already sends analyst_agent_id on create,
    the column must also be created there."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        # Note: CardCreate schema doesn't currently expose analyst_agent_id;
        # this test verifies the create path is also wired up. If the schema
        # doesn't accept it, the 422 is the expected outcome — flag this as a
        # TODO and verify the PATCH path works (covered above).
        r = await ac.post("/api/v1/kanban/cards",
            json={"project_key": "FIX-B-CREATE", "title": "With analyst",
                  "analyst_agent_id": "claude-code", "confirm_new_project": True})
        # Either 201 with column created, or 422 (schema doesn't allow it yet).
        if r.status_code == 201:
            r = await ac.get("/api/v1/kanban/columns",
                              params={"project_key": "FIX-B-CREATE"})
            assert any(c["name"] == "analyst" for c in r.json()["columns"])


@pytest.mark.asyncio
async def test_list_cards_compact_returns_summary_shape_via_http():
    """`?compact=true` must drop description, deliverables, labels, metadata
    and the op-log-derived enrichments (done_summary, completed_at,
    impediment_status). The remaining fields are exactly the dedupe surface
    flag-problem / session-retro need: id, title, column, work_type, rank.
    Confirms the API contract documented in the self-improve card
    ('compact per-card shape: id, title, column, work_type')."""
    import json as _json

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        # Build a card whose description alone is large enough to make
        # bloat obvious — mirrors a real self-improve card body.
        fat = "lorem ipsum " * 200
        cid = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": "COMPACT", "title": "fat card",
                  "description": fat, "work_type": "bug",
                  "confirm_new_project": True})).json()["id"]

        # Default (full-detail) response: must still include description +
        # deliverables so existing UI callers don't break.
        r_full = await ac.get("/api/v1/kanban/cards",
            params={"project_key": "COMPACT"})
        assert r_full.status_code == 200, r_full.text
        assert len(r_full.json()["items"]) == 1
        full_item = r_full.json()["items"][0]
        assert "description" in full_item
        assert "deliverables" in full_item
        full_size = len(_json.dumps(r_full.json()))

        # Compact response: same status, different shape, much smaller.
        r_compact = await ac.get("/api/v1/kanban/cards",
            params={"project_key": "COMPACT", "compact": "true"})
        assert r_compact.status_code == 200, r_compact.text
        items = r_compact.json()["items"]
        assert len(items) == 1
        item = items[0]
        assert set(item.keys()) == {"id", "title", "column", "work_type", "rank"}, (
            f"compact shape drifted: keys={sorted(item.keys())}"
        )
        assert item["id"] == cid
        assert item["title"] == "fat card"
        assert item["column"] == "Backlog"
        assert item["work_type"] == "bug"
        # 'description' must not be present (or if present, must be empty).
        assert "description" not in item
        assert "deliverables" not in item
        assert "done_summary" not in item
        assert "impediment_status" not in item

        compact_size = len(_json.dumps(r_compact.json()))
        # At minimum the compact payload must be far smaller than the full one.
        assert compact_size * 5 < full_size, (
            f"compact payload ({compact_size}B) not substantially smaller "
            f"than full ({full_size}B)"
        )


@pytest.mark.asyncio
async def test_list_cards_ready_query_param_filters_via_http():
    """The router must forward ?ready=true to the service-layer filter so a
    frontend or planning agent can ask 'what is dispatchable right now?'
    over HTTP without re-implementing the dep walk."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": "READY-P", "title": "parent",
                  "confirm_new_project": True})).json()
        parent_id = parent["id"]
        child = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": "READY-P", "title": "child",
                  "depends_on": [parent_id]})).json()
        child_id = child["id"]

        r = await ac.get("/api/v1/kanban/cards",
            params={"project_key": "READY-P", "ready": "true"})
        assert r.status_code == 200, r.text
        ids = {c["id"] for c in r.json()["items"]}
        assert parent_id in ids
        assert child_id not in ids

        # Without the filter both cards come back.
        r = await ac.get("/api/v1/kanban/cards",
            params={"project_key": "READY-P"})
        assert {c["id"] for c in r.json()["items"]} == {parent_id, child_id}


@pytest.mark.asyncio
async def test_list_cards_blocking_query_param_filters_via_http():
    """The router must forward ?blocking=true to the service-layer filter so
    a planning agent can ask 'which cards are still being waited on?'."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        parent = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": "BLOCK-P", "title": "parent",
                  "confirm_new_project": True})).json()
        parent_id = parent["id"]
        child = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": "BLOCK-P", "title": "child",
                  "depends_on": [parent_id]})).json()
        child_id = child["id"]
        standalone = (await ac.post("/api/v1/kanban/cards",
            json={"project_key": "BLOCK-P", "title": "standalone"})).json()
        standalone_id = standalone["id"]

        r = await ac.get("/api/v1/kanban/cards",
            params={"project_key": "BLOCK-P", "blocking": "true"})
        assert r.status_code == 200, r.text
        ids = {c["id"] for c in r.json()["items"]}
        assert parent_id in ids
        assert child_id not in ids
        assert standalone_id not in ids


@pytest.mark.asyncio
async def test_delete_dispatch_pause_clears_an_active_pause():
    from datetime import UTC, datetime, timedelta

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dispatch_pause import set_paused_until

    async with KanbanSessionLocal() as s:
        await set_paused_until(s, datetime.now(UTC) + timedelta(minutes=10))
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get("/api/v1/kanban/dispatch-pause")
        assert r.json()["paused"] is True

        r = await ac.delete("/api/v1/kanban/dispatch-pause")
        assert r.status_code == 200, r.text
        assert r.json() == {"cleared": True, "was_paused": True}

        r = await ac.get("/api/v1/kanban/dispatch-pause")
        assert r.json()["paused"] is False


@pytest.mark.asyncio
async def test_delete_dispatch_pause_is_idempotent_when_not_paused():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.delete("/api/v1/kanban/dispatch-pause")
        assert r.status_code == 200, r.text
        assert r.json() == {"cleared": False, "was_paused": False}


@pytest.mark.asyncio
async def test_get_dispatch_pause_includes_paused_providers_field():
    """GET /dispatch-pause must always carry a `paused_providers` list so the
    frontend banner can show a per-provider pause without a second endpoint.
    Empty list when nothing is paused -- no per-provider pause and no legacy
    global pause either."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get("/api/v1/kanban/dispatch-pause")
        assert r.status_code == 200, r.text
        body = r.json()
        # Existing fields unchanged so consumers that only read those keep working.
        assert body["paused"] is False
        assert body["paused_until"] is None
        # New field present and an empty list.
        assert body["paused_providers"] == []


@pytest.mark.asyncio
async def test_get_dispatch_pause_lists_active_per_provider_pauses():
    """A per-provider pause must appear under `paused_providers` on GET. A
    legacy global pause alone (no per-provider) leaves the list empty -- the
    field is per-provider-only by design (the global pause has its own
    `paused` flag for consumers that don't care about the split)."""
    from datetime import UTC, datetime, timedelta

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dispatch_pause import set_paused_until

    transport = ASGITransport(app=app)

    # Per-provider pause only.
    async with KanbanSessionLocal() as s:
        await set_paused_until(
            s, datetime.now(UTC) + timedelta(minutes=10), provider="minimax",
        )
        await s.commit()

    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get("/api/v1/kanban/dispatch-pause")
        assert r.status_code == 200, r.text
        body = r.json()
        # Per-provider pause does NOT trip the legacy `paused` flag (its slot
        # is independent -- see dispatch_pause.is_dispatch_paused provider=...).
        assert body["paused"] is False
        assert "minimax" in body["paused_providers"]


@pytest.mark.asyncio
async def test_get_dispatch_pause_paused_providers_only_lists_unexpired_entries():
    """Expired per-provider entries must not show up -- they are stale rows
    the next is_dispatch_paused tick will self-clear; listing them would
    lie to the operator about what is currently frozen."""
    from datetime import UTC, datetime, timedelta

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dispatch_pause import set_paused_until

    future = datetime.now(UTC) + timedelta(minutes=10)
    past = datetime.now(UTC) - timedelta(minutes=1)

    async with KanbanSessionLocal() as s:
        await set_paused_until(s, future, provider="minimax")
        await set_paused_until(s, past, provider="bedrock")
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get("/api/v1/kanban/dispatch-pause")
        body = r.json()
        assert body["paused_providers"] == ["minimax"]


@pytest.mark.asyncio
async def test_delete_dispatch_pause_clears_per_provider_pauses():
    """The operator-override DELETE must wipe every per-provider pause, not
    just the legacy global slot -- a single click should un-freeze the
    whole device, regardless of which providers have per-provider deadlines."""
    from datetime import UTC, datetime, timedelta

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dispatch_pause import (
        is_dispatch_paused,
        list_paused_providers,
        set_paused_until,
    )

    future = datetime.now(UTC) + timedelta(minutes=10)

    async with KanbanSessionLocal() as s:
        # Both legacy and per-provider slots active.
        await set_paused_until(s, future)
        await set_paused_until(s, future, provider="minimax")
        await set_paused_until(s, future, provider="bedrock")
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.delete("/api/v1/kanban/dispatch-pause")
        assert r.status_code == 200, r.text
        # Existing response contract preserved (the global pause was active).
        assert r.json() == {"cleared": True, "was_paused": True}

        # GET now shows no legacy pause and no per-provider pauses.
        r = await ac.get("/api/v1/kanban/dispatch-pause")
        body = r.json()
        assert body["paused"] is False
        assert body["paused_providers"] == []

    # Belt-and-braces: read the slots directly. After the DELETE both must
    # be empty -- the route must not have leaked any pause state behind the
    # legacy-only path.
    async with KanbanSessionLocal() as s:
        assert await is_dispatch_paused(s) is False
        assert await list_paused_providers(s) == []


@pytest.mark.asyncio
async def test_delete_dispatch_pause_clears_only_per_provider_when_no_legacy():
    """DELETE must wipe per-provider pauses even when no legacy global pause
    was active. Otherwise an operator who only sees the per-provider banner
    has no way to un-freeze via the API."""
    from datetime import UTC, datetime, timedelta

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dispatch_pause import (
        list_paused_providers,
        set_paused_until,
    )

    future = datetime.now(UTC) + timedelta(minutes=10)

    async with KanbanSessionLocal() as s:
        await set_paused_until(s, future, provider="minimax")
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.delete("/api/v1/kanban/dispatch-pause")
        assert r.status_code == 200, r.text

    async with KanbanSessionLocal() as s:
        assert await list_paused_providers(s) == []


# ---- manual per-subscription pause (kaart f056b2888a…) --------------------
#
# The endpoint shape mirrors the global /dispatch-pause route (GET reports
# the state, DELETE/clear is the bulk wipe) but adds a per-provider PUT that
# toggles one subscription's manual pause at a time. The PUT body is
# ``{paused: bool}`` so the UI can ship one toggle without first having to
# read the current value.


@pytest.mark.asyncio
async def test_get_dispatch_pause_includes_manually_paused_providers_field():
    """GET /dispatch-pause must surface `manually_paused_providers` (empty list
    by default) so the frontend banner can show the operator toggle state
    distinctly from the auto-tripped time-based pause. Field always present,
    even when nothing is paused -- mirroring the `paused_providers` field's
    always-present contract."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.get("/api/v1/kanban/dispatch-pause")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["paused"] is False
        assert body["paused_until"] is None
        assert body["manually_paused_providers"] == []


@pytest.mark.asyncio
async def test_put_subscription_pause_turns_a_provider_on():
    """PUT /dispatch-pause/subscription/{provider} with {paused: true} adds
    the provider to the manual-pause set, visible on GET and to the
    dispatcher via is_manually_paused."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.put(
            "/api/v1/kanban/dispatch-pause/subscription/anthropic",
            json={"paused": True},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["provider"] == "anthropic"
        assert body["paused"] is True
        assert "anthropic" in body["manually_paused_providers"]

        # GET reflects it.
        r = await ac.get("/api/v1/kanban/dispatch-pause")
        assert "anthropic" in r.json()["manually_paused_providers"]


@pytest.mark.asyncio
async def test_put_subscription_pause_can_be_toggled_off():
    """PUT {paused: false} clears the manual pause for that provider only --
    the operator can flip the toggle back on without a separate DELETE."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dispatch_pause import is_manually_paused

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.put(
            "/api/v1/kanban/dispatch-pause/subscription/bedrock",
            json={"paused": True},
        )
        assert r.status_code == 200, r.text
        assert r.json()["paused"] is True

        r = await ac.put(
            "/api/v1/kanban/dispatch-pause/subscription/bedrock",
            json={"paused": False},
        )
        assert r.status_code == 200, r.text
        assert r.json()["paused"] is False
        assert "bedrock" not in r.json()["manually_paused_providers"]

    async with KanbanSessionLocal() as s:
        assert await is_manually_paused(s, "bedrock") is False


@pytest.mark.asyncio
async def test_put_subscription_pause_rejects_unknown_provider():
    """An unknown provider in the path must surface as 422 — not silently
    land — so the operator sees the rejection at toggle time and the dispatch
    gate never queries an unknown subscription."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.put(
            "/api/v1/kanban/dispatch-pause/subscription/not-a-real-provider",
            json={"paused": True},
        )
        assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_put_subscription_pause_does_not_touch_other_providers():
    """Toggling one provider on/off must not affect the manual state of the
    other providers — independent slots, just like the time-based slots."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        for provider in ("anthropic", "bedrock", "minimax"):
            r = await ac.put(
                f"/api/v1/kanban/dispatch-pause/subscription/{provider}",
                json={"paused": True},
            )
            assert r.status_code == 200, r.text

        # All three show as paused on GET.
        r = await ac.get("/api/v1/kanban/dispatch-pause")
        paused = set(r.json()["manually_paused_providers"])
        assert paused == {"anthropic", "bedrock", "minimax"}

        # Toggle anthropic off — the others stay paused.
        r = await ac.put(
            "/api/v1/kanban/dispatch-pause/subscription/anthropic",
            json={"paused": False},
        )
        assert r.status_code == 200, r.text
        assert r.json()["manually_paused_providers"] == ["bedrock", "minimax"]


@pytest.mark.asyncio
async def test_delete_dispatch_pause_clears_manual_pauses_too():
    """The bulk-clear DELETE /dispatch-pause must wipe manual pauses along
    with the time-based ones — a single 'Resume auto-dispatch now' click
    un-freezes everything, regardless of how the freeze started."""
    from datetime import UTC, datetime, timedelta

    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dispatch_pause import (
        is_manually_paused,
        set_paused_until,
    )

    future = datetime.now(UTC) + timedelta(minutes=10)

    async with KanbanSessionLocal() as s:
        await set_paused_until(s, future, provider="minimax")  # time-based
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        # Add a manual pause alongside the time-based one.
        r = await ac.put(
            "/api/v1/kanban/dispatch-pause/subscription/anthropic",
            json={"paused": True},
        )
        assert r.status_code == 200, r.text
        assert "anthropic" in r.json()["manually_paused_providers"]

        # The bulk clear wipes both kinds.
        r = await ac.delete("/api/v1/kanban/dispatch-pause")
        assert r.status_code == 200, r.text

        r = await ac.get("/api/v1/kanban/dispatch-pause")
        body = r.json()
        assert body["manually_paused_providers"] == []
        assert body["paused_providers"] == []

    async with KanbanSessionLocal() as s:
        assert await is_manually_paused(s, "anthropic") is False


@pytest.mark.asyncio
async def test_delete_dispatch_pause_reports_cleared_true_for_manual_only():
    """DELETE /dispatch-pause must report `cleared=true` whenever ANY pause
    kind was active (manual + time-based + global). FCR-blokkade: the
    previous implementation mirrored `cleared` off the legacy global slot
    only, so a manual-only pause was silently wiped but the response said
    `cleared=false` — the banner treated that as a failure and displayed a
    false error toast. The operator never saw the resume take effect."""
    from app.kanban.db import KanbanSessionLocal
    from app.kanban.dispatch_pause import (
        is_manually_paused,
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        # Set ONLY a manual pause (no global, no time-based).
        r = await ac.put(
            "/api/v1/kanban/dispatch-pause/subscription/anthropic",
            json={"paused": True},
        )
        assert r.status_code == 200, r.text

        # The bulk clear must report cleared=true so the banner refreshes
        # and the operator sees the resume actually happened.
        r = await ac.delete("/api/v1/kanban/dispatch-pause")
        assert r.status_code == 200, r.text
        assert r.json()["cleared"] is True, (
            "manual-only pause should still report cleared=true — the "
            "response must reflect that something was wiped, regardless "
            "of which kind"
        )

    async with KanbanSessionLocal() as s:
        assert await is_manually_paused(s, "anthropic") is False


@pytest.mark.asyncio
async def test_delete_dispatch_pause_reports_cleared_false_when_nothing_was_paused():
    """Inverse of the above: when no pause of any kind is set, the bulk
    clear must report `cleared=false` (nothing to do) so the banner can
    skip the refresh and the operator doesn't see a misleading success."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        r = await ac.delete("/api/v1/kanban/dispatch-pause")
        assert r.status_code == 200, r.text
        assert r.json()["cleared"] is False
