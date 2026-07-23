---
name: intake-authoring
description: Use when a human wants to author a new app-idea or inceptie-kaart — turns a free conversation with the user into a promotable intake-column kanban card carrying a design-doc (kind=spec) and a TDD plan (kind=plan). Runs interactively outside the autonomous dispatcher.
---

# intake-authoring

Turn a free conversation about a new app-idea into a **promotable** kanban
intake-card: one card in the `intake` column of the meta-project with a
`spec`-deliverable (the design-doc) and a `plan`-deliverable (the
implementation plan) attached. Once the card is on the board, a human clicks
the existing **Promote** button (`create_project_from_intake`) to birth the
real project.

This skill is the **voordeur** of the inceptie-pipeline (gat A in
`docs/cockpit/product-inceptie-pipeline.md` §2.3). Decision:
[`docs/cockpit/intake-authoring-flow-decision.md`](../../../docs/cockpit/intake-authoring-flow-decision.md).

## When to use

- A human says "I have an idea for a new app / project / tool" and wants to
  formalise it as a Cockpit project — the conversation is the input.
- After a long chat, the human wants a portable design-doc + TDD plan that
  survives past the session.
- The output target is **the meta-project's intake column** (not a new
  project — that doesn't exist yet, by design).

## When NOT to use

- The human wants to capture a small change inside an existing project —
  that's `flag-problem` (for problems) or a normal Backlog card via the
  dispatcher. Don't route ordinary bug-fix ideation through here.
- The idea already has a repo. Use `superpowers:brainstorming` + `writing-plans`
  directly, land the plan in the existing repo's `docs/plans/`, then file a
  normal Backlog card.
- The human wants the skill to **promote** the intake card into a project.
  That is the Promote button's job (`create_project_from_intake`). This skill
  stops at "promotable"; promotion is a deliberate human click.

## The contract — three artefacts, exactly once

1. **One card in the `intake` column of the meta-project** (`create_card` via
   the `cockpit-kanban` MCP server, `column="intake"`). The card's `title` is
   a short slug of the idea; `description` is a 2-4 sentence summary.
   `create_project_from_intake` reads `title` + `description` from this card
   and copies them onto the first Backlog card of the new project — so make
   them the *kern* of the idea.
2. **One `spec`-deliverable** carrying the brainstorming design-doc
   (`attach_deliverable(card_id, kind="spec", ref=<design-md-body>)`).
3. **One `plan`-deliverable** carrying the writing-plans plan
   (`attach_deliverable(card_id, kind="plan", ref=<plan-md-body>)`).

**Route for `kind="plan"` — verified working on a childless card.** The
analyst-style `add_plan_attachment` rejects childless cards
(`mcp_server.py:573`, requires `child_card_ids`). `attach_deliverable(kind="plan")`
is the intake-correct path: it only checks `ref` non-empty
(`mcp_server.py:349-372`, `schemas.py:205-211`) and accepts `kind="plan"`
on any card. Conventions §3 documents this; an in-session smoke test
(card `7b807d6a237c488ab603cfc4a7741670`, deleted) confirmed both `plan` and
`spec` land.

**Do NOT call `create_project_from_intake`.** Promotion is the human's click,
not the skill's.

## Flow

**Step 1 — confirm trigger and announce.** State out loud: "I'm using the
intake-authoring skill to turn this conversation into an intake card."
Briefly recap the contract (3 artefacts, no promotion) and ask the human to
confirm. If they wanted something else (e.g. just brainstorming for an
existing repo), stop and hand back to plain brainstorming.

**Step 2 — design-doc.** **REQUIRED SUB-SKILL:** Use
`superpowers:brainstorming`. Follow it through phase 1-4 (understanding →
exploration → design presentation → design documentation). The skill writes
its output to `docs/plans/YYYY-MM-DD-<topic>-design.md`. Read that file
back into context — that's the design-doc body you'll attach as `spec`.

**Step 3 — implementation plan.** **REQUIRED SUB-SKILL:** Use
`superpowers:writing-plans`. It writes the plan to
`docs/plans/YYYY-MM-DD-<feature-name>.md`. Read it back — that's the plan
body you'll attach as `plan`.

**Step 4 — resolve the meta-project key.** **Do not guess.** Use the
`cockpit-kanban` MCP `resolve_project_key` tool with this repo's working
directory. A hand-typed or guessed key creates an orphaned bucket invisible
from the real board (this is the lesson in `flag-problem` step 1, learned
the hard way). If MCP is unavailable, fall back to
`curl -s "http://localhost:8000/api/v1/kanban/project-key?project_path=$(git rev-parse --show-toplevel)"`.

**Step 5 — land the card + two deliverables.**

```
card = create_card(
    project=<resolved meta-project key>,
    column="intake",
    title="<short slug of the idea>",
    description="<2-4 sentence kern of the idea>",
)
attach_deliverable(card_id=card.id, kind="spec", ref=<design-md body>)
attach_deliverable(card_id=card.id, kind="plan", ref=<plan-md body>)
```

Stop here. The card is now promotable via the Promote button. Tell the human
the card id and that they can Promote whenever they're ready.

## Common mistakes

| Excuse | Why it's wrong |
|---|---|
| "I'll just call `create_project_from_intake` to finish the flow" | Promotion is the human's click. The skill hands them a promotable card; nothing more. |
| "I'll use `add_plan_attachment` to land the plan" | That tool rejects childless cards (`mcp_server.py:573`). Use `attach_deliverable(kind="plan", ...)`. |
| "The meta-project key is `claude-cockpit`, I'll type it" | Guessing creates an orphaned bucket. Resolve via `resolve_project_key` or the REST endpoint — every time. |
| "I'll skip the brainstorming approval gate, the user already agreed" | The approval gate is the entire reason `brainstorming` is reused. Section-by-section approval is what catches the "we built the wrong thing" failure mode. |
| "I'll create the card in Backlog, it's basically the same" | `intake` is the only column `create_project_from_intake` accepts (`inception_service.py:89`). Anywhere else, Promote fails. |

## Why interactive, not autonomous

Intake is human-werk by definition: the dispatch-loop never picks up `intake`
column cards (`_DISPATCH_COLUMNS = ("Backlog", "To Resume")`,
`docs/cockpit/kanban-conventions.md` §1). The brainstorming-approval gates are
many-turn conversational flows that don't compress into single-shot
`report_impediment` decisions (decision §4). Run this skill in a dedicated
interactive session with the human present.

## Alternative form: Product analyse (forward-looking, *niet* de intake-flow)

Een **tweede** kaartvorm die deze skill kan produceren, naast de
inceptie-kaart hierboven: een **forward-looking Product analyse** voor een
**bestaand project**. Dit is **geen** meta-project `intake`-kaart — er is
geen promotiestap, geen `create_project_from_intake`, en de kaart landt
rechtstreeks in `Backlog` van een project dat je met `resolve_project_key`
hebt opgezocht.

De vorm bestaat *prospectively*: bestaand kaart `87b99d2d…` ("Product
analyse - https://github.com/donkruger/Kanban") heeft alleen een titel en
wordt **niet** met terugwerkende kracht omgezet — stap 1 van de
`product-analysis`-skill valt graceful terug op de legacy
*bare-title*-default als de beschrijving leeg is. De nieuwe vorm is voor
**toekomstige** kaarten waar de gebruiker de vier velden wel kan invullen.

### Wanneer deze vorm (en wanneer niet)

- **Wel** — de gebruiker zegt *"vergelijk deze toepassing met de onze"*,
  *"wat kunnen we leren van X"* of *"Product analyse - <url>"* en geeft een
  voorgedachte premisse, focusvragen en gewenste diepgang mee. De skill
  zet dan om tot een **Backlog**-kaart van een **bestaand** project.
- **Niet** — voor een nieuw app-idee (de inceptie-flow hierboven), een
  klein idee binnen een bestaand project dat geen aparte analyse verdient
  (een gewone Backlog-kaart via de dispatcher), of een periodieke sweep
  over een bronnenlijst (dat is `market-research`).

### Het contract — één Backlog-kaart, niets meer

1. **Eén Backlog-kaart in een bestaand project** (`create_card` via
   `cockpit-kanban` MCP, `project=<resolved key>`, `column="Backlog"`).
   `work_type="analysis"` — dat is een bestaande `WORK_TYPES`-waarde
   (`schemas.py:35`), geen nieuw veld. **Geen** `card.agent` instellen:
   skills zijn geen personas, en `product-analysis` is geen rol. De
   `analyst`-persona in modus 2 pikt de kaart op aan de hand van
   `work_type="analysis"` + de titel + de `product-analysis`-skill-verwijzing
   in de beschrijving.
2. **De `product-analysis`-skill is de executor** — de beschrijving noemt
   de skill expliciet zodat het matching-pad in
   [`.claude/agents/analyst.md`](../../../.claude/agents/analyst.md) (modus
   2, *"Vraagt de kaart om één specifieke externe applicatie/repo/product te
   analyseren"*) hem herkent. Geen `plan`/`spec`-deliverable, geen
   `add_plan_attachment` — dat zijn kind-kaart-families, niet van toepassing
   op een kale analyse-kaart.
3. **`resolve_project_key` gebruiken**, geen gok — net als stap 4 hierboven.
   Een verkeerd getypt project-key maakt een onzichtbare bucket.

### De vier verplichte velden in de `description`

De beschrijving heeft **exact vier** vaste velden, in deze volgorde. Het
idee: stap 1 van de `product-analysis`-skill leest ze 1-op-1 en heeft
precies de input die hij nodig heeft om de premisse te toetsen (zie
[`product-analysis/SKILL.md`](../product-analysis/SKILL.md) stap 1).

```
URL/product: <URL of productnaam>
Premisse/aanleiding: <wat de gebruiker denkt dat waar is>
Focusvragen: <focusvragen>  —  of, als er geen zijn: "geen — gebruik de standaard"
Diepgang: <learning analysis | go/no-go (adopt/integrate)>
```

**Taal van de veld-labels is fixed** — `URL/product`, `Premisse/aanleiding`,
`Focusvragen`, `Diepgang`. Die zijn letterlijk wat de
`product-analysis`-skill herkent; afwijken breekt de afspraak. De body van
elk veld is vrij, behalve `Focusvragen` waar de literal *"geen — gebruik de
standaard"* de standaard-uitwijking is.

### Flow

**Stap P1 — bevestig trigger en vorm.** Zeg hardop: *"Ik gebruik de
intake-authoring-skill, alternative form Product analyse, om deze vraag in
een Backlog-kaart van een bestaand project om te zetten."* Bevestig dat
dit **geen** inceptie-kaart is en geen promotie-flow — de gebruiker geeft
het project op waarin de Backlog-kaart moet landen.

**Stap P2 — verzamel de vier velden.** Vraag door tot elk veld een
concrete inhoud heeft (of de "geen — gebruik de standaard"-uitwijking
voor Focusvragen). Een leeg veld is geen antwoord — de skill kan de
premise pas toetsen als die er staat.

**Stap P3 — resolve project key.** `resolve_project_key` van dit project
(of de REST-fallback uit stap 4 hierboven). Vertel de gebruiker expliciet
welk project-key je kreeg zodat een tikfout niet ongezien blijft.

**Stap P4 — land de kaart.**

```
card = create_card(
    project=<resolved project key>,
    column="Backlog",
    title="Product analyse - <naam of URL>",
    description=(
        "URL/product: <URL of productnaam>\n"
        "Premisse/aanleiding: <...>\n"
        "Focusvragen: <focusvragen | geen — gebruik de standaard>\n"
        "Diepgang: <learning analysis | go/no-go (adopt/integrate)>\n\n"
        "Executor: de product-analysis skill (.claude/skills/product-analysis)."
    ),
    work_type="analysis",
)
```

Stop hier. De kaart staat op `Backlog` van het opgegeven project, met de
juiste `work_type` en een verwijzing naar de skill in de beschrijving. De
dispatcher pakt 'm van `Backlog` op en routeert 'm naar de `analyst`-persona
in modus 2 (zie `analyst.md`); de analyst gebruikt de
`product-analysis`-skill voor de feitelijke analyse.

### Veelgemaakte fouten

| Excus | Waarom fout |
|---|---|
| *"Ik zet `card.agent='product-analyst'`, dan is de routing expliciet"* | Persona-waarden zijn een gesloten set (`engineer` / `analyst` / `reviewer`); `product-analysis` is een skill, geen persona. `work_type="analysis"` + de skill-verwijzing in de beschrijving is wat de `analyst`-persona triggert. |
| *"Ik maak een inceptie-kaart en promoot die naar Backlog"* | Product analyse is geen inceptie — er is geen promote-stap en geen `spec`/`plan`-deliverable. De kaart gaat rechtstreeks in `Backlog` van een bestaand project. |
| *"De veld-labels zijn niet heilig, 'Premisse' werkt ook"* | De `product-analysis`-skill leest de labels letterlijk (`URL/product`, `Premisse/aanleiding`, `Focusvragen`, `Diepgang`). Andere varianten breken het matchende lees-pad. |
| *"Ik laat `Diepgang` leeg, dat is default learning analysis"* | Geen veld = geen toetsbare claim. De skill heeft juist die vraag nodig om te beslissen tussen `analysis`-doc of `decision`-doc + register-rij (zie `product-analysis/SKILL.md` §5). |
| *"Ik pas bestaande kaart `87b99d2d…` retroactief aan"* | Dat is een imperatief buiten deze skill. Bestaande bare-title-kaarten blijven via de legacy default lopen; de nieuwe vorm is voor toekomstige kaarten. |
