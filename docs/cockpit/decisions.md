# Beslis-register — alle genomen productbeslissingen (index)

> **Dit is de canonieke, browsbare index van "welke richtingsbeslissingen zijn genomen
> en wat was de uitkomst".** Eén regel per beslissing: datum, de vraag, de uitkomst, en
> een link naar het diepere beslisdocument + de kanban-kaart.
>
> **Dit register is een index, geen archief.** De redenering, de trade-offs en de
> alternatieven staan in het gelinkte document — die worden hier **niet** gedupliceerd.
> Verandert een beslissing, dan wordt het bron-document bijgewerkt en krijgt het register
> een nieuwe regel (de oude blijft staan, met een `↩︎ herzien door`-verwijzing).

**Waarom dit bestaat.** Een beslissing werd tot 2026-07-15 op drie losse plekken
vastgelegd — de kaart-Done-summary, een `*-decision.md`, soms een memory-file — zonder
index. De Done-kolom is een firehose (je kunt er "alle beslissingen" niet uit filteren)
en [`kanban-followups.md`](./kanban-followups.md) dekt alléén de
"upstream-deliberately-NOT-adopted"-klasse. Een sessie die vroeg *"doen we same-vendor
multi-account?"* moest toevallig op de juiste spike-doc stuiten. Dit register is die
canonieke plek.

**Voor je een beslissing heropent:** zoek 'm hier eerst op. Staat er een uitkomst, dan is
de vraag beslist — heropenen kan, maar dan met een expliciete weerlegging tegen het
gelinkte document (zie [`reopen-completed-decision-analysis.md`](./reopen-completed-decision-analysis.md)),
niet omdat de beslissing onvindbaar was.

## Het register (nieuwste eerst)

| Datum | Vraag | Uitkomst | Document | Kaart |
|---|---|---|---|---|
| 2026-07-15 | Inzicht in verbruik per subscription — zelf inkantelen of Langfuse koppelen? | **Inkantelen. Langfuse NO-GO** (conditioneel bewaard). Langfuse meet $-kosten per LLM-call; een vast-tarief-abonnement verbruikt **quota**, geen $ — het kan de gestelde vraag ("hoeveel van mijn 5h-venster is op?") structureel niet beantwoorden. Bovendien doet Cockpit zelf nul LLM-calls (het spawnt CLI's), dus er is geen instrumentatiepunt; de omwegen zijn proxy (auth-/ToS-risico op subscription-auth), OTel (metrics ≠ traces) of batch-ETL (data rondpompen die we al lokaal hebben). De data ligt er al: `UsageService` parst de JSONL al mét `model_breakdowns` — wat ontbreekt is één dimensie (`model → subscription`) + één scherm. Heropenen alleen als een §5.3-trigger vuurt (Cockpit doet eigen in-proces LLM-calls / echte pay-per-token-attributie / prompt-versiebeheer+evals). **En passant: gekwantificeerde bug** — `AnthropicUsageProvider` telt MiniMax-tokens mee (36,9% van alle tokens op deze host), dus de attributie-fix moet vóór de registry-fix landen. | [`subscription-verbruik-inzicht-analyse.md`](./subscription-verbruik-inzicht-analyse.md) | `a410468d…` |
| 2026-07-15 | Filteren we de 19 `cockpit-kanban`-MCP-tools per persona (engineer/analyst) om system-prompt-tokens te besparen? (`token-optimization-analysis.md` §4 R3) | **NO-GO — geen vervolgkaart.** De premisse ("alle 19 schemas in élke system-prompt") is achterhaald: Claude Code 2.1.210 defert MCP-schemas achter `ToolSearch`, waardoor de 19 tools **388 tokens** kosten i.p.v. ~4.994 — 1,1% van een 36.660-token baseline; de CLI vangt al ~92%. Het voorgestelde mechanisme bestaat bovendien niet: `--allowedTools`/`--disallowedTools` zijn permissie-poorten, geen schema-filters (`--allowedTools` kost netto **+109** tokens). Alleen een rol-gescopete MCP-mount zou grip geven, voor max ~184 tokens (0,5%) — tegenover permanente plumbing en een faalmodus die de leaf-spike-analyst (die de unie van beide rolsets nodig heeft) op de ship-stap breekt. Ook de matrix zelf is minder scheidbaar dan aangenomen: de **verplichte** `session-retro` roept `create_card` aan. Misfire-preventie hoort server-side (bestaand `{"error": …}`-patroon), niet in een allowlist. **Heropenen** alleen als de meting in §7 van 388 → ~5.000 springt. | [`per-persona-mcp-allowlist-decision.md`](./per-persona-mcp-allowlist-decision.md) | `28e1558e…` |
| 2026-07-15 | Hoe modelleren we "kaart wacht op een niet-kanban business-trigger" (bv. "activeert pas bij tweede-executor-provider-onboarding")? | **`metadata.gated_on` als machine-leesbare gate.** Geen dedicated kolom (disproportionele infra voor één bit informatie), geen hergebruik van `scheduled_at` (semantisch verkeerd — kloktrigger ≠ business-trigger). Orthogonaal met `depends_on` (kaart-DAG) en `scheduled_at` (klok); `_is_gated` past in dezelfde filter-trits als `_is_due` / `_awaiting_plan_ref`. Canonieke set/clear: `mcp.set_card_gate` + REST `POST /cards/{cid}/set-gate`, met `**Gate:** …`-audit-comment in de activity-feed. | [`kanban-conventions.md` §3a](./kanban-conventions.md) | `f8ef71a0…` |
| 2026-07-15 | Wat wordt "bekijken & overnemen" als een gedispatchte sessie geen tmux-pane heeft — input-streaming of tmux behouden? | **tmux blijft de interactieve transport; takeover = promotie.** Geen input-streaming-UX en geen categorie "human-takeover-kaarten": een headless run wordt op afroep via `claude --resume <session_id>` gepromoveerd tot een echte, attachbare pane mét historie (gemeten). De transport-keuze verschuift van dispatch-tijd naar takeover-tijd. | [`human-takeover-headless-decision.md`](./human-takeover-headless-decision.md) | `80c812af…` |
| 2026-07-15 | Waar staat een gedecomponeerde analyse, en hoe zie je dat haar vervolgtaken landen? | **Parkeerkolom `Awaiting Subtasks` + subtaak-rollup + vijf-statussenvocabulaire.** Een parent met ≥1 kind-kaart (parent-generiek, niet analyse-specifiek) gaat op de Done-move naar `Awaiting Subtasks` i.p.v. `Done`, en sluit automatisch zodra het laatste kind `Done` haalt. De bestaande `parent_card_id`-relatie wordt zichtbaar gemaakt op de parent, met een statusbadge per kind. `ReadyState` gaat van 3 → 5: `blocked`→`dependent` (wacht op andere kaarten — DAG of kinderen), `dispatching`→`in_progress`, plus nieuw `impeded` + `completed`. `completed` is **afgeleid** (`column == "Done"`), géén opgeslagen label — anders dan `not-feasible`/`no-action-needed`, die informatie dragen die de kolom niet heeft. Complementair aan de uitkomst-poort hieronder: die beslist *of* een analyse mag afsluiten, dit beslist *waarheen*. | [`analyse-levenscyclus-decision.md`](./analyse-levenscyclus-decision.md) | `d0089809…` |
| 2026-07-15 | Hoe krijgt de analyse-fase een afdwingbaar gevolg (analyses gaan naar Done zonder resultaat)? | **Uitkomst-poort op de Done-move.** Een analyse-kaart mag Done alleen binnen met een expliciete `outcome` uit een gesloten enum (`decomposed` — geverifieerd tegen echte kind-kaarten / `not_feasible` → label / `no_action_needed`); "input nodig" blijft `report_impediment`. Prompt-instructie alleen is afgewezen — dat was de vorige twee rondes en niets verifieerde het. | [`analysis-outcome-contract-decision.md`](./analysis-outcome-contract-decision.md) | `e95729bb…` |
| 2026-07-14 | Meerdere accounts binnen één vendor (§7-fork abonnement-flexibiliteit)? | **NO-GO — vendor-divers.** Subscription-identiteit blijft `{cli, provider}`; de same-vendor-spike is afgesloten, C1–C4 niet geopend. | [`spike-same-vendor-multi-account-isolation.md`](./spike-same-vendor-multi-account-isolation.md), [`subscription-flexibiliteit-analyse.md`](./subscription-flexibiliteit-analyse.md) §7 | `290f6fb7…` |
| 2026-07-14 | ACP of een per-CLI stream-json-parser als gestructureerd transport achter `SpawnTransport`? | **Conditionele GO op gestructureerd transport, NO-GO op ACP als eerste.** Eerste slice met `claude -p --output-format stream-json`; event-model ACP-isomorf; ACP-adapter gepoort op tweede-provider-onboarding. | [`acp-transport-decision.md`](./acp-transport-decision.md) | zie doc |
| 2026-07-14 | Synchrone in-sessie-subagent vs. async kanban-kind-kaart — welk model wint? | **Complementair, niet concurrerend.** Grens op één as: durability + bordzichtbaarheid vs. gedeelde in-memory context. Async-decompositie blijft één laag diep aan de bordkant. | [`sync-vs-async-delegation-decision.md`](./sync-vs-async-delegation-decision.md) | zie doc |
| 2026-07-14 | Plans-pagina is leeg — oplappen of herbestemmen? | ⏳ **NOG NIET BESLIST — aanbeveling, geen uitkomst.** De analyse *adviseert* herbestemmen (Optie B: read-only mensvenster op de spec-/plan-laag, `kanban_plans` uitfaseren), maar §7 van het doc parkeert dit expliciet op een menselijke go/no-go die nooit kwam. Deze regel stond hier tot 2026-07-15 als genomen beslissing (backfill-fout, commit `4101d56`) — dat onderdrukte heropening terwijl niets uitgevoerd werd. Alternatieven A (volledig uitfaseren) en C (writer aanhaken) staan nog open. | [`plans-feature-decision.md`](./plans-feature-decision.md) §7-8 | `45ac606e…` (review: `a70a9272…`) |
| 2026-07-14 | Welke tool voert het intake-interview (vrij gesprek → ingevulde intake-kaart)? | **`superpowers:brainstorming` + `writing-plans`** in een dunne `intake-authoring`-skill. Niet spec-kit (zware dep, dubbele orkestratie). `intake_kind` vervalt voor de MVP. | [`intake-authoring-flow-decision.md`](./intake-authoring-flow-decision.md) | `f2fe8548…` |
| 2026-07-14 | `intake_kind` nu toevoegen, of YAGNI? | **Geen standalone veld nu** (geen enum zonder lezer), maar het echte gemis — de interview-/intake-authoring-flow — krijgt één `analysis`-vervolgkaart waarin `intake_kind` mét consument meekomt. ↩︎ afgesloten door `intake-authoring-flow-decision.md`. | [`intake-kind-decision.md`](./intake-kind-decision.md) | `646f5860…` |
| 2026-07-11 | Database-plafond — blijven op SQLite of naar Postgres? | **Blijven op SQLite.** Nu goedkoop hardenen (één metric toevoegen), niet migreren. | [`database-scaling-decision.md`](./database-scaling-decision.md) | zie doc |
| 2026-07-11 | Schema-migraties — `create_all` + handmatige renames of Alembic? | **Alembic invoeren**, forward-only en SQLite-first. | [`schema-migrations-decision.md`](./schema-migrations-decision.md) | zie doc |
| 2026-07-11 | Orchestratie-substraat — tmux + CLI-scraping vs. Claude Agent SDK / headless? | **Incrementeel abstraheren** — niet migreren en niet bevriezen. Headless/gestructureerd transport náást tmux; tmux blijft default voor human-in-the-loop. | [`orchestration-substrate-decision.md`](./orchestration-substrate-decision.md) | zie doc |
| 2026-07-11 | Headless `SessionEnd`-retro voor niet-gedispatchte sessies bouwen? | **Niet bouwen.** In plaats daarvan de bestaande in-session retro uitbreiden naar álle gedispatchte sessies — concreet: het analyst-gat sluiten. | [`headless-session-retro-decision.md`](./headless-session-retro-decision.md) | zie doc |
| 2026-07-11 | Spec-driven development fase 0 — minimale index of structurele consolidatie? | **Optie B (maximalistisch).** `docs/plans/` gearchiveerd → `docs/plans-legacy/`; `docs/cockpit/` expliciet én afdwingbaar canoniek; promotie-ledger + check-script. | [`spec-driven-development-fase-0-decision.md`](./spec-driven-development-fase-0-decision.md) | zie doc |
| 2026-07-10 | Reviewer-agent + Review-kolom — wenselijk? | **Wél bouwen, in lichtere vorm** (REVISED): feature-compliance-review als subagent-call binnen dezelfde engineer-sessie vóór `move_card Done`. Geen aparte persona, geen Review-kolom. | [`reviewer-agent-decision.md`](./reviewer-agent-decision.md) | zie doc |
| 2026-07-10 | Kan een completed beslissing weerlegd + heropend worden mét context? | De twee bouwstenen die "genoeg context" leveren bestaan al; aanbeveling incl. een `reopen_card`-achtige tool zodat ook een agent een beslissing kan heropenen. | [`reopen-completed-decision-analysis.md`](./reopen-completed-decision-analysis.md) | zie doc |
| 2026-07-09 | Past de `updates` (self-update) feature nog bij Cockpit's missie? | **Houden, zoals het is.** Geen aanpassing aan `scripts/update.sh`, router of page. | [`updates-feature-decision.md`](./updates-feature-decision.md) | zie doc |
| 2026-07-09 | Upstream verwijderde Docker-support — overnemen? | **Niet overnemen.** Cockpit blijft bij Docker als primaire/aanbevolen flow. | [`upstream-docker-removal-decision.md`](./upstream-docker-removal-decision.md) | zie doc |
| 2026-07-09 | Welke cadans voor het terugkerende zelfverbeteringsonderzoek? | Voorstel voor een terugkerende cadans bovenop de bestaande scheduling-infrastructuur. | [`recurring-cadence-proposal.md`](./recurring-cadence-proposal.md) | zie doc |
| 2026-07-08 | Upstream verwijderde legacy Presence — overnemen? | **Niet overnemen.** Presence blijft in Cockpit staan zoals het is. | [`upstream-presence-removal-decision.md`](./upstream-presence-removal-decision.md) | zie doc |
| 2026-07-08 | Upstream Agent Team Presets / launch-orchestration adopteren? | **Niet adopteren** — concurrerend orchestratie-paradigma naast kanban-dispatch, geen complement. Alleen de universele provider-correctness-bugs zijn gecherrypickt. | [`upstream-agent-teams-decision.md`](./upstream-agent-teams-decision.md), [`kanban-followups.md`](./kanban-followups.md) | zie doc |
| 2026-07-04 | Model-switching binnen Claude Code — bouwen of integreren? | Decided (build-vs-integrate); implementatie niet gestart. | [`spike-claude-code-model-switching.md`](./spike-claude-code-model-switching.md) | zie doc |
| 2026-07-03 | Declaratieve workflow-orchestration — bouwen of integreren? | Decided (build-vs-integrate); implementatie niet gestart. | [`spike-declarative-workflow-orchestration.md`](./spike-declarative-workflow-orchestration.md) | zie doc |
| 2026-06-18 | Sync-laag — bevriezen of snoeien? | **Optie 3: snoei het dode, bevries de kern.** `sync.py` + tests verwijderd; HLC/op-log/LWW blijven als gedocumenteerde dormante kern. | [`sync-hlc-freeze-vs-prune.md`](./sync-hlc-freeze-vs-prune.md) | zie doc |

## Conventie — een beslissing landt hier

Een `[beslissing]`-kaart (of elke analyse-kaart waarvan de deliverable een
richtingsbeslissing is) is **pas klaar** als deze drie dingen kloppen:

1. **Het beslisdocument bestaat** — `docs/cockpit/<onderwerp>-decision.md`, met bovenaan
   een quote-blok dat de kanban-kaart (titel + id) noemt. Dit is waar de redenering leeft.
2. **Er staat een regel in dit register** — bovenaan de tabel (nieuwste eerst), met
   datum, vraag, uitkomst-in-één-zin, doc-link en kaart-id. Link-only: kopieer de
   redenering niet.
3. **De Done-summary van de kaart verwijst naar het document**, niet andersom. De
   summary is vluchtig; het document is canoniek.

Herzie je een bestaande beslissing? Werk het bron-document bij, voeg een **nieuwe**
regel bovenaan toe en markeer de oude regel met `↩︎ herzien door <link>` — zo blijft de
chronologie leesbaar en verdwijnt er geen historie.

### Hoe nieuwe regels worden ingevoegd — append-friendly via `merge=union`

Omdat **elke** nieuwe beslissings-regel op exact dezelfde positie wordt ingevoegd
(direct onder de `|---|---|---|---|` header van de tabel — er is maar één "nieuwste
eerst"-plek), zou een conventionele three-way merge bij twee gelijktijdige inserts
op een `CONFLICT (content)` in dit bestand uitkomen. Een haastige `git checkout
--ours` zou dan een regel uit de index laten verdwijnen — precies wat dit register
probeert te voorkomen.

Daarom draagt `docs/cockpit/decisions.md` in `.gitattributes` de
[`merge=union`](https://git-scm.com/docs/gitattributes#_defining_merge_attributes)
strategie:

```gitattributes
docs/cockpit/decisions.md merge=union
```

`merge=union` is een file-level merge: bij een conflict houdt git de **unie van
beide kanten' toegevoegde regels**, in plaats van een regel-tegen-regel-resolutie.
Een insert van sessie A en een insert van sessie B op dezelfde positie resulteren
daardoor in **beide rijen in het merge-resultaat**; de volgorde tussen twee
gelijktijdige inserts is niet-deterministisch, maar de `Datum`-kolom maakt de
chronologie expliciet — daar wijkt niets van af.

**Wat dit betekent voor jou als auteur van een nieuwe regel:**

- Schrijf je rij zoals je altijd zou doen: op de "nieuwste eerst"-plek, direct onder
  de header.
- **Forceer geen rebase / re-sort** van bestaande rijen om de tabel "mooi" te
  houden — dat is precies wat de volgende sessie weer ongedaan maakt, en het
  introduceert geen waarde die de `Datum`-kolom niet al biedt.
- Draai de `.gitattributes`-regel niet terug. Als je denkt dat 'ie weg mag:
  lees eerst kanban-kaart `16ce4d89…` (de merge-conflict-incident die 'm
  rechtvaardigt) en de acceptatiecriteria daarvan.

**Wat dit NIET doet:**

- Het verandert de **leesconventie** niet — het register blijft "nieuwste eerst"
  in de geest (de `Datum`-kolom is canonical, niet de regelvolgorde).
- Het beschermt niet tegen edits aan *dezelfde* regel door twee sessies
  (union houdt beide varianten, geen semantische merge) — een regel is append-only,
  bestaande rijen worden niet herzien in dit register (zie de "↩︎ herzien door"-regel
  hierboven voor revisies).

### Header-conventie — wat bovenaan elk `*-decision.md` hoort te staan

Elk beslisdocument begint — direct onder de `# Titel`-regel, vóór de eerste
`##`-sectie of `>`-blokquote — met dit vier-velden-header:

```markdown
**Datum:** YYYY-MM-DD
**Status:** besloten | herzien | voorgesteld
**Kaart:** `<card-id>`
**Uitkomst:** <één zin — dezelfde zin als de register-regel>
```

| Veld | Bron | Verplicht? |
|---|---|---|
| **Datum** | `git log --reverse --format=%ad --date=short -- <doc>` wanneer niet in het doc | ja |
| **Status** | Canonical waarden `besloten` / `herzien` / `voorgesteld`; vrije toevoeging tussen haakjes blijft toegestaan | ja |
| **Kaart** | `<card-id>` in backticks wanneer beschikbaar; anders `_zie doc — geen hex-id in dit beslisdoc vastgelegd_` (placeholder, refactor-TODO) | ja |
| **Uitkomst** | Eerste zin van de overeenkomstige register-rij, verbatim. Voor `plans-feature-decision.md` (`⏳ NOG NIET BESLIST`) blijft die tekst expliciet staan. | ja |

**Backfill-volgorde** bij het aanmaken van een nieuw beslisdoc: trek `Datum` uit
`git log --reverse`, kopieer `Uitkomst` van de register-rij die je zojuist
hebt aangemaakt, zet `Status: besloten` (of `herzien` als de revisieteller
hoger is), en vul `Kaart` met de hex-id als die bekend is.

**Validatie:** `bash scripts/check-decision-register.sh --check-headers`
controleert dat alle vier de velden bestaan én dat `**Uitkomst:**` (na
whitespace-normalisatie) een prefix is van de register-rij. Advies-only;
`--strict` geeft exit 1 voor CI-gebruik. De validatie staat los van de
bestaande link-presence-check — beide klassen worden door dezelfde run
geflagd.
