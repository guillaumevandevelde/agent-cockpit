# Card work-type → agent-routing — analyse & aanbevelingen

> Status: **analyse, geen besluit.** Input voor kanban-kaart "Analyse - Auto dispatch
> analyse - engineer workflow optimalisatie" (2026-07-09). Bouwt voort op
> `kanban-dispatch-spec.md`, `multi-agent-kanban.md` en `kanban-spec.md`.

## De vraag

> "Vandaag weet de auto dispatch de intentie van een kaart niet meteen. Idealiter maken
> we dit heel duidelijk op basis van een soort werklabel. Labels die ik zie: Analyse,
> feature, bug... Analyse zou naar analyst kunnen gaan, features en bugs naar de
> engineer."

Plus een bredere vraag: is de app + agent/skill-configuratie (personas, skills) breed
genoeg aligned met de intentie "software-ontwikkeling zo autonoom mogelijk: analyse,
ontwikkeling, beheer"?

## 1. Huidige stand van zaken (geverifieerd in de code)

### 1a. `labels` bestaat, maar is puur decoratief

`KanbanCard.labels` (`backend/app/kanban/models.py:43`) is een JSON-kolom met vrije
tekst-tags. UI: comma-separated invoerveld in
`frontend/src/features/kanban/components/CardEditDialog.tsx:409-440`, gerenderd als
badges in `CardItem.tsx:18,45`. **Nergens in `backend/app/kanban/dispatch.py` wordt
`labels` gelezen** (bevestigd met grep — nul hits). De labels "Analyse"/"feature"/"bug"
die je op het bord ziet hebben dus vandaag **geen enkel effect** op welke agent een
kaart oppakt. De intuïtie in de vraag klopt precies.

### 1b. Wat wél bepaalt welke persona een kaart draait

`_phase_target_agent()` (`dispatch.py:61-85`):

1. Analyst-fase → altijd persona `"analyst"` (vast, alleen actief als
   `analyst_agent_id` gezet is en `analyst_run_id` nog leeg — `resolve_phase()`,
   `dispatch.py:88-107`).
2. Executor-fase → `agent_override` (expliciete override bij dispatch/redispatch) wint,
   anders `card.agent` **als dat matcht met een bestaand `.claude/agents/<naam>.md`
   bestand**, anders kolom-naam-als-persona (`_persona_for_card`, `:352-361` —
   niet-vaste kolommen corresponderen 1-op-1 met agent-bestandsnamen, gesynchroniseerd
   via `sync_agent_columns` in `router.py`), **anders hardcoded fallback `"engineer"`**
   (`dispatch.py:85`).

Concreet: een kaart die in **Backlog** verschijnt zonder dat een mens het `agent`-veld
handmatig zet, wordt altijd door **engineer** opgepakt — ongeacht labels, titel, of
inhoud. Er is vandaag dus geen automatische "intentie-detectie" van welk type werk een
kaart is; er is alleen een **manueel te zetten `card.agent`-veld** (dropdown in
`CardEditDialog.tsx`, met "Auto (= card.agent)"-placeholder) of de twee-fase
`analyst_agent_id`/`executor_agent_id`-mechaniek.

### 1c. De analyst/executor-split bestaat al — maar met een ander doel

`multi-agent-kanban.md` beschrijft precies de analyst→executor flow die de vraag
oproept, **maar met een andere intentie**: het is bedoeld om één grote kaart op te
splitsen in N kind-kaarten die parallel door executors gedraaid worden (`add_plan_attachment`,
max 50 kinderen). Het is **opt-in per kaart** via twee losse dropdowns
("Analyst-agent"/"Executor-agent" in de UI), niet gekoppeld aan een label of aan het
idee "dit is een pure analyse-taak die na het plan gewoon klaar is". Een kaart met
label "Analyse" die **geen** decompositie nodig heeft (bijv. deze kaart zelf — puur
onderzoek + document, geen kind-kaarten) past niet goed in dat model.

### 1d. Er zijn maar twee persona-bestanden — en vervuilde restverwijzingen naar meer

`.claude/agents/` bevat alleen `analyst.md` en `engineer.md`. Maar
`backend/app/api/v1/kanban/router.py:45-50` definieert `_IMPEDIMENT_AGENTS`:

```python
_IMPEDIMENT_AGENTS = {
    "developer": ["analyst", "testing", "code-review"],
    "tester": ["developer", "analyst"],
    "analyst": ["developer"],
    "code-review": ["developer"],
}
```

— met de comment "Mirrors the former card-flow.json `impediment_agents`". Dit
verwijst naar rollen (`developer`, `tester`, `testing`, `code-review`) die **geen van
allen** een bijbehorend `.claude/agents/*.md`-bestand hebben, en de naamgeving is zelfs
intern inconsistent (`"testing"` vs. `"tester"`). Ditzelfde patroon zit in
`backend/app/kanban/mcp_server.py:221` (docstring van `report_impediment`: "tester for
test failures, developer for code issues"). Dit is vestigial config uit een vorig
systeem (`card-flow.json`) die nooit is opgeschoond na de overstap naar de huidige
twee-rollen-realiteit.

### 1e. Geen verplichte human-review-stap in het autonome pad

De originele 6-kolommenmodel uit `kanban-spec.md` (Backlog → **Analysis** → Todo →
Doing → **Review** → Done) is in de huidige implementatie vervangen door een dynamische
set kolommen = vaste kolommen (`Backlog, Impediment, Done, To Resume`) + één kolom per
agent-bestand. Er is dus geen aparte "Review"-kolom meer waar een mens vóór Done nog
zou kijken. In `ship_mode="direct"` (zoals deze kaart) merget de engineer-sessie zelf
rechtstreeks naar `master` en pusht (`dispatch.py` → `_build_ship_instructions`,
CLAUDE.md Git Workflow). Zelf-review door dezelfde sessie + CI (`quality.yml`) is de
enige kwaliteitspoort. Dat kan een bewuste keuze zijn ("zo autonoom mogelijk"), maar is
het waard om expliciet te bevestigen.

### 1f. Wat wél goed aligned is

- **Generieke persona-UI** (`frontend/src/features/agents/` — `AgentList`,
  `AgentEditor`, `AgentWizard`) laat je `.claude/agents/*.md` bestanden beheren zonder
  code te wijzigen — nieuwe rollen toevoegen is al low-friction.
- **Provider vs. persona zijn correct gescheiden** (`_known_provider_ids`,
  `_phase_provider_id` vs. `_phase_target_agent`) — welke CLI/vendor draait is
  losgekoppeld van welke rol-instructies geladen worden. Een routing-op-basis-van-label
  hoeft dit onderscheid niet te breken.
- **Kolom = persona-naam** is een elegant, config-vrij mechanisme: een nieuwe
  `.claude/agents/tester.md` toevoegen geeft er automatisch een kolom bij
  (`sync_agent_columns`). De infrastructuur voor méér dan twee rollen bestaat al.
- **Agent Performance dashboard** (`AgentStat`/`FailureStat` in `schemas.py`) geeft al
  zichtbaarheid op sessie-succes per persona — een basis voor "beheer" van de
  autonomie, ook al voedt het nog niet terug in dispatch-beslissingen.

## 2. Aanbevelingen

Onderstaande is een **voorstel**, geen besluit — zie open vragen in §3 voordat dit
geïmplementeerd wordt.

### A — Voeg een gestructureerd `work_type`-veld toe, los van `labels`

`labels` blijft vrije tekst voor zoeken/filteren; voeg een aparte, eindige keuzelijst
toe (bijv. `work_type: "analysis" | "feature" | "bug" | "chore"`, uitbreidbaar). Reden:
een enum is ondubbelzinnig te mappen naar een persona; vrije labels ("Analyse",
"analyse", "Analysis", ...) zijn dat niet zonder fuzzy matching, en labels kunnen
meerdere tags per kaart bevatten (welke telt dan?).

Mapping (default, overrulebaar via het bestaande `card.agent`-veld voor
uitzonderingen):

| `work_type`  | Persona     |
|---|---|
| `analysis`   | `analyst`   |
| `feature`    | `engineer`  |
| `bug`        | `engineer`  |
| `chore`      | `engineer`  |

Dit dekt precies het voorstel uit de vraag (Analyse → analyst; feature/bug → engineer)
en is triviaal uit te breiden zodra er meer personas bijkomen (§B).

### B — Implementatiepunt: bij aanmaak, niet bij dispatch

Zet `card.agent` automatisch bij het aanmaken van de kaart (in `create_card`/`CardCreate`
handling), zodra `work_type` bekend is én `card.agent` nog leeg is. Voordeel: geen
wijziging nodig in `_phase_target_agent`/`_persona_for_card` — die lezen `card.agent` al
als eerste prioriteit. Nadeel: als een gebruiker `work_type` ná aanmaak wijzigt,
verandert `card.agent` niet automatisch mee (moet dan handmatig, via hetzelfde UI-veld
dat er al is). Alternatief is resolutie bij dispatch-tijd (in `_phase_target_agent`,
vóór de `"engineer"`-fallback) — robuuster tegen latere wijzigingen, maar raakt
dispatch-code die vandaag goed geïsoleerd getest is. **Voorkeur: aanmaak-tijd**, tenzij
je verwacht dat `work_type` vaak wijzigt ná creatie.

### C — Ruim de vestigial `developer`/`tester`/`code-review` restjes op

`_IMPEDIMENT_AGENTS` (router.py) en de docstring in `mcp_server.py:221` verwijzen naar
rollen die niet bestaan. Twee opties, geen voorkeur zonder jouw input (zie §3, vraag 3):
(1) bouw ze echt (een `tester`/`code-review`-persona zou natuurlijk aansluiten bij de
bestaande `code-review`/`security-review`/`verify` skills die al in de superpowers-set
zitten maar door `engineer.md` nooit expliciet aangeroepen worden vóór shippen), of
(2) reduceer de fallback-map tot de twee rollen die vandaag echt bestaan
(`analyst`/`engineer`) tot er een concreet plan is voor meer rollen.

### D — Laat `engineer.md` de bestaande `code-review`/`verify`-skills expliciet aanroepen

`engineer.md` §"Zelfreview" is nu een narratief ("lees je eigen diff kritisch") zonder
een concrete tool-aanroep. Er bestaat al een `/code-review`-skill in dit account met
precies dat doel. Expliciet maken (bijv. "roep de `code-review`-skill aan op effort
`medium` vóór je shipt") maakt zelf-review consistent en toetsbaar in plaats van
persona-tekst die een sessie kan overslaan onder tijdsdruk.

## 3. Open vragen — beslissingen die bij jou liggen

1. **Welke `work_type`-waarden wil je precies?** Is de tabel in §2A voldoende
   (analysis/feature/bug/chore → analyst of engineer), of wil je bug en feature op
   termijn ook laten splitsen (bijv. bug altijd eerst kort geanalyseerd door `analyst`
   voor reproductie/root-cause, dan pas naar `engineer`)?
2. **Vervangt `work_type` het vrije `labels`-veld, of blijven beide naast elkaar
   bestaan?** (Aanbeveling A gaat uit van "beide", met `work_type` puur voor routing.)
3. **`developer`/`tester`/`code-review` in `_IMPEDIMENT_AGENTS`: bouwplan of cruft?**
   Bepaalt of §2C een opschoning of een nieuwe-personas-bouwtaak wordt.
4. **Is het ontbreken van een verplichte human-review-stap vóór Done (bij
   `ship_mode="direct"`) een bewuste keuze,** of wil je alsnog een lichte gate (bijv.
   altijd `ship_mode="pull-request"` voor kaarten met `work_type="bug"` op een
   productie-kritiek project)?
5. **Moet de work_type→persona-mapping zelf configureerbaar zijn per project** (zoals
   `default_agent` nu al per kolom is), of is een hardcoded mapping in dispatch.py
   voldoende zolang er maar twee/drie rollen zijn?

## 4. Scope van dit document

Dit is bewust **alleen analyse + aanbeveling**, geen implementatie: de exacte
taxonomie (§3.1) en of `developer`/`tester` echte rollen worden (§3.3) zijn
ontwerpbeslissingen die de vorm van de implementatie direct bepalen. Zodra deze vragen
beantwoord zijn, is `work_type` (§2A) + aanmaak-tijd-mapping (§2B) een kleine,
geïsoleerde wijziging: één kolom + één mapping-functie + één plek waar `create_card`
'm aanroept.
