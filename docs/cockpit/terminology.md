# Terminology — canonieke woordenlijst

> **Bron van waarheid** voor de vijf kernbegrippen in Claude Cockpit. Elke term
> hieronder is canoniek; synoniemen die in bestaande code/copy voorkomen zijn
> gemarkeerd als "uit te faseren" en worden in volgende kind-kaarten van de
> terminologie-parent (`23ac6715…`) systematisch vervangen.
>
> **Scope van dit document:** alleen naamgeving. Functionele codewijzigingen
> zitten in kind-kaarten 2 t/m 5 (backend rename → frontend rename →
> cc_bridge-consolidatie → docs-sweep).

## De vijf begrippen

| # | Canonieke term | Wat het is | Huidige synoniemen (uit te faseren) | Rationale |
|---|---|---|---|---|
| 1 | **Agent** | Subagent-persona uit `.claude/agents/*.md` — prompt + tools + permission_mode (engineer, analyst, …). | "agent" in `AgentProvider`, `AgentProviderId`, `agent_bridge`, `AgentTeam`, `agent_activity`, `agent_mail`, `agent-performance` (deels — zie #5). | Persona is al canoniek in de Agents-feature en in `kanban-dispatch-spec`. Alleen de naamsvervuiling door overlap met #5 moet eruit. |
| 2 | **Provider** | Subscription-aanbieder (vendor): Anthropic, MiniMax, OpenAI, Bedrock. | "platform" in `platform_env.py` (`PLATFORM_ANTHROPIC`/`PLATFORM_BEDROCK`/`PLATFORM_MINIMAX`), `SpawnCommandOptions.platform`; inconsistente "provider"-copy in de Subscriptions-UI die deze *wel* bedoelt. | "Provider" is de marktconforme term voor de vendor die een LLM-dienst levert. "Platform" is intern en dubbelzinnig; in Subscriptions-UI is "provider" al in gebruik voor dit concept. Eén naam, vastgelegd. |
| 3 | **CLI** (voluit: agentic coding CLI) | Lokaal geïnstalleerde CLI-runtime die de agent-persona aanstuurt: `claude-code`, `codex-cli`, `copilot-cli`, `mimo-code`, `open-code`. | "provider" in `services/providers/`, `AgentProvider`-baseclass, `_PROVIDERS`-registry, `AgentProviderId` (frontend), DB-kolom `provider` op `BridgeSessionAttachment`/`AgentTeam`. | "Tool" botst met de bestaande, diep verankerde betekenis "toegestane tools" (Bash/Read/Edit-permissies, MCP-tools, agent's `tools:`-veld). "Runtime" is te generiek (botst met model-runtime). "CLI" matcht de bestaande IDs (`codex-cli`, `copilot-cli` eindigen al op `-cli`). |
| 4 | **Model** | Het onderliggende LLM: Sonnet-5, Opus 4.8, Fable, GPT, MiniMax-M3, … | (geen noemenswaardige overlap in naamgeving) | Gezond. Eén DRY-punt staat los: modellijsten worden per CLI los gedupliceerd; dat is technical debt, geen terminologiekwestie. |
| 5 | **Run** | Een lopende instantie van een CLI uit #3 die werkt aan een taak — typisch een tmux-sessie + process tree, al dan niet gegroepeerd in een team (lead + members). | "agent" in `AgentTeam`, `AgentTeamMember`, `AgentTeam.provider` (kolom hernoemd tegelijk met #3), `agent_bridge/`-modulenaam, `agent_activity`, agent-mail-deelnemer, agent-performance-tile. | "Sessions"-feature = transcriptgeschiedenis (read-only, file-based, **verleden tijd**) — niet hetzelfde, geen botsing. "Run" leent zich van CI/CD-terminologie (GitHub Actions Run, GitLab CI Job) waar het een "instance of a defined action" is. Korter dan "AgentInstance" en vermijdt de "agent"-dubbelzinnigheid. |

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

## Open punten voor de kind-kaarten

- **Naamruimte-prefix voor #3 in code:** de baseclass heet nu `AgentProvider`. Vervanging: `AgenticCli` (één klasse, kwalificeert zichzelf t.o.v. de algemene "cli"-term). De registry wordt `services/agentic_cli/`. DB-kolom `provider` → `cli`. Voor #5: `AgentTeam` → `AgentRunGroup`, `AgentTeamMember` → `AgentRun`. Module `agent_bridge/` → `runs/` of `agent_runs/` (kind-kaart 4 beslist).
- **Frontend `AgentProviderId` → `AgenticCliId`:** kind-kaart 3.
- **cc_bridge vs agent_bridge:** kind-kaart 4 consolideert; dit is geen naam-kwestie alleen — `cc_bridge` is de oorspronkelijke Claude-Code-only bridge, `agent_bridge` de latere multi-CLI-generalisatie die het nooit volledig heeft vervangen. De naam "cc" verwart sowieso met het bredere CLI-concept.
- **Geen migratiesysteem:** kind-kaart 2 moet de kolom-renames (`provider`, `platform`) via `ALTER TABLE … RENAME COLUMN` doen, niet via drop+recreate — de live kanban-data staat in deze db.

## Wijzigingslog

- 2026-07-10 — Eerste versie, vastgelegd door kind-kaart 1 (terminologie-glossary).