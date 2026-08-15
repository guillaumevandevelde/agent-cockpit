import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { cn } from '@/lib/utils'
import { MarkdownRenderer } from './MarkdownRenderer'

interface MarkdownPreviewToggleProps {
  value: string
  onChange: (value: string) => void
  placeholder?: string
  minHeight?: string
  disabled?: boolean
  defaultTab?: 'edit' | 'preview'
  /**
   * Opt-in for callers mounted inside a scrollable parent (kanban-kaart
   * 72476d8e…, e.g. CardDrawer's Plan tab body). Drops the preview pane's
   * own `overflow-auto` + fixed `minHeight` so the parent does the
   * scrolling and the preview grows to fit its content. Edit textarea
   * keeps its `minHeight` so editing stays usable. Default `false` keeps
   * the original standalone behaviour for the 8 other consumers
   * (MemoryEditor, AgentEditor, HookEditor, CardEditDialog,
   * MemberEditDialog, MarkdownEditor).
   */
  flexibleHeight?: boolean
}

export function MarkdownPreviewToggle({
  value,
  onChange,
  placeholder = 'Write markdown content...',
  minHeight = '300px',
  disabled = false,
  defaultTab = 'edit',
  flexibleHeight = false,
}: MarkdownPreviewToggleProps) {
  return (
    <Tabs defaultValue={defaultTab} className="w-full">
      <TabsList className="grid w-full grid-cols-2">
        <TabsTrigger value="edit">Edit</TabsTrigger>
        <TabsTrigger value="preview">Preview</TabsTrigger>
      </TabsList>
      <TabsContent value="edit" className="mt-2">
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-full p-4 font-mono text-sm border rounded-md resize-y focus:outline-none focus:ring-2 focus:ring-ring bg-background text-foreground"
          style={{ minHeight }}
          placeholder={placeholder}
          disabled={disabled}
        />
      </TabsContent>
      <TabsContent value="preview" className="mt-2">
        <div
          className={cn(
            "border rounded-md p-4 bg-muted/30",
            !flexibleHeight && "overflow-auto",
          )}
          style={flexibleHeight ? undefined : { minHeight }}
        >
          {value ? (
            <MarkdownRenderer content={value} />
          ) : (
            <p className="text-muted-foreground italic">Nothing to preview</p>
          )}
        </div>
      </TabsContent>
    </Tabs>
  )
}
