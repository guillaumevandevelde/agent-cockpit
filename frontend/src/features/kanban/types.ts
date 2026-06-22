export const DEFAULT_COLUMNS = ["Backlog", "Analysis", "Todo", "Doing", "Review", "Done"] as const;
export type Column = string;

export const PRIORITIES = ["none", "low", "medium", "high"] as const;
export type Priority = (typeof PRIORITIES)[number];

export interface KanbanColumn {
  id: string;
  project_key: string;
  name: string;
  rank: string;
  default_agent: string | null;
  created_at: string;
  updated_at: string;
}

export interface Deliverable {
  id: string;
  kind: "pr" | "branch" | "commit" | "link" | "note";
  ref: string;
  created_at: string;
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
  claimed_by?: string | null;
  claimed_at?: string | null;
  created_at: string;
  updated_at: string;
  deliverables: Deliverable[];
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
