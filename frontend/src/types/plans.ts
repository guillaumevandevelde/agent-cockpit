// Plans Overview — read-only "Plans & Specs" aggregator (Optie B, kanban
// card 885d0b61 / 9e33a359). The B+C shape is fixed by the backend
// ``GET /plans/overview`` endpoint; the SPA mirrors exactly that contract
// so a single fetch hydrates the list view, and a single follow-up fetch
// (one row clicked) hydrates the detail view via
// ``GET /plans/overview/docs/{path}``.
//
// Mirrors ``backend/app/models/schemas.py``:
//   CorrelatedCardItem / CardPlanItem / DocSpecItem /
//   PlansOverviewResponse / DocContentResponse.

export interface CorrelatedCardItem {
  /** Kanban card id (``KanbanCard.id``); used to build ``/kanban?card=<id>``. */
  card_id: string
  /** Display title; rendered as the chip text on the C row. */
  card_title: string
}

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
  /** Repo-relative path of the ``docs/cockpit/`` doc this card claims to
   *  implement (mirrors ``card.metadata["spec_doc"]``). The SPA renders
   *  this as a clickable doclink on the B row that navigates to the
   *  matching C row at ``/plans/<encoded-path>``. ``null`` when the
   *  card has no anchor or when the anchor is a URL — both cases are
   *  non-correlatable, see the read-side filter in
   *  ``backend/app/api/v1/plans.py``. */
  spec_doc: string | null
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
  /** Cards in the project whose ``metadata["spec_doc"]`` exactly equals
   *  ``path``. The SPA renders these as an "implemented by cards"
   *  chip-list on the C row, each chip linking back to its source kanban
   *  card (``/kanban?card=<id>``). Empty when no card claims this doc. */
  implemented_by: CorrelatedCardItem[]
}

export interface PlansOverviewResponse {
  /** Project key the SPA resolved to (echoed so the UI can show
   * "scoped to <bucket>"). Empty projects still get an echo. */
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
