"""De aan/uit-schakelaar voor de zelfverbeteringsloop.

Standaard aan: een bord zonder rij moet zich exact gedragen zoals vandaag.
Uitzetten is een bewuste handeling, en dat moet aan beide kanten doorwerken --
zowel de kaarten die de loop produceert als de kaarten die hij consumeert.
"""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.kanban import self_improve
from app.kanban.db import KanbanSessionLocal
from app.kanban.models import KanbanCard
from app.main import app

PK = "git:example.com/me/repo"


def test_default_is_on_for_a_board_without_a_row():
    async def _check():
        async with KanbanSessionLocal() as s:
            return await self_improve.is_enabled(s, PK)

    assert asyncio.run(_check()) is True


def test_switch_round_trips():
    async def _flow():
        async with KanbanSessionLocal() as s:
            await self_improve.set_enabled(s, PK, False)
            await s.commit()
        async with KanbanSessionLocal() as s:
            off = await self_improve.is_enabled(s, PK)
            await self_improve.set_enabled(s, PK, True)
            await s.commit()
        async with KanbanSessionLocal() as s:
            on = await self_improve.is_enabled(s, PK)
        return off, on

    off, on = asyncio.run(_flow())
    assert off is False
    assert on is True


def test_an_unreadable_value_fails_open():
    """Een rare waarde mag de loop niet stilzetten; alleen "0" betekent uit."""
    async def _flow():
        from app.kanban.models import KanbanMeta
        async with KanbanSessionLocal() as s:
            s.add(KanbanMeta(key=f"{self_improve.META_PREFIX}{PK}", value="misschien"))
            await s.commit()
        async with KanbanSessionLocal() as s:
            return await self_improve.is_enabled(s, PK)

    assert asyncio.run(_flow()) is True


@pytest.mark.parametrize("title, expected", [
    ("[self-improve] ship-recipe faalt op een gedeelde box", True),
    ("[problem] quality.yml is rood op master", True),
    ("[feature] Klikbare kaart-verwijzingen", False),
    ("Analyse - scheduled messages", False),
])
def test_recognises_loop_produced_cards_by_title(title, expected):
    assert self_improve.is_self_improve_card(KanbanCard(title=title, labels=[])) is expected


@pytest.mark.parametrize("labels, expected", [
    (["self-improve"], True),
    (["problem", "ci"], True),
    (["frontend"], False),
    ([], False),
])
def test_recognises_loop_produced_cards_by_label(labels, expected):
    card = KanbanCard(title="Een gewone titel", labels=labels)
    assert self_improve.is_self_improve_card(card) is expected


def test_api_reads_and_writes_the_switch():
    async def _flow():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            first = await ac.get("/api/v1/kanban/self-improve", params={"project_key": PK})
            off = await ac.post(
                "/api/v1/kanban/self-improve", json={"project_key": PK, "enabled": False},
            )
            after = await ac.get("/api/v1/kanban/self-improve", params={"project_key": PK})
            return first, off, after

    first, off, after = asyncio.run(_flow())
    assert first.status_code == 200, first.text
    assert first.json()["enabled"] is True, "standaard hoort aan te staan"
    assert off.status_code == 200, off.text
    assert after.json()["enabled"] is False, "de schakelaar werkte niet door"


def test_disabled_prompt_block_names_all_three_producers():
    """De instructie moet de drie skills dekken die de kaarten maken."""
    block = self_improve.DISABLED_PROMPT_BLOCK
    assert "session-retro" in block
    assert "[self-improve]" in block
    assert "[problem]" in block
    # Geen kaart filen, maar wel melden: anders verdwijnt de waarneming alsnog.
    assert "samenvatting" in block
