import { useEffect, useState, useCallback } from 'react'
import { AlertTriangle } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Alert, AlertTitle, AlertDescription } from '@/components/ui/alert'
import { getHooksStatus, installHooks } from '../api'
import type { HooksStatus } from '../types'

export function HooksStatusBanner() {
  const [status, setStatus] = useState<HooksStatus | null>(null)
  const [installing, setInstalling] = useState(false)

  const load = useCallback(async () => {
    try {
      setStatus(await getHooksStatus())
    } catch {
      setStatus(null)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleInstall = async () => {
    setInstalling(true)
    try {
      const next = await installHooks()
      setStatus(next)
      if (next.installed) {
        toast.success('Scheduling hooks installed in ~/.claude/settings.json')
      } else {
        toast.error(
          next.stale.length > 0
            ? `Hooks installed, but these events are stale and need a manual reinstall: ${next.stale.join(', ')}`
            : 'Scheduling hooks not yet installed',
        )
      }
    } catch {
      toast.error('Failed to install scheduling hooks')
    } finally {
      setInstalling(false)
    }
  }

  if (!status || status.installed) return null

  const staleList = status.stale.join(', ')
  const title = status.stale.length > 0
    ? `Scheduling hooks stale: ${staleList}`
    : 'Scheduling hooks not installed'
  const description = status.stale.length > 0
    ? `The ${staleList} hook command on disk no longer matches what the app renders — a code change landed without a reinstall. Reinstalling the missing events alone won't clear the stale entries; remove the affected entry from ~/.claude/settings.json and then click Install hooks again.`
    : `Claude Code's Notification/Stop/UserPromptSubmit/SessionStart hooks aren't wired to this app yet, so session-limit detection and auto-resume can't fire — sessions that hit their limit will stall forever instead of freeing up.`

  return (
    <Alert variant="destructive">
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
        <span>{description}</span>
        <Button size="sm" variant="outline" onClick={handleInstall} disabled={installing}>
          {installing ? 'Installing…' : 'Install hooks'}
        </Button>
      </AlertDescription>
    </Alert>
  )
}
