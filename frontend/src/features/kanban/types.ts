export const DEFAULT_COLUMNS = ["Backlog", "Analysis", "Todo", "Doing", "Review", "Done"] as const;
export type Column = string;

export const PRIORITIES = ["none", "low", "medium", "high"] as const;
export type Priority = (typeof PRIORITIES)[number];

export const PLATFORMS = ["anthropic", "bedrock", "minimax"] as const;
export type Platform = (typeof PLATFORMS)[number];

export interface KanbanColumn {
  id: string;
  project_key: string;
  name: string;
  rank: string;
  default_agent: string | null;
  default_platform: string | null;
  created_at: string;
  updated_at: string;
}

export interface Deliverable {
  id: string;
  kind: "pr" | "branch" | "commit" | "link" | "note";
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
  agent?: string | null;
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
  deliverables: Deliverable[];
}

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
  tools: string[];
  db_ok: boolean;
  error: string | null;
}

export interface DispatchPauseStatus {
  paused: boolean;
  paused_until: string | null;
}
