import { useState, useEffect, useCallback } from 'react'
import { CalendarClock, Plus, Trash2, ToggleLeft, ToggleRight, ChevronDown, ChevronRight, RotateCcw } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { RefreshButton } from '@/components/shared/RefreshButton'
import { CLICKABLE_CARD, MODAL_SIZES } from '@/lib/constants'
import { listScheduledMessages, deleteScheduledMessage, updateScheduledMessage } from './api'
import { ScheduledMessageForm } from './components/ScheduledMessageForm'
import { DeliveryLog } from './components/DeliveryLog'
import type { ScheduledMessage, MessageStatus } from './types'

function StatusBadge({ status }: { status: MessageStatus }) {
  const variants: Record<MessageStatus, string> = {
    scheduled: 'bg-blue-500 text-white',
    pending_delivery: 'bg-yellow-500 text-white',
    delivered: 'bg-green-500 text-white',
    failed: 'bg-red-500 text-white',
    cancelled: 'bg-muted text-muted-foreground',
  }
  return <Badge className={variants[status]}>{status}</Badge>
}

function fmtTrigger(msg: ScheduledMessage): string {
  if (msg.trigger_type === 'once' && msg.fire_at) {
    return `Once — ${new Date(msg.fire_at).toLocaleString()}`
  }
  if (msg.trigger_type === 'cron' && msg.cron_expr) {
    return `Cron: ${msg.cron_expr} (${msg.timezone})`
  }
  return msg.trigger_type
}

function shortPath(p: string): string {
  const parts = p.split('/')
  return parts.slice(-2).join('/')
}

interface RowProps {
  msg: ScheduledMessage
  onToggle: (msg: ScheduledMessage) => void
  onDelete: (id: number) => void
}

function MessageRow({ msg, onToggle, onDelete }: RowProps) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        className={`${CLICKABLE_CARD} rounded-lg p-4 flex items-start gap-3`}
        onClick={() => setExpanded((e) => !e)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setExpanded((v) => !v) }}
      >
        <span className="mt-1 text-muted-foreground">
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-1">
            <StatusBadge status={msg.status} />
            {msg.target_kind === 'session' && (
              <Badge
                variant="outline"
                className="gap-1"
                title={msg.session_preview ?? msg.target_session_id ?? undefined}
              >
                <RotateCcw className="h-3 w-3" />resume
              </Badge>
            )}
            <span className="text-xs text-muted-foreground font-mono">{shortPath(msg.target_project)}</span>
            {!msg.enabled && <Badge variant="outline">disabled</Badge>}
          </div>
          <p className="text-sm truncate">{msg.message}</p>
          <p className="text-xs text-muted-foreground mt-0.5">{fmtTrigger(msg)}</p>
        </div>
        <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
          <Button
            variant="ghost"
            size="icon"
            title={msg.enabled ? 'Disable' : 'Enable'}
            onClick={() => onToggle(msg)}
          >
            {msg.enabled
              ? <ToggleRight className="h-4 w-4 text-primary" />
              : <ToggleLeft className="h-4 w-4 text-muted-foreground" />}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            title="Delete"
            onClick={() => onDelete(msg.id)}
          >
            <Trash2 className="h-4 w-4 text-destructive" />
          </Button>
        </div>
      </div>
      {expanded && (
        <div className="border-l-2 border-border ml-4 pl-4 mt-1 mb-2">
          <p className="text-xs font-semibold text-muted-foreground mb-2 uppercase tracking-wide">Delivery log</p>
          <DeliveryLog messageId={msg.id} />
        </div>
      )}
    </div>
  )
}

export function ScheduledMessagesPage() {
  const [messages, setMessages] = useState<ScheduledMessage[]>([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await listScheduledMessages()
      setMessages(res.items)
    } catch {
      toast.error('Failed to load scheduled messages')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleToggle = async (msg: ScheduledMessage) => {
    try {
      await updateScheduledMessage(msg.id, { enabled: !msg.enabled })
      toast.success(msg.enabled ? 'Disabled' : 'Enabled')
      await load()
    } catch {
      toast.error('Failed to update')
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await deleteScheduledMessage(id)
      toast.success('Deleted')
      await load()
    } catch {
      toast.error('Failed to delete')
    }
  }

  const scheduled = messages.filter((m) => m.status === 'scheduled' || m.status === 'pending_delivery')
  const done = messages.filter((m) => !['scheduled', 'pending_delivery'].includes(m.status))

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <CalendarClock className="h-8 w-8" />
            Scheduled Messages
          </h1>
          <p className="text-muted-foreground">
            Schedule messages to be injected into Claude Code sessions
          </p>
        </div>
        <div className="flex items-center gap-2">
          <RefreshButton onClick={load} loading={loading} />
          <Button onClick={() => setShowForm(true)}>
            <Plus className="h-4 w-4 mr-1" />
            New
          </Button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Scheduled</CardDescription>
            <CardTitle className="text-3xl text-blue-500">{scheduled.length}</CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Delivered</CardDescription>
            <CardTitle className="text-3xl text-green-500">
              {messages.filter((m) => m.status === 'delivered').length}
            </CardTitle>
          </CardHeader>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardDescription>Failed</CardDescription>
            <CardTitle className="text-3xl text-red-500">
              {messages.filter((m) => m.status === 'failed').length}
            </CardTitle>
          </CardHeader>
        </Card>
      </div>

      {/* Pending */}
      {scheduled.length > 0 && (
        <div className="space-y-2">
          <h2 className="text-lg font-semibold">Pending</h2>
          {scheduled.map((m) => (
            <MessageRow key={m.id} msg={m} onToggle={handleToggle} onDelete={handleDelete} />
          ))}
        </div>
      )}

      {/* History */}
      {done.length > 0 && (
        <div className="space-y-2">
          <h2 className="text-lg font-semibold text-muted-foreground">History</h2>
          {done.map((m) => (
            <MessageRow key={m.id} msg={m} onToggle={handleToggle} onDelete={handleDelete} />
          ))}
        </div>
      )}

      {/* Empty state */}
      {!loading && messages.length === 0 && (
        <Card className="border-dashed">
          <CardContent className="flex flex-col items-center justify-center py-12 text-center">
            <CalendarClock className="h-12 w-12 text-muted-foreground mb-4" />
            <h3 className="text-lg font-semibold mb-2">No scheduled messages</h3>
            <p className="text-sm text-muted-foreground max-w-md mb-4">
              Create a one-time timer or a recurring cron job to inject messages into Claude Code sessions.
            </p>
            <Button onClick={() => setShowForm(true)}>
              <Plus className="h-4 w-4 mr-2" />
              Create first message
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Create dialog */}
      <Dialog open={showForm} onOpenChange={setShowForm}>
        <DialogContent className={MODAL_SIZES.MD}>
          <DialogHeader>
            <DialogTitle>New scheduled message</DialogTitle>
          </DialogHeader>
          <ScheduledMessageForm
            onCreated={() => { setShowForm(false); load() }}
            onCancel={() => setShowForm(false)}
          />
        </DialogContent>
      </Dialog>
    </div>
  )
}
