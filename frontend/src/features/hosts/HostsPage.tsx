import { useState, useEffect, useCallback } from 'react'
import { toast } from 'sonner'
import { Plus, RefreshCw, Server, Trash2, Wifi, WifiOff, HelpCircle } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { CLICKABLE_CARD } from '@/lib/constants'
import { cn } from '@/lib/utils'
import { fetchHosts, deleteHost, testHostConnection } from './api'
import { NewHostDialog } from './NewHostDialog'
import type { Host } from './types'

const STATUS_ICONS: Record<string, React.ReactNode> = {
  online: <Wifi className="h-4 w-4 text-green-500" />,
  offline: <WifiOff className="h-4 w-4 text-red-500" />,
  unknown: <HelpCircle className="h-4 w-4 text-muted-foreground" />,
}

const STATUS_BADGES: Record<string, string> = {
  online: 'bg-green-500/10 text-green-600 border-green-500/20',
  offline: 'bg-red-500/10 text-red-600 border-red-500/20',
  unknown: 'bg-muted text-muted-foreground',
}

export function HostsPage() {
  const [hosts, setHosts] = useState<Host[]>([])
  const [loading, setLoading] = useState(true)
  const [showDialog, setShowDialog] = useState(false)
  const [editHost, setEditHost] = useState<Host | null>(null)
  const [testingId, setTestingId] = useState<number | null>(null)

  const loadHosts = useCallback(async () => {
    setLoading(true)
    try {
      const data = await fetchHosts()
      setHosts(data)
    } catch {
      toast.error('Failed to load hosts')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadHosts()
  }, [loadHosts])

  async function handleDelete(host: Host) {
    if (!confirm(`Delete host "${host.alias}"?`)) return
    try {
      await deleteHost(host.id)
      toast.success(`Deleted host "${host.alias}"`)
      void loadHosts()
    } catch {
      toast.error('Failed to delete host')
    }
  }

  async function handleTest(id: number) {
    setTestingId(id)
    try {
      const result = await testHostConnection(id)
      toast.info(result.reachable ? `✅ ${result.alias} is reachable` : `❌ ${result.alias} is not reachable`)
      void loadHosts()
    } catch {
      toast.error('Connection test failed')
    } finally {
      setTestingId(null)
    }
  }

  function handleEdit(host: Host) {
    setEditHost(host)
    setShowDialog(true)
  }

  function handleAdd() {
    setEditHost(null)
    setShowDialog(true)
  }

  function handleDialogSaved() {
    void loadHosts()
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Hosts</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Remote machines for running agent sessions via SSH.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={loadHosts} disabled={loading}>
            <RefreshCw className={cn('h-4 w-4 mr-1', loading && 'animate-spin')} />
            Refresh
          </Button>
          <Button size="sm" onClick={handleAdd}>
            <Plus className="h-4 w-4 mr-1" />
            Add Host
          </Button>
        </div>
      </div>

      {loading && hosts.length === 0 ? (
        <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
          Loading hosts...
        </div>
      ) : hosts.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-sm text-muted-foreground">
          <Server className="h-12 w-12 mb-4 opacity-20" />
          <p>No hosts configured yet.</p>
          <p className="mt-1">Add a remote machine to run agent sessions on it.</p>
          <Button variant="outline" size="sm" className="mt-4" onClick={handleAdd}>
            <Plus className="h-4 w-4 mr-1" />
            Add Host
          </Button>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {hosts.map((host) => (
            <Card
              key={host.id}
              className={cn(CLICKABLE_CARD)}
              onClick={() => handleEdit(host)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  handleEdit(host)
                }
              }}
              tabIndex={0}
              role="button"
            >
              <CardHeader className="p-4 pb-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 min-w-0">
                    {STATUS_ICONS[host.status] ?? STATUS_ICONS.unknown}
                    <CardTitle className="text-base truncate">{host.alias}</CardTitle>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      type="button"
                      aria-label="Test connection"
                      className="h-7 w-7 flex items-center justify-center rounded text-muted-foreground/50 hover:text-foreground transition-colors"
                      onClick={(e) => { e.stopPropagation(); void handleTest(host.id) }}
                      onKeyDown={(e) => e.stopPropagation()}
                      title="Test connection"
                      disabled={testingId === host.id}
                    >
                      <RefreshCw className={cn('h-3.5 w-3.5', testingId === host.id && 'animate-spin')} />
                    </button>
                    <button
                      type="button"
                      aria-label="Delete host"
                      className="h-7 w-7 flex items-center justify-center rounded text-muted-foreground/50 hover:text-destructive transition-colors"
                      onClick={(e) => { e.stopPropagation(); void handleDelete(host) }}
                      onKeyDown={(e) => e.stopPropagation()}
                      title="Delete host"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </div>
              </CardHeader>
              <CardContent className="p-4 pt-2">
                <div className="space-y-1 text-xs text-muted-foreground">
                  <p className="font-mono">{host.username}@{host.hostname}:{host.port}</p>
                  {host.ssh_key_path && (
                    <p className="truncate" title={host.ssh_key_path}>Key: {host.ssh_key_path}</p>
                  )}
                </div>
                <Badge
                  variant="outline"
                  className={cn('mt-2', STATUS_BADGES[host.status] ?? STATUS_BADGES.unknown)}
                >
                  {host.status}
                </Badge>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <NewHostDialog
        open={showDialog}
        onOpenChange={setShowDialog}
        onSaved={handleDialogSaved}
        editHost={editHost}
      />
    </div>
  )
}
