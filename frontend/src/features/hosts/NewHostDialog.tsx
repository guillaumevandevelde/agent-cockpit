import { useState, useEffect } from 'react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { MODAL_SIZES } from '@/lib/constants'
import { cn } from '@/lib/utils'
import { createHost, updateHost, testHostConnection } from './api'
import type { Host } from './types'

interface NewHostDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: () => void
  editHost?: Host | null
}

export function NewHostDialog({ open, onOpenChange, onSaved, editHost }: NewHostDialogProps) {
  const isEditing = !!editHost

  const [alias, setAlias] = useState('')
  const [hostname, setHostname] = useState('')
  const [port, setPort] = useState('22')
  const [username, setUsername] = useState('')
  const [sshKeyPath, setSshKeyPath] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<string | null>(null)

  useEffect(() => {
    if (open && editHost) {
      setAlias(editHost.alias)
      setHostname(editHost.hostname)
      setPort(String(editHost.port))
      setUsername(editHost.username)
      setSshKeyPath(editHost.ssh_key_path ?? '')
      setError(null)
      setTestResult(null)
    } else if (open) {
      setAlias('')
      setHostname('')
      setPort('22')
      setUsername('')
      setSshKeyPath('')
      setError(null)
      setTestResult(null)
    }
  }, [open, editHost])

  const canSave = alias.trim() && hostname.trim() && username.trim() && !submitting

  async function handleSave() {
    if (!canSave) return
    setError(null)
    setTestResult(null)
    setSubmitting(true)

    try {
      const data = {
        alias: alias.trim(),
        hostname: hostname.trim(),
        port: parseInt(port, 10) || 22,
        username: username.trim(),
        ssh_key_path: sshKeyPath.trim() || null,
      }

      if (isEditing && editHost) {
        await updateHost(editHost.id, data)
      } else {
        await createHost(data)
      }
      onSaved()
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save host')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleTestConnection() {
    if (!hostname.trim()) return
    setTestResult(null)
    setError(null)

    try {
      // If this is an existing host, use its ID; otherwise create temporarily
      if (isEditing && editHost) {
        const result = await testHostConnection(editHost.id)
        setTestResult(result.reachable ? '✅ Reachable' : '❌ Not reachable')
      } else {
        setTestResult('Save the host first, then test the connection.')
      }
    } catch (err) {
      setTestResult(err instanceof Error ? `Test failed: ${err.message}` : 'Test failed')
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={cn(MODAL_SIZES.SM)}>
        <DialogHeader>
          <DialogTitle>{isEditing ? 'Edit Host' : 'Add Host'}</DialogTitle>
          <DialogDescription>
            {isEditing
              ? 'Update the remote machine connection details.'
              : 'Register a remote machine that can run agent sessions.'}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="alias">Alias</Label>
            <Input
              id="alias"
              value={alias}
              onChange={(e) => setAlias(e.target.value)}
              placeholder="my-server"
              autoComplete="off"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="hostname">Hostname / IP</Label>
            <Input
              id="hostname"
              value={hostname}
              onChange={(e) => setHostname(e.target.value)}
              placeholder="192.168.1.100 or host.example.com"
              autoComplete="off"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="port">Port</Label>
              <Input
                id="port"
                type="number"
                min={1}
                max={65535}
                value={port}
                onChange={(e) => setPort(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="username">Username</Label>
              <Input
                id="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="root"
                autoComplete="off"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ssh-key-path">SSH Key Path (optional)</Label>
            <Input
              id="ssh-key-path"
              value={sshKeyPath}
              onChange={(e) => setSshKeyPath(e.target.value)}
              placeholder="/home/user/.ssh/id_ed25519"
              autoComplete="off"
            />
            <p className="text-xs text-muted-foreground">
              Leave empty to use the default SSH key.
            </p>
          </div>

          {/* Test connection button (edit mode only) */}
          {isEditing && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleTestConnection}
              className="w-full"
            >
              Test Connection
            </Button>
          )}

          {testResult && (
            <div className="rounded-md bg-muted px-3 py-2 text-sm">
              {testResult}
            </div>
          )}

          {error && (
            <div className="rounded-md bg-destructive/10 border border-destructive/20 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={!canSave}>
            {submitting ? 'Saving...' : isEditing ? 'Update' : 'Add Host'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
