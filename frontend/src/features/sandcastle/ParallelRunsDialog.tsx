import { Plus, XCircle, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { MODAL_SIZES } from '@/lib/constants'

interface ParallelRunsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  prompts: { id: string; prompt: string; branch_name: string }[]
  starting: boolean
  onAdd: () => void
  onRemove: (index: number) => void
  onUpdate: (index: number, field: 'prompt' | 'branch_name', value: string) => void
  onStart: () => void
}

export function ParallelRunsDialog({
  open,
  onOpenChange,
  prompts,
  starting,
  onAdd,
  onRemove,
  onUpdate,
  onStart,
}: ParallelRunsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={MODAL_SIZES.LG}>
        <DialogHeader>
          <DialogTitle>Parallel Sandcastle Runs</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            Start multiple agent runs in parallel. Each run will execute independently in its own sandbox.
          </p>
          {prompts.map((item, index) => (
            <div key={item.id} className="space-y-2 border rounded-lg p-3">
              <div className="flex items-center justify-between">
                <Label>Run {index + 1}</Label>
                {prompts.length > 1 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => onRemove(index)}
                  >
                    <XCircle className="h-4 w-4" />
                  </Button>
                )}
              </div>
              <Textarea
                value={item.prompt}
                onChange={(e) => onUpdate(index, 'prompt', e.target.value)}
                placeholder="Describe the task for this agent..."
                rows={2}
              />
              <input
                type="text"
                value={item.branch_name}
                onChange={(e) => onUpdate(index, 'branch_name', e.target.value)}
                placeholder="Branch name (optional)"
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              />
            </div>
          ))}
          <Button variant="outline" onClick={onAdd}>
            <Plus className="h-4 w-4 mr-2" /> Add Another Run
          </Button>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button
              onClick={onStart}
              disabled={prompts.every((p) => !p.prompt.trim()) || starting}
            >
              {starting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Start {prompts.filter((p) => p.prompt.trim()).length} Runs
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
