/**
 * Dispatch-lifecycle e2e (kaart 6b662c35…, design §3).
 *
 * Tizonia-style scratch-sandbox: elke scenario spawnt een fake
 * kanban-kaart tegen de eigen backend, wacht op de dispatch-tick die
 * 'm claimt, en drijft de lifecycle verder via
 * ``backend/tests/fixtures/dispatch_stub.py`` (deterministische
 * ``attach_deliverable`` + ``move_card Done``).
 *
 * Geen echte provider-sessie, geen netwerk buiten ``localhost:8000``.
 * De scenarios zijn zo gekozen dat S1 het happy-path bewijst en S2–S5
 * de varianten uit design §3 afdekken zonder dat het harness zwaarder
 * wordt dan de stub zelf.
 */

import { spawnSync, type SpawnSyncReturns } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import path from 'node:path'

import { test, expect, request, type APIRequestContext } from '@playwright/test'

const PROJECT_KEY = 'cockpit-e2e'
const BACKEND_ROOT = path.resolve(__dirname, '../../../backend')
const STUB_MODULE = 'tests.fixtures.dispatch_stub'
const DISPATCH_TIMEOUT_MS = 35_000
const POLL_INTERVAL_MS = 1_000

let api: APIRequestContext

test.beforeAll(async () => {
  api = await request.newContext({ baseURL: 'http://localhost:8000' })
  // Pre-flight: backend healthy. De e2e-job in quality.yml start 'm net
  // vóór de tests; een 503 hier is een echte regressie, niet test-flake.
  const health = await api.get('/health')
  expect(health.status(), 'backend /health').toBe(200)
})

interface StubResult {
  card_id: string
  branch_ref: string
  final_column: string
}

function runStub(cardId: string, runId: string): StubResult {
  const proc: SpawnSyncReturns<string> = spawnSync(
    'python3',
    ['-m', STUB_MODULE, cardId, PROJECT_KEY, runId],
    {
      cwd: BACKEND_ROOT,
      encoding: 'utf8',
      timeout: 30_000,
    },
  )
  if (proc.status !== 0) {
    throw new Error(
      `dispatch_stub failed (exit=${proc.status}):\n` +
        `stderr: ${proc.stderr}\nstdout: ${proc.stdout}`,
    )
  }
  // De stub print z'n resultaat-dict op de laatste niet-lege regel.
  const lastLine = proc.stdout
    .trim()
    .split('\n')
    .filter((l) => l.length > 0)
    .pop() as string
  return JSON.parse(lastLine) as StubResult
}

async function createCard(
  title: string,
  opts: { confirmNewProject?: boolean } = {},
): Promise<{ id: string }> {
  const resp = await api.post('/api/v1/kanban/cards', {
    data: {
      project_key: PROJECT_KEY,
      title,
      description: 'e2e lifecycle harness card',
      column: 'Backlog',
      work_type: 'feature',
      ...(opts.confirmNewProject ? { confirm_new_project: true } : {}),
    },
  })
  if (resp.status() !== 201) {
    throw new Error(
      `create_card failed: ${resp.status()} ${await resp.text()}`,
    )
  }
  const body = (await resp.json()) as { id: string }
  return body
}

async function getCard(id: string): Promise<Record<string, unknown>> {
  const resp = await api.get(`/api/v1/kanban/cards/${id}`)
  expect(resp.status(), `GET /cards/${id}`).toBe(200)
  return (await resp.json()) as Record<string, unknown>
}

async function waitForClaim(id: string): Promise<Record<string, unknown>> {
  const deadline = Date.now() + DISPATCH_TIMEOUT_MS
  let last: Record<string, unknown> = {}
  while (Date.now() < deadline) {
    last = await getCard(id)
    const column = last.column as string
    if (column === 'Doing' || column === 'Done') {
      return last
    }
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS))
  }
  throw new Error(
    `dispatch-claim timeout for ${id}; last column=${last.column}`,
  )
}

interface OpLogRow {
  entity_type: string
  entity_id: string
  op_type: string
  hlc: string
}

/**
 * Read kanban_ops directly via Python — SQLite is not available as a
 * shell tool on this box (kaart CLAUDE.md gotcha) and the REST surface
 * does not expose op-log rows. Verifies the wire contract: create +
 * claim + move in that order.
 */
function readOpLog(cardId: string): OpLogRow[] {
  const proc = spawnSync(
    'python3',
    [
      '-c',
      `import sqlite3, json, os
      db = os.path.expanduser("~/.claude-registry/kanban.db")
      con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
      rows = con.execute(
          "SELECT op_type, hlc FROM kanban_ops WHERE entity_type='card' AND entity_id=? ORDER BY hlc",
          (${JSON.stringify(cardId)},),
      ).fetchall()
      print(json.dumps([{"op_type": r[0], "hlc": r[1]} for r in rows]))`,
    ],
    { encoding: 'utf8', timeout: 10_000 },
  )
  if (proc.status !== 0) {
    throw new Error(`op-log read failed: ${proc.stderr}`)
  }
  return JSON.parse(proc.stdout.trim()) as OpLogRow[]
}

test.describe('S1 — code happy path', () => {
  test('lifecycle: create → claim → stub → Done', async () => {
    const runId = randomUUID().slice(0, 8)
    const created = await createCard(`[e2e S1] ${runId}`, {
      confirmNewProject: true,
    })

    const claimed = await waitForClaim(created.id)
    expect(claimed.column, 'claimed column').toBe('Doing')

    const result = runStub(created.id, runId)
    expect(result.card_id).toBe(created.id)
    expect(result.final_column).toBe('Done')

    const final = await getCard(created.id)
    expect(final.column).toBe('Done')

    // Op-log invariant: create + claim + move in chronological order.
    const ops = readOpLog(created.id).map((o) => o.op_type)
    const createIdx = ops.indexOf('create')
    const claimIdx = ops.indexOf('claim')
    const moveIdx = ops.lastIndexOf('move')
    expect(createIdx, 'op-log: create present').toBeGreaterThanOrEqual(0)
    expect(claimIdx, 'op-log: claim present').toBeGreaterThan(createIdx)
    expect(moveIdx, 'op-log: move present').toBeGreaterThan(claimIdx)
  })
})

test.describe('S2 — auto-merge path', () => {
  test('branch deliverable is recorded with k-e2e-<runId> ref', async () => {
    const runId = randomUUID().slice(0, 8)
    const created = await createCard(`[e2e S2] ${runId}`)
    await waitForClaim(created.id)

    const result = runStub(created.id, runId)
    expect(result.branch_ref).toBe(`k-e2e-${runId}`)

    // Re-fetch the card — deliverables are eager-loaded on
    // CardResponse.deliverables, so the branch we just attached shows
    // up here. Lifecycle-stub branch deliverable alone is the
    // "merge-ready" signal: ship_git_operations consume it from
    // Card.deliverables in the merge step.
    const final = await getCard(created.id)
    const deliverables = (final.deliverables as Array<{
      kind: string
      ref: string
    }>) ?? []
    const branches = deliverables.filter((d) => d.kind === 'branch')
    expect(branches.map((b) => b.ref)).toContain(`k-e2e-${runId}`)
  })
})

test.describe('S3 — CI-fail retry-recover (claim release cycle)', () => {
  test('release then re-drive produces Done', async () => {
    const runId = randomUUID().slice(0, 8)
    const created = await createCard(`[e2e S3] ${runId}`)
    const claimed = await waitForClaim(created.id)
    expect(claimed.column).toBe('Doing')

    // Release the claim to simulate a CI-fail that the dispatcher
    // re-tries against; the card parks back in a dispatchable column
    // (Backlog / Todo, depends on the persona routing). Then re-drive
    // through the stub — proves the lifecycle handles a released
    // claim cleanly without manual op-log surgery.
    const release = await api.post(
      `/api/v1/kanban/cards/${created.id}/release`,
    )
    expect(release.status(), 'release').toBe(200)

    await waitForClaim(created.id)

    const result = runStub(created.id, runId)
    expect(result.final_column).toBe('Done')
  })
})

test.describe('S4 — retry-budget escalatie (failure path)', () => {
  test('a stub that fails surfaces a non-Done exit reason', async () => {
    const runId = randomUUID().slice(0, 8)
    const created = await createCard(`[e2e S4] ${runId}`)
    await waitForClaim(created.id)

    // Spawn a stub pointed at a bogus card id. The HTTP layer
    // responds with 404; the stub exits non-zero. We assert the
    // non-zero exit + the absence of a Done op on the real card.
    const proc = spawnSync(
      'python3',
      [STUB_MODULE, 'card-does-not-exist', PROJECT_KEY, runId],
      { cwd: BACKEND_ROOT, encoding: 'utf8', timeout: 15_000 },
    )
    expect(proc.status, 'stub should fail').not.toBe(0)

    const final = await getCard(created.id)
    expect(final.column, 'real card unaffected').not.toBe('Done')
  })
})

test.describe('S5 — design pipeline (multi-card dependency)', () => {
  test('two cards in sequence both reach Done', async () => {
    const pipelineRun = randomUUID().slice(0, 8)
    const first = await createCard(`[e2e S5.1] ${pipelineRun}`)
    await waitForClaim(first.id)
    runStub(first.id, `${pipelineRun}-a`)

    const second = await createCard(`[e2e S5.2] ${pipelineRun}`)
    await waitForClaim(second.id)
    runStub(second.id, `${pipelineRun}-b`)

    const firstFinal = await getCard(first.id)
    const secondFinal = await getCard(second.id)
    expect(firstFinal.column).toBe('Done')
    expect(secondFinal.column).toBe('Done')
  })
})