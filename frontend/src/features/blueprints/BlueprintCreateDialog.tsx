import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { MODAL_SIZES } from '@/lib/constants'
import type { BlueprintCreate } from './types'

interface BlueprintCreateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreate: (data: BlueprintCreate) => Promise<void>
}

export function BlueprintCreateDialog({
  open,
  onOpenChange,
  onCreate,
}: BlueprintCreateDialogProps) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [claudemd, setClaudemd] = useState('')
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const resetForm = () => {
    setName('')
    setDescription('')
    setClaudemd('')
    setError(null)
  }

  const handleOpenChange = (next: boolean) => {
    if (!next) resetForm()
    onOpenChange(next)
  }

  const handleCreate = async () => {
    if (!name.trim()) {
      setError('Name is required')
      return
    }
    setCreating(true)
    setError(null)
    try {
      await onCreate({
        name: name.trim(),
        description: description.trim() || null,
        claudemd: claudemd.trim() ? claudemd : null,
      })
      handleOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create blueprint')
    } finally {
      setCreating(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader>
          <DialogTitle>Create Blueprint</DialogTitle>
          <DialogDescription>
            A blueprint is a version-pinned recipe that seeds a fresh
            project's <code>.claude/</code> folder on demand.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label htmlFor="bp-name">Name</Label>
            <Input
              id="bp-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="webapp-default"
              autoFocus
            />
            <p className="text-xs text-muted-foreground">
              Lowercase letters, digits, dot, underscore, dash. Used as the
              filename in the store.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="bp-description">Description</Label>
            <Textarea
              id="bp-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="A brief summary of what this blueprint seeds..."
              className="min-h-[60px]"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="bp-claudemd">CLAUDE.md Stub (optional)</Label>
            <Textarea
              id="bp-claudemd"
              value={claudemd}
              onChange={(e) => setClaudemd(e.target.value)}
              placeholder="# Project context&#10;&#10;Add project-specific guidance..."
              className="min-h-[100px] font-mono text-sm"
            />
          </div>

          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={creating}
          >
            Cancel
          </Button>
          <Button onClick={handleCreate} disabled={creating || !name.trim()}>
            {creating ? 'Creating…' : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
