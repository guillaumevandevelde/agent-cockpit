import { useEffect, useState, useCallback } from 'react'
import { AlertTriangle } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Alert, AlertTitle, AlertDescription } from '@/components/ui/alert'
import { getHooksStatus, installHooks } from '../api'

export function HooksStatusBanner() {
  const [installed, setInstalled] = useState<boolean | null>(null)
  const [installing, setInstalling] = useState(false)

  const load = useCallback(async () => {
    try {
      const status = await getHooksStatus()
      setInstalled(status.installed)
    } catch {
      setInstalled(null)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const handleInstall = async () => {
    setInstalling(true)
    try {
      const status = await installHooks()
      setInstalled(status.installed)
      toast.success('Scheduling hooks installed in ~/.claude/settings.json')
    } catch {
      toast.error('Failed to install scheduling hooks')
    } finally {
      setInstalling(false)
    }
  }

  if (installed !== false) return null

  return (
    <Alert variant="destructive">
      <AlertTriangle className="h-4 w-4" />
      <AlertTitle>Scheduling hooks not installed</AlertTitle>
      <AlertDescription className="flex flex-wrap items-center justify-between gap-2">
        <span>
          Claude Code&apos;s Notification/Stop/UserPromptSubmit/SessionStart hooks aren&apos;t
          wired to this app yet, so session-limit detection and auto-resume can&apos;t fire —
          sessions that hit their limit will stall forever instead of freeing up.
        </span>
        <Button size="sm" variant="outline" onClick={handleInstall} disabled={installing}>
          {installing ? 'Installing…' : 'Install hooks'}
        </Button>
      </AlertDescription>
    </Alert>
  )
}
