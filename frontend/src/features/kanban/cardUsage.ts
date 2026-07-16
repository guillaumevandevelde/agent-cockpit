/** Per-card token telemetry — shared type for /api/v1/kanban/cards/{cid}/usage.
 *
 * Mirrors the backend `CardUsageResponse` in
 * `backend/app/kanban/schemas.py`. Kept as a separate module so it can be
 * imported without dragging in the rest of the kanban types. Also exported
 * from `../api.ts` for convenience.
 */
export interface CardUsageModelBreakdown {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  total_tokens: number;
}

export interface CardUsage {
  // Resolved Claude Code session_id (UUID extracted from the JSONL stem).
  // null when the JSONL hasn't been written yet (session was spawned <1s
  // ago, or failed before any model call). The UI uses null → "Awaiting
  // first response…" so a fresh dispatch doesn't flash zero tokens.
  session_id: string | null;
  // The model recorded by the dispatcher at spawn time (precedence chain:
  // override > card.model > column default > persona frontmatter).
  // Distinct from `model_breakdowns[*].model` which is the *actual* model
  // used (could differ if the platform retried under a different model).
  recorded_model: string | null;
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
  first_activity: string | null;
  last_activity: string | null;
  model_breakdowns: CardUsageModelBreakdown[];
}

export interface CardUsageResponse {
  /** null = card has no dispatch breadcrumbs (legacy, or never dispatched). */
  usage: CardUsage | null;
}
