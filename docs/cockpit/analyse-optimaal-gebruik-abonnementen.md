# Analyse: optimaal gebruik van meerdere abonnementen (Anthropic + MiniMax)

**Datum:** 2026-07-06
**Status:** Analyse / voorstel — niets hiervan is gebouwd, dit is input voor vervolgkaarten.
**Trigger:** kanban-kaart "Analyse optimaal gebruik meerdere subscripties".

---

## 1. De vraag

Je hebt een Anthropic-abonnement (Claude Code, Sonnet 5 e.a.) en een MiniMax-abonnement
(M3). Je wilt beide zo optimaal mogelijk benutten. Eigen voorstel: een **Analyse-kolom**
(Sonnet 5, Anthropic-sub) en een **Engineer-kolom** (M3, MiniMax-sub) op het kanban-bord.
Gevraagd: bekijk dit voorstel, maar denk breder — provider-agnostisch en toekomstgericht.

## 2. Bestaande bouwstenen in Cockpit (grounded facts)

Dit is geen leeg blad — er staat al opvallend veel infrastructuur die dit voorstel
(bijna) gratis mogelijk maakt. Onderzocht: `backend/app/services/providers/`,
`backend/app/kanban/dispatch.py`, `backend/app/kanban/models.py`,
`backend/app/services/usage_service.py`, `docs/cockpit/spike-claude-code-model-switching.md`,
`docs/cockpit/kanban-dispatch-spec.md`, `frontend/src/features/cc-bridge/NewSessionDialog.tsx`.

### 2.1 Subscriptie-switch bestaat al (Laag 1 uit de eerdere spike is klaar)

`backend/app/services/providers/platform_env.py` heeft `PLATFORM_ANTHROPIC`,
`PLATFORM_BEDROCK` én **`PLATFORM_MINIMAX`**, met env-var-injectie
(`ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL=MiniMax-M3[1m]`,
`CLAUDE_CODE_AUTO_COMPACT_WINDOW`) — inclusief tests
(`test_platform_env.py`, `test_minimax_config.py`, `test_minimax_credentials.py`).
De MiniMax-API-key wordt beheerd via de **Subscriptions-pagina**
(`frontend/src/features/subscriptions/`, hernoemd van "Providers") en opgeslagen in
`.env` (`minimax_credentials.py`), nooit in de database of terug naar de browser.

**Belangrijkste architecturale grens** (bevestigd in
`spike-claude-code-model-switching.md` §11.5): een sessie is óf Anthropic-OAuth
(abonnement) óf een kale API-key tegen een ander endpoint — nooit beide. Zodra
`ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` gezet wordt, slaat Claude Code de
abonnements-login voor die sessie volledig over. Je kunt dus **niet** binnen één
sessie "credits mixen" tussen Anthropic en MiniMax — je kunt wel **verschillende
sessies** elk aan een andere subscriptie hangen. Elke optie hieronder werkt met die
grens, niet ertegenin.

### 2.2 Kanban-kolom → rol/persona routing bestaat al — en platform ontbreekt daar

`backend/app/kanban/dispatch.py` heeft al een mechanisme dat **exact** past bij het
voorstel, alleen dan voor de *rol* van de agent, niet (nog) voor het *platform*:

- Een kolomnaam matcht 1-op-1 met een persona-bestand `.claude/agents/<kolom>.md`
  (`_persona_filename`, `_read_persona`). Dit is letterlijk hoe **deze sessie**
  gestart is: de kaart stond in de "Engineer"-kolom en de systeemprompt hierboven
  komt uit `.claude/agents/Engineer.md` (of vergelijkbaar).
- `KanbanCard.agent` (override) en `KanbanColumn.default_agent`
  (`backend/app/kanban/models.py:44,86`) bestaan al als schemavelden.
- `KanbanCard.transport` (`worktree`/`sandcastle`) en een `set_default_transport`
  per project bestaan al (`dispatch.py`).

**Gap, bevestigd via grep**: nergens in `dispatch.py`, `KanbanCard`, of
`KanbanColumn` komt het woord `platform` voor. `SpawnCommandOptions.platform`
(`providers/base.py:42`) wordt vandaag **alleen** gezet vanuit de handmatige
"CC Bridge → New Session"-dialoog (`NewSessionDialog.tsx`), niet vanuit
auto-dispatch. Elke kaart die de dispatcher oppikt, spawnt dus altijd met het
Anthropic-abonnement, ongeacht kolom. Dit is precies het gat dat optie A hieronder
dicht — en het is een kleine, bestaande-patroon-volgende uitbreiding, geen nieuw
concept.

### 2.3 Er is al een 4-provider agent-CLI-registry — inclusief een MiniMax-*native* CLI

`backend/app/services/providers/` registreert vier `AgentProvider`-implementaties:
`ClaudeCodeProvider`, `CodexCliProvider`, `OpenCodeProvider`, en **`MiMoCodeProvider`**
(`mimo_code.py`, binary `mimo`, config in `~/.mimocode`). Dit is een **agent-CLI**-abstractie
(welk programma in de tmux-pane draait), los van het platform/LLM-laagje uit §2.1.
Interessant: MiMoCode is (voor zover uit de naam/config op te maken) een MiniMax-eigen
CLI — dat is een tweede, mogelijk directer pad naar de MiniMax-subscriptie dan
"Claude Code CLI met omgezette env vars" (zie optie C).

### 2.4 Er wordt al 5-uurs "billing block"-gebruik bijgehouden

`backend/app/services/usage_service.py` kent `SESSION_DURATION_HOURS = 5` en
`identify_session_blocks()`: het leest lokale Claude Code JSONL-logs en groepeert ze
in Anthropic's eigen 5-uurs rate-limit-vensters (Pro/Max). Dit bestaat vandaag puur
voor dashboards/rapportage (`/api/v1/usage/*`), maar is exact het signaal dat nodig
is voor een quota-bewuste router (optie B).

### 2.5 Scheduled-messages (fase 2) is klaar en injecteert al op tijd/cron in sessies

Per `00-orientation.md`: fase 2 (timer/cron → tmux `send-keys`-injectie, APScheduler +
SQLite-jobstore, wachten-tot-idle via CC-hooks) is **functioneel compleet** (backend
139 tests groen, frontend clean build). Dit is generieke infrastructuur om iets **op
een gepland moment in een lopende of nieuw-gespawnde sessie te zetten** — niet
gebonden aan één use-case. Dat maakt het herbruikbaar voor tijd-gebaseerde
provider-routing (optie D).

---

## 3. Opties

### Optie A — Kolom-naar-platform routing (het eigen voorstel, uitgewerkt)

**Idee:** een "Analyse"-kolom met `default_platform = anthropic` +
persona/model Sonnet 5, en een "Engineer"-kolom met `default_platform = minimax`
+ `ANTHROPIC_MODEL=MiniMax-M3[1m]`. De dispatcher leest dit bij het spawnen van
elke kaart.

**Wat er al staat:** vrijwel alles uit §2.1 en §2.2 — de persona-per-kolom routing
is het bewijs dat het patroon werkt, en `platform_env.py` heeft de MiniMax-branch al
klaar staan.

**Wat nog gebouwd moet worden (klein, volgt bestaand patroon 1-op-1):**
1. `default_platform` (+ eventueel `default_model`) als kolomveld naast
   `default_agent`, of als `KanbanMeta`-key per kolom (net als
   `TRANSPORT_PREFIX`/`SHIPMODE_PREFIX` vandaag per-*project* werken — hier zou het
   per-*kolom* moeten, dus een kleine variatie).
2. `dispatch_project` geeft `platform=<resolved>` mee aan de transport, die het
   doorzet naar `SpawnCommandOptions.platform` — dezelfde weg die de handmatige
   dialoog al gebruikt.
3. UI: een platform-select op de kolom-instellingen (kanban-settings), naast de
   bestaande transport/ship-mode-toggles.

**Effort:** klein–middel (S/M) — geen nieuw concept, wel meerdere kleine
aanpassingen verspreid over backend-model, dispatch en frontend.

**Risico:** een kaart die per ongeluk in de MiniMax-kolom belandt terwijl het werk
Anthropic-specifieke tooling nodig heeft (bv. een feature die alleen met een
Anthropic-model getest kan worden) — mitigeer met een duidelijke kolomnaam en een
`comment`-hint in de card-prompt over welk platform actief is.

### Optie B — Kwota-bewuste auto-failover (bouwt voort op de bestaande usage-tracking)

**Idee:** in plaats van (of naast) een statische kolom-toewijzing: de dispatcher
checkt vóór het spawnen `identify_session_blocks()` (§2.4) voor het huidige
5-uurs-blok. Nadert het blok een ingestelde drempel (bv. 80% van een bekend
Pro/Max-token-budget), dan routeert de dispatcher **nieuw** te spawnen kaarten
automatisch naar `PLATFORM_MINIMAX`, ongeacht kolom — en logt dit zichtbaar
(`comment` op de kaart: "auto-geswitcht naar MiniMax, Anthropic-blok op 82%").

**Waarom dit meer is dan de eerdere spike al beloofde:** de eerdere spike
concludeerde dat CCR's fallback-routing "nice-to-have, niet MVP" was en de trigger-
conditie (reageert het op een 429, of enkel op statische regels?) nog onbevestigd
was. Dit optie B heeft dat probleem niet: het gebruikt Cockpit's **eigen, al
werkende** 5-uurs-blok-berekening in plaats van te vertrouwen op een externe
gateway's ongedocumenteerde fallback-logica. Geen CCR nodig, geen extra proces —
puur een extra check vóór de bestaande `spawn`-stap in `dispatch.py`.

**Wat nog gebouwd moet worden:** een drempel-instelling (globaal of per project),
een call naar `usage_service` vanuit `dispatch.py`, en het overschrijven van de
kolom's `default_platform` wanneer de drempel geraakt wordt.

**Effort:** middel (M) — het rekenwerk bestaat, de integratie in de dispatch-flow
is nieuw.

**Risico:** vals-positieve switches als de blok-berekening off-by-one zit t.o.v.
Anthropic's eigen (niet-publieke) tellogica — begin met alleen loggen/waarschuwen
("zou nu switchen") vóór je 'm daadwerkelijk laat switchen.

### Optie C — MiniMax-native CLI in plaats van env-var-omleiding

**Idee:** `MiMoCodeProvider` (§2.3) bestaat al als vierde geregistreerde
`AgentProvider`. Onderzoek of dit een MiniMax-eigen CLI is die rechtstreeks tegen
de MiniMax-subscriptie praat (analoog aan hoe Codex CLI rechtstreeks tegen een
OpenAI-account praat) — dan is dit voor MiniMax-werk mogelijk robuuster dan Claude
Code CLI + omgezette Anthropic-env-vars (geen risico op de settings.json-conflicten
die de CCR-spike in §11.4 blootlegde bij een vergelijkbare aanpak).

**Waarom dit provider-agnostisch is:** dit veralgemeent naar "voor elke extra
subscriptie: gebruik bij voorkeur de eigen native CLI via de bestaande
`AgentProvider`-registry, met platform-env-injectie als fallback wanneer er geen
native CLI is." Een kolom kan dan zowel `default_agent` (welke CLI) als
`default_platform` (welke env/auth) kiezen — orthogonale assen.

**Wat nog gebouwd moet worden:** eerst verifiëren wat MiMoCode precies is/doet
(is dit al geïnstalleerd, waar staat de credential-flow) — dit is zelf een kleine
spike-kaart, geen implementatie.

**Effort:** klein (spike) om te bepalen, daarna afhankelijk van bevindingen.

### Optie D — Tijd-/planning-gebaseerde routing via scheduled-messages

**Idee:** gebruik de al werkende scheduler (§2.5) om **wanneer** iets draait te
sturen, niet alleen **welk platform**. Twee concrete toepassingen:
1. **Reset-window-batching**: zware/lange analysetaken (bv. een groot refactor-plan)
   automatisch plannen vlak ná een Anthropic-blok-reset, zodat je het volle
   5-uurs-budget hebt in plaats van halverwege een blok te beginnen.
2. **Overflow naar MiniMax buiten kantooruren**: kaarten die 's nachts of in het
   weekend door de auto-dispatcher opgepikt worden (dus zonder dat jij het gebruik
   in de gaten houdt) standaard naar de MiniMax-kolom/platform routeren, zodat
   onbewaakte batch-runs nooit je Anthropic-kwota opeten terwijl je zelf later op
   de dag interactief met Sonnet 5 wilt werken.

**Wat nog gebouwd moet worden:** een cron-regel op projectniveau ("na 22:00 en
vóór 07:00: forceer platform=minimax voor auto-dispatch"), die de scheduler-service
al kan uitdrukken als een periodieke actie die `KanbanMeta`/kolom-instellingen
wijzigt.

**Effort:** klein–middel (S/M), leunt volledig op reeds gebouwde infrastructuur.

### Optie E — Drieledige kosten/capaciteit-ladder (niet alleen 2 kolommen)

**Idee:** in plaats van een binaire Anthropic/MiniMax-knop, een expliciete ladder:
**Sonnet 5** (zwaar redeneerwerk: architectuur, analyse, code review) →
**Haiku 4.5** (hoog-volume routinewerk, nog steeds binnen de Anthropic-sub, geen
extra kosten) → **MiniMax M3** (het meest bulk-achtige, parallelliseerbare
uitvoeringswerk, aparte subscriptie/kwota). Dit voegt een gratis tussenlaag toe
(Haiku zit al in dezelfde Anthropic-sub) vóór je uberhaupt naar de tweede
subscriptie hoeft te escaleren.

**Waarom dit de moeite waard is naast optie A:** optie A alleen (2 kolommen)
laat de goedkopere Anthropic-modelkeuze (Haiku) onbenut als tussenoptie — een
3-koloms-ladder (Analyse/Sonnet, Routine/Haiku, Bulk-engineer/MiniMax) spreidt
de belasting fijnmaziger en houdt meer werk volledig binnen de al betaalde
Anthropic-sub voordat de MiniMax-kwota wordt aangesproken.

**Effort:** klein (S) bovenop optie A — is in essentie gewoon een derde kolom met
een ander `ANTHROPIC_MODEL`, geen nieuw platform nodig voor de Haiku-stap.

### Optie F — MiniMax als resilience-laag, los van kwota

**Idee:** naast kosten/kwota-motieven: MiniMax-platform-switch is ook een kant-
en-klare **failover bij een Anthropic-incident** (outage, degraded performance).
De dispatcher zou bij een reeks spawn-/API-fouten binnen een tijdvenster tijdelijk
kunnen overschakelen op `PLATFORM_MINIMAX` voor nieuwe kaarten, los van enige
kwota-berekening — puur beschikbaarheid.

**Waarom dit vermeldenswaard is:** dit is een andere trigger (fouten, niet
budget) op dezelfde onderliggende mechaniek (§2.1), dus vrijwel gratis mee te
nemen als een tweede "reden om te switchen" in dezelfde routinglaag als optie B.

**Effort:** klein, als optie B al gebouwd is (hergebruikt dezelfde
switch-mechaniek, andere trigger-bron).

### Optie G — Concurrency/doorvoer-argument (operationeel, geen nieuwe code)

**Idee:** `dispatch.py` hanteert een concurrency-cap per project
(`DEFAULT_MAX_SESSIONS = 3`, memory-aware queuing via `get_memory_status_cached`).
Twee onafhankelijke subscripties met onafhankelijke rate-limits betekent dat je de
**effectieve** doorvoer van het bord kunt verhogen door bewust meer gelijktijdige
kaarten toe te staan wanneer een deel daarvan toch tegen een andere kwota loopt.

**Belangrijke kanttekening (eerlijkheid, geen overclaim):** de huidige cap bestaat
niet alleen om Anthropic-kwota te sparen maar ook om de **hardware** van de
gedeelde machine te beschermen (CPU/RAM voor tmux-panes/worktrees, zie
`memory_monitor`/`PendingQueue`). Twee subscripties lossen het kwota-plafond op,
maar niet het hardware-plafond — "2x doorvoer" is dus alleen waar als de machine
ook 2x de gelijktijdige sessies aankan. Dit is puur een configuratie-inzicht
(verhoog `max_sessions` bewust wanneer platform-diversiteit dat rechtvaardigt),
geen nieuwe code.

---

## 4. Aanbeveling — gefaseerd

| Fase | Optie | Waarom eerst | Effort |
|---|---|---|---|
| 1 | **A** — kolom→platform routing | Dit is letterlijk de gevraagde kaart, het patroon (kolom→iets) bestaat al 1-op-1 voor persona's, en het levert onmiddellijk het gevraagde 2-koloms-gedrag op. | S/M |
| 2 | **E** — Haiku als gratis tussenlaag | Bijna gratis bovenop fase 1 (derde kolom, geen nieuw platform), verhoogt het aandeel werk dat binnen de bestaande Anthropic-sub blijft. | S |
| 3 | **D** — tijd-gebaseerde overflow (nacht/weekend → MiniMax) | Hergebruikt de al-voltooide scheduler, voorkomt dat onbewaakte auto-dispatch je dag-kwota opeet. | S/M |
| 4 | **B** — kwota-bewuste auto-failover | Waardevol maar vereist zorgvuldige validatie van de bloktellogic voordat het automatisch (i.p.v. alleen loggend) switcht. | M |
| 5 | **F** — resilience-failover | Bijna gratis zodra B er is (zelfde mechaniek, andere trigger). | S (na B) |
| — | **C** — MiMoCode-native-pad | Eerst een korte spike om te bepalen of dit meerwaarde heeft t.o.v. de al-werkende env-var-switch. | Spike |
| — | **G** — concurrency-cap heroverwegen | Geen bouwwerk, wel een bewuste instelling zodra fase 1 draait. | — |

**Kernboodschap:** dit is geen "kies tussen 7 opties" — het zijn grotendeels
**opeenstapelbare lagen** bovenop dezelfde twee primitieven die al bestaan
(`platform_env.py`'s platform-switch, en kolom-gebaseerde routing in
`dispatch.py`). Fase 1 alleen al levert het gevraagde voorstel op; fase 2–5 zijn
verfijningen die er los van elkaar bovenop kunnen.

## 5. Toekomstgerichtheid / provider-agnostisch

De architectuur die hierboven gebruikt wordt is expliciet niet aan MiniMax
gebonden:
- `platform_env.py` volgt al het patroon "een nieuwe `PLATFORM_X`-constante +
  een branch die env-vars zet" (Bedrock, MiniMax) — een derde/vierde subscriptie
  (bv. Z.AI, DeepSeek, Kimi, GLM, elk met een Anthropic-Messages-compatibele of
  OpenAI-compatibele endpoint) volgt hetzelfde recept.
- De `AgentProvider`-registry (§2.3) ondersteunt al vier CLI's naast elkaar — een
  vijfde native CLI is een nieuwe class, geen redesign.
- Kolom→instelling-routing (persona, en straks platform) is per-kolom
  configureerbaar via het bord zelf, dus een gebruiker kan zelf N kolommen met N
  provider-combinaties inrichten zonder dat de dispatcher-code daarvoor per
  provider hoeft te weten wat "MiniMax" specifiek betekent — het leest gewoon een
  configwaarde.

Dit betekent dat de investering in optie A niet MiniMax-specifiek is: het is een
investering in "kolommen bepalen welk platform + welke CLI + welke persona een
kaart krijgt", waar MiniMax vandaag de eerste concrete invulling van is.

## 6. Open vragen / risico's

- **MiniMax ToS bij CLI-gebruik**: al gevalideerd in de eerdere spike (§2 van
  `spike-claude-code-model-switching.md`) — MiniMax documenteert dit zelf als
  ondersteund gebruikspatroon. Geen actie nodig.
- **Kostenzichtbaarheid**: `usage_service.py` volgt vandaag alleen Anthropic-
  gebruik (lokale Claude Code JSONL-logs). Voor een eerlijke vergelijking "hoeveel
  heb ik op elk platform gebruikt" zou ook MiniMax-verbruik gemeten moeten worden —
  mogelijk niet lokaal beschikbaar (MiniMax-dashboard i.p.v. lokale logs), dus dit
  vraagt een aparte data-bron, geen quick win.
- **Testbaarheid van modelkeuze**: sommige taken (bv. tool-use-zware agentic
  workflows) presteren mogelijk aantoonbaar verschillend tussen Sonnet 5 en
  MiniMax-M3 — de kolomindeling moet in de praktijk gevalideerd worden op
  daadwerkelijke kaart-uitkomsten, niet aangenomen.
- **Optie B's trigger-betrouwbaarheid**: begin read-only/loggend (zie fase 4),
  niet meteen automatisch switchend, om te voorkomen dat een verkeerd
  ingeschatte blokgrens werk ongewild naar het verkeerde platform stuurt.

## 7. Voorgestelde vervolgkaarten

1. "Kolom-naar-platform routing in kanban auto-dispatch" (optie A, fase 1)
2. "Haiku 4.5 als tussen-kolom toevoegen naast Analyse/Engineer" (optie E, fase 2)
3. "Tijd-gebaseerde platform-overflow via scheduled-messages" (optie D, fase 3)
4. "Spike: MiMoCode-provider — native MiniMax-CLI-pad verifiëren" (optie C)
5. "Kwota-bewuste platform-auto-failover (loggend, niet actief)" (optie B, fase 4)
