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
| 2026-07-15 | Wat wordt "bekijken & overnemen" als een gedispatchte sessie geen tmux-pane heeft — input-streaming of tmux behouden? | **tmux blijft de interactieve transport; takeover = promotie.** Geen input-streaming-UX en geen categorie "human-takeover-kaarten": een headless run wordt op afroep via `claude --resume <session_id>` gepromoveerd tot een echte, attachbare pane mét historie (gemeten). De transport-keuze verschuift van dispatch-tijd naar takeover-tijd. | [`human-takeover-headless-decision.md`](./human-takeover-headless-decision.md) | `80c812af…` |
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

**Validatie:** `bash scripts/check-decision-register.sh` flag't elk
`docs/cockpit/*-decision.md` dat niet vanuit dit register gelinkt is (advies,
niet-blokkerend; `--strict` geeft exit 1 voor CI-gebruik). Zo loopt het register niet
opnieuw achter zodra er een nieuw beslisdocument landt.
