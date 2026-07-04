import { Loader2 } from 'lucide-react'
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

interface NewRunDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  prompt: string
  onPromptChange: (v: string) => void
  branch: string
  onBranchChange: (v: string) => void
  starting: boolean
  onStart: () => void
}

export function NewRunDialog({
  open,
  onOpenChange,
  prompt,
  onPromptChange,
  branch,
  onBranchChange,
  starting,
  onStart,
}: NewRunDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader>
          <DialogTitle>New Sandcastle Run</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Prompt</Label>
            <Textarea
              value={prompt}
              onChange={(e) => onPromptChange(e.target.value)}
              placeholder="Describe the task for the agent..."
              rows={4}
            />
          </div>
          <div className="space-y-2">
            <Label>Branch Name (optional)</Label>
            <input
              type="text"
              value={branch}
              onChange={(e) => onBranchChange(e.target.value)}
              placeholder="agent/fix-issue-42"
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button onClick={onStart} disabled={!prompt.trim() || starting}>
              {starting && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
              Start Run
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
