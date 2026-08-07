---
status: accepted
created: 2026-08-03
---

# Card feedback fields — merge two near-duplicate controls into one

## Context

`CardDrawer.tsx` shows two near-identical feedback controls under the Done
banner on a Done card:

- `RequestReviewControl` (text-area + "Request review") → POST
  `/cards/{id}/request-review` → backend `service.request_review` posts a
  `**Review requested:**` comment and spawns a sibling analysis card.
- `ReopenControl` (text-area + "Heropen met feedback") → POST
  `/cards/{id}/reopen` → backend `service.reopen_card` posts a
  `**Revisit:**` comment and moves the same card back to `Backlog`.

The two controls share: textarea + submit button + per-prefix "already sent"
state + driven from polled activity. The only product difference is *who*
picks the work up afterwards (analyst-triage vs engineer-hervatting). From
the operator's perspective the two fields feel "vrijwel gelijk" while
cluttering the Done banner with two competing text inputs.

## Goal

One combined feedback control on a Done card: a single text-area shared by
both actions, with two submit buttons that map to the existing endpoints and
preserve the existing analytics prefixes.

## Approach (chosen)

One text-area + two submit buttons in the same `FeedbackControl` component,
mounted in `CardDrawer.tsx` under the Done banner. Each button:

- Uses the same shared `note` state.
- Hits the same endpoint it does today (`requestReview` / `reopen`) — no
  backend or schema change.
- Falls into its own "already sent" disabled state, driven by the same
  per-prefix activity scan that already exists. Posting a `**Review
  requested:**` comment disables "Vraag review aan" but leaves "Heropen met
  feedback" available, and vice versa.

### Test-id changes

- `request-review-control` → `feedback-control` (one container test-id).
- `request-review-note` → `feedback-note`.
- `request-review-submit` → `feedback-submit-review`.
- `reopen-control` is removed (use the new container).
- `reopen-note` is removed (textarea is shared).
- `reopen-submit` → `feedback-submit-reopen`.
- `review-requested-state` stays the same (already-sent read-only banner).

### Out of scope

- Backend endpoints, services, modal schemas, and prefixes — all stay
  identical; both `service.request_review` and `service.reopen_card` keep
  their existing contracts.
- `kanbanApi.requestReview` / `kanbanApi.reopen` API client methods — keep
  them as the two underlying calls.
- Impediment control surface (already on a dedicated page).
- Removing either action — the two endpoints still serve distinct products;
  the merge is UI-only.

## Files touched

- `frontend/src/features/kanban/components/CardDrawer.tsx` — replace the
  two control components with `FeedbackControl`.
- `frontend/src/features/kanban/components/CardDrawer.test.tsx` — update
  tests to target the new container + buttons.

## Testing

Frontend Vitest tests are the gate. Each existing test rewritten to use the
new test-ids:

- "submits the note via requestReview and calls onChanged on a Done card"
  → fill `feedback-note`, click `feedback-submit-review`, assert
  `kanbanApi.requestReview` was called.
- "renders the already-requested state when an `**Review requested:**`
  comment exists" → keep targeted `review-requested-state` lookup.
- "submits the rebuttal via reopen and calls onChanged on a Done card" →
  fill `feedback-note`, click `feedback-submit-reopen`, assert
  `kanbanApi.reopen` was called.
- "does not render the reopen control when the card is not in Done" →
  become "does not render the feedback control when the card is not in
  Done".

## Non-goals

- No new prefix, no schema/migration, no DB change.
- No new product distinctions between the two actions.
- No changes to dispatch / `extract_revisit_question` / `enrich_done_info`.
