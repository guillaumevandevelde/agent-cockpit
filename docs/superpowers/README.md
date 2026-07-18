# `docs/superpowers/` — taak-werkoutput (promoot naar `docs/cockpit/`)

> Deze map is **werkoutput**, geen canonieke spec-boom. De canonieke bron is
> **`docs/cockpit/`** — zie [`../cockpit/README.md`](../cockpit/README.md).

## Wat staat hier

De `superpowers:writing-plans` / `superpowers:brainstorming`-skills schrijven per taak
één gedateerd paar:

- `specs/<datum>-<naam>-design.md` — het ontwerp (*wat/waarom*).
- `plans/<datum>-<naam>.md` — het implementatieplan (*hoe*, met TDD-stappen).

Sommige taken hebben alleen een spec of alleen een plan; dat is prima.

## Het promotie-contract

Een superpowers-paar is **werkoutput van één taak**, niet de langlevende waarheid. Zodra
het werk **landt** (gemerged in `master`), wordt de blijvende kennis **gepromoot** naar een
topic-genoemd, niet-gedateerd document in `docs/cockpit/`. Vanaf dat moment is het
cockpit-document **canoniek** en dient het superpowers-paar alleen nog als
uitvoerings-/ontwerp-referentie voor die ene taak.

**Promoten =** de blijvende architectuur/beslissing samenvatten (of aanvullen) in een
`docs/cockpit/`-doc en dat doc als leidend registreren in de ledger hieronder + in
[`../cockpit/README.md`](../cockpit/README.md). Kleine of volledig in `CLAUDE.md` /
`00-orientation.md` opgenomen features hoeven geen eigen cockpit-doc — markeer ze dan als
gepromoot met dat doc als doel.

### Zichtbaar én controleerbaar

Historisch stond dit contract alleen in proza in `00-orientation.md` en werd het nooit
afgedwongen. Nu:

- **Zichtbaar:** de ledger hieronder toont per taak de promotie-status en het doeldoc.
- **Controleerbaar (advies, niet-blokkerend):** `scripts/check-superpowers-promotions.sh`
  flag't elke `plans/`- of `specs/`-file die **niet in deze ledger geregistreerd** staat.
  Zelfde filosofie als de OpenAPI-snapshot-check, maar bewust **advies i.p.v. harde gate**
  (zie [`../cockpit/spec-driven-development-analysis.md`](../cockpit/spec-driven-development-analysis.md)
  §4 optie C + §7 "vermijd theater"). De check bewijst niet dát de promotie inhoudelijk
  klopt — alleen dat elk stuk werkoutput een **bewuste** status heeft.

## Promotie-ledger

Status: **✅ gepromoot** (canoniek doc bestaat / is opgenomen) · **⏳ pending** (werk kan
geland zijn, maar nog geen canoniek `docs/cockpit/`-doc — nog te promoten) ·
**⚠️ superseded** (het plan/spec is **niet** uitgevoerd zoals beschreven — de
implementatie week af of het ontwerp werd nooit gevolgd; rij blijft staan voor
historische context met een link naar de canonieke vervanging). Sinds Fase 0b
(2026-07-11) staat alles op ✅ of ⚠️; nieuwe rijen beginnen ⏳ tot ze gepromoot zijn.

| Taak | Plan-file (`plans/`) | Spec-file (`specs/`) | Status | Canoniek doeldoc |
|---|---|---|---|---|
| Agent Bridge — Bedrock/provider selectie | `2026-05-29-agent-bridge-bedrock-platform.md` | `2026-05-29-agent-bridge-bedrock-platform-design.md` | ✅ gepromoot | `cockpit/agent-bridge.md` (Platform-selectie) |
| Claude Cockpit rebrand | `2026-06-11-claude-cockpit-rebrand.md` | `2026-06-11-claude-cockpit-rebrand-design.md` | ✅ gepromoot | `cockpit/00-orientation.md` + `CLAUDE.md` |
| Agent Bridge — session rename | `2026-06-12-agent-bridge-session-rename.md` | `2026-06-12-agent-bridge-session-rename-design.md` | ✅ gepromoot | `cockpit/agent-bridge.md` (Session rename) |
| Self-healing dev supervisor | `2026-06-12-backend-selfhealing-supervisor.md` | `2026-06-12-backend-selfhealing-supervisor-design.md` | ✅ gepromoot | `CLAUDE.md` (Self-healing dev stack) |
| Resume worktree sessions | `2026-06-12-resume-worktree-sessions.md` | `2026-06-12-resume-worktree-sessions-design.md` | ✅ gepromoot | `cockpit/agent-bridge.md` (Resume across worktrees) |
| Scheduled session resume | `2026-06-14-scheduled-session-resume.md` | `2026-06-13-scheduled-session-resume-design.md` | ✅ gepromoot | `cockpit/fase-2-spec.md` + `cockpit/fase-2-plan.md` |
| Kanban dispatch agents | `2026-06-15-kanban-dispatch-agents.md` | `2026-06-15-kanban-agents-design.md` | ✅ gepromoot | `cockpit/kanban-dispatch-spec.md` |
| Kanban MCP robustness | `2026-06-27-kanban-mcp-robustness.md` | `2026-06-27-kanban-mcp-robustness-design.md` | ✅ gepromoot | `cockpit/kanban-dispatch-spec.md` (MCP-robustness & health) |
| Skill stats | — | `2026-06-27-skill-stats-design.md` | ✅ gepromoot | `cockpit/skill-stats.md` |
| Agent Bridge — image paste | — | `2026-06-29-agent-bridge-image-paste-design.md` | ✅ gepromoot | `cockpit/agent-bridge.md` (Image paste) |
| Kanban dispatch transport | `2026-06-29-kanban-dispatch-transport.md` | `2026-06-29-kanban-dispatch-transport-design.md` | ✅ gepromoot | `cockpit/kanban-dispatch-spec.md` |
| Kanban card-edit provider dropdown | `2026-07-03-card-edit-provider-dropdown.md` | `2026-07-03-card-edit-provider-dropdown-design.md` | ✅ gepromoot | `cockpit/kanban-dispatch-spec.md` (Provider vs. persona) |
| Pre-push gate onder concurrency | — | `2026-07-03-prepush-gate-concurrency-design.md` | ✅ gepromoot | `CLAUDE.md` (gate later verwijderd 2026-07-05) |
| MiniMax providers page | `2026-07-04-minimax-providers-page.md` | `2026-07-04-minimax-providers-page-design.md` | ✅ gepromoot | `cockpit/subscriptions.md` (MiniMax-credential relocatie) |
| Code drift detection | `2026-07-05-code-drift-detection.md` | `2026-07-05-code-drift-detection-design.md` | ✅ gepromoot | `cockpit/spec-driven-development-analysis.md` (CI/drift-signaal, Fase 2) |
| Agent Mail | `2026-07-08-agent-mail-implementation.md` | — | ✅ gepromoot | `cockpit/agent-mail-spec.md` |
| Multi-agent kanban | `2026-07-08-multi-agent-kanban.md` | `2026-07-08-multi-agent-kanban-design.md` | ✅ gepromoot | `cockpit/multi-agent-kanban.md` |
| Subscription usage leftover | `2026-07-08-subscription-usage-leftover-plan.md` | `2026-07-08-subscription-usage-leftover-design.md` | ⚠️ superseded | `cockpit/subscriptions.md` (Per-provider usage/quota) — implementatie week af; zie de SUPERSEDED-banner bovenaan `plans/.../subscription-usage-leftover-plan.md` |
| Kanban model override | `2026-07-10-kanban-model-override.md` | `2026-07-10-kanban-model-override-design.md` | ✅ gepromoot | `cockpit/kanban-model-override.md` |
| Settings-gap update | — | `2026-04-10-settings-gap-update-design.md` | ✅ gepromoot | opgenomen in `CLAUDE.md` (Config-feature)¹ |

> **Fase 0b afgerond (2026-07-11):** alle voorheen ⏳ pending-rijen zijn gepromoot naar een
> canoniek `docs/cockpit/`-doc (of expliciet als "opgenomen in `CLAUDE.md`" gemarkeerd). De
> ledger is nu volledig ✅. De checker eist alleen dat elke file **hier geregistreerd** is
> (advies, niet-blokkerend).
>
> ¹ De Settings-editor-uitbreiding (Auto Mode, Plugins, Sandbox-FS, hook-events) is
> feature-pariteitswerk voor Claude Code's eigen `settings.json`, geen fork-specifieke
> architectuur — daarom gemarkeerd als opgenomen in de **Config**-feature (`CLAUDE.md`
> Features-lijst) i.p.v. een eigen cockpit-doc te krijgen. Dat is de door het promotie-contract
> toegestane escape-hatch voor "kleine of volledig in `CLAUDE.md` opgenomen features".

## Een nieuwe superpowers-plan toevoegen

1. Laat de skill het `<datum>-<naam>-design.md` / `<datum>-<naam>.md`-paar schrijven.
2. Voeg direct een rij toe aan de ledger hierboven met status ⏳ (of ✅ + doeldoc als het
   werk meteen in een cockpit-doc landt).
3. `scripts/check-superpowers-promotions.sh` blijft dan groen. Vergeet je het, dan flag't
   de check de niet-geregistreerde file (advies).
