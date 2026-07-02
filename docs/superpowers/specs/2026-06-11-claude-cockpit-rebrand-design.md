# Claude Cockpit — Rebrand Design

**Date:** 2026-06-11
**Status:** Approved design, pending implementation plan

## Goal

Rebrand the fork from **Claude Deck** to **Claude Cockpit**: new name, new look
(Claude Code orange on a black/grey neutral scale), the Claude Code "beast" logo,
and removal of all live "claude-deck" references — while preserving required and
courtesy attribution to the upstream project.

## Context

This repo is an MIT-licensed fork of [`adrirubio/claude-deck`](https://github.com/adrirubio/claude-deck)
(Copyright © 2025 Adrian Rubio-Punal and Juan A. Rubio). MIT permits renaming and
distributing derivative works; the only obligation is retaining the copyright +
license notice. The original logo/marks are *not* covered by the code license, so
we use a maintained MIT-licensed icon set rather than hand-copying a trademark.

## 1. Color system — `frontend/src/index.css`

Replace the "Matrix green" accent (hue `142°`) with Claude's signature orange
`#D97757` = `HSL 15° 63% 60%`. The existing neutral (0%-saturation) black/grey
surface scale stays — it already *is* the requested "black/grey". Orange becomes
the single brand accent.

| Token | Light mode | Dark mode | Notes |
|-------|-----------|-----------|-------|
| `--primary` | `15 63% 46%` | `15 63% 60%` | Light deepened for WCAG AA white-on-button (~4.5:1) |
| `--primary-foreground` | `0 0% 100%` | `0 0% 10%` | Dark text on the bright dark-mode orange (≈6.7:1) |
| `--accent` / `--accent-foreground` | mirror `--primary` | mirror `--primary` | |
| `--ring` | `15 63% 46%` | `15 63% 60%` | Focus ring |
| `--chart-1` | `15 63% 50%` | `15 63% 60%` | Was the brand green |

**Kept semantic (NOT rebranded):** `--destructive` (red), `--warning` (amber),
`--info` (blue), and `--success` (**green**) — because "active session / online"
badges rely on green to convey meaning. Brand color ≠ status colors. The
hardcoded `bg-green-500` active-sessions badge in `Header.tsx` stays.

Update the stale `/* Matrix green */` comments to reflect the orange accent.
The `.bg-gradient-brand` gradient is already neutral grey — no change.

## 2. Logo — Claude Code "beast" (LobeHub icon, MIT)

Source asset: `claudecode-color.svg` from `@lobehub/icons` (MIT,
[lobehub/lobe-icons](https://github.com/lobehub/lobe-icons)). It is the pixelated
Claude Code mark with baked-in fill `#D97757` — identical to our brand orange and
renders the same in light/dark mode.

- Save the SVG to `frontend/public/claude-cockpit-logo.svg`.
- **Header** (`Header.tsx`): replace the `<img src={logo-light/dark.png}>` block
  with an `<img src="/claude-cockpit-logo.svg" alt="Claude Cockpit">` (the SVG is
  self-tinting orange, so the light/dark conditional `src` is removed). Sized
  `h-10 w-10` as today.
- **Favicon** (`index.html`): point `<link rel="icon">` at the same SVG; remove
  the three old PNG favicon `<link>` lines (`favicon-32`, `favicon-16`,
  `apple-touch-icon`). Old logo/favicon PNGs in `frontend/public/` can be deleted.
- Add `@lobehub/icons` is **not** required — we vendor the single SVG file, no new
  npm dependency.

## 3. Name & tagline — user-facing text

- `frontend/index.html` `<title>`: `Claude Deck` → `Claude Cockpit`
- `Header.tsx` `<h1>`: `Claude Deck` → `Claude Cockpit`
- `Header.tsx` tagline: `Your local agent command centre` →
  **`Mission control for your local agents`**
- `Footer.tsx`: `Claude Deck v{APP_VERSION}` → `Claude Cockpit v{APP_VERSION}`;
  keep the GitHub link to upstream `adrirubio/claude-deck` as a small inline
  attribution (consistent with the Credits section).
- `ProviderContext.tsx`: `STORAGE_KEY = 'claude-deck:selected-provider'` →
  `'claude-cockpit:selected-provider'` (one-time reset of the persisted provider
  choice — acceptable).
- `ManagedPolicyCard.tsx`: internal comment "Claude Deck" → "Claude Cockpit".

## 4. File scope — "everything" (live content) + attribution

**Rename across all LIVE content** (`Claude Deck`→`Claude Cockpit`, and
`claude-deck` slugs where they are the product name, not the upstream repo URL):

- Root: `README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `CHANGELOG.md`,
  `docker-compose.yml`, `.gitignore`, `scripts/install.sh`, `scripts/deploy-docs.sh`,
  `.github/workflows/release.yml`, `backend/pyproject.toml` (description line).
- Docs site (live): `docs/.vitepress/config.ts`, `docs/.vitepress/theme/custom.css`,
  `docs/index.md`, `docs/package.json`, and all of `docs/guide/**`,
  `docs/features/**`, `docs/api/**`, `docs/changelog.md`.
- The `claudedeck.org` website link in `README.md` → removed (no cockpit site
  exists yet).

**Deliberately NOT rewritten (historical record):**

- Dated planning docs under `docs/plans/**` and `docs/superpowers/specs/**`
  (except this new spec) — they document work done under the old name; rewriting
  them is noise and slightly revisionist.
- `.playwright-mcp/*.yml` snapshots and `docs/plans/presence/*mockup.html`.
- `docs/cockpit/**` already uses the new name.

**References that intentionally stay "claude-deck":** the upstream repo URL
`github.com/adrirubio/claude-deck` everywhere it appears as a link (footer,
attribution, LICENSE), and the `LICENSE` copyright text.

## 5. Attribution

- `LICENSE`: kept fully intact (MIT requirement).
- `README.md`: add a prominent **"Credits — Forked from claude-deck"** section
  near the top, naming Adrian Rubio-Punal & Juan A. Rubio and linking
  `https://github.com/adrirubio/claude-deck`.

## Out of scope / non-goals

- No new npm dependency (single SVG is vendored).
- No database/schema changes; the `STORAGE_KEY` rename only resets a UI preference.
- No redesign of layout, components, or features — color + name + logo only.
- Historical/dated docs and snapshots are left as a record.

## Verification

- `cd frontend && npm run lint` passes.
- `npm run build` succeeds; app loads with orange theme, beast logo, "Claude
  Cockpit" title/header/tagline in both light and dark mode.
- `grep -rIin "claude.deck\|claudedeck" frontend/src frontend/index.html` returns
  only the intentional upstream-URL attribution in `Footer.tsx`.
- README renders with the Credits section; `LICENSE` unchanged.
