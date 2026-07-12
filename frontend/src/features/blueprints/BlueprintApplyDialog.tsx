import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { MODAL_SIZES } from '@/lib/constants'
import { CheckCircle2, AlertTriangle, FileText, Folder } from 'lucide-react'
import type { BlueprintApplyResponse, Blueprint } from './types'

interface BlueprintApplyDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  blueprint: Blueprint | null
  defaultProjectPath?: string
  onApply: (
    name: string,
    projectPath: string,
    force: boolean,
  ) => Promise<BlueprintApplyResponse>
}

export function BlueprintApplyDialog({
  open,
  onOpenChange,
  blueprint,
  defaultProjectPath,
  onApply,
}: BlueprintApplyDialogProps) {
  const [projectPath, setProjectPath] = useState('')
  const [force, setForce] = useState(false)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<BlueprintApplyResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setProjectPath(defaultProjectPath ?? '')
      setForce(false)
      setResult(null)
      setError(null)
    }
  }, [open, defaultProjectPath])

  const handleApply = async () => {
    if (!blueprint || !projectPath.trim()) return
    setApplying(true)
    setError(null)
    try {
      const r = await onApply(blueprint.name, projectPath.trim(), force)
      setResult(r)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Apply failed')
    } finally {
      setApplying(false)
    }
  }

  if (!blueprint) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader>
          <DialogTitle>Apply Blueprint: {blueprint.name}</DialogTitle>
          <DialogDescription>
            Materialises the recipe into{' '}
            <code>&lt;project&gt;/.claude/</code>. Atomic and idempotent.
          </DialogDescription>
        </DialogHeader>

        {!result ? (
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label htmlFor="bp-apply-path">Project Path</Label>
              <Input
                id="bp-apply-path"
                value={projectPath}
                onChange={(e) => setProjectPath(e.target.value)}
                placeholder="/path/to/project"
                autoFocus
              />
              <p className="text-xs text-muted-foreground">
                Absolute path. The blueprint writes into{' '}
                <code>&lt;project&gt;/.claude/</code>.
              </p>
            </div>
            <div className="flex items-center justify-between rounded-lg border p-3">
              <div>
                <Label htmlFor="bp-apply-force">Force overwrite</Label>
                <p className="text-xs text-muted-foreground">
                  Overwrite an already-populated <code>.claude/</code>.
                  Without this, populated projects are skipped.
                </p>
              </div>
              <Switch
                id="bp-apply-force"
                checked={force}
                onCheckedChange={setForce}
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
          </div>
        ) : (
          <div className="space-y-3 py-2">
            {result.skipped_existing ? (
              <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
                <AlertTriangle className="h-4 w-4 text-amber-500 mt-0.5" />
                <div className="text-sm">
                  <p className="font-medium">Skipped</p>
                  <p className="text-muted-foreground">
                    <code>.claude/</code> was already populated. Pass
                    <code> force=true </code> to overwrite.
                  </p>
                </div>
              </div>
            ) : (
              <div className="flex items-start gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/5 p-3">
                <CheckCircle2 className="h-4 w-4 text-emerald-500 mt-0.5" />
                <p className="text-sm font-medium">Applied successfully.</p>
              </div>
            )}

            {result.applied_skills.length > 0 && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">
                  Skills
                </p>
                <div className="flex flex-wrap gap-1">
                  {result.applied_skills.map((s) => (
                    <Badge key={s} variant="secondary">
                      {s}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
            {result.applied_agents.length > 0 && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">
                  Agents
                </p>
                <div className="flex flex-wrap gap-1">
                  {result.applied_agents.map((a) => (
                    <Badge key={a} variant="secondary">
                      {a}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
            {result.written_files.length > 0 && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">
                  Files written
                </p>
                <ul className="text-xs font-mono space-y-0.5">
                  {result.written_files.map((f) => (
                    <li key={f} className="flex items-center gap-1">
                      <FileText className="h-3 w-3 text-muted-foreground" />
                      {f}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {result.created_dirs.length > 0 && (
              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1">
                  Directories created
                </p>
                <ul className="text-xs font-mono space-y-0.5">
                  {result.created_dirs.map((d) => (
                    <li key={d} className="flex items-center gap-1">
                      <Folder className="h-3 w-3 text-muted-foreground" />
                      .claude/{d}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          {!result ? (
            <>
              <Button
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={applying}
              >
                Cancel
              </Button>
              <Button
                onClick={handleApply}
                disabled={applying || !projectPath.trim()}
              >
                {applying ? 'Applying…' : 'Apply'}
              </Button>
            </>
          ) : (
            <Button onClick={() => onOpenChange(false)}>Done</Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}