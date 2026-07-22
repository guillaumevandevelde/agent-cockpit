---
title: "Terminology — canonieke woordenlijst"
type: reference
status: active
---

# Terminology — canonieke woordenlijst

> **Bron van waarheid** voor de vijf kernbegrippen in Agent Cockpit. Elke term
> hieronder is canoniek; de kolom "Vroegere synoniemen" documenteert wat er
> is uitgefaseerd (kind-kaarten 2 t/m 5 van de terminologie-parent `23ac6715…`
> zijn inmiddels geland — backend rename, frontend rename, cc_bridge-consolidatie,
> docs-sweep). Functionele code volgt de canonieke namen; alleen in changelogs,
> git-geschiedenis en (historische) `AgentTeam`-refs naar **upstream's**
> `AgentTeamPreset`/`AgentTeamSlot` (zie `upstream-agent-teams-decision.md`)
> komen de oude termen nog voor.

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

## Stand van zaken (kind-kaarten)

- **Kind-kaart 2 (backend rename):** ✅ geland. `AgentProvider`-baseclass →
  `AgenticCli`; registry naar `services/agentic_cli/`; DB-kolom `provider` → `cli`;
  `AgentTeam` → `RunGroup` + `RunMembership`; `agent_bridge/`-modulenaam → `runs/`;
  `platform_env.py` → `provider_env.py`, `PLATFORM_*` → `PROVIDER_*`,
  `build_platform_env` → `build_provider_env`. Kolom-renames zijn via
  `ALTER TABLE … RENAME COLUMN` (geen drop+recreate — live kanban-data
  staat in deze db), cf. de afspraak hieronder.
- **Kind-kaart 3 (frontend rename):** ✅ geland. `AgentProviderId` → `AgenticCliId`.
- **Kind-kaart 4 (cc_bridge-consolidatie):** ✅ geland. Het onderscheid
  `cc_bridge` (Claude-Code-only) vs `agent_bridge` (multi-CLI) is opgelost
  door de multi-CLI-laag onder `runs/` te consolideren; `cc_bridge` blijft
  bestaan voor de Claude-Code-specifieke hook/rest-routes die geen zinvolle
  CLI-generalisatie hebben. De naam "cc" verwart nog steeds met het bredere
  CLI-concept — toekomstige opschoning, niet blokkerend.
- **Kind-kaart 5 (docs-sweep):** ← deze kaart.

## Wijzigingslog

- 2026-07-10 — Eerste versie, vastgelegd door kind-kaart 1 (terminologie-glossary).
- 2026-07-13 — Docs-sweep (`kanban-dispatch-spec`, `multi-agent-kanban`,
  `agent-mail-spec`, `upstream-agent-teams-decision`, `kanban-followups`,
  `spike-claude-code-model-switching`, `fase-1-validation`, `fase-2-plan`,
  `fase-2-spec`, `orchestration-substrate-decision`,
  `sandcastle-integration-plan`, `spec-driven-development-fase-0-decision`,
  `00-orientation`) + root `CLAUDE.md`: paden (`platform_env`/`agent_bridge` →
  `provider_env`/`runs`), identifiers (`AgentTeam` → `RunGroup`,
  `AgentTeamMember` → `RunMembership`) en docs-rondje; bewuste
  `AgentTeamPreset`/`AgentTeamSlot`-vermeldingen (upstream-specifieke
  class-namen, niet van ons) blijven staan.