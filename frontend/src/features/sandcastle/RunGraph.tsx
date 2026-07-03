import { useState, useEffect, useCallback, useMemo } from 'react'
import { GitBranch } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { getSandcastleRunGraph } from './api'
import type { SandcastleRunGraph, SandcastleGraphNode } from './types'

const NODE_W = 220
const NODE_H = 72
const COL_GAP = 260
const ROW_GAP = 16
const PADDING = 24

const STATUS_COLOR: Record<SandcastleGraphNode['status'], string> = {
  pending: '#3b82f6',
  running: '#eab308',
  completed: '#22c55e',
  failed: '#ef4444',
  cancelled: '#71717a',
}

interface LayoutNode extends SandcastleGraphNode {
  x: number
  y: number
}

interface Layout {
  nodes: LayoutNode[]
  width: number
  height: number
}

/** Lays out the graph as a shallow left-to-right fan-out: batch roots (and
 * standalone runs) in a left column, their children in a right column,
 * stacked vertically. There is no arbitrary-depth DAG in the underlying
 * data (see get_run_graph), so a hand-rolled two-column layout is enough --
 * no graph-layout dependency needed. */
function layoutGraph(graph: SandcastleRunGraph): Layout {
  const childrenByParent = new Map<string, string[]>()
  const hasParent = new Set<string>()
  for (const e of graph.edges) {
    hasParent.add(e.target)
    const list = childrenByParent.get(e.source) ?? []
    list.push(e.target)
    childrenByParent.set(e.source, list)
  }
  const roots = graph.nodes.filter((n) => !hasParent.has(n.id))

  const positions = new Map<string, { x: number; y: number }>()
  let cursorY = PADDING
  let hasBatches = false

  for (const root of roots) {
    const children = childrenByParent.get(root.id) ?? []
    if (children.length === 0) {
      positions.set(root.id, { x: PADDING, y: cursorY })
      cursorY += NODE_H + ROW_GAP
    } else {
      hasBatches = true
      const startY = cursorY
      for (const childId of children) {
        positions.set(childId, { x: PADDING + COL_GAP, y: cursorY })
        cursorY += NODE_H + ROW_GAP
      }
      const endY = cursorY - ROW_GAP - NODE_H
      positions.set(root.id, { x: PADDING, y: (startY + endY) / 2 })
    }
  }

  const width = PADDING * 2 + NODE_W + (hasBatches ? COL_GAP : 0)
  const height = Math.max(cursorY - ROW_GAP + PADDING, PADDING * 2 + NODE_H)

  const nodes: LayoutNode[] = graph.nodes.map((n) => ({
    ...n,
    ...(positions.get(n.id) ?? { x: PADDING, y: PADDING }),
  }))

  return { nodes, width, height }
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}m ${s}s`
}

interface RunGraphProps {
  projectPath: string
}

export function RunGraph({ projectPath }: RunGraphProps) {
  const [graph, setGraph] = useState<SandcastleRunGraph | null>(null)
  const [selected, setSelected] = useState<SandcastleGraphNode | null>(null)

  const load = useCallback(async () => {
    try {
      const g = await getSandcastleRunGraph(projectPath)
      setGraph(g)
      setSelected((prev) => (prev ? g.nodes.find((n) => n.id === prev.id) ?? null : null))
    } catch {
      // Best-effort refresh; keep the last known graph on a transient failure.
    }
  }, [projectPath])

  useEffect(() => { load() }, [load])

  const hasActive = graph?.nodes.some((n) => n.status === 'running' || n.status === 'pending') ?? false
  useEffect(() => {
    if (!hasActive) return
    const id = setInterval(load, 3000)
    return () => clearInterval(id)
  }, [hasActive, load])

  const layout = useMemo(() => (graph ? layoutGraph(graph) : null), [graph])

  if (!graph || !layout) {
    return <p className="text-muted-foreground text-sm">Loading graph...</p>
  }
  if (layout.nodes.length === 0) {
    return <p className="text-muted-foreground text-sm">No runs yet. Start a run to see it here.</p>
  }

  const nodeById = new Map(layout.nodes.map((n) => [n.id, n]))

  return (
    <div className="flex gap-4">
      <div className="flex-1 overflow-auto border rounded-lg bg-muted/20">
        <svg width={layout.width} height={layout.height} className="min-w-full">
          {graph.edges.map((e) => {
            const source = nodeById.get(e.source)
            const target = nodeById.get(e.target)
            if (!source || !target) return null
            const sx = source.x + NODE_W
            const sy = source.y + NODE_H / 2
            const tx = target.x
            const ty = target.y + NODE_H / 2
            const midX = (sx + tx) / 2
            return (
              <path
                key={`${e.source}->${e.target}`}
                d={`M ${sx} ${sy} C ${midX} ${sy}, ${midX} ${ty}, ${tx} ${ty}`}
                fill="none"
                stroke="currentColor"
                className="text-border"
                strokeWidth={2}
              />
            )
          })}
          {layout.nodes.map((n) => (
            <g
              key={n.id}
              transform={`translate(${n.x}, ${n.y})`}
              className="cursor-pointer"
              role="button"
              tabIndex={0}
              onClick={() => setSelected(n)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setSelected(n) }}
            >
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={8}
                className="fill-card"
                stroke={STATUS_COLOR[n.status]}
                strokeWidth={selected?.id === n.id ? 3 : 2}
              />
              <circle cx={16} cy={18} r={5} fill={STATUS_COLOR[n.status]} />
              <text x={30} y={22} fontSize={11} className="fill-muted-foreground uppercase tracking-wide">
                {n.type === 'batch' ? 'batch' : n.status}
              </text>
              <text x={12} y={42} fontSize={12} className="fill-foreground font-medium">
                {n.label.length > 26 ? `${n.label.slice(0, 26)}…` : n.label}
              </text>
              {n.type === 'run' && (
                <text x={12} y={60} fontSize={10} className="fill-muted-foreground">
                  {formatDuration(n.duration_seconds)}
                </text>
              )}
            </g>
          ))}
        </svg>
      </div>
      {selected && (
        <div className="w-72 shrink-0 border rounded-lg p-4 space-y-3">
          <div className="flex items-center justify-between">
            <Badge style={{ backgroundColor: STATUS_COLOR[selected.status], color: 'white' }}>
              {selected.status}
            </Badge>
            {selected.branch && (
              <span className="text-xs font-mono text-muted-foreground flex items-center gap-1">
                <GitBranch className="h-3 w-3" /> {selected.branch}
              </span>
            )}
          </div>
          {selected.prompt && (
            <div>
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">Prompt</p>
              <p className="text-sm whitespace-pre-wrap">{selected.prompt}</p>
            </div>
          )}
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div>
              <p className="text-muted-foreground">Duration</p>
              <p>{formatDuration(selected.duration_seconds)}</p>
            </div>
            {selected.commits_count != null && (
              <div>
                <p className="text-muted-foreground">Commits</p>
                <p>{selected.commits_count}</p>
              </div>
            )}
          </div>
          {selected.error && (
            <div>
              <p className="text-xs font-semibold text-destructive uppercase tracking-wide mb-1">Error</p>
              <p className="text-xs text-destructive whitespace-pre-wrap">{selected.error}</p>
            </div>
          )}
          <p className="text-[11px] text-muted-foreground">
            Token/cost tracking isn't wired up for sandcastle runs yet.
          </p>
        </div>
      )}
    </div>
  )
}
