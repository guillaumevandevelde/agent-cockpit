# Robuuste, betrouwbare aanpak — bord + agent-dispatch

**Datum:** 2026-06-17
**Status:** ontwerp-afweging ter beslissing (geen herstel van de huidige instantie).
**Scope:** de betrouwbaarheid van het kanban-**bord** (persistentie) en de **agent-dispatch/
transport**. *Niet* in scope: de huidige kapotte server repareren — dit beschrijft een
nieuwe aanpak die de hele klasse fouten structureel wegneemt.

---

## 1. Het datamodel is niet het probleem

`kanban_ops` is een **append-only event-log** en `rematerialize()` herbouwt de kaart-tabellen
volledig uit die log (HLC-ordening, LWW per veld, first-wins claim). Dat is een degelijk,
event-sourced fundament. De onbetrouwbaarheid zit **niet** in het model maar in de
**operationele topologie** eromheen.

## 2. Grondoorzaken van de fragiliteit (waargenomen)

| # | Oorzaak | Gevolg |
|---|---|---|
| **R1** | `kanban_database_url = "sqlite+aiosqlite:///./kanban.db"` — **relatief pad** | "Het bord" splitst in één-DB-per-CWD: main-checkout, elke worktree-backend én de test-runner lossen elk een *ander* `kanban.db` op. Kaarten "verdwijnen" (aangemaakt tegen een DB die de live server niet leest); borden lopen uiteen. |
| **R2** | De op-log is wel bron-van-waarheid, maar leeft in **dezelfde** wegwerp-/relatief-opgeloste SQLite | Event-sourcing levert pas op als de log overleeft wat de projectie níet overleeft. Nu kan een reset/verkeerde-CWD/test de log zélf wissen. |
| **R3** | Langlopende server vs. code op schijf; **geen versie-/health-handshake** | Stale proces geeft stil `-32602` op schrijf-tools terwijl de code correct oogt; niet detecteerbaar, niet fail-loud. |
| **R4** | Dispatch is **claim-then-spawn in tmux**; attentie via screen-scraping/pane-identiteit; cap via bord-scan | Gecrashte agent laat kaart vast in Doing zonder lease/heartbeat om te herclaimen; injectie en status zijn heuristisch. |

> De vorige bevinding (alle MCP-**writes** falen met `-32602`, reads werken) en de verdwenen
> bronkaart zijn symptomen van **R1+R3**, niet losse bugs.

---

## 3. Ontwerprichtingen (trade-offs)

### A — Eén canonieke store op een absoluut, CWD-onafhankelijk pad
Verplaats de bord-DB naar een vaste locatie (bv. `~/.claude-cockpit/kanban.db`, XDG-stijl),
los van elke checkout/worktree. Alle backends + de MCP wijzen naar hetzelfde bestand.
- **Voor:** dood R1 onmiddellijk; één bord ongeacht waar een backend draait; minimale wijziging
  (config + padresolutie); local-first blijft.
- **Tegen:** meerdere processen die één SQLite schrijven → leun op WAL + `busy_timeout` (al
  ingesteld) en aanvaard zeldzame schrijf-contentie. Prima bij lage volumes.

### B — De op-log als heilige ruggengraat; projectie is wegwerpbaar
Borg de append-only log op een pad/engine die **niets** (tests, resets, `create_all`) ooit
dropt — desnoods een aparte append-only JSONL naast de DB. Herbouw de projectie bij boot via
het bestaande `rematerialize()`.
- **Voor:** een gewiste/uiteengelopen projectie heelt zichzelf; tests mogen de projectie vrij
  droppen; sync-replay is hier al op ontworpen.
- **Tegen:** het durability-pad van de log moet écht onaantastbaar zijn (aparte engine, nooit
  in de test-patch); iets meer boot-kost (replay).

### C — Eén bord-eigenaar (single writer)
Niet elke worktree-backend embedt z'n eigen kanban-engine; **exact één** proces bezit het bord
(de primaire `:8000`-backend). Worktree-agents praten er alleen mee via MCP/REST en openen het
DB-bestand nooit zelf. Dit ís al de bedoeling ("alleen de backend bereikt de store").
- **Voor:** elimineert multi-writer én multi-DB volledig; het bord heeft één eigenaar.
- **Tegen:** worktree-backends mogen de kanban-DB niet initialiseren — ze moeten altijd naar
  `:8000` proxyen.

### D — Lease-gebaseerde dispatch met heartbeats
Een claim wordt een **lease met TTL + heartbeat** van de draaiende agent. Sterft de agent, dan
verloopt de lease en keert de kaart terug naar Todo (of "stale") voor herclaim; een reaper ruimt
op. Cap uit actieve leases i.p.v. bord-scan.
- **Voor:** geen permanent vastzittende Doing-kaarten; crash-veilige dispatch; zichtbare
  liveness.
- **Tegen:** vergt een heartbeat-kanaal (MCP-ping of CC-hook) + een reaper-job.

### E — Gestructureerd controlekanaal i.p.v. screen-scraping *(lange termijn)*
De container-richting (rootless podman) met een echt stdio/socket-kanaal + gestructureerde
status, in plaats van blinde tmux `send-keys` en pane-identiteit-heuristiek.
- **Voor:** deterministische injectie, echte exit-codes/status, robuuste attentie — de grootste
  betrouwbaarheidswinst voor de orchestratielaag.
- **Tegen:** grootste inspanning; eigen project-spoor (zie `containerized-sessions`-richting).

---

## 4. Aanbeveling — doelarchitectuur

De goedkoopste, hoogste-hefboom-robuustheid komt van **A + C + B** voor het bord en **D** voor
dispatch; **E** is de lange-termijn-transportupgrade (al een eigen spoor).

1. **Eén canoniek bord, één schrijver.** Absoluut DB-pad (kill R1) én alleen de primaire
   `:8000`-backend bezit het; worktree-agents bereiken het uitsluitend via MCP/REST (kill
   multi-writer/multi-DB). *(A + C)*
2. **Heilige op-log.** Log op een locatie/engine die tests en resets nooit raken; boot doet
   `rematerialize()` zodat de projectie altijd herbouwbaar is. Tests draaien enkel tegen een
   wegwerp-projectie of een volledig aparte test-DB (de conftest-patch moet waterdicht zijn).
   *(B)*
3. **Versie-/health-handshake op de MCP.** De MCP adverteert schema/versie en faalt luid; een
   stale server is detecteerbaar (health/version), geen stil `-32602`. *(neemt R3 weg via
   ontwerp, niet via reparatie)*
4. **Lease-gebaseerde dispatch.** Claims dragen TTL + heartbeat; een reaper herqueue't dode
   agents; cap uit live leases. *(D)*
5. **(Roadmap) gestructureerd container-transport.** *(E)*

### Prioriteit / afhankelijkheden
- **Eerst stap 1** (absoluut pad + single writer): kleinste diff, grootste effect — neemt R1
  en het "verdwijnende kaart"-gedrag meteen weg.
- **Dan stap 2** (heilige log + waterdichte testisolatie): maakt het bord onverwoestbaar door
  test-runs.
- **Dan stap 3** (health/version): maakt stale-server-fouten zichtbaar i.p.v. stil.
- **Dan stap 4** (leases): maakt dispatch crash-veilig.
- **Stap 5** is parallel/later, gekoppeld aan de container-richting.

---

## 4b. Stap 1 — kant-en-klare implementatiekaarten

Project: `git:github.com/guillaumevandevelde/claude-cockpit`, kolom `Todo`. Twee onafhankelijk
shipbare eenheden. *(Konden niet op het bord gezet worden: de `cockpit-kanban` MCP gaf
`-32602` op alle calls — stale-server; zie [[kanban-mcp-writes-fail]]. Staan hier klaar om
1-op-1 als kaart aangemaakt te worden zodra de server gezond is.)*

### Kaart 1A — Canoniek absoluut pad voor de kanban-DB + adoptie bestaand bord
Neemt grondoorzaak **R1** weg; lost het "verdwijnende kaart"-gedrag op. Onafhankelijk shipbaar.

- **Scope** — IN: de kanban-DB op één vaste, CWD-onafhankelijke locatie zodat elke
  backend/worktree/CLI hetzelfde bestand opent; bij eerste boot het bestaande bord adopteren.
  `claude_registry.db` (device-lokaal) blijft buiten scope. OUT: single-writer-guard (1C);
  testisolatie van de op-log (stap 2); wijzigingen aan het event-sourced model.
- **Approach** — `backend/app/config.py`: `kanban_database_url` default van relatief
  `sqlite+aiosqlite:///./kanban.db` naar een absoluut pad in de bestaande app-data-conventie
  (bv. `~/.claude-registry/kanban.db`, zelfde familie als `~/.claude-registry/backups/`),
  via `Path.home()`/`expanduser`; env-override blijft. Dir aanmaken vóór engine-gebruik
  (let op: engine wordt bij import in `app/kanban/db.py` gemaakt — padresolutie moet vóór
  `create_async_engine`). **Adoptie:** bestaat het canonieke bestand nog niet maar wél een
  legacy `./kanban.db`, kopieer die één keer (kopie, niet move; logregel). Geen merge van
  uiteengelopen DBs — main-checkout-DB als bron. Documenteer in CLAUDE.md "Gotchas".
- **Acceptance** — backend vanuit een willekeurige CWD leest/schrijft hetzelfde canonieke
  bestand; een kaart uit worktree-context is zichtbaar via de `:8000`-MCP/REST; eerste boot
  adopteert een legacy-bord met logregel; backend-tests groen (vanuit worktree-backend);
  env-override werkt nog.

### Kaart 1C — Single-writer guard (invariant + startup-check)
Bouwt op 1A. Pragmatische "één schrijver"-garantie i.p.v. een volledige netwerk-proxy (past bij
local-first/laag volume; echte proxy uitgesteld tot er aantoonbare schrijf-contentie is).

- **Scope** — IN: voorkomen dat het bord stil op een afwijkend/relatief pad belandt; bevestigen
  dat WAL het lage-volume multi-writer-geval dekt; zichtbaar maken welk DB-bestand actief is.
  OUT: een RPC/REST-proxy waarbij niet-primaire backends de DB nooit openen; leases/heartbeats
  (stap 4).
- **Approach** — startup-check in `app/kanban/db.py`/`init_kanban_db`: resolved pad relatief of
  ≠ canoniek (1A) → luide WARNING (of fail-fast achter een setting). Expose het **absolute**
  actieve DB-pad via health/diagnostics ("welk bord draai ik?"). Test met twee gelijktijdige
  schrijvers tegen één DB zonder "database is locked" (WAL + `busy_timeout` zijn al gezet).
- **Acceptance** — relatief/niet-canoniek pad → duidelijke WARNING (of weigering) i.p.v. stille
  divergentie; actief absoluut DB-pad opvraagbaar via health/diagnostics; concurrent-writers-test
  slaagt zonder lock-fouten; bestaande backend-tests groen.

---

## 5. Bewust niet doen
- De huidige draaiende instantie patchen of de `-32602` "fixen" — dat is herstel, geen
  betrouwbaarheid; de bovenstaande herarchitectuur maakt die klasse fouten overbodig.
- Het event-sourced model vervangen — het is net de sterkte; we maken de **durability** en
  **topologie** eromheen robuust.
- Een externe DB-server/broker introduceren — botst met local-first; SQLite + WAL + één
  schrijver volstaat voor dit volume.
