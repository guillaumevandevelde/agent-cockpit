---
title: "Terminology — canonieke woordenlijst"
type: reference
status: active
---

# Terminology — canonieke woordenlijst

> Anker voor de eerste-gebruik-verwijzing uit
> [`taalgebruik-conventies.md` §5](./taalgebruik-conventies.md#5-woordkeuze-welk-engels-blijft).
> Introduceer een nieuw begrip één keer met zijn definitie, en link daarna
> hierheen.

De vijf kernbegrippen staan hieronder. Ze zijn canoniek; eerdere synoniemen
zijn uitgefaseerd door de kind-kaarten 2 t/m 5 van de terminologie-parent
`23ac6715…` (backend rename, frontend rename, cc_bridge-consolidatie,
docs-sweep). De rest van de begrippen die een lezer in cockpit-teksten
tegenkomt, staat in [Aanvullende termen](#aanvullende-termen) verderop.

## De vijf begrippen

| # | Canonieke term | Wat het is | Vroegere synoniemen (uitgefaseerd) | Rationale |
|---|---|---|---|---|
| 1 | **Agent** | Subagent-persona uit `.claude/agents/*.md` — prompt + tools + permission_mode (engineer, analyst, …). | "agent" in `AgentProvider` (nu `AgenticCli`), `AgentProviderId` (nu `AgenticCliId`), `agent_bridge` (nu `runs/`), `AgentTeam` (nu `RunGroup`), `agent_activity`, `agent_mail`, `agent-performance` (deels — zie #5). | Persona is al canoniek in de Agents-feature en in `kanban-dispatch-spec`. Alleen de naamsvervuiling door overlap met #5 moest eruit. |
| 2 | **Provider** | Subscription-aanbieder (vendor): Anthropic, MiniMax, OpenAI, Bedrock. | "platform" in `platform_env.py` (nu `provider_env.py`, `PROVIDER_ANTHROPIC`/`PROVIDER_BEDROCK`/`PROVIDER_MINIMAX`), `SpawnCommandOptions.platform` (nu `.provider`); inconsistente "provider"-copy in de Subscriptions-UI die deze *wel* bedoelt. | "Provider" is de marktconforme term voor de vendor die een LLM-dienst levert. "Platform" was intern en dubbelzinnig; in Subscriptions-UI was "provider" al in gebruik voor dit concept. Eén naam, vastgelegd. |
| 3 | **CLI** (voluit: agentic coding CLI) | Lokaal geïnstalleerde CLI-runtime die de agent-persona aanstuurt: `claude-code`, `codex-cli`, `copilot-cli`, `mimo-code`, `open-code`. | "provider" in `services/providers/` (nu `services/agentic_cli/`), `AgentProvider`-baseclass (nu `AgenticCli`), `_PROVIDERS`-registry (nu `_AGENTIC_CLIS`), `AgentProviderId` (frontend, nu `AgenticCliId`), DB-kolom `provider` (nu `cli`). | "Tool" botst met de bestaande, diep verankerde betekenis "toegestane tools" (Bash/Read/Edit-permissies, MCP-tools, agent's `tools:`-veld). "Runtime" was te generiek (botste met model-runtime). "CLI" matcht de bestaande IDs (`codex-cli`, `copilot-cli` eindigen al op `-cli`). |
| 4 | **Model** | Het onderliggende LLM: Sonnet-5, Opus 4.8, Fable, GPT, MiniMax-M3, … | (geen noemenswaardige overlap in naamgeving) | Gezond. Eén DRY-punt staat los: modellijsten worden per CLI los gedupliceerd; dat is technical debt, geen terminologiekwestie. |
| 5 | **Run** | Een lopende instantie van een CLI uit #3 die werkt aan een taak — typisch een tmux-sessie + process tree, al dan niet gegroepeerd in een team (lead + members). | "agent" in `AgentTeam` (nu `RunGroup`), `AgentTeamMember` (nu `RunMembership`), `AgentTeam.provider`-kolom (nu `.cli`), `agent_bridge/`-modulenaam (nu `runs/`), `agent_activity`, agent-mail-deelnemer, agent-performance-tile. | "Sessions"-feature = transcriptgeschiedenis (read-only, file-based, **verleden tijd**) — niet hetzelfde, geen botsing. "Run" leent zich van CI/CD-terminologie (GitHub Actions Run, GitLab CI Job) waar het een "instance of a defined action" is. Korter dan "AgentInstance" en vermijdt de "agent"-dubbelzinnigheid. |

## Welke term wordt waar canoniek (beslisregels)

Kort samengevat, voor wie niet de hele tabel wil lezen:

- **Praat je over de Anthropic/MiniMax/OpenAI/Bedrock-vendor?** → **Provider**.
- **Praat je over de lokale CLI-binary die de agent aanstuurt?** → **CLI**.
- **Praat je over een subagent-persona uit `.claude/agents/`?** → **Agent**.
- **Praat je over het onderliggende LLM?** → **Model**.
- **Praat je over een lopende sessie van een CLI die aan een taak werkt?** → **Run**.

## Wat er **niet** verandert

- **Tools** (Bash/Read/Edit, MCP-tools, agent's `tools:`-veld) — die betekenis blijft; daarom geen "agentic coding tool" als verkorte term voor #3.
- **Sessions-feature** (transcriptgeschiedenis) — die naam blijft; verwijst naar het verleden (transcripts op disk), niet naar een live run.
- **Subscriptie / plan** — die UI-feature blijft; Subscriptions-UI gebruikt vandaag "provider" voor de vendor — dat wordt canoniek en stopt de eerdere inconsistente "platform"-naam in code.

## Aanvullende termen

De rest van de begrippen die in cockpit-teksten voorkomen, in een kortere
vorm. Elke ingang heeft één zin in gewone taal; waar zinvol staat er een
`file:line`-anker bij.

- **analyst** — de persona die een te grote kaart opdeelt in kind-kaarten
  met afhankelijkheden en een plan-attachment.
  Persona: `.claude/agents/analyst.md`.
- **backlog** — de kolom met kaarten die wachten op dispatch.
- **claim** — het reserveren van een kaart door één sessie, zodat geen
  tweede agent dezelfde kaart pakt.
- **column_overrides** — per-kaart override van model/provider/transport per
  agent-kolom. Veld op het model:
  `backend/app/kanban/models.py:92`.
- **comment** — een bericht aan de activiteit-feed van een kaart; geen
  UI-tekst en geen `Done`-samenvatting.
- **depends_on** — lijst sibling-kaart-ids die eerst `Done` moeten zijn
  voor deze kaart dispatchbaar wordt. Veld op het model:
  `backend/app/kanban/models.py:116`.
- **deliverable** — een aan de kaart gekoppeld artefact. De canonieke
  soorten staan in `backend/app/kanban/schemas.py:77` (`pr`, `branch`,
  `commit`, `link`, `note`); `plan`, `plan_ref` en `spec` komen uit
  speciale tools.
- **dispatch** — de poller die klaarstaande kaarten oppakt, claimt, en
  een agent-sessie start. Hart van `backend/app/kanban/dispatch.py`.
- **dispatch_failures** — teller op een kaart die mislukte
  spawn-pogingen bijhoudt. Veld op het model:
  `backend/app/kanban/models.py:144`.
- **engineer** — de persona die een kaart end-to-end uitvoert. Persona:
  `.claude/agents/engineer.md`.
- **gate** — een keuzevenster dat aan een kaart hangt; de mens kiest
  één optie. Slaat op in de `kanban_gates`-tabel.
- **held_reason** — korte code die zegt waarom de dispatcher een kaart
  nog niet oppakt (dependency, plan, gate, schema, scheduled_at).
  Veld op het model: `backend/app/kanban/models.py:125`.
- **HLC** — Hybrid Logical Clock; monotonic-timestamp die
  kanban-synchronisatie aandrijft. De waarde zit in elke `kanban_ops`-rij.
- **hook** — een script dat de agent-CLI aanroept bij events zoals
  `PreToolUse` of `PostToolUse`. Geconfigureerd in
  `.claude/settings.json`.
- **impediment** — het stopteken waar de agent een menselijke beslissing
  vraagt; de kaart staat in de kolom *Impediment*. Geopend via
  `report_impediment`, niet via `move_card`.
- **kanban** — het bord waar alle kaart-workflow leeft. Frontend in
  `frontend/src/features/kanban/`.
- **lane** — een agent-kolom op het bord; één per persona uit
  `.claude/agents/`. Synoniem van "agent-kolom".
- **merge** — een branch op master krijgen, direct of via pull-request.
  Recept: `.claude/skills/git-ship/SKILL.md`.
- **model-override** — het per-kaart, per-kolom of per-persona
  model-alias dat de persona-frontmatter overstemt. Volledige
  precedentieketen: `docs/cockpit/kanban-model-override.md`.
- **orphan-fallback** — herdispatch-pad voor kaarten die hun deliverable
  dreigen te verliezen. Vangnet:
  `scripts/sweep_orphaned_deliverables.py`.
- **outcome** — voor een analyse-kaart: de afgesproken eindstand
  (`decomposed`, `not_feasible`, `no_action_needed`, `filed_standalone`).
  Veld op `CardMove`: `backend/app/kanban/schemas.py:423`.
- **parent_card_id** — id van de parent-kaart wanneer deze kaart via
  analyst-decompositie is ontstaan. Veld op het model:
  `backend/app/kanban/models.py:114`.
- **plan_ref** — verwijzing van een kind-kaart naar het plan van zijn
  parent-kaart. Wordt geleverd door `add_plan_attachment`.
- **prompt-injector** — een per-lane prompt-uitbreiding zoals Caveman
  of Ponytail; de ingangsregels van deze prompten komen in
  `.claude/prompt-injectors/`.
- **reviewer** — de persona die een Done-kaart opnieuw toetst vóór
  goedkeuring. Persona: `.claude/agents/reviewer.md`.
- **scheduled_at** — ISO-timestamp op een kaart die de dispatch
  vasthoudt tot dat moment. Veld op het model:
  `backend/app/kanban/models.py:96`.
- **session** — een lopende agent-uitvoering; "session" verwijst vaker
  naar het transcript op disk. Zie **run** voor de live-kant.
- **ship** — een kaart naar `Done` brengen en de implementatie op
  master krijgen. Recept: `.claude/skills/git-ship/SKILL.md`.
- **skill** — een geïnstalleerde of zelfgeschreven instructie-set die
  een agent kan aanroepen. Per skill één
  `.claude/skills/<naam>/SKILL.md`.
- **spawn** — een nieuwe agent-sessie starten. De CLI-aanroep staat in
  `backend/app/services/agentic_cli/claude_code.py:94`
  (`build_spawn_command`).
- **spillover** — uitwijken naar een tweede abonnement wanneer het
  eerste zijn limiet bereikt. In
  `backend/app/kanban/subscription_pool.py:274`
  (`has_available_spillover`).
- **spec_doc** — pad in `card.metadata["spec_doc"]` dat naar het
  bronontwerpdoc wijst. Constante:
  `backend/app/kanban/schemas.py:86` (`SPEC_DOC_META_KEY`).
- **subagent** — een agent die binnen een andere agent draait, met
  eigen prompt en tools.
- **summary** — de korte tekst die een `Done`- of `Impediment`-move
  verplicht stelt. Veld op `CardMove`:
  `backend/app/kanban/schemas.py:422`.
- **work_type** — classificatie van een kaart (`feature`, `bug`,
  `chore`, `analysis`) die de routering bepaalt. Veld op het model:
  `backend/app/kanban/models.py:77`.

## Stand van zaken (kind-kaarten)

- **Kind-kaart 2 (backend rename):** geland. `AgentProvider`-baseclass →
  `AgenticCli`; registry naar `services/agentic_cli/`; DB-kolom `provider` → `cli`;
  `AgentTeam` → `RunGroup` + `RunMembership`; `agent_bridge/`-modulenaam → `runs/`;
  `platform_env.py` → `provider_env.py`, `PLATFORM_*` → `PROVIDER_*`,
  `build_platform_env` → `build_provider_env`.
- **Kind-kaart 3 (frontend rename):** geland. `AgentProviderId` → `AgenticCliId`.
- **Kind-kaart 4 (cc_bridge-consolidatie):** geland. Het onderscheid
  `cc_bridge` (Claude-Code-only) vs `agent_bridge` (multi-CLI) is opgelost
  door de multi-CLI-laag onder `runs/` te consolideren. `cc_bridge` blijft
  bestaan voor de Claude-Code-specifieke hook/rest-routes.
- **Kind-kaart 5 (docs-sweep):** deze kaart. De woordenlijst is het
  anker voor eerste-gebruik-verwijzingen uit de leesbaarheidsnorm.

## Wijzigingslog

- 2026-07-10 — Eerste versie, vastgelegd door kind-kaart 1
  (terminologie-glossary).
- 2026-07-13 — Docs-sweep over `kanban-dispatch-spec`, `multi-agent-kanban`,
  `agent-mail-spec`, `upstream-agent-teams-decision`, `kanban-followups`,
  `spike-claude-code-model-switching`, `fase-1-validation`, `fase-2-plan`,
  `fase-2-spec`, `orchestration-substrate-decision`,
  `sandcastle-integration-plan`, `spec-driven-development-fase-0-decision`,
  `00-orientation` en de root-`CLAUDE.md`. Paden, identifiers en
  docs-rondje; bewuste `AgentTeamPreset`/`AgentTeamSlot`-vermeldingen
  (upstream-specifieke class-namen) blijven staan.
- 2026-08-08 — Aanvullende termen toegevoegd zodat de woordenlijst het
  anker is voor eerste-gebruik-verwijzingen. Totaal nu 35 begrippen
  (5 canoniek + 30 aanvullend). Kaart `8d37a7b9…`.
