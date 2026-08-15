"""De reconciler bouwt een pane-resume terug die een herstart heeft weggegooid.

Dit is de testvorm die ontbrak: maak een belofte, gooi de scheduler weg zoals
een procesherstart dat doet, en toon aan dat de belofte alsnog wordt
geinstalleerd. Zonder deze test is niet te zien dat de jobstore in het geheugen
leeft, want elke afzonderlijke test draait binnen een proces.
"""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.kanban.db import KanbanSessionLocal
from app.kanban.operations import apply_operation
from app.services.scheduling.reconciler import reinstall_pending_pane_resumes

PK = "git:example.com/me/repo"


async def _card_with_pending_resume(*, cwd: str, message: str, reset_at: datetime) -> str:
    async with KanbanSessionLocal() as s:
        cid = await apply_operation(
            s, op_type="create", entity_type="card", project_key=PK, entity_id=None,
            payload={"title": "limiet geraakt", "column": "Doing"},
        )
        await apply_operation(
            s, op_type="update", entity_type="card", project_key=PK,
            entity_id=cid,
            payload={"metadata": {
                "pane_resume_pending": True,
                "pane_resume_fired": False,
                "pane_resume_attempts": 1,
                "pane_resume_reset_at": reset_at.isoformat(),
                "pane_resume_cwd": cwd,
                "pane_resume_message": message,
            }},
        )
        await s.commit()
        return cid


def test_reinstalls_a_pending_resume(monkeypatch):
    """Een kaart met een openstaande belofte levert een nieuwe poging op."""
    seen: list[tuple] = []

    async def _fake_try(cwd, reset_time, message, *, attempts=1):
        seen.append((cwd, reset_time, message, attempts))
        return True

    # Patchen waar de reconciler kijkt: hij importeert try_pane_resume lazy uit
    # app.kanban.dispatch, dus de patch moet op die module staan.
    import app.kanban.dispatch as dispatch_module
    monkeypatch.setattr(dispatch_module, "try_pane_resume", _fake_try)

    reset_at = datetime.now(UTC) + timedelta(minutes=30)
    asyncio.run(_card_with_pending_resume(
        cwd="/home/me/project", message="ga verder", reset_at=reset_at,
    ))

    installed = asyncio.run(reinstall_pending_pane_resumes())

    assert installed == 1, "de belofte werd niet opnieuw geinstalleerd"
    assert len(seen) == 1, seen
    cwd, when, message, attempts = seen[0]
    assert cwd == "/home/me/project"
    assert message == "ga verder"
    assert attempts == 1
    assert when == reset_at


def test_skips_a_resume_that_already_fired(monkeypatch):
    called: list = []

    async def _fake_try(*a, **kw):
        called.append(a)
        return True

    import app.kanban.dispatch as dispatch_module
    monkeypatch.setattr(dispatch_module, "try_pane_resume", _fake_try)

    async def _seed():
        async with KanbanSessionLocal() as s:
            cid = await apply_operation(
                s, op_type="create", entity_type="card", project_key=PK, entity_id=None,
                payload={"title": "al gevuurd", "column": "Doing"},
            )
            await apply_operation(
                s, op_type="update", entity_type="card", project_key=PK,
                entity_id=cid,
                payload={"metadata": {
                    "pane_resume_pending": True,
                    "pane_resume_fired": True,
                    "pane_resume_cwd": "/home/me/p",
                    "pane_resume_reset_at": datetime.now(UTC).isoformat(),
                }},
            )
            await s.commit()

    asyncio.run(_seed())
    assert asyncio.run(reinstall_pending_pane_resumes()) == 0
    assert called == [], "een al gevuurde belofte mag niet opnieuw draaien"


def test_skips_a_row_too_incomplete_to_rebuild(monkeypatch):
    """Een oude rij zonder cwd is niet herbouwbaar; dat mag niet crashen."""
    called: list = []

    async def _fake_try(*a, **kw):
        called.append(a)
        return True

    import app.kanban.dispatch as dispatch_module
    monkeypatch.setattr(dispatch_module, "try_pane_resume", _fake_try)

    async def _seed():
        async with KanbanSessionLocal() as s:
            cid = await apply_operation(
                s, op_type="create", entity_type="card", project_key=PK, entity_id=None,
                payload={"title": "oude vorm", "column": "Doing"},
            )
            await apply_operation(
                s, op_type="update", entity_type="card", project_key=PK,
                entity_id=cid,
                payload={"metadata": {"pane_resume_pending": True, "pane_resume_fired": False}},
            )
            await s.commit()

    asyncio.run(_seed())
    assert asyncio.run(reinstall_pending_pane_resumes()) == 0
    assert called == []


@pytest.mark.parametrize("minutes_ago", [5, 60 * 24])
def test_an_overdue_promise_is_still_rebuilt(monkeypatch, minutes_ago):
    """Een belofte waarvan het moment al voorbij is moet alsnog terugkomen."""
    seen: list = []

    async def _fake_try(cwd, reset_time, message, *, attempts=1):
        seen.append(reset_time)
        return True

    import app.kanban.dispatch as dispatch_module
    monkeypatch.setattr(dispatch_module, "try_pane_resume", _fake_try)

    reset_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    asyncio.run(_card_with_pending_resume(cwd="/x", message="m", reset_at=reset_at))

    assert asyncio.run(reinstall_pending_pane_resumes()) == 1
    assert seen == [reset_at]
