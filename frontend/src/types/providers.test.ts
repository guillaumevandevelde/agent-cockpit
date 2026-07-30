// @vitest-environment node
import { describe, expect, it } from 'vitest'
import { VENDOR_IDS, isVendorEqualToCli, isVendorId } from './providers'
import type { AgentSession } from '@/features/cc-bridge/types'
import type { VendorId } from './providers'

// ── Compile-time guards (enforced by `npm run build` which runs
//    `tsc -b`). These are the actual safety net: if a future refactor
//    widens `VendorId = string`, every `@ts-expect-error` below turns
//    into a silent no-op (TS won't error → expect-error must error) and
//    the build fails. ──────────────────────────────────────────────────

const session: AgentSession = {
  cli: 'claude-code',
  cli_display_name: 'Claude Code',
  provider: 'anthropic',
  provider_display_name: 'Anthropic',
  tmux_target: 'main:0.0',
  session_name: 's',
  window_name: 'main',
  pane_id: '%1',
  cwd: '/repo',
  pid: '1',
  status: 'active',
}
// The `provider` slot below is asserted by tsc to be a `VendorId`, which
// means the next-line literal type-narrows correctly — this is the proof
// the union works.
const _vendorSlot: VendorId = session.provider
void _vendorSlot

/**
 * Compile-time guardrails for the VendorId / AgenticCliId split (kaart
 * 4c0c7990…). The card's symptom was a `provider` field that *silently*
 * read undefined because three `filter()` predicates reached for
 * `session.provider` while the wire format had moved to `session.cli`.
 * TypeScript stayed green because both fields were `string`. These
 * assertions pin the new invariants so a future widening of
 * `VendorId = string` regresses to a type error that the compiler
 * surfaces instead of a runtime `undefined`.
 */
describe('VendorId / AgenticCliId split', () => {
  it('VENDOR_IDS enumerates the documented vendor set', () => {
    expect([...VENDOR_IDS]).toEqual([
      'anthropic',
      'bedrock',
      'minimax',
      'anthropic-compatible',
      'opencode',
      'opencode-go',
      'remote',
    ])
  })

  it('isVendorId narrows the closed union', () => {
    expect(isVendorId('anthropic')).toBe(true)
    expect(isVendorId('minimax')).toBe(true)
    expect(isVendorId('claude-code')).toBe(false)
    expect(isVendorId('not-a-vendor')).toBe(false)
  })

  it('isVendorEqualToCli is the documented cross-axis escape hatch', () => {
    expect(isVendorEqualToCli('anthropic', 'claude-code')).toBe(false)
    // Backend fallback: `provider` may equal `cli_id` when classification fails.
    expect(isVendorEqualToCli('claude-code', 'claude-code')).toBe(true)
    expect(isVendorEqualToCli('codex-cli', 'codex-cli')).toBe(true)
    expect(isVendorEqualToCli('claude-code', 'codex-cli')).toBe(false)
  })
})

describe('compile-time guards (enforced by tsc -b)', () => {
  it('blocks the misuse pattern that the original bug shipped', () => {
    // 1. Direct equality against a CLI literal MUST error. This is the
    //    exact mistake that let three `filter()` predicates ship broken.
    //    @ts-expect-error — '"claude-code"' has no overlap with VendorId.
    const _bad1 = session.provider === 'claude-code'
    void _bad1

    // 2. Cross-axis compare MUST error. Without this guardrail the badge
    //    in SessionCard.tsx silently compared two stringly-typed fields.
    //    @ts-expect-error — VendorId and AgenticCliId have no overlap.
    const _bad2 = session.provider === session.cli
    void _bad2

    // 3. Assigning a CLI literal directly into the vendor slot MUST error.
    //    @ts-expect-error — '"codex-cli"' is not assignable to VendorId.
    const _bad3: VendorId = 'codex-cli'
    void _bad3

    // The escape hatch is the only blessed path for cross-axis compare.
    const ok1: boolean = isVendorEqualToCli(session.provider, session.cli)
    expect(ok1).toBe(false)

    // Equality against a valid vendor literal stays allowed.
    const ok2 = session.provider === 'anthropic'
    expect(ok2).toBe(true)

    // String-narrowing assignment stays allowed.
    const ok3: string = session.provider
    expect(typeof ok3).toBe('string')
  })
})