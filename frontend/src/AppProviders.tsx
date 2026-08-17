import type { ReactNode } from 'react'
import { ThemeProvider } from '@/contexts/ThemeContext'
import { ProjectProvider } from '@/contexts/ProjectContext'
import { DashboardProvider } from '@/contexts/DashboardContext'
import { ProviderProvider } from '@/contexts/ProviderContext'
import { AttentionProvider } from '@/contexts/AttentionContext'

/**
 * Single source of truth for the global provider chain. `App.tsx` wraps
 * this around `<BrowserRouter>`; isolated previews wrap a single
 * component around it so a contributor does not have to trial-and-error
 * the provider wrappers an arbitrary child might read from. The router
 * is intentionally kept out — previews that exercise routing can mount
 * their own `MemoryRouter` without forcing the global one.
 *
 * `ThemeProvider` lives here (not in `main.tsx`) so the light/dark
 * screenshot flow renders without `useTheme must be used within a
 * ThemeProvider`. See kanban card `d53b0e8b…` for the decision.
 */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider>
      <ProjectProvider>
        <ProviderProvider>
          <DashboardProvider>
            <AttentionProvider>{children}</AttentionProvider>
          </DashboardProvider>
        </ProviderProvider>
      </ProjectProvider>
    </ThemeProvider>
  )
}
