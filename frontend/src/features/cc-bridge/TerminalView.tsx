import { useRef, useEffect } from 'react'
import { Monitor, Maximize2, Minimize2, X } from 'lucide-react'
import { useTerminal } from './useTerminal'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { AttentionKind } from './attention'

interface TerminalViewProps {
  target: string | null
  fullscreen?: boolean
  onToggleFullscreen?: () => void
  onClose?: () => void
  attention?: AttentionKind | null
}

export function TerminalView({ target, fullscreen, onToggleFullscreen, onClose, attention }: TerminalViewProps) {
  const wrapperRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const { connected, readOnly, setReadOnly, attach, detach, sendText } = useTerminal(containerRef, wrapperRef)

  useEffect(() => {
    if (target) {
      attach(target)
    } else {
      detach()
    }
  }, [target, attach, detach])

  return (
    <div className="flex flex-col h-full">
      <div
        ref={wrapperRef}
        className="flex-1 relative overflow-hidden"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault()
          const file = e.dataTransfer.files[0]
          if (file) sendText(file.name)
        }}
      >
        {!target && (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-muted-foreground bg-background">
            <Monitor className="h-12 w-12 mb-3" />
            <p className="text-sm">Select a session to attach</p>
          </div>
        )}
        <div
          ref={containerRef}
          className={cn(
            'absolute inset-0',
            !target && 'invisible'
          )}
        />
      </div>

      {target && (
        <div className="flex items-center justify-between px-3 py-2 border-t bg-background">
          <div className="flex items-center gap-3">
            {attention && (
              <span
                className={cn(
                  'h-2 w-2 rounded-full shrink-0',
                  attention === 'error' ? 'bg-red-500' : 'bg-yellow-500'
                )}
                title={attention === 'error' ? 'Command failed' : 'Waiting for input'}
              />
            )}
            <div className="flex items-center gap-2 text-sm">
              <button
                className={cn(
                  'px-2 py-0.5 rounded text-xs font-medium transition-colors',
                  readOnly
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:text-foreground'
                )}
                onClick={() => setReadOnly(true)}
              >
                Read-only
              </button>
              <button
                className={cn(
                  'px-2 py-0.5 rounded text-xs font-medium transition-colors',
                  !readOnly
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:text-foreground'
                )}
                onClick={() => setReadOnly(false)}
              >
                Interactive
              </button>
            </div>
            <span className={cn(
              'text-xs',
              connected ? 'text-green-500' : 'text-muted-foreground'
            )}>
              {connected ? 'Connected' : 'Disconnected'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            {onToggleFullscreen && (
              <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onToggleFullscreen} title={fullscreen ? 'Exit fullscreen' : 'Fullscreen'}>
                {fullscreen ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
              </Button>
            )}
            {onClose && (
              <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onClose} title="Close pane">
                <X className="h-3.5 w-3.5" />
              </Button>
            )}
            {connected ? (
              <Button variant="outline" size="sm" onClick={detach}>
                Detach
              </Button>
            ) : (
              <Button variant="outline" size="sm" onClick={() => attach(target)}>
                Attach
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
