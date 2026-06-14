import { apiClient } from "@/lib/api";
import type { Card, ActivityEntry } from "./types";

const BASE = "kanban";

export const kanbanApi = {
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
  }): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateCard: (id: string, body: { title?: string; description?: string }): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  move: (id: string, column: string): Promise<Card> =>
    apiClient<Card>(`${BASE}/cards/${id}/move`, {
      method: "POST",
      body: JSON.stringify({ column }),
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
};
