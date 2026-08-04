"""A card must never sit on a column the board does not render (kaart 4f0677c7…).

The board maps over whatever ``GET /api/v1/kanban/columns`` returns and buckets
cards with ``card.column === column.name``; a card whose column has no
``kanban_columns`` row therefore falls out of every lane and is invisible —
while the toolbar keeps counting it ("Dispatch all (41)" over 18 visible
Backlog cards, because 25 cards sat on a ``To Resume`` column that had no row).

``service.ensure_fixed_columns``, called from the board's own load path, repairs
that: an enabled board (≥1 ``kanban_columns`` row) gets a row for every name in
``schemas.COLUMNS``, idempotently and with no migration system involved. That is
the same invariant ``scripts/check-kanban-conventions.sh`` already asserts — the
script reported this exact board as stale ("missing fixed columns: To Resume")
without anything acting on it.

It cannot invent a policy for *non-fixed* names (an agent column whose row was
deleted, a legacy ``Doing``); the frontend renders those as a flagged
"unconfigured" lane instead (see Board.test.tsx).
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


async def _columns(ac: AsyncClient, project_key: str) -> list[dict]:
    r = await ac.get("/api/v1/kanban/columns", params={"project_key": project_key})
    assert r.status_code == 200, r.text
    return r.json()["columns"]


@pytest.mark.asyncio
async def test_fixed_column_holding_cards_is_backfilled_on_load():
    """The live-board bug: rows for Backlog/Done exist, cards live on
    ``To Resume``, no row for it — the cards render nowhere."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        for i, name in enumerate(("Backlog", "Done")):
            await ac.post("/api/v1/kanban/columns", json={
                "project_key": "P", "name": name, "rank": f"{i:04d}",
            })
        cid = (await ac.post("/api/v1/kanban/cards", json={
            "project_key": "P", "title": "resume me", "column": "To Resume",
            "confirm_new_project": True,
        })).json()["id"]

        names = [c["name"] for c in await _columns(ac, "P")]
        assert "To Resume" in names, (
            "a fixed column holding cards must get a row, otherwise the board "
            f"renders no lane for card {cid}"
        )
        # The lanes the operator already arranged keep their relative order;
        # repaired ones are appended rather than shuffled into the middle.
        assert [n for n in names if n in ("Backlog", "Done")] == ["Backlog", "Done"]
        assert names.index("Backlog") < names.index("To Resume")


@pytest.mark.asyncio
async def test_backfill_is_idempotent_across_loads():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        await ac.post("/api/v1/kanban/columns", json={
            "project_key": "P", "name": "Backlog", "rank": "0000",
        })
        await ac.post("/api/v1/kanban/cards", json={
            "project_key": "P", "title": "resume me", "column": "To Resume",
            "confirm_new_project": True,
        })

        first = [c["name"] for c in await _columns(ac, "P")]
        second = [c["name"] for c in await _columns(ac, "P")]
        third = [c["name"] for c in await _columns(ac, "P")]
        assert first == second == third, "repeated board polls must not stack lanes"
        assert first.count("To Resume") == 1


@pytest.mark.asyncio
async def test_enabled_board_gets_every_fixed_column():
    """The invariant `scripts/check-kanban-conventions.sh` asserts: a project
    with ≥1 column row has a row for every name in COLUMNS. Empty fixed lanes
    are cheap — the board collapses them to a 40px rail."""
    from app.kanban.schemas import COLUMNS

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        await ac.post("/api/v1/kanban/columns", json={
            "project_key": "P", "name": "Backlog", "rank": "0000",
        })

        names = [c["name"] for c in await _columns(ac, "P")]
        assert set(COLUMNS) <= set(names), sorted(set(COLUMNS) - set(names))
        assert len(names) == len(set(names)), f"duplicate lanes: {names}"


@pytest.mark.asyncio
async def test_board_that_was_never_enabled_is_left_alone():
    """No rows at all = never enabled. Opening such a board must not enable it
    as a side effect; ``POST /enable`` stays the deliberate action."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        await ac.post("/api/v1/kanban/cards", json={
            "project_key": "P", "title": "orphan", "column": "To Resume",
            "confirm_new_project": True,
        })
        assert await _columns(ac, "P") == []


@pytest.mark.asyncio
async def test_non_fixed_column_is_not_invented_by_the_backend():
    """An agent column whose row was deleted is not a fixed name, so the
    backend leaves it — the board flags it as an unconfigured lane instead."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        await ac.post("/api/v1/kanban/columns", json={
            "project_key": "P", "name": "Backlog", "rank": "0000",
        })
        await ac.post("/api/v1/kanban/cards", json={
            "project_key": "P", "title": "stranded", "column": "engineer",
            "confirm_new_project": True,
        })

        assert "engineer" not in [c["name"] for c in await _columns(ac, "P")]


@pytest.mark.asyncio
async def test_dispatch_counter_cards_are_all_on_a_rendered_lane():
    """AC2: the toolbar counts unclaimed Backlog + To Resume cards; every one
    of them must be findable in a lane the board actually renders."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        await ac.post("/api/v1/kanban/columns", json={
            "project_key": "P", "name": "Backlog", "rank": "0000",
        })
        for column in ("Backlog", "To Resume", "To Resume"):
            await ac.post("/api/v1/kanban/cards", json={
                "project_key": "P", "title": f"card on {column}", "column": column,
                "confirm_new_project": True,
            })

        lanes = {c["name"] for c in await _columns(ac, "P")}
        cards = (await ac.get("/api/v1/kanban/cards",
                              params={"project_key": "P"})).json()["items"]
        dispatchable = [c for c in cards
                        if c["column"] in ("Backlog", "To Resume")
                        and not c.get("claimed_by")]
        assert len(dispatchable) == 3
        assert all(c["column"] in lanes for c in dispatchable), (
            "the Dispatch-all counter would promise cards that render nowhere"
        )


@pytest.mark.asyncio
async def test_awaiting_subtasks_backfill_lands_before_done():
    """``Awaiting Subtasks`` keeps the placement its own helper defines (just
    before ``Done``, analyse-levenscyclus-decision.md §3) — a lane must not sit
    somewhere else just because the backfill created it rather than the
    parking path."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as ac:
        for i, name in enumerate(("Backlog", "Done")):
            await ac.post("/api/v1/kanban/columns", json={
                "project_key": "P", "name": name, "rank": f"{i:04d}",
            })

        names = [c["name"] for c in await _columns(ac, "P")]
        assert names.index("Awaiting Subtasks") < names.index("Done"), names
