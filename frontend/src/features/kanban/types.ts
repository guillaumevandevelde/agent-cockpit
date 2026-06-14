export const COLUMNS = ["Backlog", "Analysis", "Todo", "Doing", "Review", "Done"] as const;
export type Column = (typeof COLUMNS)[number];

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
