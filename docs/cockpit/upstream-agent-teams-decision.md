# Upstream Agentic Agent Teams — adopt or not? Trade-off + beslissing

> Kanban-kaart: "Upstream sync: Agentic Agent Teams + Team Presets overnemen". DoD van de
> kaart: **eerst een geschreven brainstorm/spec-sessie, geïnformeerd door upstream's eigen
> ontwerpredenering** (`16155c7`/`8946ee7` in `upstream/master`), **daarna pas
> implementatie.** Dit document is dat eerste deel; de aanbeveling is in deze PR ook
> geïmplementeerd (zie "Wat deze PR doet").

## Context

Upstream (`adrirubio/claude-deck`) bouwde over meerdere commits een volledig nieuw
subsysteem:

| Commit | Wat |
|---|---|
| `c12b248` | Basis "Agent Team Presets" — `AgentTeamPreset`/`AgentTeamSlot` DB-model, REST CRUD, launch-orchestratie (spawn een hele roster ineens) |
| `eb162fa` | Bugfix op die launch-orchestratie (unsafe Codex resume last-slots) |
| `16155c7` / `8946ee7` | Design spec: capability-gap-analyse voor **agentische** team-creatie (een externe agent laat via een tool-call een team met gepinde provider/model/platform/thinking-mode ontstaan) |
| `43aa42a` | Implementeert die spec: provider-fixes (G1–G3), een nieuwe MCP-shim `deck_create_team`/`deck_plan_team_launch`/`deck_launch_team`, slot-validatie, model-capability-contract |
| Latere follow-ups | Copilot/OpenCode-providersupport, team-lane-UI, slot-kleuren, dispatch-autonomy-threading |

**Onze fork nam de basis (`c12b248`) nooit over.** Ons huidige "teams"-concept
(`backend/app/services/agent_bridge/teams.py`, frontend `TeamCard.tsx`/`useTeams.ts`) is
een **read-only groepering** van al-lopende sessies — auto-detect op gedeelde
`cwd`+`provider`, of een handmatige groep. Geen presets, geen slots, geen
launch-orchestratie: een team ontstaat pas ná het spawnen van de losse sessies.

**Belangrijker: we bouwden ondertussen onze eigen, andere oplossing voor het probleem dat
upstream's Agent Teams + Agent Mail samen oplossen** (agents laten samenwerken/elkaar
werk doorgeven). Onze **kanban-dispatch** (`kanban-dispatch-spec.md`) routeert werk via
kaarten op een bord; onze **eigen Agent Mail** (`agent-mail-spec.md`, `agent-mail-plan.md`)
zit *in de kanban-store*, met durable per-rol handles en MCP-tools
(`send_mail`/`request_context`/`handoff`/`check_inbox`/`read_mail`) die al geïntegreerd
zijn met dispatch (`_run_card` haalt open handoffs op bij het claimen van een kaart).
Upstream's model is fundamenteel anders: een **losstaande roster van provider/model-slots**
die je als één eenheid spawnt, met een **eigen, generieke** MCP-shim
(`agent_mail_server.py` → straks ook `deck_create_team`) die niets weet van kanban-kaarten.

Dat zijn **twee concurrerende antwoorden** op "hoe laat ik agents samenwerken", niet twee
aanvullende features.

## Wat is er wél universeel bruikbaar, los van de teams-vraag?

Upstream's gap-analyse (`16155c7`/`8946ee7`, §3/§5) beschrijft provider-bugs die niets met
"teams" te maken hebben — ze zitten in de gedeelde spawn-laag die *elke* sessie gebruikt,
ook los van teams. Ik heb geverifieerd dat **twee ervan ook in onze fork zitten**:

- **G1 — Codex `reasoning_effort` wordt stilzwijgend genegeerd.**
  `backend/app/services/providers/codex_cli.py::build_spawn_command` leest
  `options.reasoning_effort` nergens, terwijl `copilot_cli.py` het al correct doet
  (`--effort`, regel 70-73) — het patroon staat al in de repo. `SpawnCommandOptions`
  (`base.py`) heeft het veld al; het wordt alleen door Codex genegeerd.
- **G3 — Bedrock-env-fallthrough naar de Claude-Code-tak voor niet-Codex providers.**
  `platform_env.py::build_platform_env` heeft precies upstream's bug: alleen
  `provider_id == "codex-cli"` krijgt een vroege return; **elke andere provider**
  (opencode, copilot, mimo) valt door naar de Claude-Code-specifieke tak en krijgt
  `CLAUDE_CODE_USE_BEDROCK=1`/`ANTHROPIC_MODEL` — env die voor die providers niets
  betekent of actief verkeerd is. Dit is **vandaag al bereikbaar**:
  `NewSessionDialog.tsx`'s platform-select sluit Bedrock alleen uit voor Copilot
  (`isBedrock = !isCopilot && platform === 'bedrock'`), dus OpenCode + Bedrock kiezen kan
  gewoon via de UI.
- **G2 (opencode reasoning_effort) is bij ons niet relevant als "fix"** — `open_code.py`
  leest `reasoning_effort` sowieso nergens (geen dode `--variant`-tak zoals upstream had
  om op te ruimen). Geverifieerd met `opencode --help` in deze omgeving: geen
  `--variant`/`--effort`-vlag bestaat, dus upstream's conclusie (b2: expliciet afwijzen,
  niet doen alsof het werkt) is ook hier het juiste antwoord — alleen moet de afwijzing
  nog **expliciet** gemaakt worden i.p.v. stil niks doen.

## Optie 1 — Volledige port

Neem `c12b248` + `eb162fa` + `43aa42a` + alle follow-ups letterlijk over: presets, slots,
launch-orchestratie, een tweede MCP-shim, model-capability-contract.

- **Voor:** 1-op-1 met upstream, makkelijker toekomstige upstream-syncs.
- **Tegen:**
  - Bouwt een **tweede, concurrerende orchestratielaag** naast kanban-dispatch. Twee
    "Agent Mail"-implementaties naast elkaar (kanban-store-based met durable
    rol-handles + dispatch-integratie, vs. upstream's generieke teams-shim) is
    verwarrend en dubbel onderhoud.
  - **~4000+ regels** (backend service 1386 regels, tests 1716+113 regels, frontend page
    1158 regels, plus MCP-shim-refactor) in één kaart, met hoog regressierisico.
  - Het leeuwendeel van de waarde (agentisch een roster met gepinde provider/model/platform
    laten ontstaan) past niet natuurlijk in ons model, waar dispatch al een provider per
    project/kaart kiest — er is geen duidelijke "wanneer gebruik je een preset vs. wanneer
    laat je dispatch een kaart oppakken"-scheiding zonder een aparte ontwerp-sessie.

## Optie 2 — Alleen de preset/launch-laag overnemen, zonder upstream's Agent Mail

Bouw `AgentTeamPreset`/`AgentTeamSlot` + launch-orchestratie wel (het "spawn N gepinde
providers als één eenheid"-idee), maar hergebruik onze eigen kanban-Agent-Mail i.p.v.
upstream's MCP-shim.

- **Voor:** middenweg — krijgt het concrete gemak (één klik, hele roster spawnen) zonder
  een tweede mail-systeem.
- **Tegen:** nog steeds een substantieel nieuw subsysteem (DB-model, CRUD, orchestratie,
  frontend-pagina) dat **zelf** een aparte ontwerp-sessie verdient: hoe registreren
  team-gespawnde sessies zich in het kanban-bord? Lopen ze parallel aan dispatch of
  worden het kaarten? Dat is precies het soort vraag waar de **opvolgende kaart**
  ("Agent Bridge UI-cluster: team lanes/filter/roles") van uitgaat dat die *al beantwoord*
  is — en dat is hij niet. Voortijdig bouwen zonder dat ontwerp is gokken.

## Optie 3 (aanbevolen) — Snoei het subsysteem, behoud de universele bugfixes

Splits langs de werkelijke breuklijn, net als bij de sync/HLC-beslissing
(`sync-hlc-freeze-vs-prune.md`):

- **Niet overnemen:** presets, slots, launch-orchestratie, de tweede MCP-shim, het
  model-capability-contract (G7), de team-lane-UI-follow-ups. Deze horen bij een
  concurrerend orchestratiemodel dat we bewust niet naast kanban-dispatch willen laten
  bestaan — als team-presets ooit toch gewenst zijn, is dat een **losse, bewuste** kaart
  met zijn eigen ontwerp (hoe verhoudt een preset zich tot een kanban-kaart?), niet
  bijvangst van een upstream-sync.
- **Wel overnemen (dit zijn provider-correctheidsbugs, los van de teams-vraag):**
  - **G1-equivalent:** Codex `--config model_reasoning_effort="..."` toevoegen wanneer
    `options.reasoning_effort` gezet is. **Correctie t.o.v. de eerste versie van dit
    document:** deze fork kent, anders dan upstream aannam, geen vastgelegde
    `low`/`medium`/`high`/`xhigh`-enum om tegen te valideren — `codex_config_service.py`
    en de Codex-settings-editor behandelen het als vrije tekst die 1-op-1 naar
    `config.toml` gaat, en `copilot_cli.py` valideert `reasoning_effort` ook niet (regel
    72-73: kale passthrough). Nieuwe validatie hier verzinnen zou een asymmetrie
    invoeren die nergens anders in de codebase bestaat, dus de Codex-fix volgt hetzelfde
    passthrough-patroon als Copilot: géén enum-check.
  - **G3-equivalent:** `build_platform_env` herstructureren zodat de Claude-Code-specifieke
    tak (`CLAUDE_CODE_USE_BEDROCK`/`ANTHROPIC_MODEL`) **alleen** voor `claude-code` geldt,
    expliciet per provider, niet via fallthrough. OpenCode/Copilot/MiniMax + Bedrock
    krijgen alleen de gedeelde `AWS_REGION`/`AWS_PROFILE` (geen secrets, ongewijzigd
    invariant).
  - **Frontend-pariteit:** `NewSessionDialog.tsx` mist een reasoning-effort-control voor
    Codex (stuurt het veld vandaag alléén voor Copilot) — toevoegen zodat de UI het
    backend-veld dat al bestaat ook echt kan zetten.
  - **Expliciete afwijzing i.p.v. stille no-op:** als een niet-Codex/niet-Copilot provider
    (opencode) `reasoning_effort` meekrijgt, nu een duidelijke `ValueError` in plaats van
    een stille no-op — zodat een toekomstige aanroeper (UI of MCP) een fout ziet in plaats
    van te denken dat de instelling werkte.

### Waarom dit de beste keuze is

- Adresseert de **kern van de kaart-instructie** (lees upstream's ontwerpredenering,
  beslis bewust) zonder een architecturaal conflict te importeren.
- **Volledig omkeerbaar**: niets wordt afgesloten. Team-presets kunnen ooit alsnog als
  bewuste, losse kaart komen — dan met een eigen ontwerp voor de kanban-integratie.
- Levert **vandaag al bereikbare bugs** op (OpenCode+Bedrock via `NewSessionDialog`) op
  met minimaal, goed-getest oppervlak (twee bestaande, al-geteste modules:
  `codex_cli.py`, `platform_env.py`).
- Informeert de afhankelijke kaart correct: "Agent Bridge UI-cluster (team lanes/filter/
  roles)" moet bouwen op het **bestaande** `agent_bridge/teams.py`-model
  (auto-detect/handmatige groepering van lopende sessies), **niet** op een preset-API die
  niet bestaat en bewust niet overgenomen is.

### Wanneer heroverwegen

- **Als kanban-dispatch een expliciete "spawn deze N providers als losse, niet-kaart
  sessies tegelijk"-behoefte krijgt** die dispatch zelf niet kan dekken → dan optie 2
  alsnog, als bewuste, apart ontworpen kaart (niet als upstream-sync-bijvangst).
- **Als upstream een vergelijkbare provider-bugfix nog eens raakt** (bv. een vierde
  provider met dezelfde fallthrough) → dezelfde aanpak: cherry-pick de bugfix, negeer de
  teams-laag eromheen.

## Aanbeveling

**Optie 3.** Geen presets/launch-orchestratie/tweede Agent Mail; wel de Codex-effort-fix,
de Bedrock-env-fallthrough-fix, en frontend-pariteit voor Codex reasoning effort.

## Wat deze PR doet

1. `backend/app/services/providers/codex_cli.py` — `build_spawn_command` emit
   `--config model_reasoning_effort="<effort>"` wanneer `options.reasoning_effort` gezet
   is (kale passthrough, zelfde patroon als `copilot_cli.py` — geen enum-validatie, want
   die bestaat nergens anders in deze codebase voor dit veld).
2. `backend/app/services/providers/platform_env.py` — `build_platform_env` maakt de
   provider-dispatch expliciet (whitelist op `claude-code` voor de
   `CLAUDE_CODE_USE_BEDROCK`/`ANTHROPIC_MODEL`-tak) i.p.v. "codex retourneert vroeg,
   iedereen anders krijgt Claude-env". OpenCode/Copilot/MiniMax + Bedrock krijgen alleen
   `AWS_REGION`/`AWS_PROFILE`.
3. `backend/app/services/providers/open_code.py` — `build_spawn_command` gooit een
   duidelijke `ValueError` als `reasoning_effort` gezet is (OpenCode kan geen
   thinking-mode pinnen; geverifieerd via `opencode --help`), i.p.v. het stilzwijgend te
   negeren.
4. `frontend/src/features/cc-bridge/NewSessionDialog.tsx` — reasoning-effort-control ook
   voor Codex (naast Copilot), zodat de UI het bestaande backend-veld kan zetten.
5. Regressietests: Codex `--config model_reasoning_effort=...` emissie + validatie;
   `platform_env` non-Codex/non-claude-code providers krijgen **nooit**
   `CLAUDE_CODE_USE_BEDROCK`/`ANTHROPIC_MODEL` (mandatory regression, zoals upstream's
   eigen spec voorschrijft in §6c); OpenCode + `reasoning_effort` → `ValueError`.
6. Dit document + een korte verwijzing in `kanban-followups.md` zodat de beslissing
   vindbaar is voor de afhankelijke "Agent Bridge UI-cluster"-kaart.

**Geen schemawijziging, geen nieuwe DB-tabellen, geen nieuwe MCP-tools.**
