---
title: "Beslis-register — alle genomen productbeslissingen (index)"
type: index
status: active
---

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
| 2026-07-19 | [9Router](https://github.com/decolua/9router) integreren als geheel, ernaast draaien als provider-router, of niet — en vervangt het onze provider-laag? | **NO-GO op "als geheel"; conditionele GO op "ernaast", smal en opt-in.** De premisse "9router lijkt matuurder" is een categoriefout: 9router is een **inference-router** (per *request*), Cockpit's provider-laag een **spawn-configurator** (per *sessie*, vóór het proces start) — ze zitten op verschillende lagen. Gemeten is 9router **breder** (40+ providers), niet matuurder: repo 6,5 maand oud (2026-01-05), `v0.5.35`, **519 open PR's**, 700 open vs. 592 gesloten issues, JS zonder types. Vendoring afgewezen (12 MB Next.js naast getypeerd Python/FastAPI; Cockpit doet per ontwerp **nul** LLM-calls; 519-PR-onderhoudslast; en alles wat het biedt loopt tóch via HTTP). Bestaande provider-functionaliteit **blijft ongewijzigd** — "provider" betekent hier drie dingen en 9router raakt alleen `provider_env.py` (~260 regels); de CLI-registry `agentic_cli/` (~1.400 regels) staat er volledig los van. Echte opbrengst = **format-translatie** (geverifieerd: echte Anthropic-native `/v1/messages` + `count_tokens`, met `next.config.mjs`-rewrite `/v1/v1/*`→`/api/v1/*` zodat `ANTHROPIC_BASE_URL` werkt) + **mid-sessie-failover** (vandaag sterft een sessie op een limiet, blijft de `agent:`-claim hangen en verliest de re-dispatch werk). Prijs: de "unlimited free"-tiers (Kiro, subscription-OAuth) draaien op andermans product-OAuth en raken exact het Anthropic-account waar álle dispatch op draait — **de proxy-op-subscription-auth-route is 2026-07-15 al afgewezen** (`a410468d…`), en die blijft staan. Daarom: alleen de **API-key-tier**, token-savers **uit** (RTK staat default AAN en muteert `tool_result`; Caveman/Ponytail injecteren gedragsprompts — stille kwaliteitsdegradatie in een autonome sessie), cloud-sync uit, loopback-only, nooit default. **LiteLLM is voor die overgebleven scope functioneel inwisselbaar en wint op maturiteit** → K2 kiest. De 20–40%-tokenclaim is een **ongemeten vendor-claim** → K1 meet vóór er iets op gebouwd wordt. **Heropenen** bij: 9router `1.x` + verwerkte PR-berg, legitiem gratis quotum zonder product-OAuth, Cockpit doet zelf LLM-calls, of K1 meet substantiële besparing zonder gedragsdegradatie. | [`9router-integratie-analyse.md`](./9router-integratie-analyse.md) | `27cdc2bd…` |
| 2026-07-19 | De vier Done-move-workflowpoorten (`summary_required`, analyse-outcome-contract, parent-parking, reviewer-gate) zitten alleen op `mcp_server.move_card`; de REST/UI-move omzeilt ze allemaal — centraliseren of documenteren? | **Documenteren — de asymmetrie is opzettelijk.** De poorten dwingen *agent*-discipline af (ze bestaan omdat prompt-niveau-instructies drie rondes lang genegeerd werden zolang geen machinepad ze verifieerde); een mens is niet de te disciplineren partij maar de autoriteit die corrigeert wanneer een agent een kaart verkeerd parkeerde. Zonder ongegate override-oppervlak is een kaart die door een poort verkeerd klemt (bv. parent-parking op een verweesd kind) alleen met een DB-edit los te trekken. Centraliseren is bovendien technisch uitgesloten zonder UI-herontwerp: `MoveRequest` draagt alleen `column` + `rank` — geen `summary`, geen `outcome` — dus de poorten op het REST-pad leggen zou **elke** UI-drag-naar-Done weigeren. Dat is een aparte productbeslissing, geen consistentie-fix. Vastgelegd als expliciete conventie met poorten-tabel + consequenties voor nieuwe poorten/tooling. Bevestigt en expliciteert de zijdelingse formulering in de 2026-07-18 reviewer-rij. | [`kanban-conventions.md` §3b](./kanban-conventions.md) | `19763bf4…` |
| 2026-07-18 | Onafhankelijke reviewer-agent + review-kolom-gate voor **álle** kaarten vóór Done — bouwen? (heropent de 2026-07-10-beslissing die juist voor de lichtere in-sessie FCR koos) | ✅ **GO — optie A, bouwen (mens-beslist, doorvoerkost geaccepteerd).** De 2026-07-10-FCR blijft, maar mist wat de gebruiker expliciet wil: een **onafhankelijke** gate die de engineer niet kan overslaan (de FCR is dezelfde sessie die het werk bouwde). Afgedwongen op het **agent-pad** (`mcp_server.move_card`), net als de bestaande outcome-gate/parent-parking/summary-verplichting; de dunne REST/UI-move blijft — conform diezelfde conventie — een bewust menselijk override-oppervlak. Mechanisme: `.claude/agents/reviewer.md` (creëert de `reviewer`-kolom via `sync_agent_columns`) + een redirect in `mcp_server.move_card` — een niet-reviewer-kaart die naar Done gaat wordt, mits de `reviewer`-kolom bestaat, doorgestuurd naar de reviewer-kolom met `agent` geflipt naar `reviewer` (een kolom alleen is niet genoeg — `_phase_target_agent` leest `card.agent` eerst) en de engineer-sessie opgeruimd; de dispatcher pikt 'm op als verse reviewer-sessie. Reviewer akkoord → echte Done; niet-akkoord → `report_impediment` (dat de `agent` terugzet naar de engineer voor de fix-resume). Uitgesloten: reviewer's eigen Done (anders loop) en analyse-kaarten (eigen outcome-contract + kind-kaarten zijn de review-surface). Activatie-schakelaar = kolom-bestaan (geen kolom → gedrag ongewijzigd, backwards-compat). Bekende beperking: in direct-ship-modus reviewt de reviewer ná de merge — de kaart bereikt Done pas na akkoord, maar de code staat al op master (pull-request-modus is pre-merge). | [`reviewer-agent-decision.md`](./reviewer-agent-decision.md) (REVISED 2026-07-18) | `b493d3eb…` (↩︎ herziet 2026-07-10) |
| 2026-07-18 | Code-kennisgraaf (Understand-Anything: tree-sitter + LLM → `.ua/knowledge-graph.json`) adopteren voor code-navigatie? (voorwaardelijke, uitgestelde fork-poot uit `knowledge-structure-navigation-analysis.md`) | **NO-GO nu — `not_feasible`, trigger niet gevuurd.** De kaart mocht alleen getrokken worden als code-navigatie (NIET docs) een **gemeten** pijn werd. Dat bewijs bestaat niet: geen doc en geen `Backlog`/`Impediment`-kaart registreert code-navigatie-pijn (de enige `[knowledge-structure]`-kaarten zijn de docs-poot — frontmatter + index, `25bfe803…`/`340a3010…`), en de telemetrie kan het **niet eens produceren** — `usage_service`/`dispatch_usage_service` meten tokens per sessie/model/dag zonder vraag-categorie, en "APM" is een package-manager, geen perf-monitoring. Externe evidentie leunt al tegen (83% vs 92% antwoordkwaliteit t.o.v. file-exploration). **Heropenen** alleen als per-turn/tool-call-telemetrie óf ≥3 reële sessies aantonen dat structurele code-vragen (call-graphs/impact-analyse) aantoonbaar te veel tool-calls/tokens kosten. | [`code-knowledge-graph-navigation-decision.md`](./code-knowledge-graph-navigation-decision.md) | `a4318941…` |
| 2026-07-15 | Inzicht in verbruik per subscription — zelf inkantelen of Langfuse koppelen? | **Inkantelen. Langfuse NO-GO** (conditioneel bewaard). Langfuse meet $-kosten per LLM-call; een vast-tarief-abonnement verbruikt **quota**, geen $ — het kan de gestelde vraag ("hoeveel van mijn 5h-venster is op?") structureel niet beantwoorden. Bovendien doet Cockpit zelf nul LLM-calls (het spawnt CLI's), dus er is geen instrumentatiepunt; de omwegen zijn proxy (auth-/ToS-risico op subscription-auth), OTel (metrics ≠ traces) of batch-ETL (data rondpompen die we al lokaal hebben). De data ligt er al: `UsageService` parst de JSONL al mét `model_breakdowns` — wat ontbreekt is één dimensie (`model → subscription`) + één scherm. Heropenen alleen als een §5.3-trigger vuurt (Cockpit doet eigen in-proces LLM-calls / echte pay-per-token-attributie / prompt-versiebeheer+evals). **En passant: gekwantificeerde bug** — `AnthropicUsageProvider` telt MiniMax-tokens mee (36,9% van alle tokens op deze host), dus de attributie-fix moet vóór de registry-fix landen. | [`subscription-verbruik-inzicht-analyse.md`](./subscription-verbruik-inzicht-analyse.md) | `a410468d…` |
| 2026-07-15 | Filteren we de 19 `cockpit-kanban`-MCP-tools per persona (engineer/analyst) om system-prompt-tokens te besparen? (`token-optimization-analysis.md` §4 R3) | **NO-GO — geen vervolgkaart.** De premisse ("alle 19 schemas in élke system-prompt") is achterhaald: Claude Code 2.1.210 defert MCP-schemas achter `ToolSearch`, waardoor de 19 tools **388 tokens** kosten i.p.v. ~4.994 — 1,1% van een 36.660-token baseline; de CLI vangt al ~92%. Het voorgestelde mechanisme bestaat bovendien niet: `--allowedTools`/`--disallowedTools` zijn permissie-poorten, geen schema-filters (`--allowedTools` kost netto **+109** tokens). Alleen een rol-gescopete MCP-mount zou grip geven, voor max ~184 tokens (0,5%) — tegenover permanente plumbing en een faalmodus die de leaf-spike-analyst (die de unie van beide rolsets nodig heeft) op de ship-stap breekt. Ook de matrix zelf is minder scheidbaar dan aangenomen: de **verplichte** `session-retro` roept `create_card` aan. Misfire-preventie hoort server-side (bestaand `{"error": …}`-patroon), niet in een allowlist. **Heropenen** alleen als de meting in §7 van 388 → ~5.000 springt. | [`per-persona-mcp-allowlist-decision.md`](./per-persona-mcp-allowlist-decision.md) | `28e1558e…` |
| 2026-07-15 | Hoe modelleren we "kaart wacht op een niet-kanban business-trigger" (bv. "activeert pas bij tweede-executor-provider-onboarding")? | **`metadata.gated_on` als machine-leesbare gate.** Geen dedicated kolom (disproportionele infra voor één bit informatie), geen hergebruik van `scheduled_at` (semantisch verkeerd — kloktrigger ≠ business-trigger). Orthogonaal met `depends_on` (kaart-DAG) en `scheduled_at` (klok); `_is_gated` past in dezelfde filter-trits als `_is_due` / `_awaiting_plan_ref`. Canonieke set/clear: `mcp.set_card_gate` + REST `POST /cards/{cid}/set-gate`, met `**Gate:** …`-audit-comment in de activity-feed. | [`kanban-conventions.md` §3a](./kanban-conventions.md) | `f8ef71a0…` |
| 2026-07-15 | Wat wordt "bekijken & overnemen" als een gedispatchte sessie geen tmux-pane heeft — input-streaming of tmux behouden? | **tmux blijft de interactieve transport; takeover = promotie.** Geen input-streaming-UX en geen categorie "human-takeover-kaarten": een headless run wordt op afroep via `claude --resume <session_id>` gepromoveerd tot een echte, attachbare pane mét historie (gemeten). De transport-keuze verschuift van dispatch-tijd naar takeover-tijd. | [`human-takeover-headless-decision.md`](./human-takeover-headless-decision.md) | `80c812af…` |
| 2026-07-15 | Waar staat een gedecomponeerde analyse, en hoe zie je dat haar vervolgtaken landen? | **Parkeerkolom `Awaiting Subtasks` + subtaak-rollup + vijf-statussenvocabulaire.** Een parent met ≥1 kind-kaart (parent-generiek, niet analyse-specifiek) gaat op de Done-move naar `Awaiting Subtasks` i.p.v. `Done`, en sluit automatisch zodra het laatste kind `Done` haalt. De bestaande `parent_card_id`-relatie wordt zichtbaar gemaakt op de parent, met een statusbadge per kind. `ReadyState` gaat van 3 → 5: `blocked`→`dependent` (wacht op andere kaarten — DAG of kinderen), `dispatching`→`in_progress`, plus nieuw `impeded` + `completed`. `completed` is **afgeleid** (`column == "Done"`), géén opgeslagen label — anders dan `not-feasible`/`no-action-needed`, die informatie dragen die de kolom niet heeft. Complementair aan de uitkomst-poort hieronder: die beslist *of* een analyse mag afsluiten, dit beslist *waarheen*. | [`analyse-levenscyclus-decision.md`](./analyse-levenscyclus-decision.md) | `d0089809…` |
| 2026-07-15 | Hoe krijgt de analyse-fase een afdwingbaar gevolg (analyses gaan naar Done zonder resultaat)? | **Uitkomst-poort op de Done-move.** Een analyse-kaart mag Done alleen binnen met een expliciete `outcome` uit een gesloten enum (`decomposed` — geverifieerd tegen echte kind-kaarten / `not_feasible` → label / `no_action_needed`); "input nodig" blijft `report_impediment`. Prompt-instructie alleen is afgewezen — dat was de vorige twee rondes en niets verifieerde het. | [`analysis-outcome-contract-decision.md`](./analysis-outcome-contract-decision.md) | `e95729bb…` |
| 2026-07-14 | Meerdere accounts binnen één vendor (§7-fork abonnement-flexibiliteit)? | **NO-GO — vendor-divers.** Subscription-identiteit blijft `{cli, provider}`; de same-vendor-spike is afgesloten, C1–C4 niet geopend. | [`spike-same-vendor-multi-account-isolation.md`](./spike-same-vendor-multi-account-isolation.md), [`subscription-flexibiliteit-analyse.md`](./subscription-flexibiliteit-analyse.md) §7 | `290f6fb7…` |
| 2026-07-14 | ACP of een per-CLI stream-json-parser als gestructureerd transport achter `SpawnTransport`? | **Conditionele GO op gestructureerd transport, NO-GO op ACP als eerste.** Eerste slice met `claude -p --output-format stream-json`; event-model ACP-isomorf; ACP-adapter gepoort op tweede-provider-onboarding. | [`acp-transport-decision.md`](./acp-transport-decision.md) | zie doc |
| 2026-07-14 | Synchrone in-sessie-subagent vs. async kanban-kind-kaart — welk model wint? | **Complementair, niet concurrerend.** Grens op één as: durability + bordzichtbaarheid vs. gedeelde in-memory context. Async-decompositie blijft één laag diep aan de bordkant. | [`sync-vs-async-delegation-decision.md`](./sync-vs-async-delegation-decision.md) | zie doc |
| 2026-07-17 | Plans-pagina is leeg — oplappen of herbestemmen? | ✅ **Optie B — herbestemmen.** Plans wordt read-only mensvenster op de spec-/plan-laag (B = kaart-plan-attachments + C = `docs/cockpit/`-docs); `kanban_plans` wordt uitgefaseerd. Randvoorwaarde gebruiker: de B↔C-join via `spec_doc` is 0× gepopuleerd → lever B en C éérst náást elkaar, join is uitgesteld werk. Gedecomponeerd in 4 vervolgkaarten (aggregator-backend → frontend → uitfaseren; + uitgestelde join-analyse). A (volledig uitfaseren) en C (writer aanhaken) afgewezen. _(Tot 2026-07-15 stond dit ten onrechte als beslist door backfill-fout `4101d56`; review corrigeerde naar "nog niet beslist"; dit is de eerste échte go.)_ | [`plans-feature-decision.md`](./plans-feature-decision.md) §7, §10 | `45ac606e…` (review: `a70a9272…`) |
| 2026-07-17 | Per-kaart run-ledger (orchestrator-scherm §3.2) — nieuw scherm of `CardDrawer`-uitbreiding, en `structured_events` consumeren of bestaande bronnen aggregeren? | ✅ **Bouwen, als aggregatie in een `CardDrawer`-tab.** Nieuw top-level-scherm afgewezen (dupliceert kaart-navigatie) → `Ledger`-tab naast Deliverables/Activity/Plan/Tokens/Run. `structured_events` als primaire bron **NO-GO nu** — dubbel geblokkeerd: headless is niet default (`DEFAULT_TRANSPORT="worktree"`) én `headless_runner._on_event` gooit events weg (geen store). Fase 1 = stitch bestaande durabele bronnen (git-diffstat + activity-outcome + verify/CI + per-model tokens) tot de spine `prompt → files → tests → outcome → model`; linkt naar bestaande Run/Tokens-tabs i.p.v. te dupliceren (geen overlap met CC Bridge/APM/TokensTab). `structured_events`-timeline = fase-2-verrijking, wordt kaart zodra headless een gebruikt pad is. Gedecomponeerd in 2 vervolgkaarten (backend-aggregator → frontend Ledger-tab). | [`run-ledger-decision.md`](./run-ledger-decision.md) | `4ce329cd…` |
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
