# Plans & Specs

Read-only aggregator over the platform's two real plan/spec stores: kanban card
plan-attachments and the `docs/cockpit/` decision-doc tree.

## Overview

The Plans & Specs page shows two independent, side-by-side sections for the
active project:

- **From Kanban Cards** — `plan` / `plan_ref` deliverables attached to kanban
  cards (the analyst phase's `add_plan_attachment`), scoped to the active
  project's `project_key`. Clicking a row jumps to the source card
  (`/kanban?card=<card_id>`).
- **From Cockpit Docs** — the repo-wide `docs/cockpit/*.md` index (the
  platform's canonical decision/spec-doc tree, shared across every project).
  Clicking a row opens a detail page rendering the full markdown body.

There is **no join** between the two sections. A correlation via
`card.metadata["spec_doc"]` was considered but deferred — see
[`plans-feature-decision.md`](../cockpit/plans-feature-decision.md) §5 and
§8.2 for why: the anchor has zero producers today, so joining on it would
repeat the "defined, no producer" failure this page itself replaced.

This is the read-only "human window" onto spec-driven development that
`docs/cockpit/spec-driven-development-analysis.md` identified as missing:
sessions are ephemeral, but plans and specs aren't, and there was no UI that
showed the durable ones.

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

## API

- `GET /api/v1/plans/overview?project_path=<path>` — returns
  `{ project_key, cards: CardPlanItem[], docs: DocSpecItem[] }`. `cards` is
  project-scoped; `docs` is repo-wide.
- `GET /api/v1/plans/overview/docs/{rel_path}` — full body of one
  `docs/cockpit/*.md` file, for the detail view. Path-traversal-guarded to
  stay under `docs/cockpit/`.

## See also

- [Kanban](./kanban.md) — analysts attach plan deliverables to cards; that's
  the source of the "From Kanban Cards" section.
- [`docs/cockpit/plans-feature-decision.md`](../cockpit/plans-feature-decision.md)
  — the analysis, options considered, and decomposition into follow-up cards.
