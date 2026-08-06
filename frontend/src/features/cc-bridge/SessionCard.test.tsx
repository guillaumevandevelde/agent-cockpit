// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'

import type { CCSession } from './types'
import type { AgenticCliId, VendorId } from '@/types/providers'

// On-demand git status would otherwise hit the live /agent-bridge/.../git-status
// endpoint the moment a card mounts. The card shape is the unit under test; the
// network call is a side-effect we stub here so the test stays in-process.
vi.mock('./api', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return {
    ...actual,
    fetchSessionGitStatus: vi.fn(async () => ({
      is_git_repo: false,
      branch: null,
      detached: false,
      upstream: null,
      ahead: 0,
      behind: 0,
      dirty: false,
    })),
  }
})

const { SessionCard } = await import('./SessionCard')

// Pinned to the live shape returned by /agent-bridge/sessions (kaart 47f07c2…
// pinning request): CLI=claude-code with vendor=minimax — the two panes that
// are actually running on this box at the time of writing.
const baseSession: CCSession = {
  cli: 'claude-code',
  cli_display_name: 'Claude Code',
  provider: 'minimax',
  provider_display_name: 'MiniMax',
  tmux_target: 'cc-bridge:0.0',
  session_name: 'claude-session',
  window_name: '0',
  pane_id: '%0',
  cwd: '/tmp/proj',
  pid: '12345',
  status: 'running',
}

afterEach(() => {
  cleanup()
})

describe('SessionCard CLI/vendor badge contract', () => {
  it('renders the CLI display name as the primary badge', () => {
    render(
      <SessionCard
        session={baseSession}
        gridPosition={null}
        onClick={() => {}}
        onKill={() => {}}
        onRename={async () => {}}
      />,
    )
    expect(screen.getByText('Claude Code')).not.toBeNull()
  })

  it('renders both badges when the vendor differs from the CLI, with "Subscription: …" as the vendor badge title', () => {
    render(
      <SessionCard
        session={baseSession}
        gridPosition={null}
        onClick={() => {}}
        onKill={() => {}}
        onRename={async () => {}}
      />,
    )
    // Primary badge always carries cli_display_name.
    expect(screen.getByText('Claude Code')).not.toBeNull()
    // Secondary badge carries provider_display_name only when axes differ.
    const secondary = screen.getByText('MiniMax')
    expect(secondary).not.toBeNull()
    // Tooltip on the secondary badge exposes "Subscription: …" for hover /
    // screen-reader. The exact "Subscription: <name>" shape is what the
    // product owner expects to read out on focus; pinning the prefix stops
    // a copy change from silently breaking the contract.
    expect(secondary.getAttribute('title')).toBe('Subscription: MiniMax')
  })

  it('renders the kanban-card affordance in the tile body when the session carries a card_id, and clicking it does not toggle the pane', () => {
    // The affordance is a kanban-card navigate button in the body (next
    // to the cwd/branch rows): when the backend enriched the session
    // with a card_id + card_title, the user can read the title and
    // jump from the bridge list straight to /kanban?card=<id> without
    // hovering. The click must not also fire the card's primary
    // `onClick` (which toggles the pane grid), so the handler chain
    // stops propagation. See kanban-kaart 215cd8ea…
    const onOpenCard = vi.fn()
    render(
      <SessionCard
        session={{
          ...baseSession,
          card_id: 'abc123def456',
          card_project_key: 'git:github.com/example/repo',
          card_title: 'Vindbaar maken van de kaart-knop op Agent-Bridge',
        }}
        gridPosition={null}
        onClick={() => {}}
        onKill={() => {}}
        onRename={async () => {}}
        onOpenCard={onOpenCard}
      />,
    )
    const link = screen.getByRole('button', { name: /view kanban card: vindbaar maken/i })
    expect(link).not.toBeNull()
    // The card title is rendered as readable text, not just a generic
    // icon — that's the whole point of the kanban card 215cd8ea… fix.
    expect(link.textContent).toContain('Vindbaar maken van de kaart-knop op Agent-Bridge')
    link.click()
    expect(onOpenCard).toHaveBeenCalledTimes(1)
    expect(onOpenCard).toHaveBeenCalledWith('abc123def456')
  })

  it('falls back to a shortened card id in the body affordance when card_title is empty', () => {
    // The backend coerces the `default=""` on `KanbanCard.title` to "",
    // but a kanban card whose title is genuinely blank should still
    // surface an identifying affordance — the first 8 chars of the id
    // are the next-best option.
    const onOpenCard = vi.fn()
    render(
      <SessionCard
        session={{
          ...baseSession,
          card_id: 'abc123def456',
          card_project_key: 'git:github.com/example/repo',
          card_title: '',
        }}
        gridPosition={null}
        onClick={() => {}}
        onKill={() => {}}
        onRename={async () => {}}
        onOpenCard={onOpenCard}
      />,
    )
    const link = screen.getByRole('button', { name: /view kanban card: #abc123de/i })
    expect(link).not.toBeNull()
    expect(link.textContent).toContain('#abc123de')
  })

  it('omits the kanban-card link when the session has no card_id', () => {
    render(
      <SessionCard
        session={baseSession}
        gridPosition={null}
        onClick={() => {}}
        onKill={() => {}}
        onRename={async () => {}}
      />,
    )
    expect(screen.queryByRole('button', { name: /view kanban card/i })).toBeNull()
  })

  it('omits the secondary badge when the backend reports provider equal to CLI (coalesce)', () => {
    // Codex-on-Anthropic-style scenario the card description calls out:
    // provider == cli means rendering a second badge with the same label is
    // useless noise. The type system treats VendorId and AgenticCliId as
    // disjoint, so this fixture carries an explicit cast — see providers.ts
    // for the documented runtime case (uncertain classification).
    const coalescedSession: CCSession = {
      ...baseSession,
      cli: 'codex-cli' as AgenticCliId,
      cli_display_name: 'Codex',
      provider: 'codex-cli' as unknown as VendorId,
      provider_display_name: 'Codex',
    }

    render(
      <SessionCard
        session={coalescedSession}
        gridPosition={null}
        onClick={() => {}}
        onKill={() => {}}
        onRename={async () => {}}
      />,
    )
    // The primary badge still renders.
    expect(screen.getByText('Codex')).not.toBeNull()
    // No element carries the "Subscription:" tooltip prefix — i.e. the
    // secondary badge did not render, even though provider_display_name
    // happened to be non-empty.
    expect(screen.queryByText('Subscription:')).toBeNull()
  })
})