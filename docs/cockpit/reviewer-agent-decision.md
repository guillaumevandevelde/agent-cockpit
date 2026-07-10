# Reviewer-agent + review-kolom — wenselijk? Trade-off + beslissing

> Kanban-kaart: "Onderzoek: reviewer-agent + review-kolom — is dit wenselijk?"
> DoD van de kaart: een beslisdocument met expliciete aanbeveling
> (bouwen / niet bouwen / lichter alternatief) en onderbouwing, plus een entry
> in `kanban-followups.md` zodat de vraag niet steeds opnieuw wordt gesteld
> zonder deze context.

## Context

De vraag is: voegen we bovenop de bestaande zelf-review-architectuur een
**onafhankelijke Reviewer-agent** toe (een nieuwe persona `reviewer.md` met
een eigen kolom waar kaarten doorheen moeten voor `Done`), of is dat
onnodige complexiteit?

## Geverifieerde stand van zaken

Alle vier de bullets uit de kaart-beschrijving zijn in code/doku geverifieerd
vlak voor dit onderzoek:

1. **Geen aparte Review-kolom.** Vaste kolommen (`Backlog`, `Impediment`,
   `Done`, `To Resume`) + één kolom per `.claude/agents/*.md`-persona
   (`sync_agent_columns`, `kanban-dispatch-spec.md`). Er bestaan vandaag
   alleen `analyst.md` en `engineer.md`, dus dat zijn de enige twee
   dispatch-kolommen.
2. **Kwaliteitsbewaking zit al op twee niveaus in de engineer-sessie zelf:**
   - `iteration-loop`-skill met preset `verify` — verplichte end-of-card gate
     in `engineer.md` §"Zelf-review" (frontend `npm run lint && npm run
     build`; backend pytest bewust niet lokaal — zie `git-ship`-rationale).
     Per iteratie wordt een regel toegevoegd aan
     `.claude/state/iteration-<card-id>.txt`; bij `clean` wordt
     `<loop-complete>` geëmit. Tracking is dus end-to-end zichtbaar, geen
     "denk eraan om te checken"-narrative.
   - `/code-review`-skill-aanroep op `effort medium` vlak voor ship
     (`d95b0b6`/`dde58db`, `engineer.md` §6). Dit is geen "kijk zelf nog eens
     goed"-zin maar een expliciete skill-call die in het transcript
     zichtbaar is.
3. **`_IMPEDIMENT_AGENTS` is al opgeruimd** (`35beb16`,
   "trim naar echte analyst/engineer-rollen"). De fallback-map bevatte
   eerder `developer`/`tester`/`testing`/`code-review`-rollen uit een oud
   `card-flow.json`-systeem — geen bijbehorende persona-bestanden, geen
   concreet bouwplan. Het bijbehorende beslismoment staat in
   `work-type-routing-analysis.md` §3.3 ("`developer`/`tester`/`code-review`
   in `_IMPEDIMENT_AGENTS`: bouwplan of cruft?") met expliciete motivatie
   om **niet** te bouwen tot er een concreet plan is.
4. **Persona-infrastructuur is al low-friction.** Een
   `.claude/agents/reviewer.md` zou via `sync_agent_columns` automatisch
   een kolom krijgen. Infra-kosten voor een nieuwe rol zijn dus niet het
   struikelblok — het struikelblok is of het zinvol is.

## Welk concreet probleem lost een onafhankelijke Reviewer-agent op?

De voorgestelde baten zijn:

### Baat 1: "tweede paar ogen" / geen blinde vlekken van dezelfde sessie

**Wordt al deels geleverd.** De `/code-review effort medium`-skill is een
subagent-aanroep: een verse, schone context met alleen de diff als input —
geen "ik weet wat ik bedoelde"-bias, geen herinnering aan de 3 uur debug die
tot de fix leidde. De skill wordt expliciet aangeroepen vlak voor ship
(`engineer.md` §6), niet "als ik eraan denk". De beoogde eigenschap
(onafhankelijke, context-verse review) is er al.

Wat een **onafhankelijke Reviewer-agent** hier bovenop zou doen is in
principe hetzelfde als wat `/code-review effort medium` al doet, maar dan
als een hele nieuwe sessie in plaats van een subagent-call binnen dezelfde
sessie. Het verschil is marginaal:

| Aspect | Subagent `/code-review` | Losse Reviewer-sessie |
|---|---|---|
| Verse context | ✅ | ✅ |
| Andere taakprompt (reviewen i.p.v. bouwen) | ✅ | ✅ |
| Ander model denkt na over diff | ✅ (zelfde model, andere rol) | ✅ (zelfde model, andere rol) |
| Commentaar op de kaart | via skill-output | via MCP-tools in eigen sessie |
| Concurrency-cap-blocking | nee (subagent binnen sessie) | **ja** — Reviewer-sessie claimt de hele project-cap, geen nieuwe kaarten tot reviewer klaar is |

### Baat 2: bewuste gate vóór `Done` bij `ship_mode="direct"`

**Echte gap, maar oplosbaar met lichtere middelen.** In `ship_mode="direct"`
merget de engineer-sessie rechtstreeks naar `master` en pusht, zonder
menselijke tussenkomst. Een Reviewer-kolom zou daar een AI-gate tussen
schuiven.

Maar:

- Die AI-gate heeft dezelfde "ik moet opschieten"-druk als de engineer
  (zelfde auto-pipeline, zelfde druk om door te gaan). Een tweede
  AI-sessie die dezelfde codebase-kennis heeft (alleen minder "ik heb dit
  net geschreven"-bias) is geen four-eyes-stap in de zin die "four eyes"
  impliceert. Four-eyes betekent: een onafhankelijke mens keurt goed. Twee
  AI-sessies die elkaars werk controleren zijn geen vier ogen, het is een
  spiegel.
- De échte gate ná `direct` is al **CI** (`quality.yml`: ruff + pytest +
  frontend lint + build op elke push naar `master`). Dat is een echte,
  geautomatiseerde, geïsoleerde backstop die niet moe wordt, niet "opschiet"
  onder tijdsdruk, en waar de hele suite in één keer draait. Een
  AI-reviewer-sessie is inferieur aan CI op deze as.
- Voor kaarten waar een menselijke gate *wel* wenselijk is bestaat de
  `ship_mode="pull-request"`-modus al. Een menselijke reviewer kan in die
  modus via GitHub de PR afkeuren/goedkeuren — een echte four-eyes-stap.

### Baat 3: consistentie met plan, met app-doelstelling en overige code

Een Reviewer-agent die expliciet "controleer of de implementatie matcht met
het plan-attachment op de parent-kaart" zou een nieuwe rol claimen die
anders niet bestaat. Maar:

- Voor multi-agent-kaarten met plan-attachment is de *executor* al
  verplicht om de plan-context te lezen (`_plan_context_section` in
  `dispatch.py:612-637` — prepended aan de prompt). De executor wordt
  geacht zelf te valideren dat het plan klopt tijdens het werk en
  `report_impediment` te gebruiken als dat niet zo is.
- Voor kaarten zonder plan (de meerderheid) is er geen "plan om mee te
  vergelijken" — een Reviewer-agent die "consistentie met het plan" moet
  checken heeft dan niets om te checken.

### Baat 4: voorkomen dat triviale kaarten in Done belanden zonder check

Hier is de huidige architectuur al behoorlijk strak: `iteration-loop preset
verify` + `/code-review effort medium` + CI. Een extra Reviewer-sessie
toevoegen voor analyse-kaarten (deze kaart zelf!) of voor chore-kaarten
("update version") is pure overhead — de baten zijn nul, de kosten zijn
onevenredig.

## Kosten (concreet)

| Kosten | Weging |
|---|---|
| **Extra sessie per kaart** | Een Opus-4.8-sessie met context-heropbouw (repo lezen, recente diff scannen, persona-prompt) is significant duurder dan de huidige `/code-review`-subagent-call die al binnen dezelfde sessie gebeurt. |
| **Concurrency-cap-blocking** | Het 1-actieve-sessie-per-project-cap betekent dat een Reviewer-sessie de dispatch-cap voor het hele project claimt. Terwijl de reviewer draait, kan geen nieuwe kaart opgepakt worden — dat is een doorlooptijd-kostenpost die niet in de baten terugkomt. |
| **Visuele complexiteit / nieuwe kolom** | Een extra kolom in elke project-board, met dezelfde UI-onderhoudslast. |
| **Routing-ambiguïteit** | Wie reviewt de reviewer? Of wordt de reviewer-sessie niet gereviewed (dan is de gate zwakker dan hij lijkt)? Of wordt de hele pipeline recursief ("reviewer → reviewer-of-reviewer → ...")? Geen van beide is een aantrekkelijk antwoord. |
| **Lijst met persona-bestanden groeit** | Twee is beheersbaar; drie wordt een "welke rol past bij deze kaart?"-keuze die gebruikers moeten maken bij elke kaart (zie `work-type-routing-analysis.md` §1b voor hetzelfde probleem op agent-niveau). |

## Past dit bij "zo autonoom mogelijk"?

Nee. Een onafhankelijke Reviewer-agent-stap is precies een extra tussenstap
die autonomie verlaagt zonder bewezen baat. De doelstelling in `CLAUDE.md`
is "voert werkzaamheden steeds autonomer uit" — een nieuwe gate is de
tegenovergestelde richting. Een gate heeft alleen zin als de gate iets
detecteert dat de bestaande poorten missen; dat is hier niet aangetoond.

## Lichtere alternatieven die al bestaan

In volgorde van "minste nieuwe infra, meeste waarde per moeite":

1. **`ship_mode="pull-request"` voor kaarten waar menselijke gate wenselijk
   is.** Bestaat al. Geen infra-wijziging nodig — alleen een UI-toggle in
   `CardEditDialog` of een project-default.
2. **CI strakker maken** als er een specifieke klasse bugs doorheen glippen
   (bv. een extra ruff-rule, een verplichte mutation-test-pass). Dit is een
   eenmalige inspanning met blijvend effect, en het is een echte
   geautomatiseerde gate die niet moe wordt.
3. **`/code-review effort high` of `ultra` oproepen voor risicovolle
   kaarten** in plaats van de huidige `medium`. Is een parameter-wijziging
   in `engineer.md` §6, geen nieuwe persona.
4. **Steekproefsgewijze review**: een aparte, periodieke kaart die de
   afgelopen N kaarten langsloopt met `/code-review effort high`. Dit is
   **wel** een Reviewer-agent-achtige workflow, maar dan periodiek en op
   de hele batch i.p.v. per kaart — dus zonder de concurrency-cap-blokkade
   per individuele kaart.

Geen van deze vereist een nieuwe persona of een nieuwe kolom.

## Aanbeveling

**Niet bouwen.** De voorgestelde Reviewer-agent + Review-kolom heeft geen
concreet probleem dat de huidige architectuur niet al afdekt:

- Onafhankelijke, context-verse review → al geleverd door `/code-review
  effort medium`.
- Echte geautomatiseerde gate ná push → al geleverd door CI (`quality.yml`).
- Menselijke gate vóór `Done` → al beschikbaar via `ship_mode="pull-request"`
  + GitHub PR-review.
- Tracking van de gate-uitvoering → al geleverd door `iteration-loop`'s
  `.claude/state/iteration-<card-id>.txt`-log.

Wat het **wel** toevoegt is overhead (tokens, doorlooptijd,
concurrency-cap-blokkade, visuele complexiteit), zonder iets dat de
bestaande poorten niet al dekken. De beoogde "tweede paar ogen"-eigenschap
is een illusie: het is hetzelfde model met een andere prompt, niet een
onafhankelijke partij. De CLAUDE.md-doelstelling "zo autonoom mogelijk"
pleit actief tegen een nieuwe tussenstap waar de baten niet bewezen zijn.

`work-type-routing-analysis.md` §3.3 stelde exact dezelfde vraag over
`developer`/`tester`/`code-review` in `_IMPEDIMENT_AGENTS`; de conclusie
toen was "reduceer tot de twee rollen die vandaag echt bestaan tot er een
concreet plan is voor meer rollen." Diezelfde conclusie geldt hier: er is
geen concreet plan dat de bovenstaande analyse overtuigend weerlegt.

### Wanneer heroverwegen

- Als er een specifieke, herhaaldelijk-optredende klasse bugs is die de
  huidige gate-combinatie (engineer-zelfreview + CI) doorlaat, dan is
  **CI strakker maken** (alternatief 2) het juiste antwoord — niet een
  nieuwe AI-gate die dezelfde eigenschappen heeft.
- Als een menselijke gate gewenst is voor een specifieke klasse kaarten,
  dan is **`ship_mode="pull-request"` forceren** (alternatief 1) het juiste
  antwoord — niet een AI-reviewer-sessie.
- Als er een echte vier-eyes-eis is (regulering, audit, productie-code met
  externe impact), dan is het juiste antwoord een **menselijke reviewer**,
  niet een tweede AI-sessie.

## Wat deze kaart doet

Alleen dit document + een entry in `kanban-followups.md`. Geen
codewijziging, geen nieuwe persona, geen nieuwe kolom: de beslissing is
"niets doen", d.w.z. bewust niet bouwen.

## Concrete entry voor `kanban-followups.md`

> Zie sectie **"Reviewer-agent + review-kolom — deliberately NOT adopted
> (2026-07-10)"** in `kanban-followups.md`. Verwijst naar dit document
> voor de volledige afweging.