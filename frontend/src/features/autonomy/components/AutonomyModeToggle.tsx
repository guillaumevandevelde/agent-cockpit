import { useAutonomy } from '@/contexts/AutonomyContext'
import { AUTONOMY_MODES, type AutonomyMode } from '@/types/autonomy'
import { cn } from '@/lib/utils'
import { Shield, Eye, Zap } from 'lucide-react'

const MODE_ICONS: Record<AutonomyMode, typeof Shield> = {
  plan: Eye,
  suggest: Shield,
  auto: Zap,
}

export function AutonomyModeToggle() {
  const { active, setActiveMode, loading } = useAutonomy()
  const currentMode = active?.mode ?? 'suggest'

  const cycleMode = async () => {
    const modes: AutonomyMode[] = ['plan', 'suggest', 'auto']
    const idx = modes.indexOf(currentMode)
    const next = modes[(idx + 1) % modes.length]
    await setActiveMode(next)
  }

  const Icon = MODE_ICONS[currentMode]
  const config = AUTONOMY_MODES[currentMode]

  return (
    <button
      onClick={cycleMode}
      disabled={loading}
      className={cn(
        'flex items-center gap-2 w-full rounded-md px-3 py-2 text-sm font-medium transition-colors',
        'hover:bg-accent hover:text-accent-foreground',
        'focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
      )}
      title={`Autonomy: ${config.description}. Click to cycle.`}
    >
      <Icon className="h-4 w-4 shrink-0" />
      <span className="truncate">Autonomy: {config.label}</span>
    </button>
  )
}
