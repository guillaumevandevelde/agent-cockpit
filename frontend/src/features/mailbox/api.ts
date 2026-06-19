import { apiClient } from "@/lib/api";
import type { Identity, Message } from "./types";

const BASE = "kanban/mail";

export const mailApi = {
  listIdentities: (projectKey: string): Promise<{ identities: Identity[] }> =>
    apiClient<{ identities: Identity[] }>(
      `${BASE}/identities?project_key=${encodeURIComponent(projectKey)}`
    ),

  inbox: (
    projectKey: string,
    handle: string,
    unreadOnly = false
  ): Promise<{ messages: Message[] }> => {
    const params = new URLSearchParams({ project_key: projectKey, handle });
    if (unreadOnly) params.append("unread_only", "true");
    return apiClient<{ messages: Message[] }>(`${BASE}/inbox?${params.toString()}`);
  },

  forCard: (projectKey: string, cardId: string): Promise<{ messages: Message[] }> => {
    const params = new URLSearchParams({ project_key: projectKey, card_id: cardId });
    return apiClient<{ messages: Message[] }>(`${BASE}/messages?${params.toString()}`);
  },

  send: (body: {
    project_key: string;
    from_handle: string;
    to_handle?: string | null;
    kind?: string;
    subject?: string;
    body?: string;
    card_id?: string | null;
    in_reply_to?: string | null;
  }): Promise<Message> =>
    apiClient<Message>(`${BASE}/messages`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  thread: (messageId: string): Promise<{ messages: Message[] }> =>
    apiClient<{ messages: Message[] }>(`${BASE}/messages/${messageId}/thread`),

  markRead: (messageId: string, readerHandle: string): Promise<Message> =>
    apiClient<Message>(`${BASE}/messages/${messageId}/read`, {
      method: "POST",
      body: JSON.stringify({ reader_handle: readerHandle }),
    }),
};
