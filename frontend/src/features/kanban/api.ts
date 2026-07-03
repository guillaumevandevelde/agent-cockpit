import { apiClient } from "@/lib/api";
import type { Card, ActivityEntry, KanbanColumn, AgentStatsResponse, McpHealth, DispatchPauseStatus, Gate } from "./types";

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
  }): Promise<KanbanColumn> =>
    apiClient<KanbanColumn>(`${BASE}/columns`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateColumn: (
    id: string,
    body: { name?: string; rank?: string; default_agent?: string | null }
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

  activity: (id: string): Promise<ActivityEntry[]> =>
    apiClient<ActivityEntry[]>(`${BASE}/cards/${id}/activity`),

  createCard: (body: {
    project_key: string;
    title: string;
    description?: string;
    column?: string;
    priority?: string | null;
    labels?: string[] | null;
    agent?: string | null;
    transport?: string | null;
    resume_session_id?: string | null;
    resume_project_folder?: string | null;
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
      priority?: string | null;
      labels?: string[] | null;
      transport?: string | null;
      resume_session_id?: string | null;
      resume_project_folder?: string | null;
    }
  ): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  deleteCard: (id: string): Promise<void> =>
    apiClient<void>(`${BASE}/cards/${id}`, { method: "DELETE" }),

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

  getMaxSessions: (projectKey: string): Promise<{ max_sessions: number }> =>
    apiClient<{ max_sessions: number }>(
      `${BASE}/max-sessions?project_key=${encodeURIComponent(projectKey)}`
    ),

  setMaxSessions: (projectKey: string, n: number): Promise<{ max_sessions: number }> =>
    apiClient<{ max_sessions: number }>(`${BASE}/max-sessions`, {
      method: "POST",
      body: JSON.stringify({ project_key: projectKey, max_sessions: n }),
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

  listGates: (cardId: string): Promise<Gate[]> =>
    apiClient<Gate[]>(`${BASE}/cards/${cardId}/gates`),

  answerGate: (gateId: string, answer: string): Promise<Gate> =>
    apiClient<Gate>(`${BASE}/gates/${gateId}/answer`, {
      method: "POST",
      body: JSON.stringify({ answer }),
    }),
};
