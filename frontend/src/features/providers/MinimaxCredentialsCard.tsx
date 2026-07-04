import { useState, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { fetchMinimaxPlatformStatus, setMinimaxApiKey, clearMinimaxApiKey } from '@/features/cc-bridge/api'

export function MinimaxCredentialsCard() {
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [keyInput, setKeyInput] = useState('')
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchMinimaxPlatformStatus()
      .then((data) => { if (!cancelled) setConfigured(data.configured) })
      .catch(() => { if (!cancelled) setConfigured(null) })
    return () => { cancelled = true }
  }, [])

  async function handleSave() {
    const key = keyInput.trim()
    if (!key) return
    setSaving(true)
    setError(null)
    try {
      const result = await setMinimaxApiKey(key)
      setConfigured(result.configured)
      setKeyInput('')
      setEditing(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save MiniMax API key')
    } finally {
      setSaving(false)
    }
  }

  async function handleClear() {
    setSaving(true)
    setError(null)
    try {
      const result = await clearMinimaxApiKey()
      setConfigured(result.configured)
      setKeyInput('')
      setEditing(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to clear MiniMax API key')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>MiniMax</CardTitle>
        <CardDescription>
          API key for launching Claude Code sessions against MiniMax instead of Anthropic. Sent once to
          the backend and written to its local .env file — never stored in the database, never shown again.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {configured === null && (
          <p className="text-xs text-muted-foreground">Checking configuration...</p>
        )}

        {configured === true && !editing && (
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm text-muted-foreground">MiniMax API key configured.</p>
            <div className="flex gap-2 shrink-0">
              <button
                type="button"
                className="text-xs text-muted-foreground hover:text-foreground underline"
                onClick={() => setEditing(true)}
              >
                Change
              </button>
              <button
                type="button"
                className="text-xs text-destructive hover:text-destructive/80 underline"
                onClick={handleClear}
                disabled={saving}
              >
                Clear
              </button>
            </div>
          </div>
        )}

        {(configured === false || editing) && (
          <div className="space-y-1.5">
            <Label htmlFor="minimax-api-key">MiniMax API key</Label>
            <div className="flex gap-2">
              <Input
                id="minimax-api-key"
                type="password"
                autoComplete="off"
                value={keyInput}
                onChange={(e) => setKeyInput(e.target.value)}
                placeholder="sk-..."
              />
              <Button
                type="button"
                size="sm"
                onClick={handleSave}
                disabled={!keyInput.trim() || saving}
              >
                {saving ? 'Saving...' : 'Save'}
              </Button>
              {configured === true && (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onClick={() => { setEditing(false); setKeyInput(''); setError(null) }}
                  disabled={saving}
                >
                  Cancel
                </Button>
              )}
            </div>
            {error && (
              <p className="text-xs text-destructive">{error}</p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
