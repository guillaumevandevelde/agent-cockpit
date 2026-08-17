/**
 * Agent Mail e2e (kaart 6b662c35…, design §3 — uitbreidingen op de
 * lifecycle-suite).
 *
 * Dekt drie scenario's die de Agent-Mail-bedrading bewijst tegen de
 * eigen backend:
 *
 *   M1 — cross-session registratie: twee sessies met verschillend
 *        ``session_key`` registreren zich en komen beiden in
 *        ``GET /api/v1/agent-mail/team`` terug.
 *   M2 — session-start/session-end lifecycle: hooks registreren een
 *        sessie en markeren 'm offline zodra de sessie eindigt.
 *   M3 — install status: ``GET /api/v1/agent-mail/install/status``
 *        antwoordt met de canonieke shape die de install-snippets
 *        voeden.
 */

import { randomUUID } from 'node:crypto'

import { test, expect, request, type APIRequestContext } from '@playwright/test'

const HOOK_CWD = '/tmp/cockpit-e2e-sandbox'

let api: APIRequestContext

test.beforeAll(async () => {
  api = await request.newContext({ baseURL: 'http://localhost:8000' })
})

interface TeamMember {
  id: number
  role: string | null
  sessions: Array<{ session_key: string; status: string }>
}

async function fetchTeam(): Promise<TeamMember[]> {
  const resp = await api.get('/api/v1/agent-mail/team')
  expect(resp.status(), 'GET /agent-mail/team').toBe(200)
  const body = (await resp.json()) as { members: TeamMember[] }
  return body.members
}

test.describe('M1 — cross-session registration', () => {
  test('two distinct session_keys both land in /team', async () => {
    const sessionA = `cc:${randomUUID()}`
    const sessionB = `codex:${randomUUID()}`

    const a = await api.post('/api/v1/agent-mail/agent/register', {
      data: {
        source: 'e2e',
        cli: 'claude-code',
        cwd: HOOK_CWD,
        session_key: sessionA,
      },
    })
    expect(a.status(), 'register A').toBe(201)

    const b = await api.post('/api/v1/agent-mail/agent/register', {
      data: {
        source: 'e2e',
        cli: 'codex-cli',
        cwd: HOOK_CWD,
        session_key: sessionB,
      },
    })
    expect(b.status(), 'register B').toBe(201)

    const team = await fetchTeam()
    const allSessionKeys = team.flatMap((m) => m.sessions.map((s) => s.session_key))
    expect(allSessionKeys).toContain(sessionA)
    expect(allSessionKeys).toContain(sessionB)
  })
})

test.describe('M2 — session-start → session-end lifecycle', () => {
  test('session-start hook registers a session; session-end marks it offline', async () => {
    const sessionId = randomUUID()
    const sessionKey = `cc:${sessionId}`

    const startResp = await api.post('/api/v1/agent-mail/hooks/session-start', {
      data: {
        provider: 'claude-code',
        session_id: sessionId,
        cwd: HOOK_CWD,
        pid: 99999,
      },
    })
    expect(startResp.status(), 'session-start hook').toBe(200)

    const teamAfterStart = await fetchTeam()
    const keysAfterStart = teamAfterStart.flatMap((m) =>
      m.sessions.map((s) => s.session_key),
    )
    expect(keysAfterStart).toContain(sessionKey)

    const endResp = await api.post('/api/v1/agent-mail/hooks/session-end', {
      data: {
        provider: 'claude-code',
        session_id: sessionId,
        cwd: HOOK_CWD,
      },
    })
    expect(endResp.status(), 'session-end hook').toBe(200)

    // session-end transitions the row to offline rather than deleting
    // it — the mailbox is inspectable, so the row stays around for the
    // historian. Sync observed-sessions (which /team?sync=true does by
    // default) reflects the status change.
    const teamAfterEnd = await fetchTeam()
    const offlineSession = teamAfterEnd
      .flatMap((m) => m.sessions)
      .find((s) => s.session_key === sessionKey)
    expect(offlineSession, 'session still in team after end').toBeDefined()
    expect(offlineSession?.status, 'session marked offline').toBe('offline')
  })
})

test.describe('M3 — install status endpoint', () => {
  test('responds with the canonical install-status shape', async () => {
    const resp = await api.get('/api/v1/agent-mail/install/status')
    expect(resp.status()).toBe(200)

    const body = (await resp.json()) as Record<string, unknown>
    // The shape is owned by ``AgentMailInstallStatus``; the e2e gate
    // only asserts the keys the install-snippet consumer actually
    // reads (see ``docs/cockpit/agent-mail-spec.md`` §install).
    expect(body).toHaveProperty('claude_code')
    expect(body).toHaveProperty('codex')
  })
})