import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { useAutoResume } from '../useAutoResume'

interface AutoResumeToggleProps {
  cwd: string
}

export function AutoResumeToggle({ cwd }: AutoResumeToggleProps) {
  const { enabled, loading, toggle } = useAutoResume(cwd)

  return (
    <div className="flex items-center gap-2">
      <Switch
        id="auto-resume"
        checked={enabled}
        onCheckedChange={toggle}
        disabled={loading}
      />
      <Label htmlFor="auto-resume" className="text-sm">
        Auto-resume on session limit
      </Label>
    </div>
  )
}
