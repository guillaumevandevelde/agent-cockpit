export type AutonomyMode = 'plan' | 'suggest' | 'auto'

export interface AutonomyProfile {
  id: number
  name: string
  mode: AutonomyMode
  description: string | null
  is_default: boolean
  allowed_tools: string[] | null
  denied_tools: string[] | null
  max_file_size_kb: number | null
  require_approval_for: string[] | null
  created_at: string
  updated_at: string
}

export interface ActiveAutonomy {
  mode: AutonomyMode
  profile_name: string
  description: string | null
}

export const AUTONOMY_MODES: Record<AutonomyMode, { label: string; description: string; color: string }> = {
  plan: {
    label: 'Plan',
    description: 'Read-only — agent inspects but cannot modify files',
    color: 'bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-300',
  },
  suggest: {
    label: 'Suggest',
    description: 'Interactive — agent proposes changes, you approve each',
    color: 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-300',
  },
  auto: {
    label: 'Auto',
    description: 'Full autonomy — agent auto-approves all actions',
    color: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-300',
  },
}
