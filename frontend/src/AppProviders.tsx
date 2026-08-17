import type { ReactNode } from 'react'
import { ProjectProvider } from '@/contexts/ProjectContext'
import { DashboardProvider } from '@/contexts/DashboardContext'
import { ProviderProvider } from '@/contexts/ProviderContext'
import { AttentionProvider } from '@/contexts/AttentionContext'

/**
 * Single source of truth for the global provider chain. `App.tsx` wraps
 * this around `<BrowserRouter>`; isolated previews wrap a single
 * component around it so a contributor does not have to trial-and-error
 * the six provider wrappers an arbitrary child might read from. The
 * router is intentionally kept out — previews that exercise routing
 * can mount their own `MemoryRouter` without forcing the global one.
 */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ProjectProvider>
      <ProviderProvider>
        <DashboardProvider>
          <AttentionProvider>{children}</AttentionProvider>
        </DashboardProvider>
      </ProviderProvider>
    </ProjectProvider>
  )
}
