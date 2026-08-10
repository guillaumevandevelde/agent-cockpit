import { TextBlock } from './blocks/TextBlock'
import { ThinkingBlock } from './blocks/ThinkingBlock'
import { ToolUseBlock } from './blocks/ToolUseBlock'
import { ToolResultBlock } from './blocks/ToolResultBlock'
import { ImageBlock } from './blocks/ImageBlock'
import { SubagentMessageBlock } from './blocks/SubagentMessageBlock'
import type { ContentBlock } from '@/types/sessions'

interface Props {
  block: ContentBlock
}

export function ContentBlockRenderer({ block }: Props) {
  switch (block.type) {
    case 'text':
      return <TextBlock text={block.text || ''} />

    case 'thinking':
      return <ThinkingBlock thinking={block.thinking || ''} />

    case 'tool_use':
      return (
        <ToolUseBlock
          name={block.name || ''}
          id={block.id || ''}
          input={block.input || {}}
        />
      )

    case 'tool_result':
      return (
        <ToolResultBlock
          tool_use_id={block.id || ''}
          content={block.content ?? ''}
          is_error={block.is_error || false}
        />
      )

    case 'image':
      return <ImageBlock source={block.source || {}} />

    case 'subagent_message':
      return (
        <SubagentMessageBlock
          parent_tool_use_id={block.parent_tool_use_id || ''}
          role={block.role || 'assistant'}
          text={block.text || block.thinking || ''}
          original_size={block.original_size}
          truncated={block.truncated}
        />
      )

    default:
      return (
        <div className="text-xs text-muted-foreground">
          Unknown block type: {block.type}
        </div>
      )
  }
}
