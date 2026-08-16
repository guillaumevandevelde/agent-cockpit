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


# ---- dispatcher: consumptiekant ------------------------------------------
# Card ff9877ca… — wanneer de loop uit staat pakt een dispatch-tick geen enkele
# `[self-improve]`/`[problem]`-kaart op. Met de schakelaar aan verandert er
# niets (default-gedrag).


class _FakeCard:
    """`_next_card` leest `column`/`claimed_by`/`scheduled_at`/
    `parent_card_id`/`deliverables`; we voegen `title`/`labels` toe omdat
    `is_self_improve_card` die nodig heeft."""

    def __init__(
        self,
        *,
        id="c",
        column="Backlog",
        claimed_by=None,
        scheduled_at=None,
        parent_card_id=None,
        deliverables=None,
        meta=None,
        title="",
        labels=None,
    ):
        self.id = id
        self.column = column
        self.claimed_by = claimed_by
        self.scheduled_at = scheduled_at
        self.parent_card_id = parent_card_id
        self.deliverables = deliverables or []
        self.meta = meta
        self.title = title
        self.labels = labels or []


def _loop_card(id, title="[self-improve] ship-recipe race"):
    return _FakeCard(id=id, title=title, labels=["self-improve"])


def _feature_card(id, title="[feature] iets anders"):
    return _FakeCard(id=id, title=title, labels=["dispatch"])


def test_next_card_picks_self_improve_card_when_loop_on():
    """Default: `skip_self_improve=False` (huidige gedrag) — loop-kaarten
    worden gewoon opgepakt. Bewaakt dat de nieuwe flag geen gedrag breekt."""
    from app.kanban.dispatch import _next_card

    si = _loop_card("si")
    feat = _feature_card("f1")
    picked = _next_card([si, feat])
    assert picked is not None
    assert picked.id in {"si", "f1"}


def test_next_card_skips_self_improve_card_when_flag_on():
    """Met `skip_self_improve=True` filtert `_next_card` de loop-kaarten
    weg; siblings uit de productie-stroom worden wél opgepakt."""
    from app.kanban.dispatch import _next_card

    si = _loop_card("si")
    feat = _feature_card("f1")
    picked = _next_card([si, feat], skip_self_improve=True)
    assert picked is not None
    assert picked.id == "f1"


def test_next_card_returns_none_when_only_self_improve_cards_with_flag_on():
    """Een bord vol met alleen maar loop-kaarten + schakelaar uit: niets
    om op te pakken. Analoog aan hoe `_is_gated` de hele pool leeg kan
    trekken."""
    from app.kanban.dispatch import _next_card

    si1 = _loop_card("si1", title="[self-improve] a")
    si2 = _loop_card("si2", title="[problem] b")
    assert _next_card([si1, si2], skip_self_improve=True) is None


def test_next_card_recognises_problem_label_card_under_flag():
    """Een kaart die alleen via het `problem`-label als loop-kaart telt
    (geen `[…]` in de titel) wordt óók overgeslagen. Verifieert dat we
    `is_self_improve_card`'s herkenning niet hebben ingekort tot de
    titelvorm."""
    from app.kanban.dispatch import _next_card

    labeled = _FakeCard(id="lab", title="incident op acp", labels=["problem"])
    feat = _feature_card("f1")
    picked = _next_card([labeled, feat], skip_self_improve=True)
    assert picked is not None
    assert picked.id == "f1"


@pytest.mark.asyncio
async def test_dispatch_project_skips_self_improve_cards_when_loop_off(monkeypatch):
    """End-to-end: met de loop uit spawnt `dispatch_project` geen enkele
    `[self-improve]`-kaart, ook al zijn er productie-kaarten op hetzelfde
    bord. Defence-in-depth: het signaal loopt via `_next_card` én een
    per-iteratie check, dus één ontbrekend pad is geen groen signaal."""
    from app.kanban import dispatch
    from app.kanban.models import KanbanCard
    from app.kanban.operations import apply_operation
    from tests.kanban_test_db import TestSessionLocal

    spawns = []

    async def fake_run_card(session, **kwargs):
        card = kwargs["card"]
        spawns.append((kwargs["phase"], card.id))
        # Mirror the real ``_run_card`` claim-side-effect on the in-memory
        # card object so the dispatcher's while loop doesn't re-pick it. The
        # real run also persists via apply_operation; the column move alone
        # wouldn't help because the loop keeps the original `cards` list.
        card.claimed_by = f"agent:tmux-{card.id}"
        await apply_operation(
            session, op_type="move", entity_type="card",
            project_key=PK, entity_id=card.id,
            payload={"column": "engineer", "claimed_by": card.claimed_by},
        )
        return {
            "card_id": card.id,
            "session_name": f"tmux-{card.id}",
            "claimant": card.claimed_by,
            "source_column": "Backlog",
            "spawned": True,
        }

    monkeypatch.setattr(dispatch, "_run_card", fake_run_card)

    KanbanSessionLocal = TestSessionLocal()
    PK = "git:dispatcher-self-improve"

    async with KanbanSessionLocal() as s:
        # Schakelaar UIT voor dit bord.
        await self_improve.set_enabled(s, PK, False)
        await s.commit()

        si_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={
                "title": "[self-improve] ship-recipe race",
                "column": "Backlog",
                "labels": ["self-improve"],
            },
        )
        feat_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={
                "title": "[feature] dispatcher-skip implementatie",
                "column": "Backlog",
                "labels": ["dispatch"],
            },
        )
        await s.commit()

        await dispatch.dispatch_project(s, project_key=PK, project_path="/tmp/none")
        await s.commit()

        # Alleen de feature-kaart is gespawned — niet de self-improve.
        assert spawns == [("executor", feat_id)], (
            f"dispatcher spawned {spawns!r}; met de loop uit hoort alleen de "
            f"feature-kaart opgepakt te worden"
        )

        # De self-improve-kaart staat nog onaangeraakt op Backlog.
        si = await s.get(KanbanCard, si_id)
        assert si.column == "Backlog", (
            "self-improve-kaart hoort ongeclaimd op Backlog te blijven "
            "zolang de loop uit staat"
        )

        # De feature-kaart staat op een agent-kolom (dispatch is gelukt).
        feat = await s.get(KanbanCard, feat_id)
        assert feat.column not in {"Backlog", "Done", "Impediment"}, (
            f"feature-kaart hoort geclaimd te zijn in een agent-kolom, "
            f"kreeg column={feat.column!r}"
        )


@pytest.mark.asyncio
async def test_dispatch_project_picks_self_improve_card_when_loop_on(monkeypatch):
    """Tegenhanger: met de loop AAN verandert er niets — een
    `[self-improve]`-kaart wordt gewoon opgepakt. Bewaakt dat we de
    default niet per ongeluk op `skip_self_improve=True` hebben gezet."""
    from app.kanban import dispatch
    from app.kanban.operations import apply_operation
    from tests.kanban_test_db import TestSessionLocal

    spawns = []

    async def fake_run_card(session, **kwargs):
        card = kwargs["card"]
        spawns.append((kwargs["phase"], card.id))
        card.claimed_by = f"agent:tmux-{card.id}"
        await apply_operation(
            session, op_type="move", entity_type="card",
            project_key=PK, entity_id=card.id,
            payload={"column": "engineer", "claimed_by": card.claimed_by},
        )
        return {
            "card_id": card.id,
            "session_name": f"tmux-{card.id}",
            "claimant": card.claimed_by,
            "source_column": "Backlog",
            "spawned": True,
        }

    monkeypatch.setattr(dispatch, "_run_card", fake_run_card)

    KanbanSessionLocal = TestSessionLocal()
    PK = "git:dispatcher-self-improve-on"

    async with KanbanSessionLocal() as s:
        # Default = aan; geen expliciete `set_enabled` nodig.
        si_id = await apply_operation(
            s, op_type="create", entity_type="card",
            project_key=PK, entity_id=None,
            payload={
                "title": "[self-improve] iets voor de loop",
                "column": "Backlog",
                "labels": ["self-improve"],
            },
        )
        await s.commit()

        await dispatch.dispatch_project(s, project_key=PK, project_path="/tmp/none")
        await s.commit()

        assert ("executor", si_id) in spawns, (
            f"loop-kaart hoort opgepakt te worden wanneer de loop aan staat; "
            f"spawns={spawns!r}"
        )
