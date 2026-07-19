---
title: "Brainstorm-to-impediment-bridge — van real-time dialogue naar `report_impediment`-flows"
type: reference
status: active
---

# Brainstorm-to-impediment-bridge — van real-time dialogue naar `report_impediment`-flows

> Kanban-kaart: **`[design][inceptie] Schrijf brainstorm-to-impediment-bridge.md`**
> (`57a7f9e0f926441f96b193008c8e962d`). Leaf-spike: deze doc *is* de
> deliverable. Bouwt voort op
> [`product-inceptie-pipeline.md`](./product-inceptie-pipeline.md) §7 #7
> (het open gat dat deze doc invult) en
> [`intake-card-routing-analysis.md`](./intake-card-routing-analysis.md)
> (de `intake`-kolom is per definitie mens-werk — zie ook
> [`00-orientation.md`](./00-orientation.md) oriëntatie-punt voor het bredere
> plaatje).
>
> **Design-only.** Niets in deze doc wijzigt een skill of een sessie-code.
> De uitschrijf-plicht geldt de vertaaltabel, het voorbeeld-prompt-fragment,
> de go/no-go-criteria en de economische onderbouwing van *"één impediment
> per sessie"*.

## 1. De vraag in één paragraaf

De `superpowers:brainstorming`-skill
(`~/.claude/plugins/.../superpowers/*/skills/brainstorming/SKILL.md`)
draait als **real-time human-in-the-loop**: één vraag per beurt, visuele
companion in de browser, section-by-section approval, user-review-gate op
de geschreven spec. Een **agent-gedispatchte** intake-sessie
(autonoom draaiend in een tmux-worktree, dispatcher bepaalt het prompt-
frame via `dispatch.build_card_prompt`) heeft **geen** mens aan de
andere kant van de lijn — geen browser-tab die open blijft, geen chat
die per beurt een antwoord geeft. Hoe vertalen we de brainstorm-stappen
dan naar de canonieke menselijke-decisie-flow van het Cockpit-kanban —
`report_impediment(question, options=[...])`
(`backend/app/kanban/mcp_server.py:380`)? Welke stappen zijn 1-op-1
vertaalbaar, welke moeten we overslaan, en — economisch het meest
relevant — hoe voorkomen we dat de sessie zichzelf ophangt in een reeks
impedimenten die elk de sessie beëindigen?

## 2. De asymmetrie die alles bepaalt

`report_impediment` (`backend/app/kanban/mcp_server.py:379-433`) is **geen**
chatvenster. Eén call:

1. plaatst een comment met prefix `**Impediment:** <question>` op de kaart
   (`mcp_server.py:411-416`);
2. verplaatst de kaart naar kolom `Impediment` (`mcp_server.py:411`);
3. opent — indien `options` meegegeven — een `KanbanGate` in status `open`,
   zodat de Cockpit-UI keuze-knoppen kan tonen (`mcp_server.py:423-426`);
4. **geeft de agent-claim vrij** en **beëindigt de sessie** (`mcp_server.py:428-429`).

De vervolg-sessie (na menselijk antwoord) is een **nieuwe** dispatch met
`build_card_prompt` die een `## IMPEDIMENT`-sectie in de prompt injecteert
(`dispatch.py:773-792`); de agent leest daaruit de oorspronkelijke vraag +
het menselijke antwoord (verbatim, treated as authoritative). Er is geen
"wacht tot de mens antwoordt"-modus die de sessie openhoudt — `open_gate`
doet dat wel, maar die wordt in deze context bewust **niet** gebruikt
(`mcp_server.py:437-471`, contrast genoteerd in `mcp_server.py:444-449`:
"this does NOT release the claim or end the session — it simply waits …
use `report_impediment` when you're truly stuck").

Voor een brainstorm-vertaling betekent dit:

- **Elke `report_impediment` doodt de sessie** — geen conversationele loop,
  geen hervatting zonder re-dispatch + context-heropbouw. Het dichtstbijzijnde
  wat we hebben is de `## IMPEDIMENT`-sectie in `dispatch.py:773-792`, die
  pas actief wordt bij de *volgende* dispatch — dat is geen real-time loop.
- **De "vraag" is geen open prompt** — `options` mag een gesloten lijst
  keuzes zijn (A/B/C), en anders is de vraag open tekst die de mens
  zelf moet beantwoorden via een `**Resolution:**`-comment in de UI.
- **Eén impediment per sessie is een harde economische regel**, geen
  stijlvoorkeur. Zie §5.

## 3. Decision-tabel — 9 checklist-stappen + visual companion

De checklist uit `SKILL.md` §*Checklist* (regels 22-32):

1. **Explore project context** — check files, docs, recent commits
2. **Offer the visual companion just-in-time** — eerste keer dat iets
   duidelijker *getoond* dan *verteld* kan worden
3. **Ask clarifying questions** — één tegelijk, purpose/constraints/success
4. **Propose 2-3 approaches** — met trade-offs en aanbeveling
5. **Present design** — in secties, schaal-grootte, na elke sectie approval
6. **Write design doc** — naar `docs/superpowers/specs/<datum>-<topic>-design.md`
7. **Spec self-review** — placeholder/consistency/scope/ambiguity
8. **User reviews written spec** — wachten op user-respons
9. **Transition to implementation** — invoke `writing-plans`

Plus het visual-companion-element
(`SKILL.md` §*Visual Companion*; detail in `visual-companion.md`): een
browser-server die HTML-screens naar `localhost:<port>` serveert en
click-events logt in `$state_dir/events`. Just-in-time aangeboden, niet
front-loaded.

| # | Stap | Strategie | Korte onderbouwing |
|---|---|---|---|
| 1 | Explore project context | **doordraaien** | Pure file-read; geen menselijke gate nodig. |
| 2 | Visual companion (offer + per-question browser-pad) | **niet vertaalbaar** | Browser-tab + click-event-stroom; geen mens aanwezig om te kijken. Zie §4. |
| 3 | Clarifying questions | **inferren + documenteren** (default); **`impediment+options`** (één keer) als scope-materieel | De meeste clarifying-vragen beantwoordt de agent zelf door aannames te documenteren in een *Aannames*-sectie van de spec. Eén vraag die scope-materieel is → `report_impediment(options=[…])` en sessie eindigt (zie §2 + §5). |
| 4 | 2-3 approaches | **doordraaien** | Schrijf de aanpak met aanbeveling in de spec; geen real-time "kies A/B/C"-approval. |
| 5 | Present design (sections) | **doordraaien (full-design, geen per-section approval)** | Vervangen door één full-design-schrijf in stap 6, gevolgd door self-review in stap 7. |
| 6 | Write design doc | **doordraaien** | File-write + git commit; geen gate nodig. |
| 7 | Spec self-review | **doordraaien** | Inline placeholder/consistency/scope/ambiguity-scan; geen mens nodig. |
| 8 | User reviews written spec | **niet vertaalbaar** (real-time approval-loop); alternatief: **doordraaien + eind-impediment** | Real-time "lees de file, geef feedback"-loop is geen `report_impediment`-vorm. Vervangen door een slot-impediment met de spec als bijlage of doordraaien met een sterk-scope-check. Zie §4. |
| 9 | Transition to writing-plans | **doordraaien** | Andere skill binnen dezelfde sessie; geen gate nodig. |

> **De tabel is de bron van waarheid voor de mapping.** De volgende
> secties (4 "niet vertaalbaar", 5 "impediment-economie", 6 "go/no-go")
> onderbouwen rij-voor-rij wat de tabel in het kort zegt. Als de tabel
> en een sectie op een voetnoot botsen, wint de tabel — en wordt de
> sectie in een vervolg-revisie gelijkgetrokken.

## 4. Wat **NIET** door een agent-only sessie kan (en waarom)

Drie brainstorm-elementen vergen real-time menselijke aanwezigheid die
`report_impediment` niet biedt. Voor elk: de onderbouwing in termen van
de mechanismen van `mcp_server.py` + `dispatch.py`.

### 4.1 Visual companion (browser-tab + click-events)

`visual-companion.md` regels 33-71 beschrijft een `start-server.sh`
-dat een HTTP-server op `localhost:<port>` zet, HTML-screens serveert
naar `<project>/.superpowers/brainstorm/<sid>/content/`, en click-
events logt in `<sid>/state/events` (formaat in regels 261-269). De loop
is *twee-kanterrein*: de sessie **schrijft** HTML, leest op de volgende
beurt `$state_dir/events`, en iteratieert tot de mens "tevreden" is.

- **Geen mens aanwezig** in een dispatched tmux-sessie. Een eventueel
  geopende browser-tab wordt nooit bekeken; clicks worden niet
  geregistreerd; de `$state_dir/events` blijft leeg.
- **`report_impediment` is geen surrogaat**: een `options=[...]` op een
  `KanbanGate` toont A/B/C/D-knoppen in de **Cockpit-UI** (`mcp_server.py:394-396`),
  niet in een browser-tab van de gebruiker — twee verschillende UX-kanalen
  met verschillende verwachtingen. Bovendien: de companion ondersteunt
  vrije HTML-mockups (mockup, split view, pros/cons) die de gate-UI
  niet kan renderen.
- **Conclusie:** stap 2 wordt overgeslagen. Vragen die "duidelijker
  getoond dan verteld kunnen worden" worden óf als tekst in de spec
  opgenomen, óf — als ze visueel onmisbaar zijn — maken de intake
  ongeschikt voor agent-only (§6 no-go-criteria).

### 4.2 Section-by-section approval-loop (stap 5)

`SKILL.md` regel 85 zegt: *"Ask after each section whether it looks right
so far"*. Dit is een reeks van N open vragen — "goed zo?", "verder?",
"iets missen?" — die de mens in real-time beantwoordt.

- **`report_impediment` eindigt de sessie bij elke call** (`mcp_server.py:428-429`).
  Je krijgt per sectie-goedkeuring een volledige re-dispatch + context-
  heropbouw. Voor een design met 5 secties = 5 nieuwe sessies
  (elk met eigen worktree, agent-claim-vrijgave, systemd-prompt) —
  dit is geen "approval-loop", dit is een reeks volledige koude starts.
- **Alternatief is eenmalig full-design + self-review.** Dat staat in de
  tabel als "doordraaien" voor stap 5 + 7: geen per-section approval,
  wel een grondige self-review achteraf, en bij bevindingen die de
  scope materieel veranderen alsnog één `report_impediment` (§5).

### 4.3 User reviews written spec (stap 8)

`SKILL.md` regels 122-126: *"ask the user to review the spec file before
proceeding … Wait for the user's response. If they request changes,
make them and re-run the spec review loop. Only proceed once the user
approves."*

- Dit is **per definitie** een open-text-respons-loop, niet een
  meervoudige-keuze-vraag. `options=[…]` dekt dat niet — de gebruiker
  kan "ik wil dit stuk herschreven" niet uit een A/B/C/D-lijst kiezen.
- **Twee vertaal-strategieën** zijn verdedigbaar, beide staan in de
  tabel:
  1. **Doordraaien + eind-impediment:** de agent schrijft de spec,
     draait door naar `writing-plans`, en post aan het einde één
     `report_impediment` met de spec-PDF/URL als bijlage — een
     "laatste-keer-goed-keuren"-venster. Niet sterk (de gebruiker
     leest het pas als er al implementatie-plannen bestaan), maar
     respecteert de gate.
  2. **Volledig doordraaien:** de agent gebruikt de self-review (stap 7)
     als vervanging, met een sterke scope-check vooraf. Past beter bij
     de "één impediment per sessie"-regel als er al een scope-materieel
     impediment is gebruikt voor de grootste clarifying-vraag.
- **Beide gedrag-exemplaren** laten de spec **wel** first-class in
  de kanban-geschiedenis staan via het `spec`-deliverable
  (`kanban-conventions.md` §3, deliverables-tabel regel 112). Dat
  is de audit-trail die de klassieke user-review-gate oplevert — de
  menselijke review-stap zelf is de facto overgeslagen, maar de
  artefact-registratie is identiek.

## 5. Impediment-economie — waarom maximaal één

Dit is de kern-regel. Drie redenen, in volgorde van gewicht.

### 5.1 `report_impediment` doodt de sessie (technisch)

`mcp_server.py:428-429` doet `apply_operation(op_type="release", …)`
— de `claimed_by` van de kaart wordt leeg, de dispatch-loop ziet de
kaart bij de volgende tick als "wees" of pakt 'm opnieuw op in de
`## IMPEDIMENT`-vorm via `dispatch.py:773-792`. De **huidige** sessie
is afgelopen; de tmux-worktree wordt afgesloten; de agent-rol wordt
vrijgegeven.

### 5.2 De vervolg-sessie herleidt context uit comments (economisch)

Het herspawnen kost: nieuwe worktree + verse dispatch-prompt met
`## IMPEDIMENT`-sectie die de oorspronkelijke vraag + het antwoord
verbatim herhaalt (`dispatch.py:773-792`) — maar **niet** de interne
redenering die tot die vraag leidde. De nieuwe agent moet de
`*LogActivity*`, plan-context en deliverables opnieuw lezen om te
snappen *waarom* deze vraag werd gesteld. Dat is voor één
impediment al duur (typisch 2-5 minuten context-reconstructie); voor
N impedimenten is het N× die overhead, met als bijwerking dat de
oude context verloren gaat.

### 5.3 De "kies de hoogst-lonende vraag"-regel

**Batch nooit >1 impediment per sessie.** Kies de vraag waarvan het
antwoord de rest van de brainstorm het meest verandert — typisch de
*scope*-vraag ("welke subset van features is MVP?"), niet de
*detail*-vraag ("welke library X voor Y?"). Voor detail-vragen:
**inferreer** de meest waarschijnlijke default op basis van
bestaande-conventies + codebase-context, en **documenteer** de
aanname + de afgewezen alternatieven in een *Aannames*-sectie van de
spec. De gebruiker kan later via een nieuwe kaart of een
`reopen_card` alsnog corrigeren.

> **De `open_gate`-verleiding.** `open_gate` (`mcp_server.py:437-471`)
> blokkeert **zonder** de sessie te beëindigen en geeft A/B/C/D terug
> in de tool-respons. Dat lijkt een betere brainstorm-vertaling, maar
> het houdt de sessie (en daarmee de worktree) vast tot de mens
> antwoordt — typisch een idle-timeout van 30 min
> (`mcp_server.py:_GATE_DEFAULT_TIMEOUT_SECONDS`). Voor een
> agent-gedispatchte sessie die zonder mens aan de andere kant draait,
> is dat gegarandeerd een timeout → lege gate → de agent moet alsnog
> zelf kiezen of `report_impediment` aanroepen. Kortom: `open_gate`
> vertaalt brainstorm niet; `report_impediment` wel.

## 6. Go / no-go — wanneer een brainstorm **wel** vs. **niet** volledig agent-gedreven mag

Het bridge-patroon in §3 werkt voor een belangrijke klasse van intakes,
maar is **niet universeel toepasbaar**. Hieronder de criteria; als één
no-go-trigger vuurt, moet de intake buiten de agent-sessie blijven
(optie 2 / 3 van `product-inceptie-pipeline.md` §4, of als
dispatched-but-paused-kaart in `intake`-kolom).

### 6.1 Go — volledig agent-gedreven (geen mens tijdens brainstorm)

De sessie mag zelf draaien met hoogstens **één** scope-materieel
`report_impediment` als de **criteria cumulatief** ja zijn:

1. **Domein is goed afgebakend** — de intake-beschrijving noemt een
   concreet project-type, of een afgebakende subset van één applicatie
   ("voeg X toe aan Y", "refactor Z"); niet "bouw een platform
   voor chat, files, billing én analytics" (`SKILL.md` regel 68
   noemt dat *"multiple independent subsystems"* en eist decompose-eerst).
2. **Geen visuele goedkeuring vereist** — de vraag is *textueel* of
   *structureel*; UX/IA-vragen die visueel mockups nodig hebben zijn
   een no-go (zie §4.1).
3. **Geen merkidentiteit / design-systeem-beslissing** — colors,
   typografie, layout-grids: zonder huisstijl-document of bestaand
   design-system als input is dit een gok; de companion is hier
   onmisbaar.
4. **Defaults zijn afleidbaar uit de codebase** — de agent kan
   bestaande patronen volgen; bibliotheek-keuzes, fout-paden,
   teststijl: geen open vragen waarvoor een menselijke stem nodig is.
5. **Eén scope-materieel besluit is aanwijsbaar voor de batch-impediment**
   — de "kies de hoogst-lonende vraag" uit §5.3 lukt alleen als er
   een natuurlijke grootste-beslissing is ("welke MVP-features?",
   "interne vs. publieke API?").

### 6.2 No-go — vereist menselijke / interactieve sessie

Eén of meer van:

1. **Subsystemen-multi** — *"bouw X + Y + Z"* zonder decompose. De
   `SKILL.md`-regel 67-69 is hier leidend: *"help the user decompose
   into sub-projects"*.
2. **UX/IA-beslissing met visuele component** — keuze tussen layouts,
   informatie-architectuur, kleur/typografie, motion-patronen. Visual
   companion is vereist; agent-only kan dit niet produceren
   (zie §4.1).
3. **Security / compliance / data-handling-beleid** — antwoorden zijn
   organisatie-specifiek en foutieve defaults hebben hoge kosten;
   nooit autonoom inferreren.
4. **Onbekend domein zonder codebase-context** — bv. *"bouw een X
   voor de Y-markt"* zonder bestaande repo / library-ecosysteem om
   uit af te leiden. Defaults zijn hier gokken.
5. **Multi-stakeholder-scope** — meerdere teams/users met conflicterende
   belangen; geen natuurlijke "hoogst-lonende vraag" (§5.3) aanwijsbaar.

De no-go-klasse wordt door de **dispatcher** nooit bereikt als de
kaart op de juiste plek staat: `intake`-kolom-kaarten worden niet
auto-gedispatched (`intake-card-routing-analysis.md` §1a), dus een
no-go-intake blijft per definitie op `intake` tot een mens 'm
handmatig oppakt. Een no-go-detectie **in een agent-sessie** =
onmiddellijk `report_impediment` met de no-go-trigger als
`question`; daarna is er geen dispatch meer nodig want de agent
eindigt zoals het hoort.

## 7. Voorbeeld-prompt-fragment voor de dispatched intake-sessie

Het volgende fragment hoort in de prompt-frame van een agent-gedispatchte
intake-kaart (bijv. via een toekomstige override in `build_card_prompt`
of als sessie-lokale persona-instructie). Het is **instructief**, niet
implementatie — geen regel code is gewijzigd om dit te produceren.

````markdown
## Brainstorm-vertaling voor agent-only intake

**Context.** Deze sessie draait autonoom — er is geen mens in de
terminal om per beurt te antwoorden. Daarom vervangt dit blok de
real-time dialogue-loop uit `superpowers:brainstorming` door de
canonieke Cockpit-decisie-flow `report_impediment`. Lees voor de
achtergrond `docs/cockpit/brainstorm-to-impediment-bridge.md`
(de mapping-tabel + economie).

### Verplichte regels

1. **Visual companion niet aanbieden.** Sla `SKILL.md`-stap 2 over.
   Geen `scripts/start-server.sh`. Browser-tabs en click-event-loops
   kunnen niet door een mens bekeken worden in deze sessievorm.
   UX/IA-vragen die visueel mockups vereisen zijn een no-go
   (`brainstorm-to-impediment-bridge.md` §6.2 #2); behandel ze als
   scope-materieel en ga naar regel 4.

2. **Project-context (stap 1) ongewijzigd.** Files, docs, recent
   commits — doe dit normaal.

3. **Clarifying-vragen (stap 3) + 2-3 approaches (stap 4) batchen.**
   Voor elke vraag:
   - **Doordraaien + documenteren** als de beslissing de scope niet
     materieel verandert (defaults, library-keuze, error-handling-stijl,
     naming, teststructuur). Noteer in de spec-sectie *Aannames* welke
     keuze je maakte, waarom, en welke alternatieven je afsneed.
   - **Bewaar voor impediment** als scope-materieel (welke features,
     welke users, MVP vs. volledig, library vs. stdlib voor
     kern-beslissingen).

4. **Maximaal één `report_impediment(options=[…])` per sessie.**
   Kies de vraag waarvan het antwoord de rest van het brainstorm-
   ontwerp het meest verandert. Bij twijfel: de *scope*-vraag boven
   de *detail*-vraag. De tool beëindigt deze sessie
   (`brainstorm-to-impediment-bridge.md` §5.1); een tweede
   impediment wordt genegeerd en zou oneindig herspawnen zonder
   vooruitgang. Eindig deze sessie met `report_impediment`, dan
   niet de kaart naar Done verplaatsen — de dispatch-loop doet dat
   bij re-dispatch.

5. **Full-design (stap 5) + spec self-review (stap 7).** Geen
   section-by-section approval (zie bridge §4.2). Schrijf het design
   in één keer weg in `docs/superpowers/specs/<datum>-<topic>-design.md`
   en commit het (`SKILL.md` regel 109). Doe daarna de
   placeholder/consistency/scope/ambiguity-scan uit
   `SKILL.md` regels 113-119 inline; fix elk issue alvorens verder
   te gaan.

6. **Geen user-review-gate (stap 8).** De real-time "lees de file,
   geef feedback"-loop is geen `report_impediment`-vorm
   (zie bridge §4.3). Twee opties zijn toegestaan, kies de minst
   zware tenzij de scope al een impediment gebruikte:
   - **(a) Doordraaien** als er al een scope-impediment is geweest
     in stap 3: vervang de review-gate door een sterke scope-check
     in self-review, en ga door naar `writing-plans`.
   - **(b) Eind-impediment met spec-link** als er **geen** scope-
     impediment is gebruikt en de scope gevoelig is: roep aan het
     einde `report_impediment(question="Spec geschreven en
     gecommit; bevestig of pas aan vóór we naar writing-plans
     gaan. Bestand: <pad>", options=["Approve & continue",
     "Pause voor review", "Herschrijf <sectie>"])`. Dit respecteert
     de klassieke gate ten koste van één van de §5.3-batched
     vragen (houd §6.1 #5 in de gaten).

7. **Schrijf het resultaat terug naar Cockpit.** Het design-doc is
   tevens een `spec`-deliverable op deze kaart
   (`mcp__cockpit-kanban__attach_deliverable(kind="spec", ref=<body>)`
   — `kanban-conventions.md` §3). Het plan dat `writing-plans`
   produceert is een `plan`-deliverable. Beide blijven in de
   kanban-DB, niet in een externe markdown-fileserver, conform
   de drie-bomen-regel uit `00-orientation.md`.

8. **Bij no-go-detectie (§6.2):** onmiddellijk één
   `report_impediment` met de no-go-trigger als `question`. Niet
   doorschrijven aan een design — de sessievorm is ongeschikt.
````

> **De nummers in het fragment verwijzen terug naar de
> bridge-secties**, niet naar `SKILL.md`-regels — met opzet. De
> agent hoeft de skill-bron niet te lezen om de vertaling te
> begrijpen; de bridge is self-contained.

## 8. Bron-gap & kruisverwijzingen

### 8.1 Het open gat dat deze doc invult

`product-inceptie-pipeline.md` §7 #7 luidt (verbatim):

> **`[design] Hoe vertalen we brainstorming-user-approval naar
> `report_impediment`-flows?`** — welke brainstorming-vragen
> (visual companion, "is dit goed zo?") vertalen zich 1-op-1 naar
> `options=[...]` en welke niet? Wanneer moet de sessie eindigen
> (report_impediment) en wanneer mag ze doordraaien?

Deze doc is **de invulling** van die kaart. De andere zeven items
in §7 blijven open en zijn niet door deze doc aangeraakt.

### 8.2 Gerelateerde docs

| Onderwerp | Bron | Relatie |
|---|---|---|
| `intake`-kolom + dispatcher-skip-regels | `intake-card-routing-analysis.md` §1a | Waarom een intake-sessie überhaupt in agent- vorm kan landen — `intake` wordt nooit auto-gedispatched, dus als 'ie toch dispatched wordt is dat een expliciete work_type-routing. |
| `intake_kind` enum (`brainstorm \| customer-discovery \| legacy-import`) | `intake-card-routing-analysis.md` §3 | Deze doc adresseert expliciet de `brainstorm`-variant; de andere twee varianten kunnen eigen bridge-docs krijgen. |
| Kanban dispatch prompt-frame (`build_card_prompt`) | `backend/app/kanban/dispatch.py:740-792` | Definieert het scaffold waar het §7-prompt-fragment in past. Wijzigt niets hier — dit is design-only. |
| `report_impediment` semantiek | `backend/app/kanban/mcp_server.py:379-433` | Bron van waarheid voor §2 + §5. |
| `open_gate` (de verleiding) | `backend/app/kanban/mcp_server.py:437-471` | Bron van waarheid voor §5 noot. Bewust **niet** de aanbevolen flow. |
| Comment-prefix-contract | `docs/cockpit/kanban-conventions.md` §2 | `**Impediment:** ` + `**Resolution:** ` zijn de twee prefixes die een dispatch-prompt-cycle overleven. |
| `spec`-deliverable als first-class artefact | `docs/cockpit/kanban-conventions.md` §3, regel 112 | Vervangt de "spec-leeft-in-`docs/superpowers/specs/`"-aanname uit de klassieke skill; bridge volgt canoniek drie-bomen-pad. |
| Work-type → persona routing | `docs/cockpit/work-type-routing-analysis.md` | De `intake`-kolom + (toekomstige) `work_type="intake"` is de gate die no-go-classes vasthoudt. |

## 9. Niet in deze doc (expliciete out-of-scope)

- **Geen skill-wijziging.** `superpowers:brainstorming/SKILL.md` blijft
  onaangeraakt; de vertaling is een **dispatch-laag-keuze**, geen skill-aanpassing.
- **Geen dispatch-code-wijziging.** `dispatch.py:740-792` blijft zoals het is;
  het §7-fragment is een **richtlijn**, niet een code-injectie.
- **Geen persona-uitbreiding.** Er wordt geen nieuwe `engineer`/`analyst`/
  eigen persona voor agent-intake geïntroduceerd.
- **De andere twee intake-varianten** (`customer-discovery`, `legacy-import`
  uit `intake-card-routing-analysis.md` §3) krijgen eigen bridge-docs;
  deze doc dekt alleen `brainstorm`.
- **De real-time interactive variant** (mens achter het toetsenbord; optie 2/3
  van `product-inceptie-pipeline.md` §4) staat los van deze doc — daar is
  geen vertaalslag nodig omdat de originele skill ongewijzigd werkt.

## 10. Open punten / TODO voor de implementatie-kaart

Deze doc is design-only. Zodra ze accepteert, zijn de volgende
implementatie-stappen zinvol (niet door deze doc geclaimd — door
een vervolg-kaart):

1. Een `build_card_prompt`-override (in `dispatch.py` of in een
   nieuwe persona-file) die het §7-fragment inspuit op kaarten met
   `intake_kind == "brainstorm"` op de meta-`intake`-kolom.
2. Een nieuwe sub-variant van `intake_kind` per project, of een
   `work_type`-extensie, als de dispatcher de brainstorm-vertaling
   moet onderscheiden van toekomstige bridges (`customer-discovery`,
   `legacy-import`).
3. Een validatie aan `scripts/check-kanban-conventions.sh` (of
   equivalent) die nagaat of deze bridge nog overeenkomt met de
   actuele signatuur van `report_impediment` — nodig zodra die
   tool signature verandert.

Geen daarvan is door deze doc aangeraakt — alleen gesignaleerd
als logische volgende-stap voor een toekomstige `feature`-kaart.
