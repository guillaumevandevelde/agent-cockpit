---
title: "Analyse — Lemma Platform: wat kunnen we overnemen of leren?"
type: analysis
status: active
---

# Analyse — Lemma Platform: wat kunnen we overnemen of leren?

**Datum:** 2026-07-21
**Status:** Analyse / beslisdocument (read-only spike; geen implementatie in deze kaart)
**Trigger:** kanban-kaart `b00f3705…` "Product analyse - Lemma platform". Gebruiker:
> "Het ziet er naar uit dat volgende applicatie grotendeels doet wat wij doen maar
> matuurder, bekijk grondig welke functionaliteiten we kunnen overnemen en of we
> zaken beter kunnen doen : https://github.com/lemma-work/lemma-platform"

**Verwant:**
[`openhands-analyse.md`](./openhands-analyse.md) (zelfde genre; de ACP-conclusie daar
komt hier terug),
[`orchestration-substrate-decision.md`](./orchestration-substrate-decision.md)
(tmux-scraping vs. gestructureerde events),
[`headless-stream-json-transport-spike.md`](./headless-stream-json-transport-spike.md)
+ [`acp-transport-decision.md`](./acp-transport-decision.md) (ons stream-json-transport),
[`kanban-dispatch-spec.md`](./kanban-dispatch-spec.md) (dispatcher),
[`build-prioriteiten-analyse.md`](./build-prioriteiten-analyse.md).

---

## TL;DR

1. **De premisse van de kaart klopt niet.** Lemma doet *niet* grotendeels wat wij
   doen. Lemma is een **agentic app-platform voor bedrijfsprocessen** — "geef elke
   terugkerende taak zijn eigen app" (pods met tabellen, workflows, approvals,
   en Slack/WhatsApp/Gmail als front-ends). Cockpit is een **agentic developer
   platform**: agents die software bouwen, aangestuurd via een kanban-dispatcher
   met worktrees. De doelgroep, de eenheid van werk en het eindproduct verschillen
   fundamenteel. Zie §1–2.
2. **Waar we wél overlappen — de orchestratie-substraat — zijn wij op sommige assen
   verder, niet achter.** Ons `structured_events.py` is bewust ACP-isomorf en onze
   capability-matrix dekt 6 CLI's; Lemma's daemon-events zijn een ad-hoc vocabulaire
   (`token`/`message`/`tool_call`/`tool_return`) zonder protocol-anker. Zie §3.
3. **Waar Lemma echt verder is, is het *productisering* en *robuustheid van de
   verbinding*, niet orchestratie-intelligentie.** Drie dingen zijn concreet de
   moeite waard om over te nemen; ze staan in §4 op leverage gerangschikt.
4. **De sterkste enkele les:** Lemma's daemon laat een lopende run **niet sterven
   als de verbinding wegvalt**. Het subprocess wordt vastgehouden (`_HeldRun`) en
   events gaan naar een begrensde buffer tot de client reattacht. Wij herstarten
   in dat geval de sessie via `session_recovery` — met verlies van in-flight werk.
   Dat is een echte, afgebakende verbetering. Zie §4.1.
5. **Tweede les:** Lemma's approval-model draait een goedgekeurde tool-call opnieuw
   **onder de autoriteit van de gebruiker in plaats van die van de agent**
   (privilege-scheiding). Onze gates zijn kaart-niveau en beëindigen de sessie.
   Zie §4.2.
6. **Aanbeveling:** geen strategische koerswijziging. Drie scoped follow-up-kaarten
   (§7), waarvan één een comment op een bestaande kaart wordt. Lemma is geen
   concurrent om in te halen — het is een goed uitgevoerde buur waarvan we twee
   engineering-patronen kunnen lenen.

---

## 1. Wat is Lemma (gegronde feiten, staat 2026-07-21)

Gemeten aan de repo zelf (`lemma-work/lemma-platform`, default branch `main`,
337 sterren, aangemaakt 2026-06-23, laatste push 2026-07-21 — dus **~1 maand oud
en zeer actief**). Monorepo van 7591 bestanden:

| Pad | Wat | Licentie |
|---|---|---|
| `lemma-backend/` (5258 files) | FastAPI backend, migraties, infra-compose | AGPLv3 |
| `lemma-frontend/` (502) | Next.js operator-UI | AGPLv3 |
| `lemma-python/` (747), `lemma-typescript/` (636) | SDK's, gegenereerd uit OpenAPI | Apache-2.0 |
| `lemma-cli/` (153) | `lemma` CLI + **daemon** | Apache-2.0 |
| `agentbox/` (89) | sandboxed workspace-manager, pluggable providers | Apache-2.0 |
| `lemma-stack/` (39) | self-contained lokale stack-installer | Apache-2.0 |
| `desktop/` (42) | Tauri macOS-app rond de stack-supervisor | AGPLv3 |
| `lemma-skills/` (33) | ingebouwde agent-skills | Apache-2.0 |

**Het kernbegrip is de pod:** een zelfstandige omgeving met tabellen (typed data
met row-level security), files (markdown-geheugen), agents, workflows (grafen met
menselijke approval-stappen), functions, permissions, apps (de UI) en surfaces
(Slack, Teams, Gmail, Outlook, Telegram, WhatsApp). Een pod exporteert naar platte
bestanden en importeert terug — dat is de portabiliteits-truc waarmee een coding
agent een pod kan *bouwen*.

**De raakvlakken met ons zitten in precies één component:** `lemma daemon`. Die
verbindt je lokale Claude Code / Codex / OpenCode / Cursor / Antigravity-login met
de pod: hij pakt taken uit een gedeelde queue, streamt het werk terug, en pauzeert
bij approval-gates. Dat is functioneel onze dispatcher — maar hij dispatcht
*bedrijfstaken naar een pod*, niet *kaarten naar een repo-worktree*.

---

## 2. Waarom "matuurder, doet hetzelfde" niet klopt

De overeenkomst is oppervlakkig en komt door gedeeld vocabulaire (agents, runs,
harnesses, approvals, sandboxes). De verschillen zijn structureel:

| As | Lemma | Cockpit |
|---|---|---|
| Eenheid van werk | een **pod** (bedrijfsproces) | een **kaart** (stuk software-werk) |
| Wat de agent produceert | records, replies, workflow-transities | **commits, branches, PR's** |
| Waar de agent werkt | pod-state + sandbox | **git worktree in een echte repo** |
| Menselijk contactvlak | Slack/WhatsApp/app-UI | kanban-bord + tmux-pane |
| Afhankelijkheden | workflow-graaf binnen één pod | **`depends_on`-DAG over sessies heen** |
| Zelf-verbetering | n.v.t. | expliciet doel (agents bouwen deze codebase) |

Lemma heeft **geen** equivalent van onze multi-agent-decompositie (analyst splitst
een kaart in kind-kaarten met een dep-DAG, executors wachten op hun deps — zie
[`multi-agent-kanban.md`](./multi-agent-kanban.md)). Hun workflows zijn
vóóraf-geauteurde grafen; onze DAG wordt *tijdens* het werk door een agent
gegenereerd. Dat is onze eigenlijke bijdrage en Lemma raakt eraan noch dichtbij.

Omgekeerd hebben wij **geen** equivalent van pods, tabellen met row-level security,
of messaging-surfaces — en dat willen we ook niet: dat is een ander product.

**Conclusie:** de kaart-premisse ("matuurder, doet grotendeels hetzelfde") moet
gecorrigeerd worden. Lemma is matuurder in *verpakking en distributie* (installer,
notarized desktop-app, SDK's, OTel, security-docs) en dat is voor een repo van één
maand oud indrukwekkend — maar het is een andere as dan waarop wij werken.

---

## 3. Waar wij verder zijn (en dat zo moeten houden)

Drie plekken waar de vergelijking in ons voordeel uitvalt. Dit is geen
zelffelicitatie maar een rem: het voorkomt dat we iets "overnemen" dat een
downgrade zou zijn.

**3.1 Event-vocabulaire.** Lemma's `StreamTextState`
(`lemma-cli/lemma_cli/daemon/harnesses/base.py`) emit `token` / `message` /
`tool_call` / `tool_return` — een intern, ad-hoc schema. Ons
`app/services/agentic_cli/structured_events.py` is **bewust ACP-isomorf** met een
gedocumenteerde mapping-tabel naar `session/update`-notificaties, plus twee
weloverwogen super-set-events (`rate_limit`, `session_init`). Als ACP later het
lingua franca wordt, is onze migratie een casing-vertaling en die van hen een
herschrijving. Niet overnemen.

**3.2 CLI-breedte in de configuratie-laag.** Lemma's harness-registry kent 5
harnesses. Onze `capabilities.py`-matrix dekt 6 CLI's over 21 capability-assen
(config, sessions, spawn, resume, fork, mcp, plugins, permissions, commands,
agents, skills, hooks, memory, output_styles, statusline, usage, context, doctor,
backup, restore, headless_run). Dat is een rijkere abstractie.

**3.3 Kaart-gedreven orkestratie.** De analyst/executor-splitsing, de
`depends_on`-DAG, plan-attachments, subscription-pooling per lane, en de
sync-vs-async delegatiegrens
([`sync-vs-async-delegation-decision.md`](./sync-vs-async-delegation-decision.md))
hebben geen tegenhanger in Lemma.

**Nuance — waar 3.2 in de praktijk minder waard is dan het lijkt:** onze
*headless* transport is de facto Claude-only. `resolve_cli_executable`
(`backend/app/kanban/headless_runner.py:108`) mapt `claude-code` → `claude` en
valt voor al het andere terug op de cli_id, terwijl de stream-parser de
Claude-vorm veronderstelt. Lemma's daemon heeft daarentegen **per harness een
eigen parser achter één gedeelde `StreamTextState`**, precies om die vorm-verschillen
op te vangen. Dat gat is al gefiled als kaart `88f3c990…`; §7.3 hangt de
Lemma-referentie eraan in plaats van te dupliceren.

---

## 4. Wat we concreet kunnen overnemen (gerangschikt op leverage)

### 4.1 ⭐ Run vasthouden + reattachen bij verbindingsverlies (hoogste waarde)

**Wat Lemma doet.** `lemma-cli/lemma_cli/daemon/runner.py` (937 regels) behandelt
een wegvallende websocket **niet** als het einde van de run:

- `_RunEventSink` is een indirectie voor "waar gaan de events van deze run heen":
  een live websocket, óf een **begrensde buffer** (`go_buffered()` /
  `go_live()`, cap via `max_buffered_events_per_run()`).
- `_HeldRun` + `_reap_expired_held_runs()` houden het subprocess in leven gedurende
  een **grace-window** (`hold_grace_seconds()`); pas als niemand reattacht wordt het
  getermineerd.
- Reconnect gebruikt **full-jitter exponentiële backoff** (`reconnect_delay_seconds`)
  en een ping/pong-heartbeat op een eigen task (`_heartbeat_loop`,
  `pong_miss_limit()`), zodat traag run-werk niet als dood wordt gelezen.
- De daemon adverteert capaciteit terug (`_capacity_payload(active_run_count)`),
  zodat de server kan schedulen op werkelijke bezetting.

**Waarom dit ons raakt.** Onze `session_recovery.py` is een *herstel*-mechanisme:
bij een dode sessie wordt een nieuwe Claude-sessie gespawnd met resume. Dat werkt,
maar de in-flight turn en zijn events zijn weg — en bij een backend-herstart tijdens
een lange run betaalt de agent de context opnieuw. Een hold-window met
gebufferde events verandert "herstart de sessie" in "pak de draad op". Dit is ook
precies de klasse waar onze bekende pijn zit (dode claims → reaper → re-dispatch →
kaartcontext verloren, zie de `pkill`-gotcha in CLAUDE.md).

**Nuance / niet blind kopiëren.** Lemma's daemon praat over een websocket met een
*remote* server; onze headless runner is een lokaal subprocess in hetzelfde proces
als de backend. De transport-laag verschilt, dus het over te nemen deel is het
**levenscyclus-patroon** (hold + grace + begrensde buffer + expliciete reap), niet
de websocket-machinerie. Zie kaart in §7.1.

### 4.2 ⭐ Approval die de tool onder *gebruikers*-autoriteit uitvoert

**Wat Lemma doet.** `lemma-backend/app/modules/agent/tools/approval/executor.py`:
de agent herhaalt in `request_approval` exact de tool + args die hij wil draaien.
Bij goedkeuring dispatcht `ApprovalExecutor.execute_as_user()` diezelfde tool, maar
met een context waaruit de **agent-workload-identiteit is gestript**
(`workload_type`/`workload_id`/`agent_name` op `None`). Sandbox-tools draaien dan in
een sessie gemint met het token van de *gebruiker*; in-process tools autoriseren als
de gebruiker. Twee details die het patroon af maken: `request_approval` mag zichzelf
niet goedkeuren, en een goedgekeurde tool die faalt rapporteert de fout terug in de
run in plaats van de approval-task te laten crashen.

**Waarom dit ons raakt.** *Premisse-correctie: `open_gate` is de tool die
inline blokkeert en het antwoord teruggeeft (`backend/app/kanban/mcp_server.py:846`:
"this does NOT release the claim or end the session — it simply waits (polling) for
the human's pick, then returns it so the run can continue inline"). De tool die
de sessie wél beëindigt en het antwoord voor een latere sessie op het bord
achterlaat is `report_impediment` (`mcp_server.py:761`). Volledige
premise-correctie + de bronanalyse van waarom dit patroon desondanks niet
aanstaat: `approval-privilege-separation-analyse.md` §2.1.* Dat laat
onverlet: voor "mag ik deze ene riskante actie doen" is *geen* van beide
vandaag het juiste kanaal — `open_gate` is bewust gedeprioriteerd voor
productbeslissingen (zie de docstring van `report_impediment`),
en er is vandaag geen brug tussen `open_gate` en het
permissiesysteem. Lemma's model is fijnmaziger *en* veiliger, want het
scheidt privileges in plaats van alleen toestemming te vragen.

**Nuance.** Wij draaien vandaag met `--dangerously-skip-permissions` in dispatch, dus
er ís geen autorisatiegrens tussen agent en gebruiker om te scheiden. Het patroon
overnemen betekent eerst zo'n grens invoeren — dat is een groter ontwerp dan één
kaart. Daarom is §7.2 gescoped als **analyse**-kaart, niet als feature-kaart.

### 4.3 Sandbox-providers als *smalle, optionele* protocollen

**Wat Lemma doet.** `agentbox/agentbox/providers/protocol.py` splitst de
provider-interface in één verplicht protocol (`SandboxLifecycleProvider`:
create/get_status/list_managed/delete/resolve_endpoint/close) plus **zes optionele,
`runtime_checkable` capability-protocollen**: `SandboxBootstrapProvider`,
`SandboxReleaseProvider`, `SandboxLeaseProvider`, `SandboxAdoptionProvider`,
`SandboxStoragePurgeProvider`. Concrete providers: docker, podman, e2b, daytona,
kubernetes, legacy.

**Waarom dit interessant is.** Twee dingen. (a) De **vorm**: optionele capabilities
als aparte Protocols in plaats van een dikke ABC met `NotImplementedError`-gaten —
dat is precies de vraag die onze `sandcastle_service.py` (1205 regels, docker/podman
hard-coded via `_CONTAINER_PROVIDERS`) gaat krijgen zodra er een derde provider bij
komt. (b) `SandboxAdoptionProvider.adopt(sandbox_id, provider_id)` — "herverbind met
exact deze durable generatie na een manager-herstart" — is de sandbox-variant van
hetzelfde probleem als §4.1.

**Aanbeveling: nu niet bouwen, wel noteren.** Wij hebben één sandbox-vorm die werkt
en geen tweede provider in de pijplijn. Refactoren naar een protocol-split zonder
tweede implementatie is premature abstractie (drie vergelijkbare regels > premature
abstractie — CLAUDE.md). Dit doc is de plek waar het patroon wacht tot provider #3
zich aandient.

### 4.4 Kleinere leerpunten (noteren, niet nu bouwen)

- **Gegenereerde-code-policy.** `docs/security/generated-code-policy.md`: OpenAPI is
  de bron, SDK's zijn nooit met de hand bewerkt, CI faalt op codegen-drift, en het
  verwijderen van een publieke operatie vereist een semver-major + migratienotities.
  Wij hebben de helft hiervan al (`check_openapi_snapshot.py` draait in
  `quality.yml` en `drift-report.yml`) — maar zonder de *regel* dat een
  breaking API-verwijdering een expliciete migratie-notitie vereist. Goedkope
  aanvulling op onze conventies, geen kaart waard tot we SDK's publiceren.
- **OTel met dubbel-afgedwongen redactie.** `docs/observability.md`: LLM-traces gaan
  door een **aparte, standaard-uitgeschakelde pipeline** die nooit op het algemene
  OTLP-endpoint uitkomt; prompt/response/tool-args worden bij instrumentatie
  uitgezet **en** nog eens door een export-allowlist. `make otel-smoke` is een canary
  die faalt als een prompt of SQL toch bij de Collector aankomt. Wij hebben geen OTel
  (bevestigd: geen enkele `opentelemetry`-referentie in `backend/`).
  **Bewuste niet-aanbeveling:** voor een lokaal, single-user dev-platform met
  bestaande logs weegt een OTLP-pipeline niet op tegen de onderhoudslast. Het
  *redactie-patroon* (twee onafhankelijke lagen + een test die faalt bij lek) is wél
  het onthouden waard mocht er ooit telemetrie komen.
- **Distributie.** One-line installer, notarized macOS-app, en dev-poorten die
  bewust naast de geïnstalleerde poorten liggen (3710/8710 vs 3711/8711) zodat beide
  naast elkaar draaien. Wij hebben precies dit probleem: `cockpit.sh start` weigert
  als een concurrente sessie 8000/5173 vasthoudt (zie de UI-conventie over
  isolated-component-preview). Een aparte dev-poortset is een aardige,
  kleine gedachte — maar lost ons probleem niet op, want onze botsing komt van
  *twee sessies met dezelfde rol*, niet van dev-naast-installed.

---

## 5. Wat we bewust NIET overnemen

- **Het pod-model** (tabellen, workflows, permissions, apps). Ander product,
  andere doelgroep. Zie §2.
- **Messaging-surfaces** (Slack/WhatsApp/Telegram/Gmail). Onze menselijke interface
  is het bord; een chat-surface voegt een tweede bron van waarheid toe over wat een
  agent moet doen. Dat is precies wat het kanban-contract vermijdt.
- **Lemma's event-vocabulaire.** Zie §3.1 — dat zou een downgrade zijn.
- **De AGPLv3-kern.** Lemma dual-licenseert (AGPL core, Apache tooling) met een
  commerciële uitzondering. Vermeld hier alleen omdat het betekent: **code
  letterlijk overnemen uit `lemma-backend/` of `lemma-frontend/` is licentie-technisch
  besmettelijk.** De onderdelen die we in §4 interessant vinden — `lemma-cli/`
  (daemon) en `agentbox/` — zijn Apache-2.0, dus daar mag wél uit geput worden, met
  bronvermelding. Het onderscheid is niet academisch: §4.2's `ApprovalExecutor` zit
  in de **AGPL**-backend, dus daarvan nemen we het *idee* over, niet de code.

---

## 6. Aanbeveling

**Geen koerswijziging.** De kaart-premisse dat Lemma "grotendeels doet wat wij doen
maar matuurder" houdt geen stand (§2), en op de as waar we wél overlappen zijn we op
meerdere punten verder (§3). Lemma bevestigt eerder onze richting dan dat het die
uitdaagt: ook zij concluderen dat een lokale coding-agent-login via een daemon aan
een gedeelde takenwachtrij moet hangen — precies onze dispatcher.

**Wel twee scoped verbeteringen overnemen** (§4.1, §4.2) plus één comment op een
bestaande kaart (§4.3 van de headless-parity-kaart). Beide overgenomen items gaan
over *robuustheid van een lopende run*, niet over features — dat is waar Lemma's
extra maand engineering-aandacht daadwerkelijk zichtbaar is.

**Voor de product owner:** wat dit oplevert is dat een lange agent-run een
backend-herstart of verbindingshapering **overleeft** in plaats van opnieuw te
beginnen — minder verloren werk en minder verbruikte quota per onderbreking.

---

## 7. Vervolgkaarten (in deze sessie aangemaakt)

### 7.1 Run-hold + gebufferde events over een transport-onderbreking
Analyse-kaart (het ontwerp raakt reaper, session_recovery én headless_runner, dus
scope-bepaling hoort vóór implementatie). Overgenomen patroon: §4.1.

✅ Geanalyseerd (kaart `805d747f…`) →
[`run-hold-buffered-events-analyse.md`](./run-hold-buffered-events-analyse.md).
**Uitkomst: het hold-window uit §4.1 wordt niet overgenomen.** Lemma's `_HeldRun`
bemiddelt tussen een levende daemon en een weggevallen *remote* websocket; wij
hebben die tussentoestand niet, en voor het transport dat we draaien (tmux) is de
robuustheid al bereikt doordat de agent in een onafhankelijke procesboom leeft en
liveness elke tick opnieuw uit `tmux ls` wordt afgeleid. Het echte gat zit in het
(opt-in, ongebruikte) `headless`-transport; daaruit volgden twee scoped kaarten.

### 7.2 Approval-model: privilege-scheiding tussen agent en gebruiker
Analyse-kaart. Vereist eerst een autorisatiegrens die we vandaag niet hebben
(`--dangerously-skip-permissions`), dus scope-bepaling gaat vooraf aan bouwen.
Overgenomen patroon: §4.2.

✅ Geanalyseerd (kaart `38d32e94…`) →
[`approval-privilege-separation-analyse.md`](./approval-privilege-separation-analyse.md).
**Uitkomst: `execute_as_user` wordt niet overgenomen.** Twee onafhankelijke redenen:
wij hebben één principal (zelfde OS-gebruiker, home, credentials), dus
identiteit-strippen is een no-op; en het patroon verdedigt tegen een agent die iets
doet wat hij *niet mag*, terwijl onze drie gedocumenteerde incidenten alle drie een
*toegestane* actie waren die op dat moment verkeerd was — de agent had zijn eigen
escalatie goedgekeurd. Twee premissen van de kaart bleken bovendien onjuist:
`open_gate` beëindigt de sessie **niet** (het blokkeert inline en geeft het antwoord
terug), en de autorisatiegrens *bestaat al* per `risk_class` — product-projecten
draaien met `skip_permissions=False`. Het echte gat is het spiegelbeeld: die grens
heeft **geen antwoordkanaal** (`--permission-prompt-tool` komt nergens in de codebase
voor), dus een afgedwongen permissieprompt stalt een onbemande dispatch. Drie
scoped kaarten daarop.

### 7.3 (géén nieuwe kaart) — comment op `88f3c990…`
De bestaande kaart "Headless-transport is de facto Anthropic-only" krijgt een
comment met Lemma's per-harness-parser-achter-één-`StreamTextState` als concrete
referentie-implementatie. Dedup-pass op Backlog/Impediment vond deze kaart; een
tweede kaart zou een duplicaat zijn.

---

## 8. Bewust buiten scope

- **Geen meting van Lemma's runtime-gedrag.** Deze spike is een code- en
  documentatie-lezing van de repo op 2026-07-21; de stack is niet lokaal
  geïnstalleerd of gedraaid. Alle uitspraken over gedrag zijn afgeleid uit
  broncode en projectdocumentatie, niet uit observatie. Waar dat het oordeel zou
  kunnen kantelen (§4.1's grace-window-waarden, §4.4's OTel-overhead) staat geen
  getal in dit doc — er is niets gemeten, dus er wordt niets als gemeten
  gepresenteerd.
- **Geen kosten-/besparings-claims.** Dit doc bevat geen token-, latency- of
  geldschatting; de aanbevelingen in §6 rusten op robuustheids-argumenten, niet op
  een besparingsgetal.
- **Geen licentie-advies.** §5 stelt alleen het feit vast (AGPL core / Apache
  tooling) omdat het bepaalt waar we uit mogen putten. Dat is geen juridisch oordeel.

---

## 9. Bronnen

Alle paden hieronder zijn in `lemma-work/lemma-platform` op branch `main`,
gelezen op 2026-07-21 (laatste push van de repo diezelfde dag).

- `README.md` — positionering, pod-primitieven, repo-layout, licentiemodel.
- `lemma-cli/lemma_cli/daemon/runner.py` — `_RunEventSink`, `_HeldRun`,
  `_reap_expired_held_runs`, `reconnect_delay_seconds`, `_heartbeat_loop`,
  `_capacity_payload` (§4.1).
- `lemma-cli/lemma_cli/daemon/harnesses/base.py` — `StreamTextState`,
  `emit_tool_call` / `emit_tool_return` (§3.1, §4.3).
- `lemma-cli/lemma_cli/daemon/harnesses/registry.py` + `claude_code.py` —
  harness-registry en de gedeelde stream-json-runner voor Claude Code + Cursor.
- `lemma-backend/app/modules/agent/tools/approval/executor.py` — `ApprovalExecutor`
  (§4.2). **AGPLv3** — idee overgenomen, code niet.
- `agentbox/agentbox/providers/protocol.py` — de lifecycle- +
  capability-protocol-split (§4.3).
- `docs/observability.md`, `docs/security/generated-code-policy.md` (§4.4).

Cockpit-zijde, geverifieerd in deze werkboom:
`backend/app/services/agentic_cli/structured_events.py`,
`backend/app/services/agentic_cli/capabilities.py`,
`backend/app/kanban/headless_runner.py:108` (`resolve_cli_executable`),
`backend/app/kanban/session_recovery.py`,
`backend/app/kanban/service.py:851-897` (gates),
`backend/app/services/sandcastle_service.py:26` (`_CONTAINER_PROVIDERS`).
