import type { MailMemberStatus } from '@/types/agentMail'

export function statusBadgeClass(status: MailMemberStatus | string): string {
  if (status === 'connected') return 'border-emerald-300 bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300'
  if (status === 'observed') return 'border-blue-300 bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300'
  return 'border-muted-foreground/30 bg-muted text-muted-foreground'
}

export function statusLabel(status: MailMemberStatus | string): string {
  if (status === 'connected') return 'Connected'
  if (status === 'observed') return 'Observed only'
  if (status === 'offline') return 'Offline'
  return status
}

export function statusTitle(status: MailMemberStatus | string): string {
  if (status === 'connected') return 'This repo has checked in through Agent Mail.'
  if (status === 'observed') return 'Agent Bridge sees a tmux session, but Agent Mail has not received an MCP or hook check-in.'
  if (status === 'offline') return 'No recent Agent Mail check-in or live Agent Bridge observation.'
  return status
}

export function sessionSourceLabel(source: string): string {
  if (source === 'observed') return 'Bridge'
  if (source === 'mcp') return 'MCP'
  if (source === 'hook') return 'Hooks'
  return source
}

export function sessionSourceTitle(source: string): string {
  if (source === 'observed') return 'Discovered by Agent Bridge from a tmux pane.'
  if (source === 'mcp') return 'Registered by an Agent Mail MCP tool call.'
  if (source === 'hook') return 'Registered by Agent Mail lifecycle hooks.'
  return source
}

export function sessionStatusLabel(source: string, status: MailMemberStatus | string): string {
  if (source === 'hook' && status === 'connected') return 'Recent event'
  if (source === 'hook' && status === 'offline') return 'No recent event'
  return statusLabel(status)
}

export function sessionStatusTitle(source: string, status: MailMemberStatus | string): string {
  if (source === 'hook' && status === 'connected') return 'A lifecycle hook checked in recently.'
  if (source === 'hook' && status === 'offline') return 'No recent lifecycle hook event has checked in. This does not mean the run is disconnected.'
  return statusTitle(status)
}

export function formatDateTime(value?: string | null): string {
  if (!value) return 'Never'
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}
