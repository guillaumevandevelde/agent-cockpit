# Claude Cockpit Rebrand Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebrand the fork from "Claude Deck" to "Claude Cockpit" — orange `#D97757` accent on the existing black/grey scale, the LobeHub Claude Code "beast" SVG logo, new tagline, removal of all live "claude-deck" product references, and a prominent upstream Credits section.

**Architecture:** Pure rebrand. Swap one CSS accent hue (green `142°` → orange `15°`), vendor a single MIT-licensed SVG, change user-facing strings, and rename the product across live config/docs. No logic, schema, or layout changes. Historical/dated docs and upstream repo URLs are intentionally preserved.

**Tech Stack:** React 19 + Vite + TailwindCSS/shadcn (CSS HSL custom properties), VitePress docs, Docker Compose. Frontend has no unit-test harness (per CLAUDE.md), so verification is via `npm run lint`, `npm run build`, targeted `grep` audits, and Playwright visual check.

---

### Task 1: Vendor the Claude Code "beast" logo & clean old favicons

**Files:**
- Create: `frontend/public/claude-cockpit-logo.svg`
- Delete (after Task 3 wiring, in this task just identify): old PNG logos/favicons in `frontend/public/`

- [ ] **Step 1: Create the SVG logo file**

Write `frontend/public/claude-cockpit-logo.svg` with exactly this content (LobeHub `@lobehub/icons` `claudecode-color.svg`, MIT; baked-in fill is our brand orange `#D97757`):

```svg
<svg height="1em" style="flex:none;line-height:1" viewBox="0 0 24 24" width="1em" xmlns="http://www.w3.org/2000/svg"><title>Claude Cockpit</title><path clip-rule="evenodd" d="M20.998 10.949H24v3.102h-3v3.028h-1.487V20H18v-2.921h-1.487V20H15v-2.921H9V20H7.488v-2.921H6V20H4.487v-2.921H3V14.05H0V10.95h3V5h17.998v5.949zM6 10.949h1.488V8.102H6v2.847zm10.51 0H18V8.102h-1.49v2.847z" fill="#D97757" fill-rule="evenodd"></path></svg>
```

- [ ] **Step 2: Inventory old brand assets**

Run: `ls frontend/public/`
Expected: see `logo-light.png`, `logo-dark.png`, `favicon-16.png`, `favicon-32.png`, `apple-touch-icon.png` (exact set may vary). Note which exist — they are deleted in Step 3 (references are removed in Task 3).

- [ ] **Step 3: Delete old logo/favicon PNGs**

Run (only for files that exist):
```bash
git rm -f frontend/public/logo-light.png frontend/public/logo-dark.png \
  frontend/public/favicon-16.png frontend/public/favicon-32.png \
  frontend/public/apple-touch-icon.png
```

- [ ] **Step 4: Commit**

```bash
git add frontend/public/claude-cockpit-logo.svg
git commit -m "feat(brand): add Claude Code beast SVG logo, remove old PNG assets"
```

---

### Task 2: Recolor the theme (green → orange) in `index.css`

**Files:**
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Light-mode primary/accent/ring**

In the `:root` block, replace the green values. Change `--primary: 142 60% 35%;` → `--primary: 15 63% 46%;`. Change `--accent: 142 60% 35%;` → `--accent: 15 63% 46%;`. Change `--ring: 142 60% 35%;` → `--ring: 15 63% 46%;`. Update the `/* Primary - Matrix green ... */` and `/* Accent - Matching green */` comments to `/* Primary - Claude orange */` and `/* Accent - Claude orange */`.

- [ ] **Step 2: Light-mode chart-1**

Change `--chart-1: 142 60% 40%;` → `--chart-1: 15 63% 50%;`. Leave `--chart-2..5` unchanged. Leave `--success: 142 60% 35%;` UNCHANGED (semantic green).

- [ ] **Step 3: Dark-mode primary/accent/ring**

In the `.dark` block, change `--primary: 142 70% 45%;` → `--primary: 15 63% 60%;`. Change `--primary-foreground: 0 0% 6%;` → `--primary-foreground: 0 0% 10%;`. Change `--accent: 142 70% 45%;` → `--accent: 15 63% 60%;`. Change `--ring: 142 70% 45%;` → `--ring: 15 63% 60%;`. Update the green comments to "Claude orange".

- [ ] **Step 4: Dark-mode chart-1**

Change `--chart-1: 142 70% 50%;` → `--chart-1: 15 63% 60%;`. Leave `--success: 142 70% 45%;` UNCHANGED.

- [ ] **Step 5: Verify no green accent remains**

Run: `grep -n "142 " frontend/src/index.css`
Expected: only the two `--success` lines (and any `--success`-adjacent comment) remain on hue `142`. No `--primary`, `--accent`, `--ring`, or `--chart-1` line matches.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/index.css
git commit -m "feat(brand): recolor accent from matrix green to Claude orange"
```

---

### Task 3: Rebrand the app shell (header, footer, index.html)

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/src/components/layout/Header.tsx`
- Modify: `frontend/src/components/layout/Footer.tsx`

- [ ] **Step 1: index.html — title + favicons**

In `frontend/index.html`, replace the three favicon `<link>` lines:
```html
    <link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png" />
    <link rel="icon" type="image/png" sizes="16x16" href="/favicon-16.png" />
    <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png" />
```
with a single line:
```html
    <link rel="icon" type="image/svg+xml" href="/claude-cockpit-logo.svg" />
```
And change `<title>Claude Deck</title>` → `<title>Claude Cockpit</title>`.

- [ ] **Step 2: Header.tsx — logo, name, tagline**

In `frontend/src/components/layout/Header.tsx`, replace the `<img>` block:
```tsx
          <img
            src={theme === "light" ? "/logo-light.png" : "/logo-dark.png"}
            alt="Claude Deck"
            className="h-10 w-10"
          />
```
with:
```tsx
          <img
            src="/claude-cockpit-logo.svg"
            alt="Claude Cockpit"
            className="h-10 w-10"
          />
```
Change the `<h1>` text `Claude Deck` → `Claude Cockpit`. Change the tagline `Your local agent command centre` → `Mission control for your local agents`.

- [ ] **Step 3: Remove now-unused `theme` if needed**

The `theme` from `useTheme()` was only used for the logo `src`. Check the rest of `Header.tsx` — if `theme` is no longer referenced, remove the `const { theme } = useTheme();` line and the `useTheme` import to satisfy `noUnusedLocals`. If `theme` is still used elsewhere, leave it.

- [ ] **Step 4: Footer.tsx — product name**

In `frontend/src/components/layout/Footer.tsx`, change `Claude Deck v{APP_VERSION}` → `Claude Cockpit v{APP_VERSION}`. LEAVE the `href="https://github.com/adrirubio/claude-deck"` unchanged (intentional upstream attribution).

- [ ] **Step 5: Lint**

Run: `cd frontend && npm run lint`
Expected: PASS, no unused-variable errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/index.html frontend/src/components/layout/Header.tsx frontend/src/components/layout/Footer.tsx
git commit -m "feat(brand): rebrand app shell to Claude Cockpit (logo, title, tagline)"
```

---

### Task 4: Remaining frontend string references

**Files:**
- Modify: `frontend/src/contexts/ProviderContext.tsx:16`
- Modify: `frontend/src/features/config/settings/cards/ManagedPolicyCard.tsx:9`

- [ ] **Step 1: Provider storage key**

In `ProviderContext.tsx`, change:
```ts
const STORAGE_KEY = 'claude-deck:selected-provider'
```
to:
```ts
const STORAGE_KEY = 'claude-cockpit:selected-provider'
```

- [ ] **Step 2: ManagedPolicyCard comment**

In `ManagedPolicyCard.tsx`, change the comment text `Claude Deck is not a policy authoring tool` → `Claude Cockpit is not a policy authoring tool`.

- [ ] **Step 3: Verify frontend is clean**

Run: `grep -rIin "claude.deck\|claudedeck" frontend/src frontend/index.html`
Expected: exactly ONE match — the `adrirubio/claude-deck` URL in `Footer.tsx`.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/contexts/ProviderContext.tsx frontend/src/features/config/settings/cards/ManagedPolicyCard.tsx
git commit -m "refactor(brand): rename remaining frontend claude-deck references"
```

---

### Task 5: Root config, scripts, and metadata

**Files:**
- Modify: `docker-compose.yml`, `.github/workflows/release.yml`, `scripts/install.sh`, `scripts/deploy-docs.sh`, `backend/pyproject.toml`, `.gitignore`

- [ ] **Step 1: docker-compose service name**

In `docker-compose.yml`, change the service key `claude-deck:` → `claude-cockpit:`. Then check the rest of the file for `container_name`, `image`, or other `claude-deck` strings (run `grep -n "claude.deck\|claudedeck" docker-compose.yml`) and rename each occurrence of the product name to `claude-cockpit` (keep any upstream URL unchanged).

- [ ] **Step 2: release.yml artifact names**

In `.github/workflows/release.yml`, rename the four artifact strings `claude-deck-frontend-v...` → `claude-cockpit-frontend-v...` (lines producing/listing the `.zip` and `.tar.gz`). Use:
```bash
sed -i 's/claude-deck-frontend/claude-cockpit-frontend/g' .github/workflows/release.yml
```

- [ ] **Step 3: scripts**

Run `grep -n "claude.deck\|claudedeck\|Claude Deck" scripts/install.sh scripts/deploy-docs.sh`. For `scripts/deploy-docs.sh`, rename the sibling path `../claude-deck-website` → `../claude-cockpit-website` (both the `WEBSITE_DIR` line and the error message). For `scripts/install.sh`, replace any `Claude Deck` display string → `Claude Cockpit`.

- [ ] **Step 4: backend description + .gitignore**

In `backend/pyproject.toml`, change `description = "Backend API for Claude Deck"` → `description = "Backend API for Claude Cockpit"`. In `.gitignore`, run `grep -n "claude.deck\|claudedeck" .gitignore` and rename any product-name entry to `claude-cockpit` (keep generic patterns).

- [ ] **Step 5: Verify**

Run: `grep -rIin "claude.deck\|claudedeck\|Claude Deck" docker-compose.yml .github/workflows/release.yml scripts backend/pyproject.toml .gitignore`
Expected: no remaining product-name matches (any survivor must be an `adrirubio/claude-deck` upstream URL).

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml .github/workflows/release.yml scripts backend/pyproject.toml .gitignore
git commit -m "refactor(brand): rename claude-deck to claude-cockpit in config and scripts"
```

---

### Task 6: README — rename + prominent upstream Credits

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`, `CONTRIBUTING.md`, `CHANGELOG.md`

- [ ] **Step 1: README product name + website**

In `README.md`, change the `# Claude Deck` H1 → `# Claude Cockpit`. Remove the `**Website**: [claudedeck.org](https://claudedeck.org)` line (no cockpit site yet). Replace remaining `Claude Deck` display strings in body prose → `Claude Cockpit` (run `grep -n "Claude Deck" README.md` and edit each; keep any `adrirubio/claude-deck` URL).

- [ ] **Step 2: Add Credits section near the top of README**

Immediately after the opening paragraph (before `## Why This Exists`), insert:

```markdown
## Credits — Forked from claude-deck

Claude Cockpit is a fork of [**claude-deck**](https://github.com/adrirubio/claude-deck)
by Adrian Rubio-Punal and Juan A. Rubio, used under the MIT License. Their original
copyright and license are retained in [`LICENSE`](./LICENSE). Claude Cockpit adds a
scheduled-messages feature (timer/cron → tmux injection) on top of their work.
```

- [ ] **Step 3: CLAUDE.md, CONTRIBUTING.md, CHANGELOG.md**

For each, run `grep -n "Claude Deck" <file>` and replace display-name occurrences → `Claude Cockpit`. In `CLAUDE.md`, the top fork note already references Cockpit; update the lower "# Claude Deck" original-doc heading and body references. Keep `adrirubio/claude-deck` URLs and the LICENSE copyright names untouched.

- [ ] **Step 4: Verify LICENSE untouched**

Run: `git diff --name-only | grep -x LICENSE`
Expected: NO output (LICENSE must not appear in the diff).

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md CONTRIBUTING.md CHANGELOG.md
git commit -m "docs(brand): rename to Claude Cockpit, add upstream Credits section"
```

---

### Task 7: Live VitePress docs

**Files:**
- Modify: `docs/.vitepress/config.ts`, `docs/.vitepress/theme/custom.css`, `docs/index.md`, `docs/package.json`, `docs/changelog.md`, and all live pages under `docs/guide/`, `docs/features/`, `docs/api/`

- [ ] **Step 1: docs package + vitepress display strings**

In `docs/package.json`, change `"name": "claude-deck-docs"` → `"name": "claude-cockpit-docs"`. In `docs/.vitepress/config.ts`, replace `Claude Deck` display strings (site `title`, `description`, nav text) → `Claude Cockpit`. LEAVE the three `adrirubio/claude-deck` URLs (changelog link, github social link, edit-link pattern) unchanged — there is no cockpit repo URL yet.

- [ ] **Step 2: Bulk-rename display name in live docs prose**

Run (scoped to live docs only, excludes historical `docs/plans`, `docs/superpowers`, `docs/cockpit`):
```bash
grep -rIl "Claude Deck" docs/guide docs/features docs/api docs/index.md docs/changelog.md docs/.vitepress/theme/custom.css \
  | xargs sed -i 's/Claude Deck/Claude Cockpit/g'
```

- [ ] **Step 3: Handle lowercase product slugs in live docs (not URLs)**

Run: `grep -rIn "claude-deck\|claudedeck" docs/guide docs/features docs/api docs/index.md docs/changelog.md docs/.vitepress`
For each hit: if it is part of an `adrirubio/claude-deck` URL, LEAVE it. Otherwise (e.g. a package name, a CLI/path example using the product slug) replace with `claude-cockpit`.

- [ ] **Step 4: Verify live docs**

Run: `grep -rIn "Claude Deck" docs/guide docs/features docs/api docs/index.md docs/changelog.md`
Expected: no matches.

- [ ] **Step 5: Build docs (if buildable)**

Run: `cd docs && npm run build 2>/dev/null || echo "docs build skipped (deps not installed)"`
Expected: PASS, or a clean skip message if VitePress deps aren't installed.

- [ ] **Step 6: Commit**

```bash
git add docs/.vitepress docs/index.md docs/package.json docs/changelog.md docs/guide docs/features docs/api
git commit -m "docs(brand): rebrand live VitePress docs to Claude Cockpit"
```

---

### Task 8: Full verification (build + visual)

**Files:** none (verification only)

- [ ] **Step 1: Frontend lint + build**

Run: `cd frontend && npm run lint && npm run build`
Expected: lint PASS; build succeeds, `frontend/dist` produced.

- [ ] **Step 2: Global product-name audit**

Run:
```bash
grep -rIin "Claude Deck\|claudedeck" --exclude-dir=node_modules --exclude-dir=venv --exclude-dir=.git --exclude-dir=dist \
  -- . | grep -viE "docs/plans|docs/superpowers/specs|\.playwright-mcp" | grep -vi "adrirubio/claude-deck"
```
Expected: NO output (every remaining "claude-deck" is either an upstream URL, in a historical/dated doc, or in a playwright snapshot).

- [ ] **Step 3: Visual check (Playwright)**

Start the app (`docker compose up -d` or `./scripts/dev.sh`), open `http://localhost:8000` (or `:5173`). Confirm with Playwright: the header shows the orange beast logo + "Claude Cockpit" + "Mission control for your local agents"; primary buttons/links/active-card hover borders are orange; the active-sessions badge is still green. Toggle dark mode and re-check. Capture `cockpit-rebrand-light.png` and `cockpit-rebrand-dark.png`.

- [ ] **Step 4: Final commit (screenshots optional, gitignored if large)**

```bash
git add -A
git commit -m "chore(brand): verify Claude Cockpit rebrand (build + visual)" || echo "nothing to commit"
```

---

## Notes for the implementer

- **Never touch `LICENSE`** — MIT requires the upstream copyright stay intact.
- **Keep every `github.com/adrirubio/claude-deck` URL** — it is upstream attribution, not a product reference.
- **Do not rewrite** `docs/plans/**`, `docs/superpowers/specs/**`, or `.playwright-mcp/*.yml` — historical record.
- `--success` and the hardcoded `bg-green-500` active badge stay GREEN by design (status ≠ brand).
