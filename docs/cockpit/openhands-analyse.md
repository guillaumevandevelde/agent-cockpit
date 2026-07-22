---
title: "Analyse — OpenHands: wat kunnen we overnemen of leren?"
type: analysis
status: active
---

# Analyse — OpenHands: wat kunnen we overnemen of leren?

**Datum:** 2026-07-13
**Status:** Analyse / beslisdocument (read-only spike; geen implementatie in deze kaart)
**Trigger:** kanban-kaart "Analyseer openhands". Gebruiker:
> "Analyseer openhands en kijk wat wij kunnen overnemen van functionaliteiten of
> leren van de toepassing; https://github.com/OpenHands/OpenHands. Mature toepassing
> die doet wat wij willen, maar kan niet overweg met abonnementen, enkel token based."

**Verwant:**
[`build-prioriteiten-analyse.md`](./build-prioriteiten-analyse.md) (§3.3 markeerde OpenHands
al als sterkste externe-integratie-kandidaat + agent-onafhankelijkheids-hedge),
[`orchestration-substrate-decision.md`](./orchestration-substrate-decision.md) (tmux-scraping
vs. gestructureerde events — de as waarop OpenHands' architectuur het scherpst afwijkt),
[`subscription-flexibiliteit-analyse.md`](./subscription-flexibiliteit-analyse.md) +
[`subscriptions.md`](./subscriptions.md) (abonnement-model),
[`spike-claude-code-model-switching.md`](./spike-claude-code-model-switching.md) (provider-switch).

---

## TL;DR

1. **De premisse van de kaart klopt niet meer helemaal.** OpenHands' *native* agent
   (`CodeActAgent` op LiteLLM) is inderdaad **token-/API-gebaseerd**. Maar sinds medio
   2026 kan OpenHands via het **Agent-Client Protocol (ACP)** een *externe* subscription-
   gedekte CLI aansturen — **Claude Code, Codex CLI, Gemini CLI** — waarbij "your
   subscription covers the inference cost". OpenHands is dus met precies dezelfde
   beweging bezig als Cockpit: **een abonnement-gedekte CLI orkestreren i.p.v. per-token
   te betalen.** De abonnement-kloof is smaller dan de kaart aanneemt; ons echte
   onderscheid is *subscription-first by design* (wij spawnen alleen echte CLIs), niet
   *subscription-only-mogelijk*.

2. **De sterkste les is architectonisch, niet functioneel.** OpenHands' kern is een
   **getypeerde Action/Observation-event-stream** + een **client-server
   ActionExecutionServer** achter een **Runtime-abstractie** (Docker/Local/Remote). Dat
   is exact het *gestructureerde-events*-model dat onze eigen
   [`orchestration-substrate-decision.md`](./orchestration-substrate-decision.md) als
   volgende stap aanbeveelt — waar wij vandaag nog terminal-tekst scrapen. OpenHands is
   het levende bewijs dat dit model op schaal werkt. **ACP is bovendien een kant-en-klaar,
   gestandaardiseerd transport** dat onze bespoke `agentic_cli`-capability-matrix deels
   kan vervangen i.p.v. per-CLI zelf een stream-parser te bouwen.

3. **Concreet overnemen (hoogste leverage eerst):** (a) **ACP evalueren als
   gestructureerd transport** achter de bestaande `SpawnTransport`-seam; (b)
   **path-/keyword-getriggerde skills** (glob-gebonden context-injectie) bovenop onze
   always-on/slash-skills; (c) **event-getriggerde automations** (GitHub/webhook) als
   uitbreiding van scheduled-messages; (d) **sub-agent-delegatie-patroon** (TaskToolSet)
   voor de multi-agent-laag. **Bewust niet overnemen:** de LiteLLM token-native default,
   hun volledige runtime/SDK-herbouw (wij hebben worktree + Sandcastle al).

---

## 1. Wat is OpenHands (gegronde feiten, staat medio 2026)

OpenHands (voorheen *OpenDevin*, ~80k⭐, All-Hands AI) is een volwassen, open-source
"developer control center": een self-hosted platform dat coding-agents als een altijd-
aanwezig engineering-team laat draaien. Het bestaat uit vier lagen:

| Laag | Rol | Cockpit-tegenhanger |
|---|---|---|
| **Agent Canvas** (frontend) | Web-UI die met **meerdere** Agent Servers kan verbinden en ertussen wisselt. | Cockpit-frontend (single-host). |
| **Agent Server** | REST-API die meerdere agents op één host draait. | FastAPI-backend + `runs/`/`kanban/dispatch`. |
| **Automation Server** | Scheduled + **event-getriggerde** runs (GitHub-webhooks, Slack, Linear, Notion). | Scheduled-messages (timer/cron) — **geen** webhook-triggers. |
| **Software Agent SDK** | Framework om agents te bouwen (CodeActAgent, delegatie, condenser). | n.v.t. — wij *orkestreren* CLIs, bouwen geen agent. |

**Kernabstracties in de agent-kern (uit de architectuurdocs):**

- **Event Stream + Action/Observation-lus.** De agent produceert getypeerde `Action`s en
  consumeert getypeerde `Observation`s via één centrale `EventStream`. Alle state/history
  loopt hierlangs. Dit is de bron van waarheid — geen tekst-scraping.
- **Runtime-abstractie** met een **client-server `ActionExecutionServer`** (REST:
  `/execute_action`, `/alive`). Implementaties: **Docker / Local / Remote / Apptainer
  (HPC) / API**. Plugins: Bash-sessie, Jupyter, BrowserEnv.
- **Context Condenser** — expliciete geheugen-condensatie die de conversatie-history
  inkort om tokens te sparen.
- **Sub-agent-delegatie** — een `TaskToolSet` delegeert werk aan gespecialiseerde
  sub-agents die *synchroon* draaien; daarnaast file-based agents (Markdown-definities).
- **Skills** (voorheen *microagents*) — zie §4.2.
- **LiteLLM** als provider-abstractie (OpenAI/Azure/Bedrock/Gemini/Groq/OpenRouter/
  Moonshot…), met model-routing + fallback-strategieën.
- **Agent-Client Protocol (ACP)** — JSON-RPC 2.0-protocol dat de UI ontkoppelt van de
  onderliggende agent, zodat "the editor, canvas, or SDK can become a stable surface
  while the underlying agent is swappable" — zie §3.

## 2. Cockpit ↔ OpenHands — waar staan we tegenover elkaar

| As | OpenHands | Agent Cockpit |
|---|---|---|
| **Executie-substraat** | Getypeerde Action/Observation-event-stream + REST `ActionExecutionServer`. | tmux `send-keys` + subprocess-CLI in worktree; hybride hooks (push) + **terminal-scraping** (bros). |
| **Billing (default)** | Token/API via LiteLLM. | **Subscription-first**: spawnt echte CLI die op OAuth-abonnement rijdt. |
| **Billing (uitbreiding)** | **ACP → subscription-gedekte CLI** (Claude Code/Codex/Gemini). | (Al de default.) Token-pad is de *hedge*, niet de kern. |
| **Isolatie** | Runtime-menu: Docker/Local/Remote/Apptainer/API. | `SpawnTransport`: worktree (tmux) + Sandcastle (podman). |
| **Multi-agent** | Sub-agent-delegatie (TaskToolSet), synchroon. | Analyst → kind-kaarten met dep-DAG, **async** via kanban-dispatch. |
| **Context-injectie** | Skills: always-on (`AGENTS.md`) + **keyword-** + **path/glob-getriggerd** + org/global registry. | Superpowers-skills (always-on/slash) + memory + CLAUDE.md. **Geen path/glob-trigger.** |
| **Triggers** | Scheduled **+ event/webhook** (GitHub/Slack/Linear/Notion). | Scheduled (timer/cron) + kanban-poll. **Geen webhook.** |
| **Human-in-the-loop** | Opaak proces; UI herbouwd bovenop event-stream. | **Echte attachbare tmux-pane** — mens kan live overnemen (`tmux attach`). |
| **Volwassenheid** | ~80k⭐, 72% SWE-bench, jaren productie. | Jonge fork; nichewaarde = subscription-native + human-takeover. |

De twee platforms **convergeren**: OpenHands begon token-native en bouwt nu ACP-brug naar
subscription-CLIs; Cockpit begon subscription-native en overweegt (in
`orchestration-substrate-decision.md`) een gestructureerd-events-transport. Het
**convergentiepunt is een gestructureerd run-protocol** — en daar heeft OpenHands met ACP
een voorsprong die wij kunnen lenen i.p.v. heruitvinden.

## 3. De abonnement-premisse herzien (belangrijkste correctie)

De kaart stelt: *"kan niet overweg met abonnementen, enkel token based."* Dat was tot ±
begin 2026 grotendeels waar voor OpenHands' **native** agent (LiteLLM → API-key → per-token
billing). Maar de actuele situatie is genuanceerder:

- **ACP delegeert naar een lokaal draaiende CLI met diens eigen credentials** — inclusief
  het bestaande abonnement. Een Claude-Max-abonnee kan Agent Canvas/SDK op de lokale
  Claude Code richten, en "the subscription covers inference costs rather than incurring
  per-token API charges". Op OpenHands Cloud wordt "your subscription plan's
  authentication injected at session start".
- Dit is **architectonisch identiek aan Cockpit's kernkeuze**: niet de LLM-API aanroepen,
  maar de abonnement-gedekte *harnas* orkestreren.
- **Nuance (billing-risico, 2026):** rond 15 juni 2026 kondigde Anthropic een repricing
  aan die Agent-SDK/`claude -p`/ACP-gebruik onder Pro/Max zou hebben geraakt; die
  wijziging is **gepauzeerd** (16 juni 2026) — ACP/`claude -p`/SDK werken voorlopig als
  voorheen onder abonnement. Dit raakt **beide** platforms gelijk en versterkt de
  hedge-redenering uit `build-prioriteiten-analyse.md` §4: het abonnement-substraat is een
  platform-risico, niet iets waarop je blind moet leunen.

**Gevolg voor de strategie:** het "wij kunnen abonnementen, zij niet"-onderscheid is
**geen duurzame moat**. Ons echte, verdedigbare onderscheid is smaller en scherper:
*subscription-first by design* (wij spawnen uitsluitend echte CLIs, token-billing is de
uitzondering/hedge) **plus** de **echte attachbare tmux-pane voor human-takeover** — een
transparantie-eigenschap die OpenHands' opake event-stream-proces niet gratis teruggeeft
(zie `orchestration-substrate-decision.md` §4.5). Dát moeten we bewaken, niet "abonnementen
kunnen".

## 4. Wat we concreet kunnen overnemen (gerangschikt op leverage)

### 4.1 ⭐ ACP als gestructureerd transport achter `SpawnTransport` (hoogste strategische waarde)

`orchestration-substrate-decision.md` (§5) beveelt al aan om een **headless/gestructureerd
transport** naast tmux te introduceren en agent-onafhankelijkheid in de
**capability-matrix** te absorberen. OpenHands' **ACP** is precies zo'n gestandaardiseerd,
getypeerd (JSON-RPC 2.0) run-protocol — en er bestaan **al ACP-adapters voor Claude Code,
Codex en Gemini CLI**. De vraag die dit oproept: bouwen we per CLI een eigen
stream-json-parser (huidige `agentic_cli`-lijn), of **adopteren we ACP als het transport**
zodat één integratie meteen meerdere subscription-CLIs afdekt en interoperabel is met de
bredere ACP-wereld (Zed, VS Code, JetBrains)?

- **Wat het oplevert:** getypeerde liveness/exit/rate-limit/usage-events i.p.v.
  pane-scraping (elimineert het scraping-residu uit `orchestration-substrate-decision.md`
  §2.3); agent-onafhankelijkheid als *protocol-eigenschap* i.p.v. per-CLI-adapter; directe
  overlap met de "tweede executor-provider"-hedge uit `build-prioriteiten-analyse.md` §3.3.
- **Wat te bewaken:** ACP is (nog) niet universeel; de tmux-pane blijft de default voor
  human-in-the-loop (§3). Dit is een *transport-optie*, geen big-bang-migratie.
- **Niveau:** spike/analyse — verdient een eigen kaart die ACP tegen de bestaande
  `SpawnTransport` + `agentic_cli`-capability-matrix afzet.

### 4.2 ⭐ Path- en keyword-getriggerde skills (context-injectie op glob/keyword)

OpenHands' skill-model (AgentSkills-standaard, `SKILL.md` + frontmatter) kent drie
laadmodellen die rijker zijn dan het onze:

- **Always-on** (`AGENTS.md`) ≈ onze CLAUDE.md.
- **Keyword-getriggerd** — skill activeert op een keyword in de prompt.
- **Path/glob-getriggerd** — een regel wordt geïnjecteerd zodra de agent een bestand
  aanraakt dat matcht met een glob (bv. `backend/**/*.py` → "gebruik async SQLAlchemy").

Cockpit heeft superpowers-skills (always-on of `/slash`) + memory + CLAUDE.md, maar
**geen path/glob-getriggerde injectie**. Voor een repo-onafhankelijk platform is dat een
natuurlijke uitbreiding: per doel-repo declareer je regels die alleen laden wanneer
relevant, i.p.v. één grote always-on CLAUDE.md. Sluit aan op de bestaande skills-/memory-
laag; laag-risico, duidelijke waarde.

### 4.3 Event-/webhook-getriggerde automations (uitbreiding scheduled-messages)

OpenHands' Automation Server draait niet alleen op schedule maar ook op **externe events**:
GitHub-PR-geopend, webhook, Slack-mention → spawn een agent. Cockpit heeft de scheduler-
helft (timer/cron, APScheduler in-process) maar **geen event-trigger**. Pre-built
automations (PR-review, Slack-monitoring) zijn een concreet patroon dat op onze
scheduled-messages + kanban-dispatch-laag past: een webhook-endpoint dat een kaart aanmaakt/
een sessie spawnt. Natuurlijke facet-uitbreiding; middel-groot.

### 4.4 Sub-agent-delegatie-patroon (TaskToolSet) voor de multi-agent-laag

OpenHands' `TaskToolSet` laat een agent **synchroon** werk delegeren aan gespecialiseerde
sub-agents binnen één run. Cockpit's multi-agent-laag is **async** (analyst → kind-kaarten
→ aparte executor-sessies). Beide modellen zijn geldig; de les is dat een *synchrone*
in-sessie-delegatie (zoals onze `Agent`-tool-subagents) en de *asynchrone* kanban-
decompositie **complementair** zijn. Waard om te bekijken bij de volgende iteratie van de
multi-agent-flow — niet om te vervangen, wel om het onderscheid bewust te maken.

### 4.5 Kleinere leerpunten (noteren, niet nu bouwen)

- **Context Condenser** — expliciete history-condensatie. Voor ons grotendeels afgedekt
  door de CLI-harnas zelf (auto-summarization); relevant zodra we headless/SDK-runs zonder
  ingebouwde condensatie draaien.
- **Runtime-menu (Remote/Apptainer/API)** — breder isolatie-menu dan ons worktree +
  Sandcastle. Input voor de Sandcastle-hardening-lijn (`build-prioriteiten-analyse.md` §3.4),
  niet dringend.
- **Agent Canvas multi-server-switching** — één UI naar meerdere agent-servers. Relevant
  voor de portfolio-/multi-repo-schaal (`portfolio-orchestratie.md`), niet nu.
- **LiteLLM model-routing/fallback** — parallel aan onze subscription-pool-router-idee
  (`subscription-flexibiliteit-analyse.md` §4, Optie A). Referentie-implementatie om van
  te lenen, niet om over te nemen (LiteLLM = token-pad).

## 5. Wat we bewust NIET overnemen

1. **De LiteLLM token-native default als primair pad.** Botst frontaal met subscription-
   first. Veelzeggend: OpenHands zélf beweegt via ACP naar subscription-delegatie — de
   richting die wij al gekozen hebben.
2. **Hun volledige Runtime/ActionExecutionServer herbouwen.** Wij hebben worktree + tmux +
   Sandcastle; een parallelle REST-executieserver is een big-bang zonder tussentijdse
   waarde (`orchestration-substrate-decision.md` §4.4). *Leer* het event-model, kopieer niet
   de infrastructuur.
3. **De Software Agent SDK.** OpenHands *bouwt* een agent; Cockpit *orkestreert* bestaande
   CLIs. Een eigen CodeActAgent bouwen is buiten onze bestaansreden.
4. **Opaak-proces-model voor human-in-the-loop.** De attachbare tmux-pane is onze
   transparantie-troef (§3, `orchestration-substrate-decision.md` §4.5) — die geven we niet
   op voor een puur event-stream-proces.

## 6. Aanbeveling

**OpenHands bevestigt onze richting meer dan dat het die omverwerpt.** De abonnement-kloof
uit de kaart is grotendeels gedicht (via ACP) en was nooit een duurzame moat; ons echte
onderscheid is *subscription-first + attachbare human-takeover*. De hoogste-waarde
overname is **architectonisch**: adopteer een **gestructureerd run-protocol** (ACP als
sterke kandidaat) achter de bestaande `SpawnTransport`-seam voor autonoom-gedispatchte
sessies, en houd tmux als default voor interactief werk — exact de lijn die
`orchestration-substrate-decision.md` al uitzette, nu met OpenHands/ACP als concrete,
bewezen invulling i.p.v. een zelf-te-bouwen stream-parser.

Volgorde van waarde: **ACP-transport-spike (4.1) > path/keyword-skills (4.2) >
event-automations (4.3) > delegatie-patroon (4.4)**. Geen van deze is dringend; 4.1 is de
strategisch belangrijkste omdat het tegelijk het scraping-residu opruimt, de
agent-onafhankelijkheids-hedge invult, én ons interoperabel maakt met het bredere
ACP-ecosysteem.

## 7. Voorgestelde vervolgkaarten (tekst; niet in deze kaart aangemaakt)

> Deze spike maakt géén kanban-kaarten aan. Onderstaande zijn voorstellen die een mens kan
> prioriteren en op het bord kan zetten.

1. **[spike/analysis] ACP (Agent-Client Protocol) als gestructureerd transport achter
   `SpawnTransport`.** Zet ACP (JSON-RPC 2.0, adapters voor Claude Code/Codex/Gemini) af
   tegen de bestaande `agentic_cli`-capability-matrix + de headless stream-json-optie uit
   `orchestration-substrate-decision.md` §6.1. Beslis: ACP adopteren als transport, of per
   CLI een eigen stream-parser bouwen? Lever go/no-go + gescopete implementatiekaarten.
   *Consumeert/overlapt met de headless-transport-vervolgkaart uit
   `orchestration-substrate-decision.md`.*
2. **[feature] Path-/keyword-getriggerde skills bovenop de bestaande skills-laag.**
   Voeg glob-path- en keyword-triggers toe aan het skill-/memory-injectiemodel (per
   doel-repo declareerbaar), naar het patroon van OpenHands' AgentSkills. Acceptance:
   een regel die alleen laadt wanneer de agent een matchend bestand aanraakt of een keyword
   in de prompt voorkomt.
3. **[feature] Event-/webhook-getriggerde automations** — breid de scheduler uit met een
   webhook-endpoint dat op een extern event (GitHub-PR, Slack) een kaart aanmaakt of een
   sessie spawnt. Naar het model van OpenHands' Automation Server.
4. **[analysis] Synchrone sub-agent-delegatie vs. async kanban-decompositie.** Bepaal waar
   in de multi-agent-flow een synchroon TaskToolSet-achtig delegatie-patroon waarde
   toevoegt naast de bestaande async analyst→kind-kaarten-decompositie.

## 8. Bewust buiten scope

- **Volledige feature-by-feature-audit van OpenHands.** Deze spike is gericht op
  *overneembare/leerbare* concepten t.o.v. het platformdoel, niet op een uitputtende
  inventaris.
- **ACP-implementatie.** Alleen de evaluatie-vraag wordt hier gesteld; de bouw is kaart 1.
- **De abonnement-router zelf** (`subscription-flexibiliteit-analyse.md`) — orthogonaal;
  OpenHands' LiteLLM-routing is er hooguit referentie voor.

## 9. Bronnen

- OpenHands — [github.com/OpenHands/OpenHands](https://github.com/OpenHands/OpenHands),
  [docs.openhands.dev](https://docs.openhands.dev/llms.txt)
- ACP / any coding agent —
  [openhands.dev/blog/use-any-coding-agent-in-openhands-with-acp](https://www.openhands.dev/blog/use-any-coding-agent-in-openhands-with-acp),
  [docs.openhands.dev/.../acp-agents](https://docs.openhands.dev/openhands/usage/agent-canvas/acp-agents)
- Anthropic 2026 billing-pauze (raakt ACP/`claude -p`/SDK onder abonnement) —
  [zed.dev/blog/anthropic-subscription-changes](https://zed.dev/blog/anthropic-subscription-changes),
  [claude-agent-acp#658](https://github.com/agentclientprotocol/claude-agent-acp/issues/658)
- Interne verwante analyses: `build-prioriteiten-analyse.md` §3.3/§4,
  `orchestration-substrate-decision.md`, `subscription-flexibiliteit-analyse.md`.
