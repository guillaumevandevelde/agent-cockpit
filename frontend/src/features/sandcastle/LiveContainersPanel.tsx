import { Container } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { SandcastleContainer } from './types'

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
            <div key={c.id} className="flex items-center gap-3 text-sm font-mono border rounded-md p-2 bg-muted/40">
              <span className="h-2 w-2 rounded-full bg-green-500 shrink-0 animate-pulse" />
              <span className="text-xs text-muted-foreground">{c.runtime}</span>
              <span className="font-medium truncate">{c.name}</span>
              <span className="text-xs text-muted-foreground truncate">{c.image}</span>
              <span className="ml-auto text-xs text-green-700 shrink-0">{c.status}</span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
