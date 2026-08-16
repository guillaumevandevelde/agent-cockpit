"""Aan/uit-schakelaar voor de zelfverbeteringsloop, per bord.

**Waarom dit bestaat.** De meting van 2026-08-15 over de op-log: van de 855
kaarten in zeven weken was 37% naar binnen gericht, en 77% daarvan ging over de
eigen machinerie. De feature-instroom zakte in dezelfde periode van 39 naar 1
per week, terwijl de verhouding Impediment/Done steeg van 0,09-0,14 naar
0,29-0,47. De loop convergeerde niet; hij zocht zijn eigen brandstof. Zie
``docs/cockpit/cockpit-richting-decision.md`` §2 en §8.

**Twee kanten, en dat is het punt.** Een limiet op dispatch-slots knijpt alleen
de *consumptie* af: de kaarten wachten dan langer, maar ze blijven ontstaan.
Drie skills produceren ze — ``session-retro`` draait aan het einde van élke
gedispatchte sessie, ``flag-problem`` middenin, ``session-problem-scan``
erover. Deze schakelaar raakt daarom allebei:

- **productie**: de dispatch-prompt draagt een expliciete instructie om geen
  retro te draaien en geen ``[self-improve]``-kaarten te filen;
- **consumptie**: de dispatcher slaat bestaande ``[self-improve]``-kaarten over.

Standaard staat de loop **aan**: een bord zonder rij gedraagt zich als vandaag.
Uitzetten is een bewuste handeling, en aanzetten ook.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.kanban.models import KanbanCard, KanbanMeta

META_PREFIX = "self_improve:"

# De marker die een kaart als loop-productie herkenbaar maakt. Titel-prefix is
# het betrouwbare signaal: van de 318 naar binnen gerichte kaarten droeg 199 de
# titelvorm en maar 27 het label.
_TITLE_MARKERS = ("[self-improve]", "[problem]")
_LABEL_MARKERS = ("self-improve", "problem")


def _meta_key(project_key: str) -> str:
    return f"{META_PREFIX}{project_key}"


async def is_enabled(session: AsyncSession, project_key: str) -> bool:
    """Of de zelfverbeteringsloop aan staat voor dit bord.

    Leest ``self_improve:<project_key>`` uit ``KanbanMeta``. Alleen de
    letterlijke string ``"0"`` betekent uit; een ontbrekende rij, ``"1"`` of
    iets anders betekent aan. Fail-open dus: een bord zonder rij, of een
    onleesbare waarde, gedraagt zich zoals het altijd deed.

    Hot path — een omgezette schakelaar werkt bij de volgende dispatch-tick,
    zonder herstart.
    """
    if not project_key:
        return True
    row = (await session.execute(
        select(KanbanMeta).where(KanbanMeta.key == _meta_key(project_key))
    )).scalar_one_or_none()
    if row is None:
        return True
    return row.value != "0"


async def set_enabled(session: AsyncSession, project_key: str, enabled: bool) -> None:
    """Zet de schakelaar. Idempotent; schrijft ``"1"`` of ``"0"``."""
    key = _meta_key(project_key)
    row = (await session.execute(
        select(KanbanMeta).where(KanbanMeta.key == key)
    )).scalar_one_or_none()
    value = "1" if enabled else "0"
    if row is None:
        session.add(KanbanMeta(key=key, value=value))
    else:
        row.value = value


def is_self_improve_card(card: KanbanCard) -> bool:
    """Of deze kaart voortkomt uit de zelfverbeteringsloop.

    Herkent de titelvorm ``[self-improve]`` / ``[problem]`` en dezelfde twee
    labels. Bewust ruim: een kaart die een mens zelf ``[problem]`` noemde telt
    mee, want ook die stroom hoort stil te vallen als het bord de loop uitzet.
    """
    title = (card.title or "").lower()
    if any(marker in title for marker in _TITLE_MARKERS):
        return True
    labels = card.labels or []
    if isinstance(labels, str):
        labels = [labels]
    return any(str(label).lower() in _LABEL_MARKERS for label in labels)


# De regel die in de dispatch-prompt landt zodra de loop uit staat. Expliciet
# en kort: de agent leest hem naast zijn persona en moet er niet omheen kunnen
# redeneren.
DISABLED_PROMPT_BLOCK = """\
## Zelfverbetering staat UIT voor dit bord

Draai geen `session-retro` aan het einde van deze sessie, en file geen
`[self-improve]`- of `[problem]`-kaarten. Zie je onderweg iets dat aandacht
verdient, noem het dan in je afsluitende samenvatting op deze kaart — daar
leest een mens het, en die beslist of er een kaart van komt.

Dit is een bewuste bordinstelling, geen vergissing. Zet hem niet zelf om."""
