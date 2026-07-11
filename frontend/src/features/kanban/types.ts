export const DEFAULT_COLUMNS = ["Backlog", "Analysis", "Todo", "Doing", "Review", "Done"] as const;
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

export const PROVIDERS = ["anthropic", "bedrock", "minimax"] as const;
export type Provider = (typeof PROVIDERS)[number];

// Human-readable labels for the provider ids above. Kept as Record<string,
// string> (not Record<Provider, string>) so it can be indexed by the free
// `default_provider` string on a column without a TS narrowing dance.
export const PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic",
  bedrock: "Bedrock",
  minimax: "MiniMax",
};

// Per-agent-column model+provider override carried on a card. Mirrors the
// backend shape: card.column_overrides = { "<column-name>": ColumnOverride }.
// At dispatch time the resolved target column's entry wins over the column
// defaults for both model and provider (see backend dispatch.py).
export interface ColumnOverride {
  model: string | null;
  provider: string | null;
}

// Seed suggestions shown in the model free-text field before the list has
// ever been refreshed from the installed CLI. Mirrors backend/app/kanban/
// dispatch.py MODEL_OPTIONS_SEED. Not an enum -- any string is accepted (see
// docs/superpowers/specs/2026-07-10-kanban-model-override-design.md).
export const DEFAULT_MODEL_SUGGESTIONS = ["sonnet", "opus", "haiku"] as const;

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
  created_at: string;
  updated_at: string;
}

export interface Deliverable {
  id: string;
  kind: "pr" | "branch" | "commit" | "link" | "note" | "plan" | "plan_ref";
  ref: string;
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
  claimed_by?: string | null;
  claimed_at?: string | null;
  analyst_agent_id?: string | null;
  executor_agent_id?: string | null;
  parent_card_id?: string | null;
  analyst_run_id?: string | null;
  depends_on?: string[] | null;
  created_at: string;
  updated_at: string;
  // Derived server-side from the op-log: the most recent **Done:** comment's
  // text and the time it landed. Optional because the backend doesn't always
  // have a Done-comment (card was never moved, or moved without a summary) —
  // the CardDrawer shows a banner only when both are set.
  done_summary?: string | null;
  completed_at?: string | null;
  // Free-form key/value bag mirrored from the backend `metadata` column. The
  // spec-driven-development Fase 1 card→spec link lives at `metadata[SPEC_DOC_META_KEY]`.
  metadata?: Record<string, unknown> | null;
  deliverables: Deliverable[];
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
}
