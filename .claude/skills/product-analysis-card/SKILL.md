---
name: product-analysis-card
description: Use when a human points at ONE specific external product / repo / URL and asks for a Cockpit product-analyse comparison ("Product analyse - <url>", "vergelijk deze toepassing met de onze", "wat kunnen we leren van X", "wat kunnen we overnemen van Y", "die lijkt matuurder") — creates a Backlog-kaart in an existing project with four fixed description fields and `work_type=analysis`. This skill MAKES the card; the `product-analysis` skill is the executor and runs the actual analysis. Runs interactively outside the autonomous dispatcher.
---

# product-analysis-card

A human pointed at one external product, repo, or URL and asked *"wat
kunnen we hiervan leren / overnemen / beter doen?"* — turn that into a
**Backlog-kaart in an existing project** with the four fields the
analysis-execution skill needs. This skill produces the card; the
`product-analysis` skill picks it up and does the actual analysis.

**Division of labour — read this before you start:**

| You have | You use |
|---|---|
| A free conversation about a brand-new app / project / tool (inceptie) | `new-app` (interview → cardless birth of a real project) |
| A pointer to one external product, want to compare | **this skill** (creates the Backlog-kaart) |
| A Backlog-kaart titled `Product analyse - <...>` waiting to be analysed | `product-analysis` (executes the analysis) |
| A fixed sources list, periodic sweep, "what's new in the space?" | `market-research` |
| A problem in this repo you noticed mid-task | `flag-problem` |

This skill was carved out of `intake-authoring` because that skill
housed two unrelated forms (inceptie + product-analyse). The inceptie
flow moves to a separate `new-app` skill; this skill is the
product-analyse authoring home — *verhuizing* documented in
[`docs/cockpit/kaartloze-app-inceptie-decision.md`](../../../docs/cockpit/kaartloze-app-inceptie-decision.md)
(analyse waarnaar de inceptie-flow kaartloos wordt).

## When to use

- A human says *"vergelijk deze toepassing met de onze"*, *"wat kunnen
  we leren van X"*, *"wat kunnen we overnemen van Y"*, *"die lijkt
  matuurder"*, or types the canonical trigger *"Product analyse -
  <url>"*.
- The output is a **Backlog-kaart in an existing project** — the user
  names the project. Not a new project, not the meta-project `intake`
  column.
- The human has (or is willing to provide) a premise, focus questions,
  and the desired depth — the four fixed fields below.

## When NOT to use

- A new app-idea from free conversation — that's `intake-authoring` (inceptie-flow).
- A small change inside an existing project — that's a normal Backlog card via the dispatcher, or `flag-problem` for problems.
- A periodic sweep over a sources list — that's `market-research`.
- The analysis itself — that's `product-analysis`. This skill makes the card; that one runs the analysis.

## The contract — one Backlog-kaart, nothing more

Dit is geen meta-project intake-kaart en geen promotie-flow — de kaart
landt rechtstreeks in `Backlog` van een bestaand project dat je met
`resolve_project_key` hebt opgezocht. Er is geen
`create_project_from_intake`-stap en geen `spec`/`plan`-deliverable.

1. **Eén Backlog-kaart in een bestaand project** (`create_card` via
   `cockpit-kanban` MCP, `project=<resolved key>`, `column="Backlog"`).
   `work_type="analysis"` — that is an existing `WORK_TYPES`-waarde
   (`schemas.py:35`), not a new field. **No** `card.agent` set: skills
   are not personas, and `product-analysis` is not a role. The
   `analyst`-persona modus 2 picks the card up via
   `work_type="analysis"` + the title `Product analyse - …`.
2. **The `product-analysis` skill is the executor** — no `plan`/`spec`-deliverable,
   no `add_plan_attachment`. Those are child-card families, not
   applicable to a bare analysis-kaart.
3. **`resolve_project_key` first** — no guessing. A mistyped
   project-key makes an invisible bucket (the same lesson in
   `flag-problem` step 1, learned the hard way).

## The four required fields in the `description`

The description has **exactly four** fixed fields, in this order. The
idea: step 1 of the `product-analysis` skill reads them 1-to-1 and has
exactly the input it needs to test the premise (see
[`product-analysis/SKILL.md`](../product-analysis/SKILL.md) step 1).

```
URL/product: <URL of productnaam>
Premisse/aanleiding: <wat de gebruiker denkt dat waar is>
Focusvragen: <focusvragen>  —  of, als er geen zijn: "geen — gebruik de standaard"
Diepgang: <learning analysis | go/no-go (adopt/integrate)>
```

**The field labels are fixed strings** — `URL/product`,
`Premisse/aanleiding`, `Focusvragen`, `Diepgang`. They are literally
what the `product-analysis` skill recognises; deviating breaks the
matching read-path. The body of each field is free, except for
`Focusvragen` where the literal *"geen — gebruik de standaard"* is the
default-out.

## Flow

**Step 1 — confirm trigger and form.** Say out loud: *"I'm using the
product-analysis-card skill to turn this comparison request into a
Backlog-kaart in an existing project."* Confirm this is **not** an
inceptie-kaart and not a promote-flow — the user provides the project
where the Backlog-kaart should land.

**Step 2 — collect the four fields.** Probe until each field has
concrete content (or the "geen — gebruik de standaard"-out for
Focusvragen). An empty field is no answer — the executor can only test
the premise if it exists.

**Step 3 — resolve project key.** `resolve_project_key` for this project
(or the REST-fallback below). Tell the user explicitly which project-key
you got so a typo doesn't slip past unnoticed.

```bash
# MCP preferred
project_key=$(cockpit-kanban resolve_project_key --project_path "$(git rev-parse --show-toplevel)")
# Fallback when MCP is unavailable
curl -s "http://localhost:8000/api/v1/kanban/project-key?project_path=$(git rev-parse --show-toplevel)"
```

**Step 4 — land the card.**

```
card = create_card(
    project=<resolved project key>,
    column="Backlog",
    title="Product analyse - <naam of URL>",
    description=(
        "URL/product: <URL of productnaam>\n"
        "Premisse/aanleiding: <...>\n"
        "Focusvragen: <focusvragen | geen — gebruik de standaard>\n"
        "Diepgang: <learning analysis | go/no-go (adopt/integrate)>\n"
    ),
    work_type="analysis",
)
```

The four `Label:`-rules above are literally what the
`product-analysis` skill reads (step 1); no more, no less. **The
executor is the `product-analysis` skill** (`.claude/skills/product-analysis`)
— not as a fifth `Label:`-field but as a flat pointer in the
card-text: the `analyst`-persona modus 2 recognises it by the
combination `work_type="analysis"` + the title `Product analyse - …`
+ the skill reference. Stop here: the card is on `Backlog` of the
named project with the right `work_type`.

## Common mistakes

| Excuse | Why it's wrong |
|---|---|
| *"I'll set `card.agent='product-analyst'`, then routing is explicit"* | Persona values are a closed set (`engineer` / `analyst` / `reviewer`); `product-analysis` is a skill, not a persona. `work_type="analysis"` + the skill reference in the description is what the `analyst`-persona triggers on. |
| *"I'll make an inceptie-kaart and promote that to Backlog"* | Product analyse is not an inceptie — there is no promote-step and no `spec`/`plan`-deliverable. The card goes directly in `Backlog` of an existing project. |
| *"The field labels aren't sacred, 'Premisse' works too"* | The `product-analysis` skill reads the labels literally (`URL/product`, `Premisse/aanleiding`, `Focusvragen`, `Diepgang`). Other variants break the matching read-path. |
| *"I'll leave `Diepgang` empty, that defaults to learning analysis"* | No field = no testable claim. The executor needs that question to decide between an `analysis`-doc or a `decision`-doc + register-row (see `product-analysis/SKILL.md` §5). |
| *"I'll retroactively change existing card `87b99d2d…`"* | That's an imperative outside this skill. Existing bare-title cards stay on the legacy default; the new form is for future cards. |
| *"I'll keep this in `intake-authoring` — it's been there all along"* | The two forms are unrelated; the inceptie-flow moves to a separate `new-app` skill. Mixing them creates the same drift this split solves (see [`kaartloze-app-inceptie-decision.md`](../../../docs/cockpit/kaartloze-app-inceptie-decision.md)). |

## Why interactive, not autonomous

A product-analyse-kaart is human-werk by definition: the human
provides the premise and the focus questions, and the four-field
contract doesn't compress into a single-shot autonomous decision.
Run this skill in a dedicated interactive session with the human
present.

## Legacy bare-title cards (forward-looking — not retroactively rewritten)

This four-field form applies **prospectively** (forward-looking).
Existing bare-title cards like `87b99d2d…` ("Product analyse -
https://github.com/donkruger/Kanban") have only a title and a URL —
they are **not** retroactively rewritten to the new four-field form.
Step 1 of the `product-analysis` skill falls back gracefully on the
legacy *bare-title*-default when the description is empty. The
new four-field contract is for **future** cards where the user
supplies the premise, focus, and depth.
