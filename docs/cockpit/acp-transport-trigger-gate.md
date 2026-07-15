# Trigger-poort: ACP-adaptertransport (§6 kaart 5) — status bij premature dispatch

**Datum:** 2026-07-15
**Status:** **geen actie — trigger niet gevuurd.** De ACP-per-vendor go/no-go is *niet*
geproduceerd, en dat is de juiste uitkomst: de kaart is gedispatcht vóór zijn eigen poort.
**Trigger:** kanban-kaart `a4a091fa3f6b4e209efed6014ac1ee4f` — "[spike][transport][GEPOORT — niet
nu] ACP-adaptertransport als SpawnTransport-sibling" = [`acp-transport-decision.md`](./acp-transport-decision.md)
§6 kaart 5.

**Verwant:** [`acp-transport-decision.md`](./acp-transport-decision.md) (de gepoorte kaart),
[`build-prioriteiten-analyse.md`](./build-prioriteiten-analyse.md) (P3 — de hedge die de poort
moet openen), [`structured-events-schema.md`](./structured-events-schema.md) (het event-model dat
een ACP-transport zou hergebruiken), [`kanban-conventions.md`](./kanban-conventions.md) §1
(`COLUMNS` vs `_DISPATCH_COLUMNS`).

---

## TL;DR

De kaart draagt in zijn eigen titel **"GEPOORT — niet nu"** en in zijn beschrijving *"Niet naar
Todo promoten vóór die trigger"*. Toch is hij op 2026-07-15 autonoom gedispatcht. Twee dingen
volgen daaruit, en ze zijn allebei het opschrijven waard:

1. **De poort staat nog dicht.** Er is geen tweede executor-*CLI* geonboard, dus er is geen ACP-
   adapter om tegen zijn native headless-JSON af te wegen. De spike-vraag is niet beantwoordbaar,
   niet moeilijk — zie §1. Een "go/no-go" nu zou een gok zijn met exact de onderbouwing die
   `acp-transport-decision.md` één dag eerder al bewust weigerde.
2. **De poort was prosa.** Niets in de dispatcher leest "bewust niet nu". De kaart stond in een
   agent-kolom met een `depends_on` die openging, en werd daarmee gewoon dispatch-waardig — zie
   §2. Dit is generiek: **elke** "niet nu"-kaart op dit bord is een dispatch die wacht om te
   gebeuren.

**Dispositie:** de kaart wordt gesloten met uitkomst *geen actie nodig*. De intentie is niet
verloren — ze staat op drie plekken durabel vast (§3). Wat ontbreekt is een parkeerplek; daarvoor
is een aparte kaart gefiled (§4).

---

## 1. Waarom de trigger niet gevuurd is

De poort luidt: *activeren bij tweede-executor-provider-onboarding*. Precies gelezen gaat het om
een tweede **CLI-vendor**, niet om een tweede abonnement — ACP-adapters zijn per CLI, dus alleen
een nieuwe CLI stelt de vraag "adapter of native stream-json?" überhaupt.

Dat onderscheid is hier scherp, want het bord draait al wél op meerdere *providers*:

| As | Status vandaag | Stelt de ACP-vraag? |
|---|---|---|
| **Provider/abonnement** (`anthropic` \| `bedrock` \| `minimax`) | Actief in gebruik — MiniMax draait mee op deze host | **Nee.** MiniMax loopt via de `claude-code`-CLI; het transport blijft Claude's stream-json. Er is geen tweede adapter. |
| **CLI-vendor** (`claude-code` \| `codex-cli` \| `open-code` \| `mimo-code` \| `copilot-cli`) | Alleen `claude-code` dispatcht in de praktijk; `cli_id` defaultet op `"claude-code"` (`dispatch.py:2336-2340`) | **Ja** — maar dit is de as die nog niet bewogen heeft. |

Het *mechanisme* voor een tweede CLI bestaat al: `card.agent` overlaadt persona én CLI-id, en een
kaart met `agent="codex-cli"` spawnt die CLI (`dispatch.py:2331-2340`). Wat ontbreekt is de
**onboarding** — P3 in [`build-prioriteiten-analyse.md`](./build-prioriteiten-analyse.md) vraagt om
*"één tweede provider die een kaart **afrondt** … als proof-of-hedge"*. Die kaart bestaat niet op
het bord, en P3 staat daar nog als toekomstig werk. Er is dus geen geonboarde tweede CLI, en
daarmee geen adapter om te evalueren.

**Wat er wél al klaarligt** voor het moment dat de poort opengaat — kaart 2 is afgerond, dus de
kandidatenlijst is al geklasseerd (`capabilities.py`, `headless_run`):

| CLI | `headless_run` | Native mechanisme |
|---|---|---|
| `claude-code` | supported | `claude -p --output-format stream-json` |
| `codex-cli` | supported | `codex exec --json` |
| `open-code` | supported | `opencode serve` event-API |
| `mimo-code` | unknown | niet geverifieerd |
| `copilot-cli` | unsupported | geen gedocumenteerde headless-modus |

Wanneer de trigger vuurt, is de evaluatie dus gescopet: neem de CLI die geonboard wordt, en weeg
zijn ACP-adapter tegen de native modus in deze tabel. Het ACP-isomorfe event-model uit
[`structured-events-schema.md`](./structured-events-schema.md) staat er al; dat is precies de
hergebruik-belofte van §6 kaart 5.

## 2. Waarom de kaart tóch gedispatcht is

De keten is exact reconstrueerbaar, en er zit geen bug in — elk onderdeel doet wat het hoort te
doen. Het probleem is dat de poort nergens in die keten voorkomt.

1. De kaart is op 2026-07-14 07:48 aangemaakt door een analyst-decompositie, met
   `work_type="analysis"` → `agent="analyst"`, en landt daarmee in de **`analyst`-kolom**.
2. `analyst` staat niet in `COLUMNS` (`schemas.py:21` — `["intake", "Backlog", "Impediment",
   "Done", "To Resume"]`). Daarmee valt de kaart in de **orphan-fallback** van `_next_card`
   (`dispatch.py:2255-2262`): elke onclaimde kaart in een niet-vaste kolom is dispatch-kandidaat.
   Die fallback is er met reden (een gereapte claim mag niet onzichtbaar worden), maar hij maakt
   agent-kolommen effectief tot dispatch-kolommen.
3. De enige rem die de kaart droeg was `depends_on: [0429869e…]` — de capability-kaart. Die is op
   2026-07-14 18:25 naar Done gegaan. Daarmee gaf `meets_dep_prerequisites` groen licht en was er
   niets meer over.
4. Tick pakt de kaart. `dispatch_failures: 1` laat zien dat dit al een keer eerder gebeurde.

De dispatcher kent precies vier remmen, en **geen** ervan kan "wacht op een gebeurtenis die
misschien nooit komt" uitdrukken:

| Rem | Waar | Semantiek | Bruikbaar als poort? |
|---|---|---|---|
| `claimed_by` | `_next_card` | iemand werkt eraan | Nee — tijdelijk, en de reaper heft het op. |
| `_is_due` / `scheduled_at` | `dispatch.py:2192-2205` | *tijd*-poort, fail-open | Nee — de trigger is een gebeurtenis, geen datum. Een verre datum liegt. |
| `_awaiting_plan_ref` | `dispatch.py:2208-2231` | plan-race | Nee — niet het doel. |
| `depends_on` | `dep_resolver.py:11-23` | wacht op kaart X = Done | **Bijna** — zie hieronder. |

`depends_on` is de enige kandidaat met de juiste vorm, en hij **faalt dicht** (een ontbrekende dep
= niet dispatchen, `dep_resolver.py:18-19`) — precies de veilige richting. De valkuil is
circulair: de sentinel waar je op wacht ("onboard een tweede CLI") is zélf een kaart in Backlog,
en die is dus zelf dispatch-waardig. Je verplaatst de premature dispatch dan alleen naar de
sentinel. Een poort bouwen op `depends_on` vereist een sentinel die zelf niet dispatcht — en dát
is exact het gat dat er niet is.

## 3. Waarom de intentie niet verloren gaat als de kaart sluit

De gepoorte intentie staat durabel vast, onafhankelijk van deze kaart:

- [`acp-transport-decision.md`](./acp-transport-decision.md) §6 kaart 5 — de volledige
  opdracht + acceptatiecriteria, met "bewust niet nu" als expliciete conditie.
- [`decisions.md`](./decisions.md) (rij 2026-07-14) — *"ACP-adapter gepoort op
  tweede-provider-onboarding"* in het beslisregister.
- [`build-prioriteiten-analyse.md`](./build-prioriteiten-analyse.md) P3 — de hedge die de trigger
  ís, met OpenHands / Codex / gemeterde `claude` als kandidaten.

Wie een tweede CLI onboardt, komt via P3 en via het register onvermijdelijk bij §6 kaart 5 uit.
Een kaart op het bord voegt daar niets aan toe — behalve een dispatch-risico. Dat is de kern van
de dispositie: **een "doe dit nog niet"-kaart is een contradictie in het huidige model.** Het bord
is een werkvoorraad; alles wat erop staat en niet geblokkeerd is, hoort gedaan te worden. Een
gepoorte intentie hoort in een doc, niet in een kolom.

## 4. Dispositie

- **Deze kaart** (`a4a091fa…`) → Done, uitkomst *geen actie nodig* (trigger niet gevuurd). Niet
  "afgewerkt", niet "afgewezen" — de vraag is nog steeds geldig, alleen niet nu stelbaar.
- **Heropenen** wanneer P3 vuurt: een tweede CLI-vendor rondt een kaart af. Herlees dan §6 kaart 5
  + §1 hierboven (de kandidatentabel is dan je startpunt).
- **Gefiled:** `[problem]`-kaart voor de ontbrekende parkeerplek — trigger-gepoorte kaarten hebben
  geen niet-dispatchbare houdtoestand. Verwant maar niet hetzelfde als de bestaande kaart
  `04f7c427…` (`Awaiting Subtasks`-parkeerkolom): die parkeert een parent op een *interne* conditie
  (kinderen klaar), deze poort wacht op een *externe* gebeurtenis zonder eigenaar op het bord. Een
  oplossing die beide dekt (één niet-dispatchbare houdkolom) is aannemelijk maar niet aangenomen.

## 5. Bewust buiten scope

- **Een ACP-oordeel vellen.** Zonder tweede CLI is elke uitspraak over adapterrijpheid een gok.
  `acp-transport-decision.md` heeft die gok op 2026-07-14 al expliciet geweigerd; hem hier alsnog
  maken zou die beslissing stilletjes terugdraaien.
- **De orphan-fallback in `_next_card` aanpassen.** Die fallback lost een echt probleem op
  (gereapte claims onzichtbaar). Hem hier inperken zou een werkend vangnet slopen om een
  ontbrekende feature te simuleren.
- **`scheduled_at` misbruiken als poort.** Een verre datum verzint een deadline die niemand
  bedoeld heeft, en `_is_due` faalt open: bij een onparseerbare waarde dispatcht de kaart alsnog.
