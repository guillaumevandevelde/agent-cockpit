---
title: "Isolated component preview (light + dark screenshot)"
type: reference
status: active
---

# Isolated component preview (light + dark screenshot)

Use this when you need to browser-verify a frontend change from a
**worktree** (`.claude/worktrees/<branch>/`). The shared dev stack on
`http://localhost:5173` runs from the main checkout
(`/home/vdvgu/claude-cockpit`), so a Playwright run against :5173
measures **master**, not your worktree's diff — even when no other
session holds the ports. Symptoom: drie identieke meetwaardes vóór en
ná je fix die "mijn wijziging doet niets" lijken. Onderliggende regel:
**verifieer nooit op :5173 vanuit een worktree**, ook niet wanneer
`cockpit.sh start` wel wil starten.

De head-versie van deze doc (poort-conflict-only) triggerde ook
wanneer een andere sessie de poorten vasthoudt:

```
Poort(en) al in gebruik: 8000 + 5173
```

Don't wait for the other session, and **don't** use the live project
kanban board (or any other real board data) as a screenshot fixture —
it's shared state other sessions/humans are actively looking at. This
recipe mounts just the changed component in a scratch Vite entry on an
unused port, with no backend and no board data involved.

## 0. Dependencies must already be installed

A fresh worktree has no `frontend/node_modules` (gitignored). If it's
missing, `npx vite` fetches a standalone `vite` package instead of using
the local one, which then fails to resolve `vite.config.ts`'s imports
(`vitest/config`, `@vitejs/plugin-react`). Install first — fast path is
to symlink the main checkout's already-installed `node_modules` when
`frontend/package-lock.json` is unchanged vs `origin/master` (matches
the session-end workflow's frontend gate in `git-ship` step 2):

```bash
cd frontend
if [ -d node_modules ] && [ ! -d node_modules/.bin ]; then
  # partial install from an interrupted `npm ci` — move aside (mv, not rm:
  # `rm` is deny-listed in .claude/settings.json) before symlinking fresh state
  mv node_modules "../node_modules.partial-$(date +%s)"
fi
if [ ! -d node_modules ]; then
  BASE=$(git merge-base HEAD origin/master)
  if git diff --quiet "$BASE" origin/master -- frontend/package-lock.json \
     && [ -d /home/vdvgu/claude-cockpit/frontend/node_modules/.bin ]; then
    ln -s /home/vdvgu/claude-cockpit/frontend/node_modules node_modules
  else
    npm ci
  fi
fi
```

## 1. Scratch entry point (not committed)

Create two throwaway files inside `frontend/` — they never get
committed, and you move them back out when done (step 4).

`frontend/preview.html`:

```html
<!doctype html>
<html>
  <head><meta charset="utf-8" /></head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/preview-entry.tsx"></script>
  </body>
</html>
```

`frontend/src/preview-entry.tsx` — import the **real** component you
changed and wrap it in only the providers it actually needs (check the
component's existing usages for which contexts it reads):

```tsx
import { createRoot } from 'react-dom/client'
import { ThemeProvider } from '@/contexts/ThemeContext'
import './index.css'
// Swap in the component + props under test:
import { CardItem } from '@/features/kanban/components/CardItem'
import type { Card } from '@/features/kanban/types'

const fixtureCard: Card = {
  id: 'preview-1',
  project_key: 'preview',
  title: 'Preview fixture card',
  description: 'Not a real card — only used to render CardItem in isolation.',
  column: 'Todo',
  rank: '1',
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  deliverables: [],
}

const root = createRoot(document.getElementById('root')!)
root.render(
  <ThemeProvider>
    <CardItem card={fixtureCard} onOpen={() => {}} />
  </ThemeProvider>,
)
```

## 2. Start Vite on an alternate port

```bash
cd frontend
npx vite --port 5199 --strictPort &
echo $! > /tmp/preview-vite.pid
timeout 30 bash -c 'until curl -sf http://localhost:5199/preview.html >/dev/null; do sleep 1; done'
```

`--strictPort` makes it fail fast instead of silently picking yet
another port if `5199` is also taken — pick any free port.

## 3. Screenshot with Playwright (light + dark)

Chromium is already installed in this repo for `npm run test:e2e`
(`@playwright/test` in `frontend/package.json`), so no extra install is
needed. This app's dark mode is a `class` on `<html>`
(`darkMode: ["class"]` in `tailwind.config`), not a media query, so
toggle it via `classList` rather than `page.emulateMedia`:

```bash
node - <<'EOF'
const { chromium } = require('@playwright/test')
;(async () => {
  const browser = await chromium.launch()
  const page = await browser.newPage()
  await page.goto('http://localhost:5199/preview.html')
  await page.waitForSelector('#root :first-child')

  await page.evaluate(() => document.documentElement.classList.add('light'))
  await page.screenshot({ path: '/tmp/preview-light.png' })

  await page.evaluate(() => {
    document.documentElement.classList.remove('light')
    document.documentElement.classList.add('dark')
  })
  await page.screenshot({ path: '/tmp/preview-dark.png' })

  await browser.close()
})()
EOF
```

Read `/tmp/preview-light.png` and `/tmp/preview-dark.png` with the
`Read` tool to inspect the render.

Fill every **non-optional** field of the prop type in your fixture
(check the `?` on each field) — a plausible-looking but incomplete
fixture (e.g. an omitted `deliverables: []` on `Card`) fails at
render time with a generic `Cannot read properties of undefined`,
not a type error, since TypeScript checks are stripped by the time
the browser runs the code.

## 4. Clean up

This repo's `.claude/settings.json` denies `Bash(rm:*)`, so `mv` the
scratch files out to any path outside the repo (e.g. your scratchpad
directory) instead of deleting them:

```bash
kill "$(cat /tmp/preview-vite.pid)"
mv frontend/preview.html frontend/src/preview-entry.tsx /path/to/your/scratchpad/
```

Confirm with `git status` that no scratch files remain untracked (or
staged) before committing your actual change.

## Why not the shared dev stack or the live kanban board

- The shared stack (`8000`/`5173`) may be held by another concurrent
  session — waiting on it blocks you for no reason, and restarting it
  out from under that session breaks their work.
- The live kanban board is real, shared data other sessions and the
  human operator are actively watching. Creating throwaway cards on it
  to visually test a UI change pollutes that board and can confuse
  whoever is monitoring it. This recipe never touches the backend or
  board data at all — it mounts the component directly.
