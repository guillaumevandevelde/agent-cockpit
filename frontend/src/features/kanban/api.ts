import { apiClient } from "@/lib/api";
import type {
  Card,
  ActivityEntry,
  ColumnOverride,
  KanbanColumn,
  AgentStatsResponse,
  McpHealth,
  DispatchPauseStatus,
  Gate,
  WorkType,
  WorkTypeMapping,
} from "./types";

const BASE = "kanban";

export const kanbanApi = {
  stats: (projectKey: string): Promise<AgentStatsResponse> =>
    apiClient<AgentStatsResponse>(
      `${BASE}/stats?project_key=${encodeURIComponent(projectKey)}`
    ),

  listColumns: (projectKey: string): Promise<{ columns: KanbanColumn[] }> => {
    return apiClient<{ columns: KanbanColumn[] }>(
      `${BASE}/columns?project_key=${encodeURIComponent(projectKey)}`
    );
  },

  createColumn: (body: {
    project_key: string;
    name: string;
    rank?: string;
    default_agent?: string | null;
    default_provider?: string | null;
    default_model?: string | null;
    max_sessions?: number | null;
  }): Promise<KanbanColumn> =>
    apiClient<KanbanColumn>(`${BASE}/columns`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateColumn: (
    id: string,
    body: { name?: string; rank?: string; default_agent?: string | null; default_provider?: string | null; default_model?: string | null; max_sessions?: number | null }
  ): Promise<KanbanColumn> =>
    apiClient<KanbanColumn>(`${BASE}/columns/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  deleteColumn: (id: string): Promise<void> =>
    apiClient<void>(`${BASE}/columns/${id}`, { method: "DELETE" }),

  /**
   * Promote an intake card on the meta-project to a brand-new project on
   * the kanban board (inceptie-pipeline / kanban card c33b2f14 — facet A of
   * platform-as-app-factory). The action is atomic: any failure between
   * the 6 steps rolls back filesystem + kanban-DB + Project row +
   * autodispatch-meta so the system is never left half-registered.
   */
  createProjectFromIntake: (body: {
    intake_card_id: string;
    project_name: string;
    target_path: string;
  }): Promise<{
    project_id: number;
    new_project_key: string;
    first_card_id: string;
  }> =>
    apiClient<{
      project_id: number;
      new_project_key: string;
      first_card_id: string;
    }>(`${BASE}/projects/from-intake`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listCards: (projectKey: string, column?: string): Promise<{ items: Card[] }> => {
    const params = new URLSearchParams({ project_key: projectKey });
    if (column) params.append("column", column);
    return apiClient<{ items: Card[] }>(`${BASE}/cards?${params.toString()}`);
  },

  getCard: (id: string): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards/${id}`),

  activity: (id: string): Promise<ActivityEntry[]> =>
    apiClient<ActivityEntry[]>(`${BASE}/cards/${id}/activity`),

  createCard: (body: {
    project_key: string;
    title: string;
    description?: string;
    column?: string;
    priority?: string | null;
    labels?: string[] | null;
    work_type?: string | null;
    agent?: string | null;
    model?: string | null;
    column_overrides?: Record<string, ColumnOverride> | null;
    transport?: string | null;
    resume_session_id?: string | null;
    resume_project_folder?: string | null;
    scheduled_at?: string | null;
    analyst_agent_id?: string | null;
    executor_agent_id?: string | null;
  }): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateCard: (
    id: string,
    body: {
      title?: string;
      description?: string;
      column?: string;
      agent?: string | null;
      model?: string | null;
      column_overrides?: Record<string, ColumnOverride> | null;
      priority?: string | null;
      labels?: string[] | null;
      work_type?: string | null;
      transport?: string | null;
      resume_session_id?: string | null;
      resume_project_folder?: string | null;
      scheduled_at?: string | null;
      analyst_agent_id?: string | null;
      executor_agent_id?: string | null;
      metadata?: Record<string, unknown> | null;
    }
  ): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  deleteCard: (id: string, force = false): Promise<void> =>
    apiClient<void>(`${BASE}/cards/${id}${force ? "?force=true" : ""}`, { method: "DELETE" }),

  agents: (projectPath: string): Promise<{ agents: string[] }> =>
    apiClient<{ agents: string[] }>(
      `${BASE}/agents?project_path=${encodeURIComponent(projectPath)}`
    ),

  dispatchNow: (id: string, projectPath: string, agent?: string): Promise<{ session_name: string }> =>
    apiClient<{ session_name: string }>(`${BASE}/cards/${id}/dispatch`, {
      method: "POST",
      body: JSON.stringify({ project_path: projectPath, agent }),
    }),

  redispatch: (id: string, projectPath: string, agent?: string): Promise<{ session_name: string }> =>
    apiClient<{ session_name: string }>(`${BASE}/cards/${id}/redispatch`, {
      method: "POST",
      body: JSON.stringify({ project_path: projectPath, agent }),
    }),

  redispatchAll: (projectPath: string): Promise<{ redispatched: number; results: { session_name: string }[] }> =>
    apiClient<{ redispatched: number; results: { session_name: string }[] }>(`${BASE}/redispatch-all`, {
      method: "POST",
      body: JSON.stringify({ project_path: projectPath }),
    }),

  dispatchAll: (projectPath: string): Promise<{ dispatched: number; results: { session_name: string }[] }> =>
    apiClient<{ dispatched: number; results: { session_name: string }[] }>(`${BASE}/dispatch-all`, {
      method: "POST",
      body: JSON.stringify({ project_path: projectPath }),
    }),

  move: (id: string, column: string): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards/${id}/move`, {
      method: "POST",
      body: JSON.stringify({ column }),
    }),

  reorder: (projectKey: string, column: string, orderedIds: string[]): Promise<{ items: Card[] }> =>
    apiClient<{ items: Card[] }>(`${BASE}/cards/reorder`, {
      method: "POST",
      body: JSON.stringify({ project_key: projectKey, column, ordered_ids: orderedIds }),
    }),

  claim: (id: string, claimedBy: string): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards/${id}/claim`, {
      method: "POST",
      body: JSON.stringify({ claimed_by: claimedBy }),
    }),

  release: (id: string): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards/${id}/release`, {
      method: "POST",
      body: JSON.stringify({}),
    }),

  comment: (id: string, text: string): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards/${id}/comment`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  // Flag doubt on a Done card: posts a `**Review requested:**` comment on the
  // original card and creates a new analysis card wired back to it via
  // `metadata.reviewed_card_id`. Returns that newly created review card. 409 if
  // the target card isn't in Done.
  requestReview: (id: string, note: string): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards/${id}/request-review`, {
      method: "POST",
      body: JSON.stringify({ note }),
    }),

  // Weerleg & heropen: post a `**Revisit:**` comment on a Done card and move
  // the *same* card back to Backlog so the dispatcher re-picks it. Returns
  // the reopened card (which now lives in Backlog). 409 when the card is in
  // flight (Doing/Impediment). Distinct from requestReview: a review spawns
  // a sibling analysis card, a reopen moves the existing card back into the
  // dispatch queue.
  reopen: (id: string, note: string): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards/${id}/reopen`, {
      method: "POST",
      body: JSON.stringify({ note }),
    }),

  attach: (id: string, kind: string, ref: string): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards/${id}/deliverables`, {
      method: "POST",
      body: JSON.stringify({ kind, ref }),
    }),

  projectKey: (projectPath: string): Promise<{ project_key: string }> =>
    apiClient<{ project_key: string }>(
      `${BASE}/project-key?project_path=${encodeURIComponent(projectPath)}`
    ),

  mcpStatus: (projectPath: string): Promise<{ enabled: boolean }> =>
    apiClient<{ enabled: boolean }>(
      `${BASE}/mcp-status?project_path=${encodeURIComponent(projectPath)}`
    ),

  mcpHealth: (): Promise<McpHealth> => apiClient<McpHealth>(`${BASE}/mcp-health`),

  dispatchPause: (): Promise<DispatchPauseStatus> =>
    apiClient<DispatchPauseStatus>(`${BASE}/dispatch-pause`),

  clearDispatchPause: (): Promise<{ cleared: boolean; was_paused: boolean }> =>
    apiClient<{ cleared: boolean; was_paused: boolean }>(`${BASE}/dispatch-pause`, {
      method: "DELETE",
    }),

  enable: (projectPath: string, slug?: string): Promise<{ project_key: string }> =>
    apiClient<{ project_key: string }>(`${BASE}/enable`, {
      method: "POST",
      body: JSON.stringify({ project_path: projectPath, slug }),
    }),

  disable: (projectPath: string): Promise<{ enabled: boolean }> =>
    apiClient<{ enabled: boolean }>(`${BASE}/disable`, {
      method: "POST",
      body: JSON.stringify({ project_path: projectPath }),
    }),

  getShipMode: (projectKey: string): Promise<{ mode: string }> =>
    apiClient<{ mode: string }>(
      `${BASE}/shipmode?project_key=${encodeURIComponent(projectKey)}`
    ),

  setShipMode: (projectKey: string, mode: string): Promise<{ mode: string }> =>
    apiClient<{ mode: string }>(`${BASE}/shipmode`, {
      method: "POST",
      body: JSON.stringify({ project_key: projectKey, mode }),
    }),

  getSkipPermissions: (projectKey: string): Promise<{ enabled: boolean }> =>
    apiClient<{ enabled: boolean }>(
      `${BASE}/skip-permissions?project_key=${encodeURIComponent(projectKey)}`
    ),

  setSkipPermissions: (projectKey: string, enabled: boolean): Promise<{ enabled: boolean }> =>
    apiClient<{ enabled: boolean }>(`${BASE}/skip-permissions`, {
      method: "POST",
      body: JSON.stringify({ project_key: projectKey, enabled }),
    }),

  clearColumn: (projectKey: string, column: string): Promise<{ cleared: number }> =>
    apiClient<{ cleared: number }>(`${BASE}/clear-column`, {
      method: "POST",
      body: JSON.stringify({ project_key: projectKey, column }),
    }),

  getAutodispatch: (projectKey: string): Promise<{ enabled: boolean }> =>
    apiClient<{ enabled: boolean }>(
      `${BASE}/autodispatch?project_key=${encodeURIComponent(projectKey)}`
    ),

  setAutodispatch: (projectKey: string, enabled: boolean): Promise<{ enabled: boolean }> =>
    apiClient<{ enabled: boolean }>(`${BASE}/autodispatch`, {
      method: "POST",
      body: JSON.stringify({ project_key: projectKey, enabled }),
    }),

  getDefaultTransport: (projectKey: string): Promise<{ transport: string }> =>
    apiClient<{ transport: string }>(
      `${BASE}/transport?project_key=${encodeURIComponent(projectKey)}`
    ),

  setDefaultTransport: (projectKey: string, transport: string): Promise<{ transport: string }> =>
    apiClient<{ transport: string }>(`${BASE}/transport`, {
      method: "POST",
      body: JSON.stringify({ project_key: projectKey, transport }),
    }),

  /** Active-subscription-override (fase 0 / quick win). `override` is
   * `{provider: string, model?: string|null}` when pinning, or `null` to
   * clear. Mirrors how the column/card-default precedence falls through
   * when no pin is set (see backend kanban dispatch). */
  getActiveSubscriptionOverride: (
    projectKey: string,
  ): Promise<{
    project_key: string;
    override: { provider: string; model: string | null } | null;
  }> =>
    apiClient<{
      project_key: string;
      override: { provider: string; model: string | null } | null;
    }>(
      `${BASE}/subscription-override?project_key=${encodeURIComponent(projectKey)}`
    ),

  setActiveSubscriptionOverride: (
    projectKey: string,
    override: { provider: string; model?: string | null } | null,
  ): Promise<{
    project_key: string;
    override: { provider: string; model?: string | null } | null;
  }> =>
    apiClient<{
      project_key: string;
      override: { provider: string; model?: string | null } | null;
    }>(`${BASE}/subscription-override`, {
      method: "POST",
      body: JSON.stringify({ project_key: projectKey, override }),
    }),

  listGates: (cardId: string): Promise<Gate[]> =>
    apiClient<Gate[]>(`${BASE}/cards/${cardId}/gates`),

  answerGate: (gateId: string, answer: string): Promise<Gate> =>
    apiClient<Gate>(`${BASE}/gates/${gateId}/answer`, {
      method: "POST",
      body: JSON.stringify({ answer }),
    }),

  // Resolve an Impediment card without supplying an answer: the backend picks
  // up any structured `report_impediment(options=[...])` gate answer via
  // service.latest_gate_answer and forwards it alongside the question to the
  // new agent's prompt. (When a free-text answer is supplied via the upstream
  // `ResolveImpedimentControl` textarea, the `answer?: string` overload is
  // used.)
  resolveImpediment: (id: string, projectPath: string, answer?: string): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards/${id}/resolve-impediment`, {
      method: "POST",
      body: JSON.stringify({ project_path: projectPath, answer }),
    }),

  listWorkTypeMappings: (
    projectKey: string
  ): Promise<{ project_key: string; mappings: Record<WorkType, string> }> =>
    apiClient<{ project_key: string; mappings: Record<WorkType, string> }>(
      `${BASE}/work-type-mappings?project_key=${encodeURIComponent(projectKey)}`
    ),

  bulkPutWorkTypeMappings: (
    projectKey: string,
    mappings: { work_type: WorkType; persona: string }[]
  ): Promise<WorkTypeMapping[]> =>
    apiClient<WorkTypeMapping[]>(`${BASE}/work-type-mappings/bulk`, {
      method: "POST",
      body: JSON.stringify({ project_key: projectKey, mappings }),
    }),

  deleteWorkTypeMapping: (projectKey: string, workType: WorkType): Promise<void> =>
    apiClient<void>(
      `${BASE}/work-type-mappings/${workType}?project_key=${encodeURIComponent(projectKey)}`,
      { method: "DELETE" }
    ),

  getModelOptions: (): Promise<{ provider: string; options: string[] }> =>
    apiClient<{ provider: string; options: string[] }>(`${BASE}/model-options`),

  refreshModelOptions: (): Promise<{ provider: string; options: string[] }> =>
    apiClient<{ provider: string; options: string[] }>(`${BASE}/model-options/refresh`, {
      method: "POST",
    }),

  updatePlanAttachment: (
    cardId: string,
    planMarkdown: string,
  ): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards/${cardId}/plan-attachment`, {
      method: "PATCH",
      body: JSON.stringify({ plan_markdown: planMarkdown }),
    }),

  addPlanAttachment: (
    cardId: string,
    planMarkdown: string,
    childCardIds: string[],
    dependsOnGraph: Record<string, string[]> = {},
  ): Promise<
    | { parent_card_id: string; plan_deliverable_id: string; child_card_ids: string[] }
    | { error: string; max?: number; cycle?: string[]; card_id?: string }
  > =>
    apiClient<
      | { parent_card_id: string; plan_deliverable_id: string; child_card_ids: string[] }
      | { error: string; max?: number; cycle?: string[]; card_id?: string }
    >(`${BASE}/cards/${cardId}/plan-attachment`, {
      method: "POST",
      body: JSON.stringify({
        plan_markdown: planMarkdown,
        child_card_ids: childCardIds,
        depends_on_graph: dependsOnGraph,
      }),
    }),
};

// Backwards-compatible re-export for callers that imported the free function
// before it moved onto `kanbanApi`. Mirrors the new shape exactly.
export const addPlanAttachment = kanbanApi.addPlanAttachment;
