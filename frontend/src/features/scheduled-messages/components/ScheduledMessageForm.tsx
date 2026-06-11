import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useProjectContext } from '@/contexts/ProjectContext'
import { createScheduledMessage } from '../api'
import type { ScheduledMessageCreate, TriggerType, PermissionMode } from '../types'

interface Props {
  onCreated: () => void
  onCancel: () => void
}

export function ScheduledMessageForm({ onCreated, onCancel }: Props) {
  const { projects } = useProjectContext()

  const [targetProject, setTargetProject] = useState('')
  const [message, setMessage] = useState('')
  const [triggerType, setTriggerType] = useState<TriggerType>('once')
  const [fireAt, setFireAt] = useState('')
  const [cronExpr, setCronExpr] = useState('0 9 * * 1-5')
  const [timezone, setTimezone] = useState('Europe/Brussels')
  const [permissionMode, setPermissionMode] = useState<PermissionMode>('acceptEdits')
  const [onMissing, setOnMissing] = useState<'spawn' | 'skip'>('spawn')
  const [whenBusy, setWhenBusy] = useState<'wait_until_idle' | 'send_now'>('wait_until_idle')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    if (!targetProject) { setError('Select a project'); return }
    if (!message.trim()) { setError('Message is required'); return }
    if (triggerType === 'once' && !fireAt) { setError('Set a fire date/time'); return }
    if (triggerType === 'cron' && !cronExpr.trim()) { setError('Cron expression is required'); return }

    const payload: ScheduledMessageCreate = {
      target_project: targetProject,
      message: message.trim(),
      trigger_type: triggerType,
      timezone,
      permission_mode: permissionMode,
      on_missing_session: onMissing,
      when_busy: whenBusy,
    }
    if (triggerType === 'once') payload.fire_at = new Date(fireAt).toISOString()
    if (triggerType === 'cron') payload.cron_expr = cronExpr.trim()

    setSaving(true)
    try {
      await createScheduledMessage(payload)
      onCreated()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create')
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="space-y-1.5">
        <Label>Project</Label>
        {projects.length > 0 ? (
          <Select value={targetProject} onValueChange={setTargetProject}>
            <SelectTrigger>
              <SelectValue placeholder="Select a project…" />
            </SelectTrigger>
            <SelectContent>
              {projects.map((p) => (
                <SelectItem key={p.id} value={p.path}>{p.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <Input
            value={targetProject}
            onChange={(e) => setTargetProject(e.target.value)}
            placeholder="/home/user/dev/my-project"
          />
        )}
      </div>

      <div className="space-y-1.5">
        <Label>Message</Label>
        <Textarea
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder="run tests and fix any failures"
          rows={3}
        />
      </div>

      <div className="space-y-1.5">
        <Label>Trigger type</Label>
        <div className="flex gap-4">
          {(['once', 'cron'] as const).map((t) => (
            <label key={t} className="flex items-center gap-2 cursor-pointer text-sm">
              <input
                type="radio"
                name="trigger"
                value={t}
                checked={triggerType === t}
                onChange={() => setTriggerType(t)}
              />
              {t === 'once' ? 'One-time timer' : 'Recurring cron'}
            </label>
          ))}
        </div>
      </div>

      {triggerType === 'once' && (
        <div className="space-y-1.5">
          <Label>Fire at</Label>
          <Input
            type="datetime-local"
            value={fireAt}
            onChange={(e) => setFireAt(e.target.value)}
          />
        </div>
      )}

      {triggerType === 'cron' && (
        <div className="space-y-1.5">
          <Label>Cron expression</Label>
          <Input
            value={cronExpr}
            onChange={(e) => setCronExpr(e.target.value)}
            placeholder="0 9 * * 1-5"
            className="font-mono"
          />
          <p className="text-xs text-muted-foreground">Standard 5-field cron (minute hour dom month dow)</p>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label>Timezone</Label>
          <Input value={timezone} onChange={(e) => setTimezone(e.target.value)} placeholder="Europe/Brussels" />
        </div>
        <div className="space-y-1.5">
          <Label>Permission mode</Label>
          <Select value={permissionMode} onValueChange={(v) => setPermissionMode(v as PermissionMode)}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="default">default (prompt)</SelectItem>
              <SelectItem value="acceptEdits">acceptEdits (safe)</SelectItem>
              <SelectItem value="bypass">bypass (autonomous)</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label>No session</Label>
          <Select value={onMissing} onValueChange={(v) => setOnMissing(v as 'spawn' | 'skip')}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="spawn">Spawn one</SelectItem>
              <SelectItem value="skip">Skip</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <Label>Busy session</Label>
          <Select value={whenBusy} onValueChange={(v) => setWhenBusy(v as 'wait_until_idle' | 'send_now')}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="wait_until_idle">Wait until idle</SelectItem>
              <SelectItem value="send_now">Send immediately</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="outline" onClick={onCancel}>Cancel</Button>
        <Button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Create'}</Button>
      </div>
    </form>
  )
}
