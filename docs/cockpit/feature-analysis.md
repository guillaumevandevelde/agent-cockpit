# Feature-analyse — Claude Cockpit vs. gelijkaardige tools

**Datum:** 2026-06-16
**Auteur:** Analyst (kanban-kaart "Feature analysis")
**Status:** ter beoordeling — een lijst van *mogelijke* functionaliteiten, kritisch gewogen.
Dit is geen bouwmandaat; het is input voor prioritering.

> **Opmerking over de bronkaart.** De oorspronkelijke "Feature analysis"-kaart was bij
> aanvang niet (meer) terug te vinden op het live bord (alleen `Investigate` en
> `kanban auto-pickup` stonden er). Vermoedelijk verwijderd/verplaatst door een
> gelijktijdige sessie op dezelfde checkout. Het werk is zelfstandig en goed gedefinieerd,
> dus dit document + de afgeleide Todo-kaarten zijn alsnog opgeleverd.

---

## 1. Wat Cockpit vandaag al is

Geen "nog een agent-dashboard". Cockpit is uniek doordat het **drie assen** onder één
lokaal, web-gebaseerd dak combineert — de meeste concurrenten dekken er maar één:

| As | Cockpit-features (bestaand) |
|---|---|
| **Config-governance** | MCP servers, Commands, Plugins, Hooks, Permissions, Agents, Skills, Memory, Output Styles, Status Line, Backup, Config |
| **Observability** | Sessions (transcript-viewer), Usage (kosten/tokens), Context (token/cache-analyse), Presence, Dashboard |
| **Orchestratie** | CC Bridge (tmux spawn/monitor), Kanban (agent-zelfbediening + auto-dispatch in worktrees), Scheduled Messages (timer/cron-injectie), Plans |

Architectonische ankers (uit de specs):
- **Local-first**, geen cloud-afhankelijkheid; bord-domein is sync-baar maar device-lokale
  data (tmux-targets, paden) nooit.
- **Agent-zelfbediening via MCP** — sessies lezen/schrijven het bord in-context.
- **Auto-dispatch**: poll-loop claimt Todo-kaarten, spawnt een sessie in een **git-worktree**,
  cap = 1 actieve kaart per project.
- **Pane-attentie**: notificaties routeren naar exact de juiste bridge-sessie.

---

## 2. Het concurrentielandschap (juni 2026)

| Tool | Categorie | Kernidee | Vorm |
|---|---|---|---|
| **Claudia** (getAsterisk) | Config + analytics | GUI voor Claude Code: sessie-timelines, custom agents, usage | Desktop (Tauri), AGPL |
| **Vibe Kanban** | Orchestratie | Kanban waar elke kaart een agent-taak is | Web — *aan het uitfaseren* |
| **Conductor** | Parallel-agents | Meerdere agents in eigen worktree, diff-first review | Mac-app |
| **Superset** | Parallel-agents | IDE voor 10+ agents parallel, unified dashboard | IDE |
| **Claude Squad** | Orchestratie | tmux-TUI, isolatie per taak, multi-provider | Terminal |
| **dmux** | Parallel-agents | tmux-panes + worktree/branch per agent | CLI |
| **ccusage / ccflare** | Analytics | Token/kosten per dag/sessie/project, limiet-tracking | CLI |
| **claude-dashboard / Agent View** | Observability | Real-time sessie-monitoring | TUI |
| **Nimbalyst** | Hybride | Multi-agent + visuele planning + kanban + mobiel | Desktop/web |
| **Omnara / Happy** | Remote | Claude Code remote besturen vanaf mobiel | Mobiel |
| **Anthropic Agent Teams** | Officieel | Native multi-agent-orkestratie (Opus 4.6, feb 2026) | Ingebouwd in CC |

**Twee strategische feiten die het ontwerp sturen:**

1. **Agent Teams is nu officieel** (feb 2026). Native multi-agent-orkestratie hoort steeds
   meer bij Claude Code zelf. Cockpit moet **niet** Anthropic's orkestratie-kern naprogrammeren;
   het moet de **governance-, observability- en planlaag** zijn die Anthropic niet levert.
2. **Beleidsverschuiving 4 april 2026:** Pro/Max-abonnees mogen hun abonnement niet meer met
   de meeste third-party frameworks gebruiken. Cockpit's aanpak — de **officiële `claude`-CLI**
   interactief aansturen via tmux (zoals Claude Squad/dmux) — blijft de veilige kant: het is de
   gebruiker die z'n eigen ingelogde CLI gebruikt. **Aanbeveling:** nooit de API met
   abonnements-credentials proxyen; altijd de officiële CLI driven.

**Positionering:** Cockpit's verdedigbare niche = *config-governance + observability +
planning rond zelf-bedienende agents*, lokaal en web. De scheduled-messages (timer/cron-injectie)
en de MCP-zelfbediening zijn echt onderscheidend t.o.v. het hele veld.

---

## 3. Mogelijke functionaliteiten (kritisch gewogen)

Legenda: **Aanbevolen** (sterk, scherp af te bakenen) · **Overwegen** (waardevol, maar
groot/strategisch) · **Overslaan** (buiten niche of al gedekt).

### A. Orchestratie & review

- **A1 — In-app diff-review in de Review-kolom.** *(Aanbevolen)* De Review-kolom bestaat,
  maar nazicht gebeurt extern (GitHub PR). Toon de diff/branch van de deliverable in Cockpit
  zelf met goedkeuren→merge of terugsturen→Doing. Dit is de signatuur-feature van
  Conductor/Superset en sluit de lus binnen het bord. → *Todo-kaart aangemaakt.*
- **A2 — Live pane-streaming in CC Bridge.** *(Aanbevolen)* Vandaag monitort/ontdekt Bridge
  sessies; live terminal-output bekijken in de web-UI (xterm.js over WebSocket op de tmux-pane)
  ontbreekt. Cruciaal om een autonome sessie te volgen zonder naar de terminal te switchen.
  → *Todo-kaart aangemaakt.*
- **A3 — Configureerbare concurrency-cap per project.** *(Aanbevolen)* De dispatcher is hard
  op 1 actieve kaart/project. Een instelbare cap (n parallelle worktrees) ontsluit echte
  parallel-agent-workflows — de hoofdreden waarom mensen Conductor/Superset gebruiken.
  → *Todo-kaart aangemaakt.*
- **A4 — Multi-provider-generalisatie.** *(Overwegen)* MiMoCode is al toegevoegd; een nette
  provider-abstractie (Codex, Gemini CLI, Aider, OpenCode) maakt Cockpit provider-agnostisch
  zoals Claude Squad. Groot, en spanning met de Claude-gerichte governance-features; pas
  prioriteren als parallel-agents (A3) er staat.
- **A5 — Auto-pickup repareren/hardenen.** *(Aanbevolen)* Er staat een bord-kaart dat
  auto-pickup niet meer werkt. Betrouwbare dispatch is fundament voor alle orchestratie-waarde;
  eerst stabiliseren. *(Bestaande kaart — niet gedupliceerd.)*

### B. Observability & attentie

- **B1 — Native OS-/push-notificaties bij "sessie wacht op input".** *(Aanbevolen)* De
  pane-attentie routeert al in-app; dit doortrekken naar desktop-notificaties (en later web-push)
  zodat de gebruiker niet in de tab hoeft te staan. Hoge waarde/lage kost bovenop bestaande
  infrastructuur. → *Todo-kaart aangemaakt.*
- **B2 — ccusage-pariteit in Usage.** *(Overwegen)* Eventuele gaten dichten: limiet-voortgangsbalk
  (Pro/Max), burn-rate, 5-uurs-block-tracking, per-model/-project-breakdown. Eerst auditen wat
  Usage/Context al dekt voor er kaarten komen.
- **B3 — Sessie-tijdlijn & traceerbaarheid.** *(Overwegen)* Link Plan → kanban-kaart →
  sessie → deliverable als één doorklikbaar spoor (Nimbalyst's "file traceability"). Sluit aan
  op de nieuwe Plans-feature; ontwerp-werk nodig.

### C. Config-governance (de niche — uitdiepen)

- **C1 — Config-profielen/presets.** *(Aanbevolen)* Eén klik om een bundel
  (MCP + hooks + permissions + agents) op een project toe te passen. Onderscheidend en past
  exact in Cockpit's sterkte; geen enkele orchestratie-tool doet dit. → *Todo-kaart aangemaakt.*
- **C2 — Config-diff & drift-detectie.** *(Overwegen)* Toon verschillen tussen projecten of
  t.o.v. een baseline; signaleer gevaarlijke permissions of kapotte hooks. Waardevol voor wie
  veel projecten beheert; vereist een config-snapshot-model.
- **C3 — Settings-lint/validatie.** *(Overwegen)* Statische checks op permissions/hooks
  (gevaarlijke globs, niet-bestaande hook-commando's). Klein-tot-middel; goede defensieve waarde.

### D. Remote & sync

- **D1 — Cross-device bord-sync activeren.** *(Overwegen)* Het bord-domein is bewust sync-baar
  ontworpen (append-only `kanban_ops`); de remote-store-laag activeren is een afgebakende maar
  niet-triviale brok. Pas doen als single-device af is.
- **D2 — Mobiel/remote monitoring & injectie.** *(Overwegen)* Scheduled-messages + presence
  leunen hier al naartoe; een mobiel-vriendelijke read+inject-view (à la Omnara/Happy) is een
  bekende behoefte, maar vergt auth/exposure-hardening. Strategisch, niet nu.

### E. Veiligheid

- **E1 — Rootless-podman sandbox-transport.** *(Overwegen)* Al een vastgelegde richting:
  sessies in een permissieloze container i.p.v. kale tmux. De `SpawnTransport`-abstractie is er
  al; dit raakt enkel de transport. Groot maar hoog-waarde voor veilige autonomie. *(Bestaand spoor.)*

### Overslaan
- **Eigen multi-agent-orkestratie-engine** (swarms/topologieën) — Agent Teams doet dit nu
  officieel; buiten Cockpit's niche.
- **API-proxy met abonnements-credentials** — botst met het beleid van 4 april 2026.
- **Generieke project-/issue-tracker** — het bord is agent-gericht, geen Jira-vervanger.

---

## 4. Aanbeveling — eerste tranche

Bouw in deze volgorde (elk een aparte, afgebakende Todo-kaart):

1. **A5** auto-pickup hardenen *(bestaande kaart)* — fundament.
2. **A2** live pane-streaming — direct dagelijks nut.
3. **A1** in-app diff-review — sluit de bord-lus.
4. **B1** OS-notificaties — goedkope hefboom op bestaande attentie-laag.
5. **A3** instelbare concurrency-cap — ontsluit parallel-agents.
6. **C1** config-profielen — verdiept de unieke niche.

De "Overwegen"-items zijn bewust **niet** als kaart aangemaakt: ze vergen eerst een
gebruikersbeslissing of een groter ontwerp, en kaarten ervan zou requirements verzinnen.

---

## 5. Bordstatus bij oplevering (blocker)

Bij het aanmaken van de Todo-kaarten faalde **elke schrijf-operatie** via de
`cockpit-kanban` MCP met `-32602 Invalid request parameters` (`create_card`, `comment` —
ook een minimale call). **Lees**-operaties (`list_cards`, `get_card`) werken wél. Dit is een
systemische schrijf-fout op de draaiende MCP-server en strookt met de bestaande bord-kaart
*"kanban auto-pickup — De auto pick-up van de kanban lijkt niet meer te werken"*.

Gevolg: de kaarten hieronder konden **niet** op het bord gezet worden, en deze analyse kon
ook niet als comment op een bronkaart gehangen worden. Ze staan daarom hieronder klaar om
1-op-1 als kaart aangemaakt te worden zodra het MCP-schrijfpad hersteld is (zie kaart
*kanban auto-pickup*). Het bord is **niet** met de hand (via SQLite) gemuteerd, omdat dat de
append-only op-log/HLC-sync-invarianten zou breken.

---

## Appendix — kant-en-klare Todo-kaarten

Project: `git:github.com/guillaumevandevelde/claude-cockpit`, kolom `Todo`.

### Kaart 1 — Live pane-streaming in CC Bridge (xterm.js)
**Scope** — IN: live terminal-output van een bridge/dispatch-sessie in de web-UI; read-only
stream van de tmux-pane via WebSocket; xterm.js-render in CC Bridge. OUT: terug-typen vanuit
de browser (aparte kaart); meerdere panes tegelijk tilen; opname/replay.
**Approach** — Backend: WebSocket-endpoint dat een tmux-pane volgt (`pipe-pane`/`capture-pane`
of PTY-attach) en bytes streamt; hergebruik discovery (`agent_bridge/discovery.py`) voor
pane-id/tmux-target. Frontend: xterm.js-component in `frontend/src/features/cc-bridge/`,
gevoed door de WS, met herverbinden. Sluit aan op `presence.tmux_pane == bridge.pane_id`.
**Acceptance** — sessie selecteren toont live output (< ~1 s); verbinding herstelt na drop;
`npm run build` clean; backend-tests met een fake-pane.

### Kaart 2 — In-app diff-review in de Review-kolom
**Scope** — IN: diff van een branch/commit-deliverable tonen in Cockpit; "Goedkeuren →
merge/Done" en "Terugsturen → Doing" met comment. OUT: inline-commentaar per regel; volledige
PR-conversatie; conflictoplossing in de browser.
**Approach** — Backend: endpoint dat `git diff`/`git show` voor de worktree-branch teruggeeft
(deliverable kind=branch|commit, `kanban_deliverables`); merge via bestaande git-ship/merge-pad.
Frontend: diff-viewer in `frontend/src/features/kanban/` (Review-drawer). Resolve branch/commit
via `attach_deliverable`-referenties.
**Acceptance** — Review-kaart met branch toont volledige diff; "Goedkeuren" merget + → Done;
"Terugsturen" → Doing met comment in de feed; backend-tests dekken diff + beide acties;
`npm run build` clean.

### Kaart 3 — OS-notificaties bij "sessie wacht op input"
**Scope** — IN: native browser-/OS-notificatie (Notification API) wanneer een sessie input
nodig heeft, bovenop de in-app pane-attentie; klik focust de juiste bridge-sessie. OUT:
mobiele web-push/service-worker; e-mail/Slack; geluidsthema's.
**Approach** — Frontend: haak in op `useAttentionNotifications`/`AttentionContext`; vraag
Notification-permission; toon notificatie bij overgang naar "waiting for input"; deep-link via
`tmux_pane`. Debounce/dedup per wachtmoment.
**Acceptance** — met toestemming verschijnt notificatie + klik navigeert naar de sessie; geen
duplicaten per wachtmoment; nette fallback zonder toestemming; `npm run build` clean.

### Kaart 4 — Configureerbare concurrency-cap per project (auto-dispatch)
**Scope** — IN: de harde cap van 1 actieve `agent:`-kaart per project instelbaar maken (n,
device-lokaal, opt-in); dispatcher spawnt tot n parallelle worktrees per project. OUT: globale
cap; resource-aware scheduling; auto-tuning.
**Approach** — Backend: cap als `KanbanMeta`-key per project (zoals `autodispatch:<key>`),
default 1; dispatcher telt actieve `agent:%`-kaarten in Doing en spawnt tot de cap i.p.v.
skippen bij ≥1. Frontend: numerieke instelling naast de auto-dispatch-toggle. Behoud
claim-first-spawn-race-veiligheid.
**Acceptance** — cap=n → tot n gelijktijdige sessies/project; default blijft 1; tests dekken
cap-telling + race; `npm run build` clean.

### Kaart 5 — Config-profielen: bundel toepassen op een project
**Scope** — IN: named profiel = bundel MCP + hooks + permissions + agents; met één actie
toepassen op een project; profielen oplijsten/bewerken/verwijderen. OUT: automatische
drift-handhaving (C2); cross-device delen; versie-historie.
**Approach** — Backend: profiel-model + service die bestaande config-services (mcp, hooks,
permissions, agents) hergebruikt om een bundel naar een project's `.claude`-config te schrijven;
idempotent (merge, conflicten rapporteren). Frontend: sectie onder Config met profiel-editor +
"Toepassen op project" via ProjectSwitcher.
**Acceptance** — profiel aanmaken + toepassen schrijft juiste MCP/hooks/permissions/agents;
opnieuw toepassen idempotent; conflicten getoond, niet stil overschreven; backend-tests dekken
apply + idempotentie; `npm run build` clean.

---

## Bronnen
- [Best Claude Code GUI in 2026 — Nimbalyst](https://nimbalyst.com/blog/best-claude-code-gui-tools-2026/)
- [Best Multi-Agent Coding Tools 2026 — Nimbalyst](https://nimbalyst.com/blog/best-multi-agent-coding-tools-2026/)
- [Vibe Kanban](https://vibekanban.com/) · [Claudia](https://claudia.so/) · [Claude Squad review](https://vibecodinghub.org/tools/claude-squad)
- [Claude multi-agent ecosystem (Agent Teams, beleid)](https://codex.danielvaughan.com/2026/04/09/claude-multi-agent-ecosystem/)
- [The Code Agent Orchestra — Addy Osmani](https://addyosmani.com/blog/code-agent-orchestra/)
- [ccusage / monitors](https://claudefa.st/blog/tools/monitors/claude-code-usage-monitor)
