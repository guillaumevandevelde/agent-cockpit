import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: 'http://localhost:8000',
  },
  // Dispatch lifecycle + agent-mail scenarios share the live
  // ``kanban.db`` and the backend's dispatch-tick; running them with
  // more than one worker races on the auto-claim path and turns
  // deterministic fixtures into flakes. The smoke spec runs in its
  // own project (parallelism-safe) so it can keep its default
  // worker count. See kaart 6b662c35… / docs/cockpit/
  // e2e-soak-harness-design.md §6.
  projects: [
    {
      name: 'dispatch',
      testMatch: /e2e\/dispatch\/.*\.spec\.ts/,
      use: { ...devices['Desktop Chrome'] },
      workers: 1,
    },
    {
      name: 'smoke',
      testMatch: /e2e\/smoke\.spec\.ts/,
      use: { ...devices['Desktop Chrome'] },
    },
  ],
})
