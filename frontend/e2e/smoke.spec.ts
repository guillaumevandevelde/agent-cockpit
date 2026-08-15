import { test, expect } from '@playwright/test'

// Every page's content is rendered inside <main> by MainLayout, while the
// shared Header also renders its own top-level <h1>Claude Cockpit</h1> brand
// heading. Scoping to `main h1` (rather than a bare `h1`) avoids Playwright's
// strict-mode violation from matching both headings, and targets the actual
// per-page heading each test intends to assert on.

test('dashboard loads', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('main h1')).toContainText('Dashboard')
})

test('kanban board loads', async ({ page }) => {
  await page.goto('/kanban')
  await expect(page.locator('main h1')).toContainText('Kanban')
})

// The 'scheduled messages page loads' smoke test was removed on 2026-08-15.
// Scheduled-messages was retired by decision on 2026-08-04 (see
// docs/cockpit/scheduled-trigger-consolidatie-decision.md) and the route no
// longer exists in frontend/src, so the test asserted on a page that cannot
// render. It had been failing the e2e gate on master ever since.

test('cc bridge page loads', async ({ page }) => {
  await page.goto('/cc-bridge')
  await expect(page.locator('main h1')).toContainText('Agent Bridge')
})
