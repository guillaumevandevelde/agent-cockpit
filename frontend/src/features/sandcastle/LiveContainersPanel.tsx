import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, Container, Radio } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { CLICKABLE_CARD } from '@/lib/constants'
import { streamContainerLogs } from './api'
import type { SandcastleContainer } from './types'

function ContainerRow({ container: c }: { container: SandcastleContainer }) {
  const [expanded, setExpanded] = useState(false)
  const [lines, setLines] = useState<string[]>([])
  const logsEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!expanded) return
    setLines([])
    const es = streamContainerLogs(c.name, c.runtime, (line) => {
      setLines((prev) => [...prev, line])
    })
    return () => es.close()
  }, [expanded, c.name, c.runtime])

  useEffect(() => {
    if (expanded) logsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [lines, expanded])

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        className={`${CLICKABLE_CARD} flex items-center gap-3 text-sm font-mono rounded-md p-2 bg-muted/40`}
        onClick={() => setExpanded((v) => !v)}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setExpanded((v) => !v) }}
      >
        <span className="text-muted-foreground">
          {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </span>
        <span className="h-2 w-2 rounded-full bg-green-500 shrink-0 animate-pulse" />
        <span className="text-xs text-muted-foreground">{c.runtime}</span>
        <span className="font-medium truncate">{c.name}</span>
        <span className="text-xs text-muted-foreground truncate">{c.image}</span>
        <span className="ml-auto text-xs text-green-700 shrink-0">{c.status}</span>
      </div>
      {expanded && (
        <div className="border-l-2 border-border ml-4 pl-4 mt-1 mb-2">
          <div className="flex items-center gap-2 mb-1">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Container Logs</p>
            <span className="flex items-center gap-1 text-xs text-green-600 font-medium">
              <Radio className="h-3 w-3 animate-pulse" /> Live
            </span>
          </div>
          <pre className="text-xs bg-muted p-2 rounded overflow-auto max-h-64 font-mono whitespace-pre-wrap">
            {lines.length > 0 ? lines.join('') : '(waiting for output…)'}
            <div ref={logsEndRef} />
          </pre>
        </div>
      )}
    </div>
  )
}

export function LiveContainersPanel({ containers }: { containers: SandcastleContainer[] }) {
  if (containers.length === 0) return null
  return (
    <Card className="border-green-500/30">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <Container className="h-4 w-4 text-green-600" />
          Live Containers
          <Badge className="bg-green-500 text-white text-xs">{containers.length} running</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {containers.map((c) => (
            <ContainerRow key={c.id} container={c} />
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
