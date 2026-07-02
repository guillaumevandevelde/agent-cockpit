import { useEffect, useRef, useState } from 'react'
import { Folder, FileText, ChevronUp, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useFileBrowser } from './useFileBrowser'

interface FileBrowserPopoverProps {
  onSelect: (path: string) => void
}

export function FileBrowserPopover({ onSelect }: FileBrowserPopoverProps) {
  const [open, setOpen] = useState(false)
  const { listing, loading, navigate } = useFileBrowser()
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (open && !listing) navigate()
  }, [open, listing, navigate])

  useEffect(() => {
    if (!open) return
    function handleClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open])

  return (
    <div ref={containerRef} className="relative">
      <Button
        variant="ghost"
        size="icon"
        className="h-7 w-7"
        title="Choose file"
        onClick={() => setOpen((v) => !v)}
      >
        <Folder className="h-3.5 w-3.5" />
      </Button>

      {open && (
        <div className="absolute bottom-9 right-0 z-50 w-80 rounded-md border bg-popover shadow-md flex flex-col h-72">
          <div className="flex items-center gap-2 px-3 py-2 border-b bg-muted/50 rounded-t-md">
            {listing?.parent && (
              <button
                className="text-muted-foreground hover:text-foreground shrink-0"
                onClick={() => navigate(listing.parent!)}
                title="Go up"
              >
                <ChevronUp className="h-3.5 w-3.5" />
              </button>
            )}
            <span className="text-xs font-mono text-muted-foreground truncate flex-1" title={listing?.path}>
              {listing?.path ?? '…'}
            </span>
          </div>

          <div className="flex-1 overflow-y-auto">
            {loading && (
              <div className="flex items-center justify-center h-full">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              </div>
            )}
            {!loading && listing?.entries.map((entry) => (
              <button
                key={entry.path}
                className="flex items-center gap-2 w-full px-3 py-1.5 text-sm text-left hover:bg-accent transition-colors"
                onClick={() => {
                  if (entry.is_dir) {
                    navigate(entry.path)
                  } else {
                    onSelect(entry.path)
                    setOpen(false)
                  }
                }}
              >
                {entry.is_dir
                  ? <Folder className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  : <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                }
                <span className="truncate font-mono text-xs">{entry.name}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
