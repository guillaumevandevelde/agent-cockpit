import { useState, useEffect, useCallback } from 'react'
import { CheckCircle2, XCircle, Clock } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { listDeliveryAttempts } from '../api'
import type { DeliveryAttempt } from '../types'

interface Props {
  messageId: number
}

function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return <Badge variant="outline">—</Badge>
  if (outcome === 'success') return <Badge className="bg-green-500 text-white gap-1"><CheckCircle2 className="h-3 w-3" />success</Badge>
  if (outcome === 'timeout') return <Badge variant="outline" className="gap-1"><Clock className="h-3 w-3" />timeout</Badge>
  return <Badge variant="destructive" className="gap-1"><XCircle className="h-3 w-3" />{outcome}</Badge>
}

function fmt(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString()
}

export function DeliveryLog({ messageId }: Props) {
  const [attempts, setAttempts] = useState<DeliveryAttempt[]>([])
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setAttempts(await listDeliveryAttempts(messageId))
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }, [messageId])

  useEffect(() => { load() }, [load])

  if (loading) return <p className="text-sm text-muted-foreground">Loading…</p>
  if (attempts.length === 0) return <p className="text-sm text-muted-foreground">No delivery attempts yet.</p>

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b text-muted-foreground text-left">
            <th className="pb-2 pr-4 font-medium">Fired at</th>
            <th className="pb-2 pr-4 font-medium">Action</th>
            <th className="pb-2 pr-4 font-medium">Wait (s)</th>
            <th className="pb-2 pr-4 font-medium">Session</th>
            <th className="pb-2 pr-4 font-medium">Outcome</th>
            <th className="pb-2 font-medium">Error</th>
          </tr>
        </thead>
        <tbody>
          {attempts.map((a) => (
            <tr key={a.id} className="border-b last:border-0">
              <td className="py-2 pr-4 whitespace-nowrap">{fmt(a.fired_at)}</td>
              <td className="py-2 pr-4">{a.action ?? '—'}</td>
              <td className="py-2 pr-4">{a.wait_duration_s ?? '—'}</td>
              <td className="py-2 pr-4 font-mono text-xs">{a.resolved_session ?? '—'}</td>
              <td className="py-2 pr-4"><OutcomeBadge outcome={a.outcome} /></td>
              <td className="py-2 text-destructive text-xs">{a.error ?? ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
