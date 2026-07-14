import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Building2 } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { RefreshButton } from '@/components/shared/RefreshButton'
import { fetchPortfolioOverview } from './api'
import type { PortfolioOverview, PortfolioProject, PortfolioTotals } from './types'

const POLL_INTERVAL_MS = 10_000

type KindFilter = 'all' | 'meta' | 'product' | 'archived'

const FILTERS: { key: KindFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'meta', label: 'Meta only' },
  { key: 'product', label: 'Product only' },
  { key: 'archived', label: 'Archived' },
]

const KIND_VARIANT: Record<string, 'default' | 'secondary' | 'outline'> = {
  meta: 'default',
  product: 'secondary',
  archived: 'outline',
  unknown: 'outline',
}

function formatWhen(iso: string | null): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  const secs = Math.round((Date.now() - then) / 1000)
  if (secs < 60) return 'just now'
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

const COUNT_COLUMNS: { key: keyof PortfolioTotals; label: string }[] = [
  { key: 'backlog', label: 'Backlog' },
  { key: 'todo', label: 'Todo' },
  { key: 'doing', label: 'Doing' },
  { key: 'impediment', label: 'Impediment' },
  { key: 'done_24h', label: 'Done 24h' },
]

function Count({ value }: { value: number }) {
  return (
    <span className={value === 0 ? 'text-muted-foreground' : 'font-medium'}>{value}</span>
  )
}

function ProjectRow({ project }: { project: PortfolioProject }) {
  return (
    <tr className="border-t hover:bg-muted/40">
      <td className="py-2 pr-3">
        <div className="flex items-center gap-2">
          <span className="font-medium">{project.name}</span>
          {project.stale && (
            <Badge
              variant="destructive"
              className="gap-1"
              data-testid="stale-badge"
              title={`Stale — last flagged ${formatWhen(project.stale_since)}`}
              aria-label={`Project is stale (last flagged ${formatWhen(project.stale_since)})`}
            >
              <AlertTriangle className="h-3 w-3" aria-hidden="true" />
              stale
            </Badge>
          )}
        </div>
        <div className="text-xs text-muted-foreground truncate max-w-[22rem]">
          {project.project_key}
        </div>
      </td>
      <td className="py-2 pr-3">
        <Badge variant={KIND_VARIANT[project.kind] ?? 'outline'}>{project.kind}</Badge>
      </td>
      <td className="py-2 pr-3 text-center">
        {project.autodispatch_enabled ? (
          <Badge variant="default">on</Badge>
        ) : (
          <span className="text-muted-foreground">off</span>
        )}
      </td>
      {COUNT_COLUMNS.map((c) => (
        <td key={c.key} className="py-2 pr-3 text-center tabular-nums">
          <Count value={project.totals[c.key]} />
        </td>
      ))}
      <td className="py-2 pr-3 text-right text-muted-foreground whitespace-nowrap">
        {formatWhen(project.last_activity)}
      </td>
      <td className="py-2 text-right text-muted-foreground whitespace-nowrap">
        {formatWhen(project.last_dispatch)}
      </td>
    </tr>
  )
}

export function PortfolioPage() {
  const [overview, setOverview] = useState<PortfolioOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<KindFilter>('all')

  const load = useCallback(async (opts?: { silent?: boolean }) => {
    if (!opts?.silent) setLoading(true)
    try {
      setOverview(await fetchPortfolioOverview())
    } catch {
      setOverview(null)
    }
    if (!opts?.silent) setLoading(false)
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    const id = setInterval(() => {
      if (document.hidden) return
      void load({ silent: true })
    }, POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [load])

  const totals = overview?.totals
  const projects = overview?.projects ?? []
  const visibleProjects =
    filter === 'all' ? projects : projects.filter((p) => p.kind === filter)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Building2 className="h-6 w-6" />
          <div>
            <h1 className="text-2xl font-semibold">Portfolio</h1>
            <p className="text-sm text-muted-foreground">
              Kanban stats across every project, at a glance.
            </p>
          </div>
        </div>
        <RefreshButton onClick={() => load()} loading={loading} />
      </div>

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <Button
            key={f.key}
            size="sm"
            variant={filter === f.key ? 'default' : 'outline'}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </Button>
        ))}
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        {COUNT_COLUMNS.map((c) => (
          <Card key={c.key}>
            <CardHeader className="pb-1">
              <CardTitle className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
                {c.label}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-semibold tabular-nums">
                {totals ? totals[c.key] : '—'}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardContent className="pt-6 overflow-x-auto">
          {loading && !overview ? (
            <p className="text-sm text-muted-foreground">Loading…</p>
          ) : !overview || overview.projects.length === 0 ? (
            <p className="text-sm text-muted-foreground">No projects tracked yet.</p>
          ) : visibleProjects.length === 0 ? (
            <p className="text-sm text-muted-foreground">No projects match this filter.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-muted-foreground uppercase tracking-wide">
                  <th className="pb-2 pr-3 font-medium">Project</th>
                  <th className="pb-2 pr-3 font-medium">Kind</th>
                  <th className="pb-2 pr-3 font-medium text-center">Dispatch</th>
                  {COUNT_COLUMNS.map((c) => (
                    <th key={c.key} className="pb-2 pr-3 font-medium text-center">
                      {c.label}
                    </th>
                  ))}
                  <th className="pb-2 pr-3 font-medium text-right">Activity</th>
                  <th className="pb-2 font-medium text-right">Dispatched</th>
                </tr>
              </thead>
              <tbody>
                {visibleProjects.map((p) => (
                  <ProjectRow key={p.project_key} project={p} />
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
