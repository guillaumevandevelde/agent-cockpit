import { Loader2, CheckCircle, XCircle, AlertCircle } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import type { SandcastleRun } from './types'

export function StatusBadge({ status }: { status: SandcastleRun['status'] }) {
  const variants: Record<SandcastleRun['status'], { icon: React.ReactNode; className: string }> = {
    pending: { icon: <Loader2 className="h-3 w-3 animate-spin" />, className: 'bg-blue-500 text-white' },
    running: { icon: <Loader2 className="h-3 w-3 animate-spin" />, className: 'bg-yellow-500 text-white' },
    completed: { icon: <CheckCircle className="h-3 w-3" />, className: 'bg-green-500 text-white' },
    failed: { icon: <XCircle className="h-3 w-3" />, className: 'bg-red-500 text-white' },
    cancelled: { icon: <AlertCircle className="h-3 w-3" />, className: 'bg-muted text-muted-foreground' },
  }
  const { icon, className } = variants[status]
  return <Badge className={`${className} gap-1`}>{icon}{status}</Badge>
}
