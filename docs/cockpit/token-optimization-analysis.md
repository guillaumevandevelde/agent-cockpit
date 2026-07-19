---
title: "Token-optimalisatie — analyse & aanbevelingen"
type: analysis
status: active
---

# Token-optimalisatie — analyse & aanbevelingen

> **Type:** analyse/beslisdoc (leaf spike). Bron-kaart: *"Analyse - Tokenoptimalisatie"*
> (`1f31b252300d4250b23c49921e8fa998`). Vraag: *"Sturen we soms te veel context mee?
> Zijn er tools die dit kunnen verminderen?"*
>
> Verwant: [`kanban-model-override.md`](./kanban-model-override.md) (model-precedentie),
> [`spike-claude-code-model-switching.md`](./spike-claude-code-model-switching.md),
> [`agent-mail-spec.md`](./agent-mail-spec.md).

## TL;DR

Het platform doet **geen directe Anthropic-API-calls** — het spawnt Claude Code
CLI-sessies (tmux). Alle tokenkost zit dus **binnen de gespawnde sessies** en wordt
gedreven door wat het platform **injecteert**: de persona, de dispatch-prompt, CLAUDE.md,
MCP-toolschemas en SessionStart-hooks. Er is geen plek waar wij zelf een `messages.create`
met te veel context versturen; de hefboom is *configuratie en promptsamenstelling*, niet
API-payloads.

De grootste, laaghangende hefboom is **modelkeuze**: voor de chore in R1 staat
`engineer.md` op `model: 'sonnet'`; alleen `analyst.md` blijft op `opus`. Elke engineer-
sessie draait daardoor standaard op Sonnet tenzij een card/kolom-override Opus
terughaalt; analyst-sessies blijven Opus tot dezelfde heuristiek voor analyst toegepast
wordt. Opus kost grofweg 5× de input- en een veelvoud van de outputprijs van Sonnet.
Sonnet-default met selectieve Opus-escalatie is volgens externe benchmarks de grootste
kostenbesparing (60–90% op reële workloads).

Ranglijst (impact × moeite) staat in [§4](#4-aanbevelingen-geprioriteerd). Niets hiervan is
in dit spike geïmplementeerd — dit doc is de beslisbasis; de concrete follow-up-kaarten
staan in [§5](#5-voorgestelde-follow-up-kaarten).

## 1. Methode & scope

- **Read-only codebase-verkenning** van de dispatch-/prompt-/hook-paden.
- **Meting** van de statische context-artefacten (bestandsgroottes, promptbouwers).
- **Webonderzoek** naar Claude Code token-optimalisatie-technieken (2026), zie [§6](#6-bronnen).
- **Ground truth**: de prompt die déze sessie ontving is exact een gedispatchte
  card-prompt — die is direct als referentie gebruikt.

**Buiten scope:** micro-optimalisatie van individuele service-responses; de app zelf
verbruikt geen LLM-tokens buiten de gespawnde sessies.

## 2. Waar gaan de tokens naartoe?

Een gedispatchte sessie betaalt tokens voor (grofweg, in volgorde van injectie):

| Bron | Waar gebouwd | Grootte (gemeten) | Frequentie | Cachebaar? |
|---|---|---|---|---|
| **CLAUDE.md** | repo-root | 221 regels / ~16,7 KB (**≈ 4.2k tokens**) | elke sessie, elke turn (system) | ja (prompt-cache read) |
| **Persona** (`analyst.md`) | `.claude/agents/` | ~7,8 KB (**≈ 2.0k tokens**) | elke analyst-sessie (in user-prompt) | deels |
| **Persona** (`engineer.md`) | `.claude/agents/` | ~4,8 KB (**≈ 1.2k tokens**) | elke engineer-sessie | deels |
| **Ship-instructies** | `_build_ship_instructions()` | **≈ 2.0k tokens** | elke executor-dispatch | zwak (per-sessie user-turn) |
| **MCP-fallback + flag-problem + session-end blok** | `dispatch.build_card_prompt` | **≈ 0.7–1.0k tokens** | elke dispatch | zwak |
| **MCP-toolschemas** (`cockpit-kanban`) | MCP-server, 19 tools | schema per tool in system-prompt | elke sessie | ja |
| **SessionStart-injecties** | hooks (`superpowers`-skill, agent-mail-roster) | ~2–3 KB | elke sessie-start | ja |
| **MEMORY.md** | auto-memory | index + gerecalde items | elke sessie | ja |

> Tokenschattingen ≈ chars/4; bedoeld om *relatieve magnitude* te tonen, niet als facturatie.

Belangrijkste observaties:

1. **Modeldefault = Opus voor analyst, Sonnet voor engineer.** `head -8
   .claude/agents/*.md` → `engineer.md` staat op `sonnet`, `analyst.md` op `opus`.
   De precedentieketen (`card.column_overrides → card.model > column.default_model >
   persona-frontmatter > geen flag`, zie [`kanban-model-override.md`](./kanban-model-override.md))
   valt dus standaard terug op Sonnet voor engineer-sessies en op Opus voor analyst-
   sessies. Engineer-werk (implementatie, tests, chores) haalt zelden Opus-only-baat;
   analyst-decompositie mogelijk wél — daarom is Sonnet voor engineer nu de default
   en blijft analyst Opus totdat een vervolgkaart hetzelfde doet voor analyst.
2. **CLAUDE.md is 221 regels** — boven Anthropic's richtlijn van <200 regels. Het bevat
   uitgebreide git-workflow-recepten (merge-via-detached-worktree, remote-branch-hygiene,
   pre-push-historie) die als *baseline* in élke sessie meegaan, ook sessies die nooit
   shippen (analyst, read-only spikes).
3. **De dispatch-prompt herhaalt ~2–3k tokens statische boilerplate** (ship + MCP-fallback +
   flag-problem + session-end) bij elke dispatch. Identiek per sessie; staat in de
   *user-turn*, dus profiteert minder van cross-sessie prompt-caching dan system-prompt-content.
4. **Er wordt niets structureel dubbel gestuurd op elke turn.** De agent-mail
   `UserPromptSubmit`-hook is netjes gegate op ongelezen mail (`build_prompt_submit_context`
   → `None` als 0 unread/pending) en injecteert dan één regel. Dat is géén probleem.

## 3. Wat doen we al goed (behouden)

- **Deferred MCP-tools / ToolSearch.** Niet-essentiële MCP-servers (Google Drive, Gmail,
  Calendar, Atlassian, …) worden lazy geladen via `ToolSearch` i.p.v. hun volledige schemas
  vooraf in de system-prompt te zetten. Dit is precies de "trim MCP tool bloat"-techniek uit
  het webonderzoek en bespaart significant.
  **Nagekomen correctie (2026-07-15):** dit geldt óók voor onze eigen `cockpit-kanban`-server
  — gemeten kosten de 19 tools **388** i.p.v. ~4.994 tokens. Deze bullet had dus gelijk en
  R3 hieronder had ongelijk; R3 is daarop afgewezen. Zie
  [`per-persona-mcp-allowlist-decision.md`](./per-persona-mcp-allowlist-decision.md).
- **Geen directe API-calls met opgeblazen payloads.** Doordat we de CLI spawnen, erven we
  Claude Code's eigen context-management (prompt-caching, compaction) gratis mee.
- **Multi-agent kanban = subagent-patroon op sessie-niveau.** Analyst splitst → executors
  pakken kind-kaarten los op. Zware analyse-context lekt niet in de uitvoeringssessies. Dit
  is exact de "isolated subagents for high-context work"-aanbeveling.
- **`.mcp.json` is minimaal** (alleen `cockpit-kanban`), en `~/.claude.json` heeft geen
  globale mcpServers — gedispatchte sessies erven dus niet stiekem tientallen persoonlijke
  tools. (Zie verificatie-item R5.)

## 4. Aanbevelingen (geprioriteerd)

| # | Aanbeveling | Impact | Moeite | Risico |
|---|---|---|---|---|
| **R1** | **Sonnet-default per persona/kolom, Opus selectief.** Zet `engineer.md` frontmatter op `sonnet` (of laat leeg → platform-default) en houd Opus voor `analyst` + expliciete card-overrides. Evt. per-kolom `default_model`. | **Zeer hoog** (5×-input-verschil) | Laag (config) | Middel — kwaliteitsregressie op complexe engineer-kaarten; mitigatie: card/kolom-override laat je per kaart escaleren. |
| **R2** | **CLAUDE.md afslanken tot <200 regels.** Verplaats de gedetailleerde git-ship-recepten (detached-worktree-merge, remote-branch-hygiene, pre-push-historie) naar een apart doc/skill dat alleen bij het shippen wordt geraadpleegd; houd CLAUDE.md bij oriëntatie + pointers. | Hoog (elke sessie × elke turn) | Laag–middel | Laag — recepten blijven bestaan, alleen niet meer als baseline. |
| ~~**R3**~~ | ~~**Per-persona MCP-tool-allowlist.**~~ **AFGEWEZEN (2026-07-15) — zie [`per-persona-mcp-allowlist-decision.md`](./per-persona-mcp-allowlist-decision.md).** De premisse hieronder ("19 schemas in élke system-prompt") is **onjuist**: Claude Code defert MCP-schemas achter `ToolSearch`, dus de 19 tools kosten **388** i.p.v. ~4.994 tokens (1,1% van de baseline). `--allowedTools` filtert bovendien geen schemas — het is een permissie-poort en kost netto **+109** tokens. Max haalbare besparing 0,5%; risico: breekt de leaf-spike-analyst. | ~~Middel~~ **≈ nul** | ~~Middel~~ | ~~Laag–middel~~ |
| **R4** | **Dispatch-boilerplate dedupliceren/verwijzen.** De MCP-fallback- en flag-problem-blokken dupliceren bestaande skills (`flag-problem`). Overweeg ze in te korten tot een verwijzing ("volg de `flag-problem`-skill") of te verplaatsen naar een stabiel, cachebaar promptsegment. | Middel | Middel | **Hoger** — dit is het contract dat de agent volgt; te agressief trimmen schaadt betrouwbaarheid. Meten vóór snijden. |
| **R5** | **Verifieer MCP-isolatie van gedispatchte sessies.** Bevestig dat spawn draait met project-scoped MCP (evt. `--strict-mcp-config`) zodat een sessie nooit de globale/persoonlijke MCP-servers van de host-gebruiker erft. Nu lijkt dit oké (`.mcp.json` minimaal), maar het is niet afgedwongen. **Let op:** `--strict-mcp-config` vereist ook `--mcp-config <project>/.mcp.json` erbij — zonder die tweede vlag laadt Claude Code **geen enkele** MCP-server (dus ook geen `cockpit-kanban`), wat acceptance-checks op MCP-beschikbaarheid juist onderuit haalt. | Laag–middel (preventief) | Laag | Laag. |
| **R6** | **Meet vóór/na.** Er is geen tokentelemetrie per dispatch. De `usage`-feature bestaat al; koppel per-kaart tokengebruik zodat R1–R4 meetbaar worden i.p.v. giswerk. | Middel (enabler) | Middel | Laag. |

**Aanbevolen volgorde:** R1 eerst (grootste besparing, laagste moeite, direct terugdraaibaar
via override), dan R2, dan R6 (meten), daarna R3/R4 op basis van de meetdata.

## 5. Follow-up-kaarten — aangemaakt op 2026-07-15

Dit spike levert alleen dit doc op (geen code). De vervolgkaarten hieronder zijn
**aangemaakt op het bord** (Backlog) via review-kaart `4ec799e8`:

| # | Kaart | Aanbeveling | Type |
|---|---|---|---|
| 1 | `d17b6e6a` Sonnet-default voor engineer-persona, Opus selectief | R1 | chore |
| 2 | `a738497d` CLAUDE.md < 200 regels: git-ship-recepten naar apart doc/skill | R2 | chore |
| 3 | ~~`28e1558e` Per-persona MCP-tool-allowlist voor gedispatchte sessies~~ → **NO-GO** (2026-07-15), geen vervolgkaart — [beslisdoc](./per-persona-mcp-allowlist-decision.md) | R3 | analysis ✅ |
| 4 | `8a2ad986` Per-dispatch tokentelemetrie koppelen aan de usage-feature | R6 | feature |
| 5 | `00fa8325` Verifieer/enforce project-scoped MCP-config bij spawn | R5 | chore |

Geen onderlinge `depends_on`: de aanbevolen volgorde (R1 → R2 → R6 → R3) is prioritering,
geen contract — geen van de vijf consumeert de output van een ander.

**R4 (dispatch-boilerplate dedupliceren) is bewust géén kaart.** Het raakt het contract dat
elke agent volgt en het doc merkt zelf op: *"Meten vóór snijden"*. Het blijft §4-prosa tot
R6 (`8a2ad986`) meetdata levert die de ingreep rechtvaardigt.

> **Nagekomen review (2026-07-15, kaart `4ec799e8`).** Deze §5 stond oorspronkelijk als
> "kandidaat-kaarten voor menselijke triage" en die triage bleef uit — de kaarten zijn
> nooit aangemaakt en de analyse verdampte op Done. De review heeft alle dragende claims
> hierboven opnieuw tegen de code getoetst (beide persona's nog `opus`; CLAUDE.md
> intussen **230** regels, dus §2 se punt 2 is eerder verslechterd dan opgelost; 19
> MCP-tools; `.mcp.json` minimaal) — niets was geïmplementeerd, alles nog geldig.
> De systemische oorzaak (leaf-spike maakt geen eigen kaarten) is inmiddels opgelost via
> kaart `75b54887`; deze analyse dateert van vóór die fix.

## 6. Bronnen

- [How to Manage Claude Code Token Usage — MindStudio](https://www.mindstudio.ai/blog/how-to-manage-claude-code-token-usage)
- [Claude Code Token Optimization 2026 (60–90% besparing) — ofox.ai](https://ofox.ai/blog/claude-code-token-optimization-2026/)
- [How to Reduce Claude Code Token Usage: 8 methoden — agensi.io](https://www.agensi.io/learn/how-to-reduce-claude-code-token-usage)
- [Claude Code Token Optimization: 19 changes — buildtolaunch](https://buildtolaunch.substack.com/p/claude-code-token-optimization)
- [7 Practical Ways to Reduce Claude Code Token Usage — KDnuggets](https://www.kdnuggets.com/7-practical-ways-to-reduce-claude-code-token-usage)
- [Claude Code Context Window / Context Management — claudefa.st](https://claudefa.st/blog/guide/mechanics/context-management)

**Kernpunten uit de bronnen (samengevat):** aggressieve prompt-caching; `/compact` &
`/clear` op ~60% context i.p.v. 90%; **Sonnet-default met selectieve Opus**; geïsoleerde
subagents voor context-zware taken; **MCP-tool-bloat trimmen**; CLAUDE.md < 200 regels
(baseline-kost per turn); scoped taken i.p.v. brede opdrachten.
