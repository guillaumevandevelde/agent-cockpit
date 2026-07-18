/** Per-card run ledger — shared type for /api/v1/kanban/cards/{cid}/run-ledger.
 *
 * Mirrors the backend `RunLedger` and its step schemas in
 * `backend/app/kanban/schemas.py`. Kept as a separate module (like
 * `cardUsage.ts`) so it can be imported without dragging in the rest of the
 * kanban types. Also re-exported from `../api.ts` for convenience.
 *
 * Every step is best-effort: a missing source (a card with no branch yet, a
 * gc'd worktree, no iteration-loop run) yields an `available: false` step
 * with an explanatory `note` — the component renders that as an empty/"not
 * yet" state per step rather than failing the whole tab. See
 * docs/cockpit/run-ledger-decision.md §5.
 */

/** Spine step 1 — what the card asked for. Always present on the card row. */
export interface RunLedgerTaskStep {
  title: string;
  description: string;
}

/** Spine step 2 — the dispatch prompt the model received, reconstructed
 * deterministically at request time (persona preamble omitted). */
export interface RunLedgerContextStep {
  available: boolean;
  prompt: string | null;
  phase: string | null;
  ship_mode: string | null;
  impediment_question: string | null;
  impediment_answer: string | null;
  revisit_question: string | null;
}

export interface RunLedgerFileChange {
  path: string;
  insertions: number;
  deletions: number;
}

/** Spine step 3 — diffstat of the card's `branch` deliverable against
 * origin/master. Best-effort: no branch / unregistered project / pruned ref
 * all yield `available: false` + a `note`. */
export interface RunLedgerFilesStep {
  available: boolean;
  branch: string | null;
  files: RunLedgerFileChange[];
  files_changed: number;
  insertions_total: number;
  deletions_total: number;
  note: string | null;
}

/** Spine step 4 — verify/CI outcome. `status`/`last_line` come from the
 * local iteration-loop progress file (routinely gc'd once the card is Done);
 * `ci_url` (the `pr` deliverable ref) is the durable surface. */
export interface RunLedgerTestsStep {
  available: boolean;
  status: string | null;
  iteration_count: number | null;
  last_line: string | null;
  ci_url: string | null;
  note: string | null;
}

/** Spine step 5 — what was accepted + which model did it. Tokens are NOT
 * re-derived here — see `RunLedger.usage_url` / the Tokens tab. */
export interface RunLedgerOutcomeStep {
  column: string;
  outcome_text: string | null;
  outcome_source: string | null;
  model: string | null;
  completed_at: string | null;
}

export interface RunLedger {
  card_id: string;
  task: RunLedgerTaskStep;
  context: RunLedgerContextStep;
  files: RunLedgerFilesStep;
  tests: RunLedgerTestsStep;
  outcome: RunLedgerOutcomeStep;
  usage_url: string;
}
