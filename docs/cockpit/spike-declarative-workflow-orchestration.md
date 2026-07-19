---
title: "Spike: declaratieve multi-agent workflow-orchestratie — ADR"
type: decision
status: decided
---

# Spike: declaratieve multi-agent workflow-orchestratie — ADR

**Date:** 2026-07-03
**Status:** Decided (build-vs-integrate) — implementation not started
**Trigger:** analyse van [microsoft/conductor](https://github.com/microsoft/conductor) (MIT)

---

## 1. Wat conductor is (grounded facts)

Conductor is een **CLI-tool** (Python, installeerbaar via `uv`/`pipx`/`pip` of een
install-script — geen server/daemon) die multi-agent workflows uitvoert die in
platte **YAML** gedefinieerd zijn:

```yaml
workflow:
  name: simple-qa
  entry_point: answerer
agents:
  - name: answerer
    model: gpt-5.2
    prompt: "Answer: {{ workflow.input.question }}"
    output: { answer: { type: string } }
    routes:
      - to: $end
output:
  answer: "{{ answerer.output.answer }}"
```

- **Step-types**: `agent` (LLM-call via Copilot/Claude/Claude Agent SDK/Hermes),
  `script` (shell command, routing op exit-code of geparste JSON-stdout), `set`
  (Jinja2-template-evaluatie, **geen LLM, geen subprocess**), `terminate`
  (`success`/`failed` met reden), `wait` (polling/delay).
- **Routing**: Jinja2-templates + expression-evaluatie, *first match wins*. Expliciet
  "no LLM in the orchestration loop, no tokens spent deciding what runs next."
- **Sub-workflows**: herbruikbaar, met `input_mapping`, bruikbaar binnen `for_each`
  voor parallelle fan-out.
- **Registries**: named registries (GitHub repo of lokale map); `conductor run
  qa-bot@official#v1.2.3` pinnnt een workflow op versie.
- **Validatie**: `conductor validate` detecteert stale template-refs, missende
  inputs en undeclared dependencies **zonder** de workflow uit te voeren.

Conductor kent **geen enkel begrip** van tmux, git worktrees, Claude Code sessies,
kanban-kaarten of claims — het is een generieke LLM-workflow-engine die zelf geen
idee heeft wat een "Cockpit-sessie" is.

---

## 2. Het gat in Cockpit (concreet, niet hypothetisch)

Vandaag ketent Cockpit's kanban-dispatch (`backend/app/kanban/dispatch.py`) stappen
alleen impliciet: elke kolom = een agent-persona (`_persona_for_card` /
`target_agent`), en de **agent zelf beslist** — via een `move_card`-tool-call vanuit
zijn eigen LLM-redenering — naar welke kolom (dus: welke volgende agent) de kaart
gaat. Er is geen deterministische routing-laag: geen conditie-evaluatie op
exit-codes, geen Jinja2-expressie op kaart-velden, geen `script`/`set`/`wait`
tussenstap zonder een volledige LLM-sessie te spawnen.

Concreet ontbreekt:
- **Deterministische routing** ("als tests groen én PR>0 commits → Review, anders
  → Impediment") zonder dat een LLM die beslissing zelf moet nemen en correct
  moet uitvoeren via een tool-call.
- **Non-LLM tussenstappen**: een losse shell-check of een berekende waarde kost nu
  altijd een volledige agent-sessie (tmux + Claude Code + tokens), terwijl
  conductor's `script`/`set` step-types dat gratis (geen tokens, geen sessie)
  doen.
- **Sub-workflow compositie**: kanban heeft wel een concurrency-cap
  (`get_max_sessions`) en `dispatch_all_pending`, maar geen begrip van
  "fase A moet volledig klaar zijn voor fase B start" — dat is nu impliciet via
  hoe kaarten handmatig worden aangemaakt.
- **Pre-run validatie**: er is geen `conductor validate`-equivalent — een kapotte
  workflow-definitie (typo in een kolomnaam, ontbrekende persona-file) wordt pas
  zichtbaar als de dispatcher hem probeert uit te voeren.

Dit is exact het gat dat de kaart beschrijft, en de bestaande architectuur
bevestigt het: `dispatch.py` is uitsluitend een *spawn*-laag (claim → move →
transport), geen *routing*-laag.

---

## 3. Optie A — conductor als externe engine

Zoals sandcastle wordt aangestuurd (`docs/cockpit/sandcastle-integration-plan.md`):
Cockpit-UI beheert/launcht workflow-runs, conductor draait als subprocess.

**Waarom dit hier minder goed past dan bij sandcastle:**

| | Sandcastle | Conductor |
|---|---|---|
| Vult een **hard te herbouwen** gat | Ja — Docker-isolatie, 6 provider-CLI's, branch-strategieën | Nee — routing-logica is een paar honderd regels |
| Kent Cockpit's domeinobjecten | Nee, maar heeft er ook geen synchronisatie mee nodig (losstaande runs) | Nee, en heeft ze wel nodig — een `agent`-step moet een CC-sessie in een worktree spawnen, claims respecteren, aan een kanban-kaart hangen |
| Resultaat van integratie | Eigen runs-tabel, eigen lifecycle, geen kaart-koppeling nodig | Twee bronnen van waarheid: conductor's run-state vs. het kanban-board — moeten voortdurend gereconcilieerd worden |
| Extra procesgrens | Node.js subprocess (al geaccepteerd patroon) | Python subprocess of library-embed, **plus** custom step-type-plugins om terug te bellen naar Cockpit's `dispatch_card` / claim-API |

Om conductor's `agent`-step een echte Cockpit-sessie te laten spawnen (tmux,
worktree, persona, ship-mode) moet je alsnog een custom step-type/plugin bouwen
die exact `_run_card` aanroept — je herbouwt dus de dispatcher-integratie sowieso,
maar nu met een tweede systeem (conductor's workflow-run) dat naast het
kanban-board moet blijven kloppen. Dat is meer bewegende delen voor hetzelfde
resultaat, en de kern-waarde van conductor (Jinja2-routing, step-vocabulaire) is
klein genoeg om native over te nemen (zie optie B).

---

## 4. Optie B — lichte native workflow-laag (aanbevolen)

Neem conductor's **vocabulaire en semantiek** over (step-types, Jinja2-routing,
"no LLM in the loop" voor niet-agent stappen, pre-run validatie), maar implementeer
ze **in** de bestaande dispatcher in plaats van er een tweede engine naast te
zetten. Dit past op de bestaande architectuurprincipes uit
`kanban-dispatch-spec.md`:

- **Trigger blijft polling** — dezelfde APScheduler-tick die vandaag
  `run_dispatch_tick` aanroept, evalueert ook niet-agent stappen (`script`/`set`/
  `wait`) synchroon in de tick zelf — geen sessie, geen tokens.
- **Claimant blijft de sessie** — alleen `agent`-stappen spawnen nog een sessie;
  de claim/move/compensatie-machinerie die er al is (claim-before-spawn,
  race-safe) hoeft niet te veranderen.
- **Eén bron van waarheid** — het kanban-board zelf draagt de workflow-state
  (welke stap een kaart nu is), geen los run-log dat moet syncen.
- **Reuse**: `pyyaml` (workflow-definities) en `apscheduler` (trigger) zijn al
  dependencies. Enige nieuwe dependency: **Jinja2** (klein, veelgebruikt, geen
  extra proces) voor de routing-expressies en `set`-stappen.

### Ruw ontwerp (voor de vervolgkaart, niet nu bouwen)

- Kaarten krijgen een optioneel `workflow_step` (Jinja2-routes + step_type) i.p.v.
  alleen een kolom-naam — de agent hoeft dan niet meer zelf `move_card` te
  callen; de dispatcher evalueert `routes` na afloop van de stap.
- `script`/`set`/`wait`/`terminate` stappen draaien **inline in de dispatch-tick**,
  zonder sessie te spawnen — alleen `agent`-stappen gaan via de bestaande
  `_run_card`/transport-flow.
- Een workflow-definitie leeft als YAML naast de agent-persona's
  (`.claude/workflows/<name>.yaml`), met een validatie-stap (analoog aan
  `conductor validate`) die vóór dispatch draait: checkt of elke `routes.to`
  een bestaande kolom/agent is en of elke Jinja2-referentie een bekend veld is.

---

## 5. Aanbeveling

**Optie B** — bouw een lichte native routing-laag, geïnspireerd op conductor's
step-vocabulaire en Jinja2-determinisme, bovenop de bestaande kanban-dispatcher.
Conductor zelf niet integreren: de waarde die het zou toevoegen (deterministische
routing, non-LLM stappen, pre-run validatie) is klein genoeg om native te bouwen,
en native bouwen vermijdt een tweede bron van waarheid naast het kanban-board —
precies het risico dat optie A zou introduceren.

## 6. Vervolgkaarten (uit scope van deze spike)

1. **Deterministische routing voor kanban-kaarten** — `workflow_step` op
   `KanbanCard` (step_type + Jinja2 `routes`), dispatcher evalueert routes na een
   `agent`-stap i.p.v. de agent zelf `move_card` te laten beslissen.
2. **Non-LLM step-types in de dispatch-tick** — `script` (shell + exit-code/JSON
   routing) en `set` (Jinja2 computed value) uitgevoerd inline in
   `run_dispatch_tick`, geen sessie/tokens.
3. **Workflow-definitie + validatie** — YAML-workflow-bestand per project
   (`.claude/workflows/<name>.yaml`) plus een validatie-pass (stale
   kolom/agent-refs, ontbrekende Jinja2-inputs) vóór dispatch, analoog aan
   `conductor validate`.
4. *(Stretch, niet inplannen voor v1)* Sub-workflow-compositie en een
   workflow-registry — pas relevant zodra er meerdere herbruikbare
   multi-stap-ketens ontstaan.

## 7. Open vragen voor de vervolgkaarten

- Blijft `move_card`-door-de-agent een geldige fallback voor kaarten zonder
  `workflow_step` (backwards compatible), of wordt declaratieve routing verplicht
  zodra een workflow-definitie aanwezig is?
- Hoe verhoudt een `wait`-stap zich tot de bestaande `PendingQueue` (geheugendruk-
  gebaseerde retry) — zelfde mechanisme hergebruiken of apart houden?
