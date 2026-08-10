import { useState } from 'react'
import { ChevronDown, ChevronRight, Bot, MessageSquare } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

interface Props {
  parent_tool_use_id: string
  role: 'assistant' | 'thought' | string
  text: string
  original_size?: number
  truncated?: boolean
}

/** Collapsible nested card for a subagent text/thinking frame.
 *
 * Acceptance criterion: ``SubagentMessage`` renders as a collapsible nested
 * card under the spawning assistant message; **collapsed by default**, so
 * the outer agent's stream stays uncluttered until the operator clicks.
 *
 * The frame is rendered with a distinct color + an indent so the visual
 * nesting is obvious — operator lanes that opted into
 * ``CLAUDE_CODE_FORWARD_SUBAGENT_TEXT=1`` see a tree of subagent
 * reasoning below the parent tool_use, not a flat timeline.
 *
 * When the backend applied the 4 KiB trim, the block surfaces a
 * ``(…N bytes truncated…)`` indicator + the original byte count from
 * ``original_size``. Frames >64 KiB are dropped at the consumer and never
 * reach this point.
 */
export function SubagentMessageBlock({ parent_tool_use_id, role, text, original_size, truncated }: Props) {
  const [open, setOpen] = useState(false)
  const isThought = role === 'thought'
  const Icon = isThought ? Bot : MessageSquare
  const preview = text.length > 80 ? text.slice(0, 80) + '…' : text

  return (
    <div
      className={`ml-4 border-l-2 ${
        isThought
          ? 'border-amber-500/40 bg-amber-50/5'
          : 'border-indigo-500/40 bg-indigo-50/5'
      } rounded-md p-2`}
      data-testid="subagent-message-block"
      data-parent-tool-use-id={parent_tool_use_id}
    >
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setOpen((prev) => !prev)}
        className="flex items-center gap-1 px-1 py-0 h-auto w-full justify-start"
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        <Icon className={`h-3 w-3 ${isThought ? 'text-amber-700' : 'text-indigo-700'}`} />
        <span className="text-xs font-semibold">
          Subagent {isThought ? 'thinking' : 'message'}
        </span>
        <Badge variant="outline" className="text-[10px] px-1 py-0">
          {parent_tool_use_id}
        </Badge>
        {!open && (
          <span className="text-xs text-muted-foreground italic truncate max-w-[200px]">
            {preview}
          </span>
        )}
      </Button>

      {open && (
        <div className="mt-2 ml-4 text-xs whitespace-pre-wrap break-words">
          {isThought ? (
            <span className="text-amber-900">{text}</span>
          ) : (
            <span className="text-indigo-900">{text}</span>
          )}
          {truncated && original_size !== undefined && (
            <div className="mt-1 text-[10px] text-muted-foreground italic">
              (…{original_size - text.length} bytes truncated…)
            </div>
          )}
        </div>
      )}
    </div>
  )
}
