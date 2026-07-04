import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { SandcastleStats } from './types'

interface StatsCardProps {
  stats: SandcastleStats
}

export function StatsCard({ stats }: StatsCardProps) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm">Run Statistics</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="text-center">
            <div className="text-2xl font-bold">{stats.total_runs}</div>
            <div className="text-xs text-muted-foreground">Total Runs</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-bold text-green-600">{stats.runs_by_status?.completed || 0}</div>
            <div className="text-xs text-muted-foreground">Completed</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-bold text-yellow-600">{stats.active_runs}</div>
            <div className="text-xs text-muted-foreground">Active</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-bold text-blue-600">{stats.recent_runs_24h}</div>
            <div className="text-xs text-muted-foreground">Last 24h</div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
