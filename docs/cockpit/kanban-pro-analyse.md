---
title: "Analyse — Kanban Pro (donkruger/Kanban): wat kunnen we overnemen of leren?"
type: analysis
status: active
---

# Analyse — Kanban Pro (donkruger/Kanban): wat kunnen we overnemen of leren?

**Datum:** 2026-07-22
**Status:** Analyse (read-only spike; geen implementatie in deze kaart)
**Trigger:** kanban-kaart `87b99d2d…` "Product analyse - https://github.com/donkruger/Kanban". Gebruiker:
> "Bekijk volgende applicatie en zie wat je er kunt van leren. Dit bord ziet er heel
> gebruiksvriendelijk uit en heeft heel wat features. Het kanban bord is ook binnen onze
> applicatie de kern. Wees kritisch en heb geen voorkeur voor ons applicatie, bekijk dus
> neutraal de beste aanpak."

**Bron:** <https://github.com/donkruger/Kanban> — **proprietary** (`NOASSERTION`, eigen
"Kanban Pro License Agreement"), branch `main`, gemeten 2026-07-22. Plus
<https://goodguyapps.com> (feature-pagina, gemeten 2026-07-22).

---

## TL;DR

- **Premisse getoetst — half waar, en de helft die niet klopt is belangrijk.** "Heel wat
  features" **klopt**: 5 views (Board/List/Calendar/Notes/Gantt), custom fields, reacties,
  bijlagen, ⌘K-zoek, per-ticket terminal. "Zie wat je er kunt van **leren**" via deze repo
  **klopt niet**: het is een **3-bestands DMG-release-repo** (LICENSE, README.md,
  SECURITY.md — 23 KB) van **closed-source** software. Er valt geen regel code te lezen, en
  we kunnen het niet draaien (macOS/Windows-only; wij draaien WSL/Linux). Alles hieronder
  komt uit hun README, hun uitzonderlijk goede `SECURITY.md`, en hun marketingpagina.
- **Laagverschil is de kern.** Kanban Pro is een **mens-first bord dat een agent kan
  hosten** (jij opent een terminal ín een ticket). Cockpit is een **agent-first dispatcher
  waarvan het bord de werkwachtrij is** (de dispatcher spawnt zonder mens). Zelfde woord,
  tegengestelde richting van controle. Hun feature-lijst is dus geen achterstandslijst.
- **Wat we overnemen (gerangschikt):** (1) hun *bord vertelt de waarheid over
  uitvoerbaarheid* — bij ons toont het bord "Ready" voor kaarten die de dispatcher
  structureel nooit oppakt; (2) **data-portabiliteit** — hun boards zijn `.md`+YAML op
  schijf, onze board leeft in een SQLite-DB zonder migratiesysteem, zonder export en
  zónder backup-dekking; (3) hun **`SECURITY.md` als dreigingsmodel** — de hunne benoemt
  expliciet wat het product *niet* beschermt; de onze noemt agent-executie helemaal niet;
  (4) kleine UX-schuld: onze ⌘K-palette vindt een kaart maar kan 'm niet openen.
- **Wat we bewust NIET overnemen:** Gantt/Calendar/Notes-views, custom fields, emoji-reacties
  en pseudo-profielen (mens-team-features; ons "team" zijn agents), en de filesystem-first
  opslag als *primaire* store (onze CRDT-op-log + concurrente multi-sessie-schrijvers vragen
  een DB, niet een map met bestanden).
- **Vervolg:** 4 vervolgkaarten, aangemaakt als kinderen van deze analyse-kaart.

## 1. Wat Kanban Pro feitelijk is (gegronde feiten, staat 2026-07-22)

Kanban Pro is een **proprietary Electron-desktopapp** van GoodGuyApps (Don Kruger),
gratis maar niet open source. De GitHub-repo is uitsluitend een distributiekanaal.

| Meting | Waarde (2026-07-22) | Bron |
|---|---|---|
| Repo-inhoud | **3 bestanden** (`LICENSE`, `README.md`, `SECURITY.md`), 23 KB | `gh api .../git/trees/main?recursive=1` |
| Licentie | Proprietary; *"No source code is licensed by this agreement"* | `LICENSE` §2 |
| Repo aangemaakt | 2026-04-07 → **3,5 maanden oud** | `gh api repos/donkruger/Kanban` |
| Releases | **17** (v1.1.42 … v1.1.51), oudste 2026-04-08 | releases-API |
| Cadans | ~17 releases / 15 weken ≈ **1 release per week** | idem |
| Laatste release | v1.1.51-early-access, 2026-07-20 | idem |
| Stars / forks | 53 / 3 | repo-API |
| Issues | **1 open, 0 gesloten** | issues-API |
| Open PR's | 0 (het is geen samenwerkingsrepo) | pulls-API |
| Downloads (alle assets, alle releases) | **5.477** | releases-API |
| Platforms | macOS (signed + notarized) en Windows (**unsigned**, SmartScreen-waarschuwing) | `README.md` |
| Status | "Early Access"; alleen de laatste release krijgt fixes | `SECURITY.md` |

**Architectuur, zoals zij het zelf documenteren** (`SECURITY.md` — dit is de enige
technisch inhoudelijke bron die er is, en hij is opvallend eerlijk):

- Tickets = **plain Markdown + YAML-frontmatter** op schijf; bordtopologie in
  `.kanban/board.json`; historie in een **append-only `.kanban/audit.ndjson`**;
  collaboratieve locks in `.kanban/locks/`.
- Geen server, geen account, geen cloud-DB, **geen telemetrie**. Synchronisatie is jouw
  iCloud/Dropbox/Git-map; "Kanban Pro is not in that loop".
- Gehardende Electron-shell: `contextIsolation: true`, `nodeIntegration: false`,
  `sandbox: true`, strikte CSP (`connect-src 'self'`), alle privileged operaties via een
  begrensde `contextBridge`-preload-API.
- **PTY in het main-process**, niet in de renderer. Geïnstalleerde AI-CLI's worden
  gedetecteerd en gestart als *literal binary + argument-array* (geen shell-interpolatie).
- **Zij roepen zélf nooit een LLM aan** en bewaren geen API-keys. Secret-achtige env-vars
  worden geredacteerd vóór de terminal-resume-state naar schijf gaat.
- Auto-gegenereerde agent-contextbestanden. *(Feitelijke inconsistentie in hun eigen
  documentatie: `SECURITY.md` noemt `CLAUDE.md` / `AGENTS.md` / `MAPPING.md`, de website
  noemt één bestand `KP-CONTEXT.md`. Ongeverifieerd welke de shipping-vorm is.)*

**Featurelijst** (website, 2026-07-22 — vendorclaim, niet geverifieerd, want niet
draaibaar op Linux): Board (⌘1), List (⌘2), Calendar (⌘3), Notes/Zen (⌘4), Gantt met
dependencies (⌘5); ⌘K global search; ⌘⇧C command palette; custom fields (text/number/
date/dropdown/tag); rich comments + emoji-reacties; "Pseudo Profiles" (lokale identiteiten
zonder account); drag-and-drop bijlagen; terminal per ticket met hervatbare sessies;
CLI-support voor Claude Code, Codex, Cursor Agent, aider en Amp. Prijs: gratis, geen
abonnement — met een expliciet voorbehoud in `LICENSE` §4 dat toekomstige versies betaald
kunnen worden.

**Maturiteits-oordeel:** jong (3,5 maanden), zeer hoge cadans (wekelijks), kleine
gebruikersbasis (5.477 downloads totaal), één open issue. Dat is het profiel van een
gepolijst maar nog niet beproefd één-ontwikkelaar-product — **niet** "matuurder dan wij".

## 2. De premisse getoetst

De kaart zegt: *"Dit bord ziet er heel gebruiksvriendelijk uit en heeft heel wat
features."* Twee stukken, twee verschillende uitkomsten.

**Het feature-deel klopt.** Op de as *mens bedient een bord* is Kanban Pro breder dan
Cockpit: wij hebben één view (Board), zij vijf. Wij hebben geen bord-zoek/filter, zij ⌘K.
Wij hebben geen custom fields. Dat is geen misperceptie van de gebruiker — het is waar.

**Het "leren van de repo"-deel klopt niet.** De repo bevat **geen source**. Wie "bekijk
deze applicatie en leer ervan" leest als "lees hun implementatie", loopt tegen een
DMG-distributiekanaal aan. Wat er wél te leren valt zit in hun **`SECURITY.md`** — en dat
blijkt, per ongeluk, de waardevolste vondst van deze analyse te zijn (§4.3).

**Het laagverschil is de eigenlijke analyse.** Beide producten zijn "een kanban-bord met
AI-integratie", maar de richting van controle is tegengesteld:

| | Kanban Pro | Agent Cockpit |
|---|---|---|
| Wie beweegt de kaart? | **De mens**, met de muis | **De agent**, via `mcp__cockpit-kanban__move_card` |
| Wat doet de AI? | Draait in een terminal die jij opent ín een ticket | Wordt **autonoom gespawnd** door de dispatcher (`dispatch.py:3299-3320`) |
| Is een mens nodig? | Ja, altijd — hij is de operator | Nee; auto-dispatch claimt en spawnt zelf |
| Rol van het bord | Werkoppervlak voor een mens | **Werkwachtrij + coördinatiesubstraat** voor N parallelle sessies |
| Concurrency-model | `.kanban/locks/`, één mens per machine | HLC + append-only op-log (`models.py:28-37`), `claimed_by`-claims, dep-DAG |
| Wat is "af"? | De mens sleept naar Done | Poort-geverifieerde `Done`-move met verplichte `summary` + `outcome` (`mcp_server.py`) |

Concreet gevolg: **hun featurelijst is geen achterstandslijst voor ons.** Een Gantt-view
voor een bord waar de volgorde door een dependency-DAG en een dispatcher bepaald wordt,
lost een probleem op dat wij niet hebben. Omgekeerd heeft Kanban Pro niets dat lijkt op
`depends_on`-gates, claim-reaping, worktree-isolatie of een outcome-contract — omdat een
mens die dingen zelf doet.

De vraag die wél overdraagt is niet *"welke views missen wij?"* maar: **"vertelt ons bord
de waarheid over wat er gaat gebeuren?"** Daar staan we slechter voor dan gedacht (§4.1).

## 3. Waar wij staan (met verwijzingen)

Wat Cockpit vandaag heeft op ditzelfde terrein, elk met een verwijzing:

- **Eén view, geen bord-zoek/filter.** `frontend/src/features/kanban/KanbanPage.tsx` rendert
  alleen kolommen; er is geen enkel zoek-/filterinvoerveld in
  `frontend/src/features/kanban/` (geverifieerd met een `placeholder="zoek|search|filter"`-grep,
  0 hits). Bij 21 Backlog-kaarten + 11 Impediment-kaarten (gemeten op dit bord, 2026-07-22)
  is dat al voelbaar.
- **Command palette bestaat wél**, met kaarten erin —
  `frontend/src/features/command-palette/useCommandPaletteData.ts:47-60` haalt alle kaarten
  op — maar `onSelect` doet `navigate('/kanban')` (`:58`), **zonder de kaart-id**, terwijl
  `KanbanPage.tsx:118` een volledig geïmplementeerde `?card=<id>`-deeplink heeft. Je vindt
  de kaart en landt op het bord.
- **Append-only historie hebben we al**, en beter dan zij: `KanbanOp`
  (`backend/app/kanban/models.py:28-37`) is een HLC-gestempelde op-log per entiteit — dus
  hun `.kanban/audit.ndjson` is hier geen leerpunt.
- **Opslag: SQLite, en dat is fragieler dan het lijkt.** `backend/app/kanban/db.py:26`
  (`settings.kanban_database_url`). `CLAUDE.md` documenteert het zelf: *"No database
  migration system — schema changes require deleting the db"*. Er is **geen export**
  (grep op `to_markdown|export.*markdown` in `backend/app/kanban/` + de kanban-router: 0
  hits) en `backend/app/services/backup_service.py` back-upt uitsluitend Claude/Codex-
  **configuratie**bestanden — de kanban-DB komt er niet in voor. Het volledige werkgeheugen
  van het platform hangt dus aan één ongemigreerde, niet-geëxporteerde, niet-geback-upte
  `.db`.
- **Bijlagen** hebben we (`backend/app/kanban/attachments.py`, `KanbanAttachment`).
- **Ready-state-badge per kaart** (`components/ReadyStateBadge.tsx`) met zes toestanden:
  `ready` / `dependent` / `missing_dep` / `in_progress` / `impeded` / `completed` — inclusief
  het onderscheid tussen een levende en een verweesde dep
  (`dangling-depends-on-analyse.md` §1.3/§4). Dat is verder dan Kanban Pro komt.
- **`SECURITY.md`** bestaat (29 regels), met reporting-flow en een "Impact Model" dat
  Cockpit beschrijft als *"a local-only tool … reads/writes your real Claude Code and Codex
  CLI configuration files"*. Het noemt agent-executie, `--dangerously-skip-permissions`
  (`services/agentic_cli/claude_code.py:78-79`) en kaarttekst-als-prompt **niet**.
- **Relevante eerdere beslissingen** (`decisions.md`, gecheckt): geen enkele beslissing raakt
  bord-export, bord-portabiliteit of het dreigingsmodel. Deze analyse heropent dus niets.
  `f8ef71a0…` (`metadata.gated_on` als gate) is wél relevant als *input* voor §4.1.

## 4. Wat we concreet kunnen overnemen (gerangschikt op leverage)

### 4.1 ⭐ Het bord moet de waarheid vertellen over dispatchbaarheid

**Bij hen:** triviaal — het bord ís de staat, en een mens beweegt de kaarten. Er kan geen
kloof zijn tussen "wat het bord toont" en "wat er gebeurt".

**Bij ons is die kloof er wel, en hij is gemeten.** De dispatcher houdt een kaart uit
dispatch op **vier** gronden (`backend/app/kanban/dispatch.py:3299-3301, 3318-3320`):

| Dispatcher-filter | Betekenis | Zichtbaar op het bord? |
|---|---|---|
| `meets_dep_prerequisites` | open `depends_on` | ✅ `dependent` / `missing_dep` |
| `_is_due` (`dispatch.py:3223`) | `scheduled_at` in de toekomst | ✅ eigen badge (`CardItem.tsx:138`) |
| `_awaiting_plan_ref` (`dispatch.py:3239`) | kind-kaart zonder `plan_ref`-deliverable | ❌ **onzichtbaar** |
| `_is_gated` (`dispatch.py:3264`) | `metadata.gated_on` gezet | ❌ **onzichtbaar** |

`KanbanPage.tsx:223-270` berekent de ready-state uitsluitend uit kolom, `claimed_by` en
`depends_on`. Een kind-kaart zonder `plan_ref` en een gegate kaart krijgen daardoor het
**groene `ready`-badge** terwijl de dispatcher ze structureel nooit oppakt. Dat is precies
de faalmodus die al twee keer als "auto-dispatch lijkt te hangen" is gediagnosticeerd, en
waarvoor we een CLI-sweeper hebben geschreven (`scripts/sweep_dangling_plan_refs.py`) die
niemand in de UI ziet.

**Wat het kost:** vrijwel niets. Beide signalen staan **al** in de payload die het bord
binnenkrijgt — `Card.deliverables` (`types.ts:218`) en `Card.metadata` (`types.ts:217`).
Het is puur een ontbrekende berekening plus twee badge-toestanden. Geen backend-wijziging,
geen nieuw endpoint.

**Wat het de product owner oplevert:** "waarom staat dit bord stil?" wordt op het bord zelf
beantwoord in plaats van via een log-dive of een sweeper-script.

✅ Geïmplementeerd (kaart `3b4c3d79…`) — `ReadyStateBadge` draagt nu een `awaiting_plan_ref`
(`Awaiting plan`, amber) en een `gated` (`Gated`, rood, ⚠) toestand; `KanbanPage.tsx` spiegelt
de dispatcher-precedentie (gated > missing_dep > awaiting_plan_ref > dependent > ready) en
de gated-tooltip toont de `gated_on`-trigger via het bestaande `title`-attribuut.

### 4.2 ⭐ Portabiliteit en duurzaamheid van de borddata

**Bij hen** is dit de hele productthese: `.md` + YAML op jouw schijf, *"fully portable,
AI-agent readable, no vendor lock-in"*. Je kunt hun app morgen weggooien en je borden in
`less` lezen.

**Bij ons** hangt alles aan één SQLite-bestand (`kanban/db.py:26`) met — per onze eigen
`CLAUDE.md` — **geen migratiesysteem** ("schema changes require deleting the db"), **geen
export** en **geen backup-dekking** (`backup_service.py` raakt alleen configbestanden). De
Done-kolom van dit bord is het institutionele geheugen van het project: elke `summary`,
elk `outcome`, elke `**Impediment:**`-comment, de hele beslisketen. Eén schema-wijziging
die "even de db weggooien" vraagt, wist dat.

Het punt is *niet* dat we naar markdown-op-schijf moeten migreren (zie §5). Het punt is dat
hun ontwerp een eigenschap heeft die de onze mist: **de data overleeft de applicatie.** De
smalle overname is een export + backup-dekking, niet een opslagmigratie.

**Kost:** een serializer over het bestaande `service.get_card`-oppervlak en een pad-toevoeging
in `backup_service.py`. **Ongemeten schatting** van de omvang; niet gemeten binnen deze spike.

### 4.3 ⭐ Hun `SECURITY.md` is een dreigingsmodel — de onze is een meldadres

Dit is de vondst die het meest reist, juist omdat het het enige inhoudelijke document in
hun repo is. Hun `SECURITY.md` doet vier dingen die de onze niet doet:

1. **Een expliciet vertrouwensmodel**, met "Untrusted data" als eigen categorie —
   *"Project folders you did not create … treat a board from someone else like any other
   untrusted document."*
2. **Een sectie "By design (not defects)"** die vastlegt wat géén bug is: *"The terminal and
   any AI CLI you launch run with your full user privileges. That is the point of a
   terminal."*
3. **Een expliciete lijst van ontbrekende controls** — *"Honest note on controls we do not
   yet have: no Workspace Trust gate, no in-app agent approval prompt, no OS-keychain
   storage. We would rather tell you that plainly than imply protections that aren't
   there."*
4. **In-scope vs. out-of-scope** voor rapporteurs, zodat triage niet elke keer opnieuw
   onderhandeld wordt.

**Waarom dit voor ons scherper telt dan voor hen.** Kanban Pro **waarschuwt de mens** dat hij
onbekende borden moet auditen voordat hij er een agent op loslaat, en adviseert *"keep
external AI agents in their default (human-approval) modes … reserve any auto-approve/YOLO
mode for disposable, isolated environments."* Cockpit **doet precies dat waar zij voor
waarschuwen, geautomatiseerd**: de dispatcher spawnt `claude --dangerously-skip-permissions`
(`claude_code.py:78-79`) met de kaarttekst als prompt, zonder mens in de lus. Dat is een
verdedigbare ontwerpkeuze — worktree-isolatie, security-profielen
(`models/security_profile.py`), `check-kanban-meta-security-conflicts.sh` — maar hij staat
**nergens opgeschreven** als vertrouwensgrens. Onze `SECURITY.md` §"Impact Model" heeft het
alleen over "reads/writes your config files".

Dit wordt acuut zodra de repo publiek gaat: er staan al twee kaarten in Impediment over
publieke zichtbaarheid (`fab0719c…`, `29da4564…`). Een lezer die dan `SECURITY.md` opent,
moet **kaarttekst = prompt = uitvoerbare instructie** daar kunnen vinden, niet ontdekken.

**Kost:** één documentwijziging, nul code. Hoogste ratio van deze analyse.

### 4.4 Kleinere leerpunten (noteren, niet nu bouwen)

- **⌘K vindt maar opent niet.** `useCommandPaletteData.ts:58` gooit de kaart-id weg terwijl
  `KanbanPage.tsx:118` de `?card=`-deeplink al ondersteunt. Eénregelig; wordt kaart 4.
- **Een bord-filter is bij ~30 open kaarten geen luxe meer.** Zij hebben ⌘K + List-view; wij
  hebben nul filterinvoer op het bord.
- **"Pseudo Profiles"** — lokale identiteiten zonder account — is conceptueel exact onze
  `agent:`-claim-prefix en de Agent-Mail-repo-identiteit. Onafhankelijke convergentie op
  hetzelfde idee; niets te doen, wel een bevestiging dat de vorm klopt.
- **Hun agent-contextbestand** (`KP-CONTEXT.md` / `CLAUDE.md`) is de bestandsgebaseerde
  tegenhanger van onze MCP-server: vendor-neutraal by construction, elke CLI kan het lezen.
  Zie §5 waarom we het toch niet overnemen.
- **Hun releasehygiëne**: één "Early Access"-kanaal, expliciet *"older builds are not
  patched"*. Eerlijker dan een impliciete supportbelofte.
- **Windows-installer is unsigned en dat staat gewoon in de README.** Onder-beloven en
  klopmaken is ook een productkeuze.

## 5. Wat we bewust NIET overnemen

**Markdown-op-schijf als *primaire* store.** Verleidelijk (§4.2 leunt erop), maar het botst
frontaal met wat Cockpit ís. Ons bord heeft **N gelijktijdige schrijvers** — meerdere
gedispatchte sessies muteren dezelfde kaarten via MCP terwijl de dispatcher-tick leest — en
dat wordt vandaag opgelost met een HLC-gestempelde op-log (`models.py:28-37`) plus
SQLite-transacties en `busy_timeout` (`db.py:41`). Kanban Pro lost hetzelfde probleem op met
`.kanban/locks/` en de aanname dat er één mens tegelijk werkt. Een map met bestanden zou ons
dwingen tot een eigen locking-/merge-laag voor precies dat wat de DB gratis geeft. Overnemen
doen we de **eigenschap** (data overleeft de app → export), niet het **mechanisme**.

**Gantt-, Calendar- en Notes-views.** Een Gantt visualiseert een tijdlijn met menselijk
geplande duur. Onze volgorde komt uit een dependency-DAG en een dispatcher; "hoe lang duurt
deze kaart" is geen veld dat wij hebben of willen invullen. Calendar zou alleen `scheduled_at`
tonen, dat al een badge heeft (`CardItem.tsx:138`). Notes/Zen dupliceert `docs/cockpit/`, dat
onder git leeft — een tweede documentstore zou juist portabiliteit kosten.

**Custom fields (text/number/date/dropdown/tag).** Ons kaartmodel is bewust een **contract**
tussen dispatcher, persona's en poorten: `work_type` stuurt de persona-routing, `metadata.spec_doc`
voedt de spec-drift-detectie, `metadata.gated_on` is een dispatch-filter, `outcome` wordt door
de Done-poort geverifieerd. Vrij definieerbare velden zijn precies het tegenovergestelde:
betekenis die alleen in het hoofd van de invuller bestaat. Voor een mens-bord is dat
flexibiliteit; voor een machine-gelezen bord is het drift.

**Emoji-reacties, rich comments, real-time notificaties.** Coördinatiefeatures voor een
mens-team. Onze comment-stroom heeft een **prefix-contract** (`**Summary:**` /
`**Impediment:**` / `**Resolution:**`, zie `kanban-conventions.md`) juist omdat agents hem
parsen. Een 👍 heeft daar geen semantiek.

**Een auto-gegenereerd agent-contextbestand per bord.** Aantrekkelijk als
MCP-onafhankelijke fallback, maar de winst is bij ons al binnengehaald: de kaarttekst staat
**letterlijk in de dispatch-prompt** (`_build_ship_instructions` / `build_card_prompt`), en
voor bordmutaties bestaan er al twee paden (MCP + de REST-fallback die in elke
dispatch-prompt gedocumenteerd staat). Een derde, gegenereerd bestand zou een vierde
staat-kopie zijn die kan driften — en gegenereerde bestanden in de werkboom zijn hier al een
bekende merge-pijn (Impediment-kaart `efb8187b…` over `README.md` + `llms.txt`).

**Gehardende Electron / CSP / notarization.** Niet van toepassing: Cockpit is een lokale
web-app zonder renderer-sandbox-grens van dit type.

## 6. Aanbeveling

**Smal en gericht overnemen — vier kaarten, geen van alle een view of een feature-kloon.**

Kanban Pro is niet matuurder dan Cockpit; het is een ander product op een andere laag, dat
één ding structureel beter doet dat wij ons ook kunnen permitteren: **de belofte dat je
data en je grenzen expliciet zijn.** De drie ⭐-items gaan alle drie over die eigenschap —
het bord dat de waarheid vertelt (§4.1), data die de applicatie overleeft (§4.2), grenzen
die opgeschreven staan (§4.3) — plus één opgeruimde UX-schuld (§4.4).

Wat we níet moeten doen is de featurelijst als achterstand behandelen. Vijf views bouwen
voor een bord dat primair door agents gelezen wordt, is werk in de verkeerde richting.

## 7. Vervolgkaarten (in deze sessie aangemaakt)

Alle vier zijn kinderen van deze analyse-kaart en hebben via `add_plan_attachment` een
`plan_ref` gekregen; ze zijn onderling onafhankelijk (`depends_on_graph={}`).

- `3b4c3d79…` — **[feature] Bord toont "Ready" voor kaarten die de dispatcher nooit oppakt —
  `awaiting_plan` en `gated` ontbreken in de ready-state** — sluit de kloof uit §4.1 met twee
  extra badge-toestanden, puur frontend.
- `39d2d54a…` — **[feature] Bord-export + kanban-DB in de backup — borddata moet de
  applicatie overleven** — §4.2: een export-pad (verliesvrij) plus dekking in
  `backup_service.py`.
- `98531194…` — **[chore] `SECURITY.md` uitbreiden met het dreigingsmodel van autonome
  agent-dispatch** — §4.3: vertrouwensgrenzen, "by design (not a defect)", en een eerlijke
  lijst ontbrekende controls, vóór de repo publiek gaat.
- `90b51b5f…` — **[feature] Command palette opent de gevonden kaart + tekst-/labelfilter op
  het bord** — §4.4: `?card=<id>` doorgeven in plaats van `navigate('/kanban')`, plus een
  lichte filter op het bord.

## 8. Bewust buiten scope

- **De applicatie zelf draaien.** Kanban Pro levert alleen macOS- en Windows-builds; deze
  omgeving is WSL/Linux. Alle UX-uitspraken over hún product komen uit hun README, hun
  website en hun `SECURITY.md` — **niet** uit gebruik. De demovideo in hun README is niet
  bekeken (geen video-capaciteit in deze sessie).
- **Hun implementatie.** Closed source; er is geen code om te lezen. Elke architectuur-
  uitspraak in §1 is hun eigen documentatie, niet verificatie.
- **De feitelijke tegenspraak `KP-CONTEXT.md` vs. `CLAUDE.md`/`AGENTS.md`/`MAPPING.md`** is
  gesignaleerd, niet uitgezocht.
- **Kwantificering van §4.2** (hoeveel werk een export/backup is) — geen schatting gedaan;
  bewust ongemeten gelaten in plaats van gegokt.
- **Of onze SQLite-store op termijn moet wijken** — dat is een aparte vraag met een eigen
  spoor (`database-scaling-decision.md`); deze analyse raakt 'm niet.

## 9. Heropenen wanneer?

Niet van toepassing — dit is een leer-analyse, geen go/no-go, en er is geen regel in
`decisions.md` gecreëerd. Wél de moeite van heroverwegen waard als Kanban Pro open source
zou gaan (dan valt er wél implementatie te lezen) of als Cockpit ooit een mens-first
bord-modus zou willen, waarmee hun view-model alsnog relevant wordt.

## 10. Bronnen

- <https://github.com/donkruger/Kanban> — README.md, SECURITY.md, LICENSE; repo-, releases-,
  issues- en pulls-API. Alle cijfers gemeten **2026-07-22**.
- <https://goodguyapps.com> — featurepagina, gemeten **2026-07-22** (vendorclaim).
- Intern: `backend/app/kanban/dispatch.py` (`_is_due` :3223, `_awaiting_plan_ref` :3239,
  `_is_gated` :3264, dispatch-filters :3299-3320) · `backend/app/kanban/models.py:28-37`
  (op-log) · `backend/app/kanban/db.py:26,41` · `backend/app/services/backup_service.py` ·
  `backend/app/services/agentic_cli/claude_code.py:78-79` ·
  `frontend/src/features/kanban/KanbanPage.tsx:118,223-270` ·
  `frontend/src/features/kanban/components/ReadyStateBadge.tsx` ·
  `frontend/src/features/command-palette/useCommandPaletteData.ts:47-60` ·
  `frontend/src/features/kanban/types.ts:217-218` · `SECURITY.md` · `CLAUDE.md` (gotchas) ·
  [`decisions.md`](./decisions.md) · [`dangling-depends-on-analyse.md`](./dangling-depends-on-analyse.md) ·
  [`kanban-conventions.md`](./kanban-conventions.md).
- Zusteranalyses met dezelfde structuur: [`openhands-analyse.md`](./openhands-analyse.md),
  [`jira-lessen-analyse.md`](./jira-lessen-analyse.md),
  [`9router-integratie-analyse.md`](./9router-integratie-analyse.md),
  [`lemma-platform-analyse.md`](./lemma-platform-analyse.md).
