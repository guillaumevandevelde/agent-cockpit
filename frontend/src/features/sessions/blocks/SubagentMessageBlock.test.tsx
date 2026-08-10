// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

import { SubagentMessageBlock } from './SubagentMessageBlock'

afterEach(() => {
  cleanup()
})

describe('SubagentMessageBlock', () => {
  it('renders a collapsible nested card under the parent tool_use_id', () => {
    render(
      <SubagentMessageBlock
        parent_tool_use_id="toolu_subagent_01"
        role="assistant"
        text="subagent reasoning"
      />,
    )
    const block = screen.getByTestId('subagent-message-block')
    expect(block).toBeTruthy()
    expect(block.getAttribute('data-parent-tool-use-id')).toBe('toolu_subagent_01')
    // The badge surfaces the parent id so the operator can correlate the
    // subagent card with the spawning tool_use in the outer agent's stream.
    expect(screen.getByText('toolu_subagent_01')).toBeTruthy()
  })

  it('is collapsed by default — body text hidden until the operator clicks', () => {
    render(
      <SubagentMessageBlock
        parent_tool_use_id="toolu_subagent_01"
        role="assistant"
        text="the answer is 42"
      />,
    )
    // Collapsed body text is hidden — the preview shown by the header is
    // a separate text node (≤80 chars + ellipsis). The full expanded body
    // uses ``whitespace-pre-wrap`` for code/quoted text; we look for the
    // container that has that class to confirm the body is hidden.
    const body = document.querySelector('.whitespace-pre-wrap')
    expect(body).toBeNull()
    // Click the toggle to expand.
    fireEvent.click(screen.getByRole('button'))
    const expandedBody = document.querySelector('.whitespace-pre-wrap')
    expect(expandedBody).toBeTruthy()
    expect(expandedBody?.textContent).toContain('the answer is 42')
  })

  it('surfaces the truncation indicator when the frame was trimmed', () => {
    render(
      <SubagentMessageBlock
        parent_tool_use_id="toolu_subagent_big"
        role="assistant"
        text="small preview"
        original_size={8192}
        truncated
      />,
    )
    fireEvent.click(screen.getByRole('button'))
    expect(screen.getByText(/bytes truncated/)).toBeTruthy()
    // 8192 - 14 chars ≪ 8192 — the assert is on the indicator presence,
    // not the exact subtraction, because the indicator formats the diff
    // for the reader.
  })

  it('distinguishes thought vs. text frames by label', () => {
    const { rerender } = render(
      <SubagentMessageBlock
        parent_tool_use_id="toolu_subagent_thinking"
        role="thought"
        text="pondering"
      />,
    )
    expect(screen.getByText(/Subagent thinking/)).toBeTruthy()

    rerender(
      <SubagentMessageBlock
        parent_tool_use_id="toolu_subagent_message"
        role="assistant"
        text="done"
      />,
    )
    expect(screen.getByText(/Subagent message/)).toBeTruthy()
  })
})
