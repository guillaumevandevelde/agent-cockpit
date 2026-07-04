import { MinimaxCredentialsCard } from './MinimaxCredentialsCard'

export function ProvidersPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Providers</h1>
        <p className="text-sm text-muted-foreground mt-1">
          One-time credentials for platforms Claude Code sessions can launch against.
        </p>
      </div>
      <MinimaxCredentialsCard />
    </div>
  )
}
