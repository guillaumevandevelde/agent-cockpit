---
name: product-analysis
description: Use when a human points at ONE specific external application, repo or product — usually with a URL — and asks what Cockpit can learn from it, adopt from it, or do better ("Product analyse - <url>", "vergelijk deze toepassing met de onze", "wat kunnen we overnemen van X", "die lijkt matuurder"). Produces one grounded docs/cockpit/<naam>-analyse.md plus 0–N scoped child cards. NOT for periodic ecosystem sweeps (that's market-research) and NOT for problems in this repo (that's flag-problem). This skill EXECUTES the analysis; the `product-analysis-card` skill is the AUTHORING half (creates the Backlog-kaart — see that skill for the kaartvorm).
---

# product-analysis

A human handed you one external product and a question of the shape *"wat
kunnen we hiervan leren / overnemen / beter doen?"*. Your output is **one
grounded analysis doc + 0–N scoped child cards** — not a summary of their
README, and not a wall of text that ends in "interessant, wellicht later".

This skill exists because this loop already ran four times ad hoc
(`openhands-analyse.md` 2026-07-13, `jira-lessen-analyse.md` 2026-07-14,
`9router-integratie-analyse.md` 2026-07-19, `lemma-platform-analyse.md`
2026-07-21). The four docs converged on nearly the same structure by
accident. This skill makes that structure — and the two failure modes those
runs each had to discover the hard way — explicit.

Siblings, so you pick the right one:

| You have | Use |
|---|---|
| One named external product + "what can we learn?" | **this skill** |
| A fixed sources list, periodic sweep, "what's new in the space?" | `market-research` |
| Something broken in *this* repo you noticed mid-task | `flag-problem` |
| A human wants to author a new app idea (interview → new project) | `new-app` |
| One or two web pages to inform a card you're already on | just `WebFetch` — no skill |

## The two rules that carry the most weight

**1. The user's premise is a hypothesis, not a given.** In all three
external-product runs so far, the premise in the card was wrong or partly
wrong, and *correcting it* was the highest-value output of the analysis:

| Card premise | What the analysis found |
|---|---|
| OpenHands "kan niet overweg met abonnementen, enkel token based" | Premise-correcting §3 — the subscription framing needed revision, not the product |
| 9Router "lijkt matuurder" | Category error: inference-router (per *request*) vs. our spawn-configurator (per *sessie*). Measured: 6,5 months old, 519 open PRs — broader, not more mature |
| Lemma "doet grotendeels wat wij doen maar matuurder" | §2 "waarom 'matuurder, doet hetzelfde' niet klopt" |

So: state the premise verbatim in the doc, then test it against measured
facts, and put the verdict in the TL;DR. Never open with "X is indeed more
mature" because the card said so.

**2. Compare on layer, not on label.** Two products that both say "agent
orchestration" can sit on different layers and not be alternatives at all.
Before you write a single "wij vs. zij" row, answer explicitly: *do this
product and Cockpit operate on the same layer?* If the answer is no, that
sentence is the analysis — see `9router-integratie-analyse.md` §4.

## Step 1 — pin the scope from the card

Read the card and write down, before fetching anything. The card carries
its scope in up to four fixed fields in the description — the
`product-analysis-card`-skill publishes the same labels so the contract is
defined exactly once:

- **`URL/product`** — the URL or product name, and what kind of thing it is.
- **`Premisse/aanleiding`** — the user's premise, **verbatim** (quote it in
  the doc — it becomes the `Trigger:` block). This is the field that the
  *twee regels die het meest wegen* (top of this skill) will test against
  measured facts.
- **`Focusvragen`** — the focus questions, if any ("kijk vooral naar hoe
  zij X doen"). Field value is the literal string **`geen — gebruik de
  standaard`** when the user has no specific question; treat that as
  *default focus: what do they have that we lack, and what do we do that
  they don't*.
- **`Diepgang`** — is this a *learning* analysis (`type: analysis`) or a
  *go/no-go on adopting/integrating* (`type: decision` + a row in
  `decisions.md`)? A card that asks "moeten we dit integreren?" is a
  decision doc; "wat kunnen we leren van X?" is an analysis doc.

The labels are fixed strings (`URL/product`, `Premisse/aanleiding`,
`Focusvragen`, `Diepgang`) because the `product-analysis-card`-skill emits
exactly those — keep them in sync if either side evolves.

**Legacy bare-title cards.** If the card is a bare title with a URL and no
premise at all (the existing-kaart form, e.g. `87b99d2d…`), do **not**
`report_impediment` for that alone.
Default to the generic focus above and say so in the doc. The four
fixed-field contract is the *forward-looking* form; legacy cards stay on
the legacy default. Escalate only on a real product fork (see Step 7).

## Step 2 — ground *their* facts (with a date and a commit)

Never take a claim from a landing page or a README as fact. For each fact
that will carry a recommendation, record where it came from and when.

For a GitHub product, at minimum:

```bash
# NOTE: default branch is often `master`, not `main` — resolve it, don't guess.
BRANCH=$(gh api repos/OWNER/REPO --jq .default_branch)
gh api repos/OWNER/REPO --jq '{stars:.stargazers_count,created:.created_at,pushed:.pushed_at,license:.license.spdx_id,lang:.language}'
gh api "repos/OWNER/REPO/contents/README.md?ref=$BRANCH" --jq .content | base64 -d | head -200
gh api repos/OWNER/REPO/releases --jq '.[0:5][] | {tag:.tag_name,date:.published_at}'
gh api "repos/OWNER/REPO/pulls?state=open&per_page=1" --jq 'length'   # quote the URL: `?` and `&` glob in zsh
```

Quote every URL containing `?` or `&` — in zsh an unquoted `?` is a glob and
the command never runs; the error reads like an empty API response.

Maturity is a **measured** property, not a vibe: repo age, release cadence,
open-vs-closed issue ratio, open PR backlog, typed vs. untyped, test presence.
Write the numbers down with the date you measured them.

## Step 3 — ground *our* side in code, not memory

The most expensive recurring error is describing our own product from
memory. Every "wij doen dat al" / "wij missen dat" claim in the doc needs a
file reference (`backend/app/kanban/dispatch.py:1187`) or a doc reference
(`docs/cockpit/<x>.md §N`). If you cannot point at where it lives, you don't
know that it exists.

**Start from `docs/cockpit/cockpit-capability-baseline.md`** — one screen per
capability area (dispatch, worktrees + ship, multi-agent DAG,
providers/pool, board + Done gates, session lifecycle, observability), each
claim already carrying a `file:line`. It saves you re-deriving our own side
from scratch, which the first three analyses each did independently.

It is a **starting point, not evidence**: it carries a measurement date and a
commit sha at the top, and line numbers drift. Anything you lift from it into
your analysis you re-verify against the current code first — and if you find
it stale, fix the baseline in the same session.

Check `docs/cockpit/decisions.md` before you recommend anything: a
neighbouring decision may already have settled the exact fork you're about
to reopen (the 9Router run had to defend against reopening three of them).

## Step 4 — filter to what is actually adoptable

Rank the findings by leverage, not by how interesting they are. For each
candidate, three questions:

1. **Which layer does it touch here?** Name the file/module it would change.
2. **What does it unlock that we can't do today?** In product terms — what
   the product owner can then see, do or decide.
3. **What does it cost?** Dependencies, a new protocol surface, a
   credential surface, ongoing maintenance.

Anything that survives becomes a ⭐-ranked subsection in §4 of the doc.
Anything that does not survives as a line in §5 *"wat we bewust NIET
overnemen"* — that section is not padding; it is what stops the next
session re-proposing the same rejected idea.

**Cost and saving claims must be measured or labelled.** A number that
carries a recommendation needs the measurement and the command that produced
it in the doc. If you cannot measure it inside this spike, write
**"ongemeten schatting"** next to it. A vendor's own "saves 20–40%" is a
claim, not a measurement — see `9router-integratie-analyse.md` §9 (K1) and
`token-saver-meet-harnas.md` for what a real measurement recipe looks like.

## Step 5 — write the doc

Copy `templates/analyse-doc.md` from this skill directory to
`docs/cockpit/<product>-analyse.md` (or `<product>-integratie-analyse.md`
for a go/no-go) and fill it in. Keep the section numbering — the four
existing docs share it, and cross-references between them cite section
numbers.

Frontmatter is mandatory (`scripts/check-doc-frontmatter.sh`):
`type: analysis` + `status: active` for a learning analysis;
`type: decision` + `status: decided` for a go/no-go.

Length: the four existing docs land at 235–410 lines. Below ~150 lines you
probably summarised instead of analysed; above ~450 you are writing their
documentation for them.

## Step 6 — decision docs also touch the register

Only if you wrote `type: decision`:

1. Add the four-field header block right under the H1 — `**Datum:**`,
   `**Status:**`, `**Kaart:**`, `**Uitkomst:**` — where `Uitkomst` is the
   first sentence of your register row, verbatim.
2. Add one row to `docs/cockpit/decisions.md` (newest first).
3. Verify: `bash scripts/check-decision-register.sh --check-headers`.

For every doc, decision or not: `./scripts/generate-doc-index.py` to
refresh `README.md` + `llms.txt`.

## Step 7 — dedupe, then file the child cards

The analysis is worthless if the follow-ups live only in prose. Everything
in §4 that you recommend doing becomes a card, or explicitly does not.

1. **Dedupe immediately before creating** — `list_cards` on `Backlog` *and*
   `Impediment` right now, not the scan you did an hour ago. A parallel
   session may have filed an overlapping card inside that window. On a
   match: `comment` on the existing card with what your analysis adds,
   don't duplicate.
2. **Create as children** — `create_card(parent_card_id=<your card>, ...)`,
   1–5 cards, each with a title plus 2–5 sentences of acceptance criteria.
   Soft, speculative ideas stay prose in §5; they are not cards.
3. **Always `add_plan_attachment`** — even when the children are fully
   independent (`depends_on_graph={}`). This is what writes the `plan_ref`
   deliverable onto each child; a child without one is held out of dispatch
   silently by `_awaiting_plan_ref` and looks merely "not started".
4. Mirror the created cards into §7 of the doc, with their ids.

Escalate with `report_impediment(options=[…])` only for a genuine
**product** fork you cannot responsibly settle — the kind that changes *what*
the cards should be. Express the options as product trade-offs, not
implementation choices.

## Step 8 — close the card with an outcome

Move your card to `Done` with an `outcome`:

- `decomposed` — you filed child cards (the normal ending; verified against
  real children, so it cannot be claimed falsely).
- `not_feasible` — the analysis concludes "don't do this". The rationale
  goes in the `summary`.
- `no_action_needed` — the doc is steering only and no card applies. The
  justification goes in the `summary`.

The `summary` leads with the product meaning ("Product owner kan nu beslissen
of …"), engineering detail after.

## Common mistakes

| Excuse | Why it's wrong |
|---|---|
| "The card said they're more mature, so I compared feature lists" | The premise is a hypothesis. Test it — see the table at the top; it was wrong 3 out of 3 times. |
| "Their README says it supports 40 providers" | A README is a claim. Measure what you will build on. |
| "We already do that, I'm pretty sure" | Every claim about our own product needs a `file:line` or a doc §. Memory is the top source of wrong rows in the comparison table. |
| "I listed 12 things we could adopt" | Rank by leverage and cut. 2–4 ⭐-items plus a "smaller learnings" paragraph is the shape that gets acted on. |
| "I wrote the follow-ups as a numbered list in §7" | Prose follow-ups die with the doc. Cards, with acceptance criteria, as children of your card. |
| "Independent children don't need a plan attachment" | They do. Without `plan_ref` they are silently undispatchable. |
| "It saves 20–40% (per their docs)" | Measure it, or write **ongemeten schatting** next to it. |
| "I skipped §5 because nothing was rejected" | If nothing was rejected the filter didn't run. §5 is what prevents the same idea being re-proposed next quarter. |

## Quick reference

```text
pin scope + quote premise  →  ground THEIR facts (date + commit)
  →  ground OUR side (file:line)  →  compare on layer, not label
  →  rank by leverage, write §5 non-goals  →  doc from templates/analyse-doc.md
  →  (decision? header + decisions.md row) + generate-doc-index.py
  →  dedupe → child cards → add_plan_attachment (always)
  →  Done with an outcome
```
