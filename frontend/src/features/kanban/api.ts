import { apiAssetUrl, apiClient, apiUpload } from "@/lib/api";
import { spawnSession } from "@/features/cc-bridge/api";
import type { SpawnSessionResponse } from "@/features/cc-bridge/types";
import type {
  Card,
  ActivityEntry,
  ColumnOverride,
  KanbanColumn,
  AgentStatsResponse,
  McpHealth,
  DispatchPauseStatus,
  Gate,
  PoolEntry,
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
    body: {
      name?: string;
      rank?: string;
      default_agent?: string | null;
      default_provider?: string | null;
      default_model?: string | null;
      max_sessions?: number | null;
      // Per-lane RTK (token-saver) opt-in (kaart c31333bf…). Surfaced as
      // a bool on the API; the SQLite column stores 0/1.
      token_saver_enabled?: boolean;
      // Per-lane prompt-injector opt-in (kaart d0446fd8…). Independent
      // switches — toggling one does not move the other. Board-wide
      // kill-switch (/api/v1/kanban/prompt-injector) overrides both.
      caveman_enabled?: boolean;
      ponytail_enabled?: boolean;
    }
  ): Promise<KanbanColumn> =>
    apiClient<KanbanColumn>(`${BASE}/columns/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  deleteColumn: (id: string): Promise<void> =>
    apiClient<void>(`${BASE}/columns/${id}`, { method: "DELETE" }),

  listCards: (projectKey: string, column?: string): Promise<{ items: Card[] }> => {
    const params = new URLSearchParams({ project_key: projectKey });
    if (column) params.append("column", column);
    return apiClient<{ items: Card[] }>(`${BASE}/cards?${params.toString()}`);
  },

  getCard: (id: string): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards/${id}`),

  // Per-dispatch token telemetry (kanban card 8a2ad986). Returns `{usage: null}`
  // for cards without dispatch breadcrumbs (legacy cards, or cards that
  // haven't been dispatched yet) — distinct from "card not found" which the
  // existing `getCard` resolves as 404.
  getCardUsage: (id: string): Promise<import("./cardUsage").CardUsageResponse> =>
    apiClient<import("./cardUsage").CardUsageResponse>(`${BASE}/cards/${id}/usage`),

  // Per-card run ledger (docs/cockpit/run-ledger-decision.md). Stitches the
  // task → context → files → tests → outcome+model spine from existing
  // durable sources. Every step is best-effort (`available: false` + a note
  // for a missing source); 404 only when the card itself doesn't exist.
  getRunLedger: (id: string): Promise<import("./runLedger").RunLedger> =>
    apiClient<import("./runLedger").RunLedger>(`${BASE}/cards/${id}/run-ledger`),

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

  // Promote a headless-dispatched card's session to an attachable tmux pane
  // (docs/cockpit/human-takeover-headless-decision.md §7). The claim, branch
  // and worktree stay the same — only the transport promotes.
  takeOver: (id: string, projectPath: string): Promise<{ session_name: string; tmux_target: string }> =>
    apiClient<{ session_name: string; tmux_target: string }>(`${BASE}/cards/${id}/take-over`, {
      method: "POST",
      body: JSON.stringify({ project_path: projectPath }),
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

  uploadAttachment: (id: string, file: File): Promise<Card> => {
    const form = new FormData();
    form.append("file", file);
    return apiUpload<Card>(`${BASE}/cards/${id}/attachments`, form);
  },

  deleteAttachment: (id: string, attachmentId: string): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards/${id}/attachments/${attachmentId}`, {
      method: "DELETE",
    }),

  attachmentUrl: (id: string, attachmentId: string): string =>
    apiAssetUrl(`${BASE}/cards/${id}/attachments/${attachmentId}`),

  projectKey: (projectPath: string): Promise<{ project_key: string }> =>
    apiClient<{ project_key: string }>(
      `${BASE}/project-key?project_path=${encodeURIComponent(projectPath)}`
    ),

  /**
   * "Wacht op jou" — PO-facing aggregation of every human-blocked item in
   * the project (kanban card `c7ea21b0…`). Returns a flat, oldest-first
   * sorted list across impediment_needs_answer / gate_open /
   * review_requested / awaiting_plan_ref. Empty when nothing is waiting
   * (no 404 for an unknown project — a wachtrij is a *view*, not a write).
   */
  wachtrij: (projectKey: string): Promise<import("./types").WachtrijResponse> =>
    apiClient<import("./types").WachtrijResponse>(
      `${BASE}/wachtrij?project_key=${encodeURIComponent(projectKey)}`
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

  // Kaart f056b2888a…: toggle the operator manual pause for one subscription.
  // ``paused=true`` writes the slot; ``paused=false`` clears it. Returns the
  // updated ``manually_paused_providers`` list so the dialog can refresh
  // without a follow-up GET round-trip.
  setSubscriptionPause: (
    provider: string,
    paused: boolean,
  ): Promise<{
    provider: string;
    paused: boolean;
    manually_paused_providers: string[];
  }> =>
    apiClient<{
      provider: string;
      paused: boolean;
      manually_paused_providers: string[];
    }>(`${BASE}/dispatch-pause/subscription/${encodeURIComponent(provider)}`, {
      method: "PUT",
      body: JSON.stringify({ paused }),
    }),

  enable: (projectPath: string, slug?: string): Promise<{ project_key: string }> =>
    apiClient<{ project_key: string }>(`${BASE}/enable`, {
      method: "POST",
      body: JSON.stringify({ project_path: projectPath, slug }),
    }),

  // Start the spec-driven /new-app interview for a fresh project. The
  // `directory` is the cockpit repo (where `.claude/skills/new-app` lives);
  // the new repo doesn't exist yet, so the interview runs in the cockpit
  // checkout and creates the new repo at the end. See
  // docs/cockpit/kaartloze-app-inceptie-decision.md §4.
  startNewApp: (directory: string): Promise<SpawnSessionResponse> =>
    spawnSession({
      cli: "claude-code",
      directory,
      mode: "plain",
      prompt: "/new-app",
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

  // Per-lane RTK (token-saver) opt-in (kaart c31333bf…).
  // Board kill-switch: read on every dispatch tick so toggling off
  // via this endpoint takes effect on the next spawn without a
  // backend restart. The per-lane column flag (Column.token_saver_enabled)
  // is orthogonal and is sent via the column update endpoint.
  getTokenSaver: (projectKey: string): Promise<{ enabled: boolean }> =>
    apiClient<{ enabled: boolean }>(
      `${BASE}/token-saver?project_key=${encodeURIComponent(projectKey)}`
    ),

  setTokenSaver: (projectKey: string, enabled: boolean): Promise<{ enabled: boolean }> =>
    apiClient<{ enabled: boolean }>(`${BASE}/token-saver`, {
      method: "POST",
      body: JSON.stringify({ project_key: projectKey, enabled }),
    }),

  // Per-lane prompt-injector opt-in (kaart d0446fd8…,
  // Caveman + Ponytail). Board kill-switch: read on every dispatch
  // tick so toggling off takes effect on the next spawn without a
  // backend restart. The per-lane column flags are orthogonal and
  // are sent via the column update endpoint.
  getPromptInjector: (projectKey: string): Promise<{ enabled: boolean }> =>
    apiClient<{ enabled: boolean }>(
      `${BASE}/prompt-injector?project_key=${encodeURIComponent(projectKey)}`
    ),

  setPromptInjector: (projectKey: string, enabled: boolean): Promise<{ enabled: boolean }> =>
    apiClient<{ enabled: boolean }>(`${BASE}/prompt-injector`, {
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

  /** Subscription pool (fase 1b). Ordered list of pool entries;
   * `null` means the pool is unset and dispatch falls back to today's
   * per-column defaults. Mirrors backend `KanbanMeta` storage; the
   * kanban dispatcher picks the first under-drempel / non-paused entry
   * and routes onto it.
   *
   * Kaart b36ca702…: the optional `column` parameter selects the
   * per-column tail (`subscription_pool:<project_key>:<column>`).
   * Without it, the read is board-wide and returns the same shape as
   * before. With it, the response's `pool` field is the per-column
   * tail if one is configured (including the explicit-empty `[]`
   * value), or the board-wide pool when the column has no per-column
   * row. The selected `column` is echoed back so a UI that re-saves
   * keeps the round-trip consistent. */
  getSubscriptionPool: (
    projectKey: string,
    column?: string | null,
  ): Promise<{
    project_key: string;
    column: string | null;
    pool: PoolEntry[] | null;
  }> => {
    const params = new URLSearchParams({ project_key: projectKey });
    if (column) params.set("column", column);
    return apiClient<{
      project_key: string;
      column: string | null;
      pool: PoolEntry[] | null;
    }>(`${BASE}/subscription-pool?${params.toString()}`);
  },

  setSubscriptionPool: (
    projectKey: string,
    pool: PoolEntry[] | null,
    column?: string | null,
  ): Promise<{
    project_key: string;
    column: string | null;
    pool: PoolEntry[] | null;
  }> =>
    apiClient<{
      project_key: string;
      column: string | null;
      pool: PoolEntry[] | null;
    }>(`${BASE}/subscription-pool`, {
      method: "POST",
      body: JSON.stringify({
        project_key: projectKey,
        pool,
        column: column ?? null,
      }),
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

  getMinimaxModelOptions: (): Promise<{ provider: string; options: string[] }> =>
    apiClient<{ provider: string; options: string[] }>(`${BASE}/model-options/minimax`),

  refreshMinimaxModelOptions: (): Promise<{ provider: string; options: string[] }> =>
    apiClient<{ provider: string; options: string[] }>(`${BASE}/model-options/minimax/refresh`, {
      method: "POST",
    }),

  getColumnEffectiveModel: (
    columnId: string,
  ): Promise<{
    provider: string;
    model: string | null;
    provider_source: string;
    model_source: string;
    global_override: { provider: string; model: string | null } | null;
    pool_choice: { provider: string; model: string | null } | null;
    column_default_provider: string | null;
    column_default_model: string | null;
    persona_model: string | null;
  }> =>
    apiClient<{
      provider: string;
      model: string | null;
      provider_source: string;
      model_source: string;
      global_override: { provider: string; model: string | null } | null;
      pool_choice: { provider: string; model: string | null } | null;
      column_default_provider: string | null;
      column_default_model: string | null;
      persona_model: string | null;
    }>(`${BASE}/columns/${columnId}/effective-model`),

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
