"""Auto-resume-instellingen overleven een herstart.

Het gat: ``set_enabled`` schreef alleen naar een geheugen-dict, dus wie
auto-resume aanzette had hem na een herstart stil weer uit staan — geen
foutmelding, de sessie werd simpelweg nooit hervat.

De test bootst een herstart na door de dicts van de service te legen en de
hydrate-route te draaien, zoals de opstartroutine dat doet.
"""
import asyncio

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.scheduling import auto_resume_store
from app.services.scheduling.auto_resume import auto_resume_service
from app.services.scheduling.reconciler import hydrate_auto_resume

CWD = "/home/me/een-project"


def _simulate_restart() -> None:
    """Gooi de in-geheugen-cache weg, zoals een procesherstart doet."""
    auto_resume_service._enabled.clear()
    auto_resume_service._messages.clear()


def test_setting_survives_a_restart():
    async def _flow():
        await auto_resume_store.save(CWD, enabled=True)
        _simulate_restart()
        assert auto_resume_service.is_enabled(CWD) is False, "opzet klopt niet"
        await hydrate_auto_resume()
        return auto_resume_service.is_enabled(CWD)

    assert asyncio.run(_flow()) is True


def test_message_survives_a_restart():
    async def _flow():
        await auto_resume_store.save(CWD, enabled=True, message="ga verder waar je was")
        _simulate_restart()
        await hydrate_auto_resume()
        return auto_resume_service.get_message(CWD)

    assert asyncio.run(_flow()) == "ga verder waar je was"


def test_hydrate_does_not_overwrite_a_fresher_in_memory_value():
    """Wat deze draai al is gezet, is verser dan de rij."""
    async def _flow():
        await auto_resume_store.save(CWD, enabled=False)
        _simulate_restart()
        auto_resume_service.set_enabled(CWD, True)   # verser
        await hydrate_auto_resume()
        return auto_resume_service.is_enabled(CWD)

    assert asyncio.run(_flow()) is True


def test_the_api_route_persists_the_toggle():
    """De route moet doorschrijven, niet alleen de cache aanraken.

    Lezen en schrijven gaan allebei via de route, met dezelfde sleutel. Let op:
    ``{cwd:path}`` levert het pad ZONDER leidende slash, dus de sleutel die de
    API vastlegt is niet dezelfde string als de ``cwd`` die de session-hook
    doorgeeft. Dat is bestaand routegedrag en staat los van de persistentie die
    deze test bewaakt; zie de notitie in docs/cockpit/architectuur.md.
    """
    async def _flow():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as ac:
            posted = await ac.post(
                f"/api/v1/session-hooks/auto-resume{CWD}", params={"enabled": True},
            )
            _simulate_restart()
            await hydrate_auto_resume()
            read_back = await ac.get(f"/api/v1/session-hooks/auto-resume{CWD}")
        return posted, read_back

    posted, read_back = asyncio.run(_flow())
    assert posted.status_code == 200, posted.text
    assert read_back.status_code == 200, read_back.text
    assert read_back.json()["enabled"] is True, "de route schreef niet door naar de tabel"


def test_save_is_idempotent_and_partial():
    """Twee keer opslaan mag niet dubbel aanmaken; een veld weglaten laat het staan."""
    async def _flow():
        await auto_resume_store.save(CWD, enabled=True, message="eerste")
        await auto_resume_store.save(CWD, enabled=False)      # message ongemoeid
        rows = await auto_resume_store.load_all()
        return [r for r in rows if r[0] == CWD]

    rows = asyncio.run(_flow())
    assert len(rows) == 1, rows
    assert rows[0][1] is False
    assert rows[0][2] == "eerste"
