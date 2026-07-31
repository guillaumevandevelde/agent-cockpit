export type Column = string;

export const PRIORITIES = ["none", "low", "medium", "high"] as const;
export type Priority = (typeof PRIORITIES)[number];

// Structured routing hint for auto-dispatch. Distinct from `labels` (free-form
// tags). Mirrors backend/app/kanban/schemas.py WORK_TYPES. See
// docs/cockpit/work-type-routing-analysis.md §2A.
export const WORK_TYPES = ["analysis", "feature", "bug", "chore"] as const;
export type WorkType = (typeof WORK_TYPES)[number];

// Compact icon prefix for each work_type so the badge is scannable at a
// glance when several cards are stacked on the board. The Record<WorkType,
// string> type means adding a fifth work_type in WORK_TYPES is caught by the
// type system here too.
export const WORK_TYPE_ICONS: Record<WorkType, string> = {
  analysis: "📊",
  feature: "✨",
  bug: "🐛",
  chore: "🔧",
};

export const PROVIDERS = [
  "anthropic",
  "bedrock",
  "minimax",
  "anthropic-compatible",
  "opencode-go",
  "opencode",
] as const;
export type Provider = (typeof PROVIDERS)[number];

// Human-readable labels for the provider ids above. Kept as Record<string,
// string> (not Record<Provider, string>) so it can be indexed by the free
// `default_provider` string on a column without a TS narrowing dance.
export const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  bedrock: "Bedrock",
  minimax: "MiniMax",
  "anthropic-compatible": "Compatible endpoint",
  "opencode-go": "OpenCode Go",
  opencode: "OpenCode Zen",
};

// Per-agent-column model+provider override carried on a card. Mirrors the
// backend shape: card.column_overrides = { "<column-name>": ColumnOverride }.
// At dispatch time the resolved target column's entry wins over the column
// defaults for both model and provider (see backend dispatch.py).
export interface ColumnOverride {
  model: string | null;
  provider: string | null;
  endpoint_name: string | null;
}

// CLI ids the subscription pool can route on. Mirrors the backend's
// `services/agentic_cli/__init__.py` registered adapters (the same
// source `dispatch._known_cli_ids` and `subscription_pool._known_pool_clis`
// consult). Order matches the `agentic_cli` registry at the time of
// writing — the backend tolerates a different order (it's a set in the
// router), but the frontend keeps the canonical order so the dialog's
// CLI dropdown reads top-down in the same order as the registry.
export const KNOWN_POOL_CLIS = [
  "claude-code",
  "codex-cli",
  "copilot-cli",
  "mimo-code",
  "open-code",
] as const;
export type PoolCli = (typeof KNOWN_POOL_CLIS)[number];

// Human-readable labels for the CLI ids above. Kept as Record<string,
// string> (not Record<PoolCli, string>) so an unknown id — e.g. one
// round-tripped from a future backend that registers a new adapter —
// still resolves via the fallback `?? cli_id`. Mirrors
// `subscription_pool._known_pool_clis` / `registry._seedable_clis`.
export const POOL_CLI_LABELS: Record<string, string> = {
  "claude-code": "Claude Code",
  "codex-cli": "Codex CLI",
  "copilot-cli": "Copilot CLI",
  "mimo-code": "MiMo Code",
  "open-code": "OpenCode",
};

// One entry in the subscription pool (fase 1b). Mirrors backend
// app/kanban/subscription_pool.py PoolEntry: `provider` is the vendor
// the CLI authenticates against, `model` is an optional model pin
// (null = fall through to the column/card/persona chain), and
// `drempel` is the fraction (0..1] at which the router spills to the
// next entry. Priority is the entry's position in the pool list —
// index 0 is the preferred subscription.
//
// `cli` discriminates the snapshot lookup key — the pool router now
// routes per `{cli, provider}` (kaart 8f40d443…), so an OpenCode
// subscription stops cannibalising the claude-code signal. Optional
// (legacy rows / older API responses round-trip without the field);
// the dialog defaults new entries to `claude-code` and treats
// `null`/missing as `claude-code` everywhere it reads.
//
// History: an earlier build carried `cli` but never consumed it
// (kaart 0b3ad6e2… dropped it as "a UI promise the code did not
// keep"). It is reintroduced here alongside the consuming router
// change so the field stays honest — the regression test
// `test_no_signal_snapshot_does_not_count_as_zero_percent` pins the
// "missing ≠ 0%" contract and the API rejects unknown CLI ids at
// write time.
export interface PoolEntry {
  cli?: string | null;
  provider: string;
  model: string | null;
  endpoint_name: string | null;
  drempel: number;
}

// Seed suggestions shown in the model free-text field before the list has
// ever been refreshed from the installed CLI. Mirrors backend/app/kanban/
// dispatch.py MODEL_OPTIONS_SEED. Not an enum -- any string is accepted (see
// docs/superpowers/specs/2026-07-10-kanban-model-override-design.md).
export const DEFAULT_MODEL_SUGGESTIONS = ["sonnet", "opus", "haiku"] as const;

// Model suggestions for the MiniMax provider. MiniMax exposes its models via
// the Anthropic-compatible endpoint (the ANTHROPIC_MODEL env var — see
// backend/app/services/agentic_cli/provider_env.py MINIMAX_DEFAULT_MODEL).
// The dynamic getModelOptions() list is claude-code-only, so without this a
// minimax column/override would suggest sonnet/opus/haiku, which MiniMax
// rejects. Not an enum -- free text is still accepted; these only back the
// datalist. Keep in sync with the backend default by hand.
//
export const MINIMAX_MODEL_SUGGESTIONS = ["MiniMax-M3"] as const;

// OpenCode Go + Zen subscription model seed — manual mirror of the
// backend ``opencode_catalogs.MODEL_CATALOG`` (same manual-sync contract
// as MINIMAX_MODEL_SUGGESTIONS above — keep the two files in lockstep).
// The backend catalog docstring explains why a runtime fetcher is
// worse than a curated seed.
export const OPENCODE_GO_MODEL_SUGGESTIONS = [
  "deepseek-v4-flash",
  "deepseek-v4-pro",
  "glm-5.1",
  "glm-5.2",
  "grok-4.5",
  "hy3",
  "kimi-k2.6",
  "kimi-k2.7-code",
  "kimi-k3",
  "mimo-v2.5",
  "mimo-v2.5-pro",
  "minimax-m2.7",
  "minimax-m3",
  "qwen3.6-plus",
  "qwen3.7-max",
  "qwen3.7-plus",
] as const;
export const OPENCODE_ZEN_MODEL_SUGGESTIONS = [
  "glm-5",
  "glm-5.1",
  "glm-5.2",
] as const;

// Providers with a static, non-claude model suggestion list. Providers absent
// here fall back to the dynamic claude-code options from getModelOptions().
export const PROVIDER_MODEL_SUGGESTIONS: Record<string, readonly string[]> = {
  minimax: MINIMAX_MODEL_SUGGESTIONS,
  "opencode-go": OPENCODE_GO_MODEL_SUGGESTIONS,
  opencode: OPENCODE_ZEN_MODEL_SUGGESTIONS,
};

// Resolve which model suggestions to show for a given provider. Falls back to
// the dynamic claude-code list when the provider has no static list (or is
// unset / the "column default" sentinel).
export function modelSuggestionsForProvider(
  provider: string | null | undefined,
  fallback: readonly string[],
): readonly string[] {
  return (provider && PROVIDER_MODEL_SUGGESTIONS[provider]) || fallback;
}

// Work-type → persona routing. Mirrors backend/app/kanban/schemas.py
// WORK_TYPE_PERSONA_DEFAULTS. Kept in sync by hand — there are only four
// work_types and the mapping rarely changes; promote to a generated
// constants file if a fifth is added.
export const WORK_TYPE_PERSONA_DEFAULTS: Record<WorkType, string> = {
  analysis: "analyst",
  feature: "engineer",
  bug: "engineer",
  chore: "engineer",
};

export interface WorkTypeMapping {
  id: string;
  project_key: string;
  work_type: WorkType;
  persona: string;
  created_at: string;
  updated_at: string;
}

export interface KanbanColumn {
  id: string;
  project_key: string;
  name: string;
  rank: string;
  default_agent: string | null;
  default_provider: string | null;
  default_model: string | null;
  max_sessions: number | null;
  // Per-lane RTK (token-saver) opt-in flag (kaart c31333bf…,
  // docs/superpowers/specs/2026-07-24-token-saver-integration-design.md).
  // Surfaced as a bool on the API; the SQLite column stores 0/1.
  token_saver_enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface Deliverable {
  id: string;
  kind: "pr" | "branch" | "commit" | "link" | "note" | "plan" | "plan_ref" | "spec";
  ref: string;
  created_at: string;
}

export interface Attachment {
  id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  created_at: string;
}

export interface PlanAttachmentPayload {
  parent_card_id: string;
  plan_deliverable_id: string;
}

export interface Card {
  id: string;
  project_key: string;
  title: string;
  description: string;
  column: Column;
  rank: string;
  priority?: string | null;
  labels?: string[] | null;
  work_type?: string | null;
  agent?: string | null;
  model?: string | null;
  column_overrides?: Record<string, ColumnOverride> | null;
  transport?: string | null;  // worktree | sandcastle | auto (null)
  resume_session_id?: string | null;
  resume_project_folder?: string | null;
  scheduled_at?: string | null;
  // Per-dispatch telemetry breadcrumbs written by dispatch._run_card after a
  // successful spawn. Combined they let the per-card usage endpoint attribute
  // the spawned session's JSONL transcript back to this card. Legacy cards
  // dispatched before this feature landed (or cards still on Backlog) have
  // all-null here. See kanban card 8a2ad986.
  dispatch_started_at?: string | null;
  dispatch_session_id?: string | null;
  dispatch_project_folder?: string | null;
  dispatch_model?: string | null;
  // Provider (vendor subscription) the dispatcher resolved for the last spawn.
  // Drives the provider badge on the card so an operator can see at a glance
  // which provider picked up the card. Null for never-dispatched/legacy cards.
  dispatch_provider?: string | null;
  claimed_by?: string | null;
  claimed_at?: string | null;
  analyst_agent_id?: string | null;
  executor_agent_id?: string | null;
  parent_card_id?: string | null;
  analyst_run_id?: string | null;
  depends_on?: string[] | null;
  // Why the dispatcher last passed this card over, written by the dispatch
  // tick (`dep_resolver.classify_hold`). Null = dispatchable, or not yet
  // ticked. The board prefers these over re-deriving the dispatcher's filters
  // locally: the local copy of that logic could only ever agree with the
  // dispatcher by luck, and reproduced its blind spots faithfully.
  held_reason?:
    | "gated"
    | "dangling_dep"
    | "missing_parent"
    | "awaiting_plan_ref"
    | "dependent"
    | "scheduled"
    | null;
  held_since?: string | null;
  held_blocker?: string[] | null;
  created_at: string;
  updated_at: string;
  // Derived server-side from the op-log: the most recent **Done:** comment's
  // text and the time it landed. Optional because the backend doesn't always
  // have a Done-comment (card was never moved, or moved without a summary) —
  // the CardDrawer shows a banner only when both are set.
  done_summary?: string | null;
  completed_at?: string | null;
  // Impediment-lane classification. Populated by
  // `service.impediment_status_for_card` from open KanbanGate rows + the
  // op-log comment feed; `null` for cards outside the Impediment column.
  // Drives the per-cause badge (and Redispatch quick-action for
  // `dispatch_failed`) in the column view so an operator can tell at a
  // glance whether a blocked card needs a human decision or an infra
  // redispatch. See kanban card `c5eb6f89`.
  impediment_status?:
    | "needs_answer"
    | "dispatch_failed"
    | "resolved"
    | "no_question"
    | null;
  // Free-form key/value bag mirrored from the backend `metadata` column. The
  // spec-driven-development Fase 1 card→spec link lives at `metadata[SPEC_DOC_META_KEY]`.
  metadata?: Record<string, unknown> | null;
  deliverables: Deliverable[];
  // Screenshots/images attached to the card. Injected (by absolute path) into
  // the dispatch prompt so the spawned session can Read them. Optional so
  // responses from older clients / summary endpoints round-trip.
  attachments?: Attachment[];
}

// Machine-readable card → spec-doc link key inside `Card.metadata`. Mirrors the
// backend `SPEC_DOC_META_KEY` in app/kanban/schemas.py — keep the two in sync.
export const SPEC_DOC_META_KEY = "spec_doc";

export interface Gate {
  id: string;
  card_id: string;
  project_key: string;
  question: string;
  options: string[];
  status: "open" | "answered";
  answer?: string | null;
  created_at: string;
  answered_at?: string | null;
}

export interface ActivityEntry {
  hlc: string;
  op_type: string;
  entity_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface AgentStat {
  agent: string;
  tasks: number;
  completed: number;
  failed: number;
  in_progress: number;
  success_rate: number | null;
  avg_duration_seconds: number | null;
  median_duration_seconds: number | null;
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  total_tokens: number;
}

export interface StatsTotals {
  total_tasks: number;
  completed: number;
  failed: number;
  in_progress: number;
  success_rate: number | null;
  avg_duration_seconds: number | null;
}

export interface FailureStat {
  agent: string | null;
  reason: string;
  count: number;
}

export interface AgentStatsResponse {
  project_key: string;
  totals: StatsTotals;
  agents: AgentStat[];
  common_failures: FailureStat[];
  tokens_available: boolean;
}

export interface McpHealth {
  ok: boolean;
  advertised_endpoint: string | null;
  routes_to_mount: boolean;
  message_post_status: number | null;
  tool_call_ok: boolean;
  protocol_version: string | null;
  tools: string[];
  db_ok: boolean;
  error: string | null;
}

export interface DispatchPauseStatus {
  paused: boolean;
  paused_until: string | null;
  // Per-provider pause slots independently active from the legacy global
  // ``paused`` flag (see kanban-limit cards). Optional so older responses
  // without the field don't crash consumers — treat undefined as [] at the
  // use site. Names are the same provider IDs the column override uses
  // (``PROVIDER_LABELS`` maps them to display labels).
  paused_providers?: string[];
  // Operator-toggled indefinite pause per subscription (kaart f056b2888a…).
  // Independent from both ``paused`` (auto-tripped global) and
  // ``paused_providers`` (auto-tripped per-provider) — a manual toggle has
  // no deadline and coexists with the time-based slots. Optional for the same
  // reason as ``paused_providers``: treat undefined as [] at the use site.
  manually_paused_providers?: string[];
}

// Mirrors backend/app/services/run_service.py RunInstance. Used by the
// "Run this branch" preview flow on Done cards (kanban-card d2689f2d).
export type RunStatus =
  | "pending"
  | "starting"
  | "healthy"
  | "unhealthy"
  | "failed"
  | "stopped";

export interface RunInstance {
  id: number;
  instance_id: string;
  project_path: string;
  command: string[];
  env_keys: string[];
  port: number;
  url: string;
  health_path: string | null;
  status: RunStatus;
  transport: "container" | "subprocess";
  container_id: string | null;
  pid: number | null;
  log_path: string | null;
  error: string | null;
  started_at: string;
  stopped_at: string | null;
}

// PO-wachtrij ("Wacht op jou") — see kanban card `c7ea21b0…` and
// `docs/cockpit/product-owner-volgbaarheid-analyse.md` §2b/§4.1/§5 kaart B.
// Mirrors the backend `WachtrijItem` / `WachtrijResponse` shapes from
// `backend/app/kanban/schemas.py`.

export type WachtrijKind =
  | "impediment_needs_answer"
  | "gate_open"
  | "review_requested"
  | "awaiting_plan_ref";

// Human-readable label per kind, shown on the wachtrij card. Centralised
// here so the badge is consistent if the same kind surfaces elsewhere
// later (e.g. an inbox notification feed).
export const WACHTRIJ_KIND_LABELS: Record<WachtrijKind, string> = {
  impediment_needs_answer: "Impediment",
  gate_open: "Open gate",
  review_requested: "Review",
  awaiting_plan_ref: "Wacht op plan",
};

export interface WachtrijItem {
  card_id: string;
  card_title: string;
  card_column: string;
  kind: WachtrijKind;
  reason: string;
  created_at: string;
  wait_seconds: number;
}

export interface WachtrijResponse {
  project_key: string;
  total: number;
  items: WachtrijItem[];
}
