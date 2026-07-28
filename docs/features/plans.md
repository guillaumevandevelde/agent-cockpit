# Plans & Specs

Read-only aggregator over the platform's two real plan/spec stores: kanban card
plan-attachments and the `docs/cockpit/` decision-doc tree.

## Overview

The Plans & Specs page shows two side-by-side sections for the active project,
correlated by `card.metadata["spec_doc"]` (kanban plan
`2026-07-28-plans-b-c-correlation`):

- **From Kanban Cards** — `plan` / `plan_ref` deliverables attached to kanban
  cards (the analyst phase's `add_plan_attachment`), scoped to the active
  project's `project_key`. Clicking a row jumps to the source card
  (`/kanban?card=<card_id>`). When the card has a `metadata.spec_doc`
  anchor pointing at a `docs/cockpit/*.md` file, the row also surfaces an
  inline doclink that navigates straight to the matching doc detail page.
- **From Cockpit Docs** — the repo-wide `docs/cockpit/*.md` index (the
  platform's canonical decision/spec-doc tree, shared across every project).
  Clicking a row opens a detail page rendering the full markdown body.
  When the doc is the `metadata.spec_doc` of one or more project cards,
  the row also surfaces an "Implemented by cards" chip-list; each chip is
  a kanban-card link (`/kanban?card=<id>`) so the user can jump from a
  doc to the cards that implement it.

The detail page (`/plans/<encoded-path>`) carries the same correlation
chip-list alongside the markdown body — the doc page hydrates the
overview in parallel so a refresh never loses the back-references.

### `spec_doc` and `implemented_by` — both UI directions

The correlation is **bidirectional**, surfaced as two new fields on the
existing list-view response shape (`GET /plans/overview`):

- `CardPlanItem.spec_doc: str | None` — the card's `metadata.spec_doc`
  anchor, when it is a non-empty repo-relative path (URL anchors
  — `http://`/`https://`, any case — normalise to `null` because there
  is no C row to jump to). Surfaced on the B side as an inline doclink
  ("Open spec doc: docs/cockpit/…") that navigates to the matching
  detail page.
- `DocSpecItem.implemented_by: list[CorrelatedCardItem]` — every card in
  the active project whose `metadata.spec_doc` exactly equals this
  doc's path. Each entry carries `card_id` + `card_title`. Surfaced on
  the C side as an "Implemented by cards" chip-list and on the detail
  page as a sibling section below the markdown body. Cards are
  deduplicated per path and sorted by `card_id`, so the chip order is
  stable across requests.

The match is exact — equality with the C path is the contract, by
design — so a typo in a card's anchor shows the literal string on the B
row without silently rewriting it to `null`. Only URL anchors normalise
to `null` on the B side; see
[`plans-feature-decision.md`](../cockpit/plans-feature-decision.md) §10
for the measured adoptie-gate rationale.

## History — why this page used to be empty

Before kanban card `885d0b61…` (2026-07), this page browsed a dedicated
`kanban_plans` database table via a full CRUD API (`GET/POST/PUT/DELETE
/plans`). That table had **zero live writers** in the normal workflow — the
only writers were a manual `POST /plans` call no UI ever made, and a
one-time `migrate_plans_to_kanban.py` import script for legacy
`~/.claude/plans/*.md` files that Cockpit sessions don't write to on this
box. The page was therefore always empty, while the platform's actual
plans/specs lived in two other stores this page didn't read.

[`plans-feature-decision.md`](../cockpit/plans-feature-decision.md) has the
full diagnosis. The resolution ("Optie B") was to repurpose the page as a
read-only aggregator over the stores that *are* populated (above), and phase
out `kanban_plans` entirely — table, `KanbanPlanService`, the CRUD routes,
and the migration script were removed (kanban card `528c5ca2…`).

The B↔C correlation (kanban card `725fbdd35bfa413e98c24315d0a174d1`,
plan `2026-07-28-plans-b-c-correlation`) was implemented on top of the
same Optie-B foundation once the `spec_doc` anchor had a measurable
producer population (the adoptie-gate documented in
`plans-feature-decision.md` §8.2/§10).

## API

- `GET /api/v1/plans/overview?project_path=<path>` — returns
  `{ project_key, cards: CardPlanItem[], docs: DocSpecItem[] }`. `cards` is
  project-scoped; `docs` is repo-wide. The `spec_doc` /
  `implemented_by` correlation fields are built from the same
  `KanbanCard × KanbanDeliverable` LEFT JOIN that powers `cards`, so a
  single round-trip produces the entire response.
- `GET /api/v1/plans/overview/docs/{rel_path}` — full body of one
  `docs/cockpit/*.md` file, for the detail view. Path-traversal-guarded to
  stay under `docs/cockpit/`. The detail page hydrates the overview in
  parallel so the chip-list survives a refresh.

## See also

- [Kanban](./kanban.md) — analysts attach plan deliverables to cards; that's
  the source of the "From Kanban Cards" section. The `spec_doc` anchor
  is set on the card via the kanban `metadata` bag.
- [`docs/cockpit/plans-feature-decision.md`](../cockpit/plans-feature-decision.md)
  — the analysis, options considered, and decomposition into follow-up cards
  (including the B↔C correlation follow-up).
- [`docs/superpowers/plans/2026-07-28-plans-b-c-correlation.md`](../superpowers/plans/2026-07-28-plans-b-c-correlation.md)
  — the implementation plan that landed the `spec_doc` / `implemented_by`
  correlation (Task 1 backend + Task 2 frontend).
