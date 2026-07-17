// Plans Overview — read-only "Plans & Specs" aggregator (Optie B, kanban
// card 885d0b61 / 9e33a359). The B+C shape is fixed by the backend
// ``GET /plans/overview`` endpoint; the SPA mirrors exactly that contract
// so a single fetch hydrates the list view, and a single follow-up fetch
// (one row clicked) hydrates the detail view via
// ``GET /plans/overview/docs/{path}``.
//
// Mirrors ``backend/app/models/schemas.py``:
//   CardPlanItem / DocSpecItem / PlansOverviewResponse / DocContentResponse.

export interface CardPlanItem {
  /** Unique row id (KanbanDeliverable.id). Stable across refreshes. */
  deliverable_id: string
  /** ``"plan"`` for the markdown body, ``"plan_ref"`` for an analyst
   *  delegation token. The SPA renders them with the same card chrome
   *  — they're siblings in B, not a hierarchy. */
  kind: 'plan' | 'plan_ref'
  /** The parent kanban card. The SPA sends the user to the kanban card
   *  (``/kanban?card=<id>``) when they click a B row. */
  card_id: string
  card_title: string
  /** Truncated body / JSON envelope; full text lives on the kanban card. */
  excerpt: string
  created_at: string
}

export interface DocSpecItem {
  /** Repo-relative path, e.g. ``"docs/cockpit/foo.md"``. Always under
   *  ``docs/cockpit/`` — the backend's path-traversal guard rejects anything else. */
  path: string
  /** H1 of the first line, with the ``# `` prefix preserved. */
  title: string
  /** ISO8601 UTC (filesystem mtime). */
  modified_at: string
  size_bytes: number
}

export interface PlansOverviewResponse {
  /** Project key the SPA resolved to (echoed so the UI can show
   *  "scoped to <bucket>"). Empty projects still get an echo. */
  project_key: string
  cards: CardPlanItem[]
  docs: DocSpecItem[]
}

export interface DocContentResponse {
  path: string
  title: string
  content: string
  modified_at: string
  size_bytes: number
}
