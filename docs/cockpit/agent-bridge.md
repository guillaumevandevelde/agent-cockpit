# Agent Bridge — spawn, terminal-relay & per-sessie configuratie

> **Canoniek** voor "hoe Claude Cockpit lokale agent-sessies start en bedient". Verzamelt
> de blijvende beslissingen van vier superpowers-taken die elk een stuk van de Agent Bridge
> hebben toegevoegd. Voor de TDD-stappen/bestandsdetails van één taak: zie de gelinkte
> superpowers-spec. Bij overlap: **dit document eerst.**

## Wat is de Agent Bridge

De Agent Bridge start en bedient **agent-CLI-sessies** (Claude Code, Codex CLI, OpenCode,
MiMoCode) in een **tmux**-sessie op de backend-host en relayt de pty naar de browser. Het is
de laag onder Kanban-dispatch (die spawnt via dezelfde transport) én de standalone "New
Session"-flow.

Huidige module-indeling (de code is sinds de originele specs hernoemd — zie
[`terminology.md`](./terminology.md), begrip **Run**):

| Rol | Locatie |
|---|---|
| Spawn / rename / kill | `backend/app/services/runs/spawn.py` |
| Pty-relay (websocket ↔ tmux) | `backend/app/services/runs/pty_relay.py` |
| Provider-abstractie (per-CLI spawn-command) | `backend/app/services/agentic_cli/` |
| Per-platform env-builder | `backend/app/services/agentic_cli/provider_env.py` |
| Resumable-sessies aggregatie | `backend/app/services/runs/resumable.py` |
| Image-attachments | `backend/app/services/runs/attachments.py` |
| REST-router | `backend/app/api/v1/runs/router.py` (gemount op `/api/v1/agent-bridge`) |
| Frontend | `frontend/src/features/cc-bridge/` |

De vier gepromote deelfeatures hieronder delen één principe: **de Agent Bridge slaat geen
secrets op** en injecteert alleen niet-geheime configuratie in de gespawnde tmux-omgeving; de
onderliggende SDK/CLI lost credentials zelf op uit de host-omgeving.

## Platform-selectie (Anthropic / Bedrock / MiniMax)

**Superpowers-tegenhanger:** [`../superpowers/specs/2026-05-29-agent-bridge-bedrock-platform-design.md`](../superpowers/specs/2026-05-29-agent-bridge-bedrock-platform-design.md).

De "New Session"-dialog kan per Claude-Code-sessie een **platform** kiezen. Blijvende
beslissingen:

- **Credentials komen van de host, niet van Cockpit.** Platform-keuze zet alleen
  niet-geheime env-vars; de AWS-SDK-credential-chain (env / `~/.aws/credentials` / instance
  role) resolvet de echte credentials. Cockpit verzamelt of bewaart nooit AWS-secrets.
- **Env-builder als enige uitbreidingspunt.** `provider_env.py` vertaalt een platform-keuze
  naar een `dict[str,str]`; `Anthropic` → **lege** env-map (byte-identiek aan de originele
  spawn = volledige backward-compat). Bedrock → `CLAUDE_CODE_USE_BEDROCK=1` + optionele
  region/profile/model. MiniMax → `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` naar de
  MiniMax-endpoint (zie [`subscriptions.md`](./subscriptions.md) voor waar de MiniMax-key
  wordt beheerd).
- **Injectie via losse argv-elementen** (`tmux new-session -e KEY=VALUE`), nooit
  shell-geïnterpoleerd → geen quoting/injectie-risico; waarden met `\n`/`\0` worden geweigerd.
- **Laatste keuze onthouden** in browser-`localStorage` (alleen niet-geheime velden), geen
  backend-persistentie.

Vertex bleef expliciet buiten scope; het env-builder-model laat het later toe zonder de
plumbing te herzien.

## Session rename

**Superpowers-tegenhanger:** [`../superpowers/specs/2026-06-12-agent-bridge-session-rename-design.md`](../superpowers/specs/2026-06-12-agent-bridge-session-rename-design.md).

Een sessie krijgt een betekenisvolle naam i.p.v. `<basename>-<uuid4>`, bij spawn (default =
worktree-naam) én achteraf inline vanaf de sessiekaart.

- **Rename de echte tmux-sessie** (`tmux rename-session`), geen aparte display-name-overlay.
  Eén bron van waarheid, geen persistente opslag nodig (de repo heeft geen migratiesysteem),
  overleeft backend-herstart, en discovery leest de tmux-naam toch al.
- **Attentie blijft correct.** Badges joinen op `pane_id` (`%N`), stabiel over een rename;
  alleen `tmux_target` verandert. De UI herselecteert de pane op `pane_id` na een rename.
- **Naamregels:** saniteer naar `[a-zA-Z0-9_-]`, trim tot 20 chars, weiger leeg na sanitatie,
  dedupe tegen lopende tmux-sessies (`-<hex4>` bij collisie). REST: `POST
  /agent-bridge/sessions/{target}/rename`, `ValueError` → HTTP 400.

## Resume across worktrees

**Superpowers-tegenhanger:** [`../superpowers/specs/2026-06-12-resume-worktree-sessions-design.md`](../superpowers/specs/2026-06-12-resume-worktree-sessions-design.md).

Claude bewaart transcripts **per working directory**, dus een sessie die in een git-worktree
draaide leeft onder een aparte Claude-project-folder en was niet resumebaar vanuit de UI.

- **Geaggregeerde resumable-sessies:** `GET /agent-bridge/resumable-sessions?directory=…` somt
  de sessies op van het project **plus al zijn git-worktrees** (`git worktree list
  --porcelain`), elk met een `worktree_label` (`main` of de worktree-basename), gesorteerd op
  `modified_at` desc. Niet-git of geen transcripts → valt terug op alleen de eigen folder,
  geen fout.
- **Resume landt in de eigen cwd van de sessie.** Voor `mode == "resume"` met `project_folder`
  wordt de directory **altijd** uit het transcript afgeleid (`_resolve_project_directory`),
  niet uit de dropdown — de resume-target wordt volledig door de sessie bepaald. Verwijderde
  worktree → duidelijke spawn-fout.

## Image paste / drag-drop

**Superpowers-tegenhanger:** [`../superpowers/specs/2026-06-29-agent-bridge-image-paste-design.md`](../superpowers/specs/2026-06-29-agent-bridge-image-paste-design.md)
(oorspronkelijk als design-draft geschreven; sindsdien geïmplementeerd in
`backend/app/services/runs/attachments.py` + `BridgeSessionAttachment`-model).

Een browsergebruiker plakt/sleept een afbeelding in een terminal; Cockpit slaat het bestand
op de host op en injecteert een prompt met het **pad** dat de tmux-agent kan lezen. Blijvende
beslissingen:

- **Upload naar de host, geef het agent-leesbare pad door.** De kritieke invariant: als de
  API een `agent_path` teruggeeft, moet het tmux-agent-proces dat pad kunnen lezen. Bij een
  filesystem-namespace-mismatch (Docker) faalt de API luid met config-guidance i.p.v. een
  onleesbaar pad te injecteren (`bridge_attachment_agent_root`).
- **Geen terminal-image-rendering, geen base64 in de input-stream.** Alleen bestand opslaan +
  padreferentie injecteren, want dat is wat de agent-CLI's daadwerkelijk consumeren.
- **Injectie via `tmux send-keys -l`** met een Enter-delay; newlines worden uit de prompt
  gestript (embedded `\n` kan anders submitten). Bestandsnaam van de gebruiker komt nooit in
  de prompt — alleen het server-gegenereerde pad (prompt-injectie-preventie).
- **Zelfde write-authorization als de terminal-websocket** (kortlevende single-use token +
  same-origin-check); validatie op file-signature, niet alleen `Content-Type`;
  server-gegenereerde bestandsnamen; nooit buiten de attachment-root schrijven.
- **Retentie-cleanup** verwijdert verlopen rijen + bestanden (default 7 dagen, configureerbaar).

## Sessie-lifecycle: wees-sessies (bewust beperkt)

Dit document beschrijft bewust géén volledige sessie-lifecycle — spawn/relay/config
zijn hierboven vastgelegd, maar "wanneer stopt een sessie" is elders opgelost, en
maar gedeeltelijk. Twee bestaande opkuis-paden zijn **kaart-gescoped**:
`session_cleanup.py` (kilt tmux + worktree wanneer een kanban-kaart naar
Done/Impediment gaat) en `reap_stale_claims()` (release't `agent:`-claims op dode
sessies, gevonden door over `cards` te itereren). Een sessie die buiten dispatch om
gespawnd wordt — de "New Session"-dialoog, een handmatige test van de Agent Bridge
zelf — heeft nooit een kaart en dus geen claim om te reapen. Zonder een apart
mechanisme is zo'n sessie **volledig onzichtbaar**: ze blijft draaien, eet RAM, en
bezet een registry-slot. Zie
[`spawn-test-bridge-sessions-analyse.md`](./spawn-test-bridge-sessions-analyse.md)
(bevinding 6) voor de volledige analyse; het slot-lek zelf is inmiddels
zelfhelend via `SessionRegistry`'s reconciliatie tegen `tmux list-panes`
(`backend/app/services/scheduling/session_registry.py`) — wat resteert is de
tmux-sessie zelf.

**Gekozen grens: zichtbaar maken, nooit automatisch killen.**

- `scripts/list-orphan-bridge-sessions.sh` — read-only, geen `--apply`. Draait
  bij elke `cockpit.sh start` (via `run_doctor`) en op aanvraag via
  `cockpit.sh doctor` (check 7). Rapporteert Cockpit-gespawnde tmux-sessies
  zonder levende kanban-claim; WARN, nooit een harde FAIL.
- Detectie leunt volledig op tmux/DB als bron van waarheid, niet op een
  in-memory dict die een backend-herstart overleeft: (1) *Cockpit-gespawnd* —
  elke `spawn_session()`-aanroep zet `COCKPIT_RUNTIME` via `tmux new-session -e`
  (`backend/app/services/agentic_cli/provider_env.py:build_spawn_env`), dus
  `tmux show-environment -t <sessie> COCKPIT_RUNTIME` onderscheidt een
  Cockpit-sessie van een willekeurige andere tmux-sessie op dezelfde host;
  (2) *geclaimd* — hergebruikt `scripts/kanban_active_worktrees.py` (dezelfde
  query die `worktree-gc.sh` vertrouwt): een `agent:`-claim buiten
  Done/Impediment beschermt de sessie; (3) *oud genoeg* — tmux's eigen
  `#{session_created}` moet minstens `ORPHAN_GRACE_S` (default 120s) geleden
  zijn, zodat een sessie tussen spawn en het committen van haar kaart-claim
  nooit vals-positief flagt.
- **Waarom geen auto-kill.** Een handmatig gespawnde debug-sessie is voor dit
  script niet te onderscheiden van een vergeten test-sessie — beide hebben
  geen kaart. Automatisch killen zou de `worktree-gc`-postmortem ("actieve
  claim weggekilld onder iemands handen") één laag dieper herhalen. Een mens
  beoordeelt de WARN-regel en killt handmatig (`tmux kill-session -t <naam>`)
  wanneer gewenst — vergelijkbaar met hoe `worktree-gc.sh` en
  `cleanup-test-projects.sh` allebei dry-run-by-default zijn met een losse
  `--apply`-vlag; dit script heeft zelfs geen `--apply` omdat "killen" hier
  bewust buiten scope blijft.

## Zie ook

- [`subscriptions.md`](./subscriptions.md) — waar de MiniMax-key + per-provider quota worden beheerd.
- [`kanban-dispatch-spec.md`](./kanban-dispatch-spec.md) — de dispatcher spawnt via dezelfde transport.
- [`terminology.md`](./terminology.md) — Agent / Provider / CLI / Model / Run naamgeving.
- [`spawn-test-bridge-sessions-analyse.md`](./spawn-test-bridge-sessions-analyse.md) — analyse achter de wees-sessie-detectie hierboven.
