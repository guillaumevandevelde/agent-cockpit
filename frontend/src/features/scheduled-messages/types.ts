export type TriggerType = 'once' | 'cron'
export type TargetKind = 'project' | 'session' | 'sandcastle'
export type PermissionMode = 'default' | 'acceptEdits' | 'bypass'
export type MessageStatus = 'scheduled' | 'pending_delivery' | 'delivered' | 'failed' | 'cancelled'
export type DeliveryOutcome = 'success' | 'failed' | 'timeout'

export interface ScheduledMessage {
  id: number
  target_project: string
  message: string
  trigger_type: TriggerType
  fire_at: string | null
  cron_expr: string | null
  timezone: string
  permission_mode: PermissionMode
  enabled: boolean
  status: MessageStatus
  on_missing_session: 'spawn' | 'skip'
  when_busy: 'wait_until_idle' | 'send_now'
  target_kind: TargetKind
  target_session_id: string | null
  project_folder: string | null
  session_preview: string | null
  sandcastle_config_id: number | null
  created_at: string
  updated_at: string
  last_fired_at: string | null
}

export interface DeliveryAttempt {
  id: number
  fired_at: string
  resolved_session: string | null
  action: string | null
  wait_duration_s: number | null
  delivered_at: string | null
  outcome: DeliveryOutcome | null
  error: string | null
}

export interface ScheduledMessageListResponse {
  items: ScheduledMessage[]
}

export interface ScheduledMessageCreate {
  target_project: string
  message: string
  trigger_type: TriggerType
  fire_at?: string
  cron_expr?: string
  timezone?: string
  permission_mode?: PermissionMode
  on_missing_session?: 'spawn' | 'skip'
  when_busy?: 'wait_until_idle' | 'send_now'
  target_kind?: TargetKind
  target_session_id?: string
  project_folder?: string
  session_preview?: string
  sandcastle_config_id?: number
}

export interface ResumableSession {
  id: string
  project_folder: string
  project_name: string
  summary: string
  modified_at: string
  worktree_label: string
}

export interface ScheduledMessageUpdate {
  message?: string
  fire_at?: string
  cron_expr?: string
  permission_mode?: PermissionMode
  enabled?: boolean
}

export interface AutoResumeStatus {
  cwd: string
  enabled: boolean
}
