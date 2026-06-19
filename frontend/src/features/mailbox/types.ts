export const MESSAGE_KINDS = [
  "context_request",
  "context_response",
  "handoff",
  "note",
] as const;
export type MessageKind = (typeof MESSAGE_KINDS)[number];

export const MESSAGE_STATUSES = ["unread", "read", "answered"] as const;
export type MessageStatus = (typeof MESSAGE_STATUSES)[number];

export interface Identity {
  id: string;
  project_key: string;
  handle: string;
  display_name: string | null;
  last_session: string | null;
  created_at: string;
  last_seen_at: string | null;
}

export interface Message {
  id: string;
  project_key: string;
  from_handle: string;
  to_handle: string | null;
  kind: MessageKind;
  subject: string;
  body: string;
  card_id: string | null;
  in_reply_to: string | null;
  status: MessageStatus;
  created_at: string;
  read_at: string | null;
}
