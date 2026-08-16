import { useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'
import { Header } from './Header'
import { Sidebar } from './Sidebar'
import { Footer } from './Footer'
import { SidebarContext } from '@/contexts/SidebarContext'
import { useAttentionNotifications } from '@/hooks/useAttentionNotifications'
import { CommandPalette } from '@/features/command-palette/CommandPalette'

export function MainLayout() {
  const [collapsed, setCollapsed] = useState(false)
  const [mobileOpen, setMobileOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  useAttentionNotifications()

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setPaletteOpen((prev) => !prev)
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [])

  return (
    <SidebarContext.Provider value={{ collapsed, setCollapsed, mobileOpen, setMobileOpen }}>
      <div className="flex h-screen flex-col bg-gradient-brand">
        <Header />
        <div className="flex flex-1 overflow-hidden">
          <Sidebar />
          <main className="flex-1 overflow-y-auto p-3 sm:p-6">
            <Outlet />
          </main>
        </div>
        <Footer />
      </div>
      {mobileOpen && (
        <div className="sm:hidden fixed inset-0 z-40">
          <div
            className="absolute inset-0 bg-black/50"
            onClick={() => setMobileOpen(false)}
            aria-hidden="true"
          />
          <div className="relative h-full w-64">
            <Sidebar variant="overlay" onClose={() => setMobileOpen(false)} />
          </div>
        </div>
      )}
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </SidebarContext.Provider>
  )
}
