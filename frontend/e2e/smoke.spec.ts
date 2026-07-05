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

test('scheduled messages page loads', async ({ page }) => {
  await page.goto('/scheduled-messages')
  await expect(page.locator('main h1')).toContainText('Scheduled Messages')
})

test('cc bridge page loads', async ({ page }) => {
  await page.goto('/cc-bridge')
  await expect(page.locator('main h1')).toContainText('Agent Bridge')
})
