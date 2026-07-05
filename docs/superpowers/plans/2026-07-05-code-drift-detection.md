# Code Drift Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden CI so drift (style/type rot, contract mismatches between backend and frontend, unnoticed functional regressions, security regressions) is caught before it lands on `master`, without reintroducing the local pre-push-gate contention problem that got the old gate removed (`265e874`).

**Architecture:** `master` gains GitHub branch-protection requiring the existing `quality.yml` `backend`/`frontend` jobs to pass. The kanban dispatch system's existing `pull-request` ship mode (already the default) is extended to auto-merge on green and poll to completion before marking a card `Done`, so the gate costs no new local infrastructure. `quality.yml`'s `backend` job gains a widened ruff ruleset, non-blocking mypy, and a blocking high-severity bandit check. A new non-required `security.yml` workflow adds semgrep + gitleaks and replaces `codeql.yml` (which produces no usable signal on this private repo without a paid GHAS license). A new `check_openapi_snapshot.py` script diffs the FastAPI OpenAPI schema against a committed snapshot to catch backend/frontend contract drift. The `frontend` job gains coverage reporting; a new non-required `e2e` job runs Playwright smoke tests against the backend-served production build. A weekly `drift-report.yml` posts a visibility summary. A new `auto-fix-on-red-ci.yml` workflow comments `@claude fix the failing checks` on a PR the first time `quality.yml` fails for it (reusing the existing OAuth-token-based `claude.yml`), guarded by a label so it only fires once per PR.

**Tech Stack:** GitHub Actions, ruff, mypy, bandit, semgrep (Community Edition), gitleaks, pytest, vitest + `@vitest/coverage-v8`, Playwright, `gh` CLI, FastAPI's built-in OpenAPI generation.

## Global Constraints

- Repo `guillaumevandevelde/claude-cockpit` stays **private** — do not propose or perform any visibility change.
- Do not reintroduce a local pre-push gate. All new checks run in GitHub Actions, never as a local git hook.
- `codeql.yml` is removed, not "fixed" — GHAS-gated code scanning on a private repo without a GHAS license produces no usable signal.
- Newly added strict tooling (mypy, the parts of the widened ruff ruleset that need manual judgment, semgrep, the new `e2e` job) lands **non-blocking first** (`continue-on-error: true` or simply not in branch protection's required-contexts list) whenever the current codebase has too large a backlog to clean up as part of this change. Only bandit's high-severity tier and gitleaks are blocking from day one, because their current violation count is zero/near-zero.
- Backend commands run from `backend/` with the venv active: `source venv/bin/activate`. Frontend commands run from `frontend/`.
- Never commit secrets. Never disable `gitleaks` findings without inspecting them first.
- The auto-fix-on-red-CI workflow (Task 10) must fire **at most once per PR** — it shares the Claude subscription's OAuth quota (`secrets.CLAUDE_CODE_OAUTH_TOKEN`) with interactive usage.
- Existing tests in `backend/tests/test_kanban_dispatch.py::TestBuildShipInstructions` assert exact substrings of `_build_ship_instructions()`'s output for both ship modes (e.g. `"gh pr create --draft" in instructions`, `"gh pr create" not in instructions` for `direct` mode). Task 9 must keep those substrings intact — do not touch `direct` mode's generated text at all.

---

### Task 1: Widen the backend ruff ruleset and land the mechanical fixes

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: ~188 files under `backend/app/` and `backend/tests/` (mechanical fixes only, applied by `ruff --fix`, not hand-edited)

**Interfaces:** none (config + mechanical fix, no new functions).

Today `backend/pyproject.toml`'s `[tool.ruff.lint]` only selects `["E9","F63","F7","F82"]` — syntax-error-level rules only. Widening to a realistic set surfaces 2335 violations; 1909 are mechanically auto-fixable (import sorting, `Optional[X]` → `X | None`, `Dict`/`List` → `dict`/`list`, `datetime.timezone.utc` → `datetime.UTC`, unused imports, redundant `open()` modes). The FastAPI `Depends(...)` pattern in route signatures triggers 66 false-positive `B008` hits (bugbear misreads dependency-injection defaults as mutable-default-argument bugs) — silenced via `flake8-bugbear.extend-immutable-calls`, a documented ruff config knob, not a code change. The remaining ~492 violations (`E501` line-too-long, `B904` raise-without-`from`, `E402`, and a handful of others) need manual, case-by-case review that's out of scope for this change — they're explicitly `ignore`d with a comment so they stay visible as a deliberate, named gap rather than a silent one.

- [ ] **Step 1: Update the ruff config**

Edit `backend/pyproject.toml`. Replace:

```toml
[tool.ruff.lint]
select = ["E9", "F63", "F7", "F82"]
```

with:

```toml
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]
ignore = [
    "E501",   # line-too-long — 300+ pre-existing violations, needs a wrapping pass; not in scope here
    "B904",   # raise-without-from-inside-except — 150+ call sites need case-by-case `from err`/`from None` review
    "E402",   # module-import-not-at-top-of-file — some files intentionally manipulate sys.path first
    "E702",   # multiple-statements-on-one-line-semicolon
    "SIM117", # multiple-with-statements
    "B017",   # assert-raises-exception
    "SIM102", # collapsible-if
    "SIM105", # suppressible-exception
]

[tool.ruff.lint.flake8-bugbear]
# FastAPI's Depends()/Query()/etc. are meant to be called in argument
# defaults — that's how the framework's dependency injection works. Without
# this, B008 misflags every route handler that takes a dependency.
extend-immutable-calls = [
    "fastapi.Depends",
    "fastapi.Query",
    "fastapi.Path",
    "fastapi.Body",
    "fastapi.Header",
    "fastapi.Cookie",
    "fastapi.Form",
    "fastapi.File",
]
```

- [ ] **Step 2: Check the violation count**

```bash
cd backend && source venv/bin/activate && ruff check app tests
```

Expected: reports errors (up to ~2335) — this is expected before the fix step below.

- [ ] **Step 3: Apply the mechanical fixes**

```bash
ruff check app tests --fix --unsafe-fixes
```

Expected output ends with something like `Found 2467 errors (1909 fixed, 558 remaining).` (the exact count may drift slightly as the codebase changes before this task runs, but it should land at roughly 550-560 remaining, all in the `ignore`d categories above plus zero `B008` — confirmed by this repeat check).

- [ ] **Step 4: Confirm zero remaining errors**

```bash
ruff check app tests
```

Expected: `All checks passed!` (the `ignore` list absorbs the ~492 residual manual-review items; `extend-immutable-calls` absorbs the 66 `B008` false positives).

- [ ] **Step 5: Run the full backend test suite to confirm the mechanical fixes didn't change behavior**

```bash
pytest -q
```

Expected: all tests pass, same pass count as before this task (the fixes are syntax-level — import order, type-annotation syntax, `--unsafe-fixes` also covers a couple of additional safe rewrites like f-string cleanup — none change runtime behavior).

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/app backend/tests
git commit -m "chore(backend): widen ruff ruleset, land mechanical fixes"
```

---

### Task 2: Add non-blocking mypy to the backend CI job

**Files:**
- Modify: `backend/requirements-dev.txt`
- Modify: `.github/workflows/quality.yml` (`backend` job)

**Interfaces:** none.

Running `mypy app --ignore-missing-imports` today reports 217 errors across 31 of 163 files (confirmed by a local dry run) — too large to clean up as part of this change, and not something that should silently block every future merge. Land it as a visible, non-blocking CI step now; a follow-up task can clean up the backlog and flip it to blocking later.

- [ ] **Step 1: Add mypy to dev dependencies**

Edit `backend/requirements-dev.txt`. Current content:

```
-r requirements.txt
pytest>=8.0.0
pytest-asyncio>=0.24.0
ruff>=0.11.0
```

New content:

```
-r requirements.txt
pytest>=8.0.0
pytest-asyncio>=0.24.0
ruff>=0.11.0
mypy>=1.13.0
```

- [ ] **Step 2: Add the CI step**

Edit `.github/workflows/quality.yml`. In the `backend` job, after the existing `ruff check app tests` step and before `pytest -q`, add:

```yaml
      - run: mypy app --ignore-missing-imports
        working-directory: backend
        continue-on-error: true
```

- [ ] **Step 3: Verify locally**

```bash
cd backend && source venv/bin/activate && pip install -r requirements-dev.txt && mypy app --ignore-missing-imports
```

Expected: prints errors (around 217) and exits non-zero — this is fine, `continue-on-error: true` means it won't fail the job. Confirm the command itself runs (no crash/config error), which is what matters here.

- [ ] **Step 4: Commit**

```bash
git add backend/requirements-dev.txt .github/workflows/quality.yml
git commit -m "ci(backend): add non-blocking mypy check"
```

---

### Task 3: Replace `codeql.yml` with GHAS-free security scanning

**Files:**
- Modify: `backend/requirements-dev.txt`
- Modify: `backend/app/services/agent_bridge/teams.py:84`
- Modify: `backend/app/services/mcp_server_test_service.py:26`
- Modify: `.github/workflows/quality.yml` (`backend` job)
- Create: `.github/workflows/security.yml`
- Delete: `.github/workflows/codeql.yml`

**Interfaces:** none.

`codeql.yml` requires GitHub Advanced Security to produce results on a private repo, which this repo doesn't have (and isn't getting — repo stays private). Replace it with tools that are free regardless of visibility: **bandit** (Python security linter, already confirmed to run clean at high-severity after two 2-line fixes below), **semgrep** Community Edition (public rulesets, confirmed to work via `pip install semgrep` — no login needed, but registry-based configs are slow, so it runs as a separate non-required job), and **gitleaks** (secret scanning — especially relevant given this project's provider-credential UI features).

Two real bandit findings exist today, both `B324` (MD5 used for a non-security identifier, not a password/token) — the correct fix is `usedforsecurity=False`, not blocking the underlying design:

- [ ] **Step 1: Fix the two high-severity bandit findings**

In `backend/app/services/agent_bridge/teams.py`, line 84, change:

```python
        team_id = "auto-" + hashlib.md5(cwd.encode()).hexdigest()[:8]
```

to:

```python
        team_id = "auto-" + hashlib.md5(cwd.encode(), usedforsecurity=False).hexdigest()[:8]
```

In `backend/app/services/mcp_server_test_service.py`, line 26, change:

```python
        return hashlib.md5(config_str.encode()).hexdigest()
```

to:

```python
        return hashlib.md5(config_str.encode(), usedforsecurity=False).hexdigest()
```

- [ ] **Step 2: Add bandit to dev dependencies**

Edit `backend/requirements-dev.txt`, add a line:

```
bandit>=1.8.0
```

- [ ] **Step 3: Verify bandit is clean at high severity**

```bash
cd backend && source venv/bin/activate && pip install -r requirements-dev.txt && bandit -r app -lll
```

Expected: `No issues identified.` (`-lll` filters to high-severity only; both findings were just fixed in Step 1).

- [ ] **Step 4: Add the blocking bandit step to `quality.yml`**

Edit `.github/workflows/quality.yml`. In the `backend` job, after the `mypy` step added in Task 2, add:

```yaml
      - run: bandit -r app -lll
        working-directory: backend
      - run: bandit -r app -f txt
        working-directory: backend
        continue-on-error: true
```

The first `bandit` step (high-severity only, `-lll`) is blocking — it should stay at zero. The second (full report, all severities) is advisory-only, matching the ~155 low + 3 medium findings that exist today and haven't been triaged.

- [ ] **Step 5: Remove the CodeQL workflow**

```bash
git rm .github/workflows/codeql.yml
```

- [ ] **Step 6: Create the semgrep + gitleaks workflow**

Create `.github/workflows/security.yml`:

```yaml
name: Security

on:
  push:
    branches: [master]
  pull_request:
    branches: [master]

jobs:
  semgrep:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v6
        with:
          python-version: "3.11"
      - run: pip install semgrep
      - run: semgrep scan --config p/security-audit --config p/python --config p/typescript
        continue-on-error: true

  gitleaks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
        with:
          fetch-depth: 0
      - uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

`semgrep` is `continue-on-error: true` — it wasn't feasible to establish a clean baseline locally in a reasonable time (a scoped local run against `backend/app` alone didn't finish inside 120 seconds; registry-based rulesets fetch and compile on every run). It stays advisory until a follow-up task establishes and triages a baseline. `gitleaks` has no historical secrets to triage (this is an existing repo with no known leaked credentials) — it's blocking from day one, since any finding here is a real emergency, not noise.

- [ ] **Step 7: Commit**

```bash
git add backend/requirements-dev.txt backend/app/services/agent_bridge/teams.py \
  backend/app/services/mcp_server_test_service.py .github/workflows/quality.yml \
  .github/workflows/security.yml
git commit -m "security: replace GHAS-gated CodeQL with bandit + semgrep + gitleaks"
```

---

### Task 4: Backend/frontend OpenAPI contract-drift check

**Files:**
- Create: `backend/scripts/check_openapi_snapshot.py`
- Create: `backend/openapi.snapshot.json`
- Modify: `.github/workflows/quality.yml` (`backend` job)

**Interfaces:**
- Produces: `contract_shape(schema: dict) -> dict` and a CLI script runnable as `python scripts/check_openapi_snapshot.py` (exit 0 = matches, exit 1 = drifted) or `python scripts/check_openapi_snapshot.py --update` (rewrites the snapshot).

FastAPI's `app.openapi()` (confirmed locally — returns a dict directly, no server needed) includes `info.version`, which changes on every `bump-version.sh` run independent of the actual API shape. Comparing only `paths` + `components` avoids false "drift" alarms on every version bump.

- [ ] **Step 1: Write the snapshot script**

Create `backend/scripts/check_openapi_snapshot.py`:

```python
"""Diff the live FastAPI OpenAPI schema against a committed snapshot.

Only `paths` and `components` are compared. `info.version` tracks the app
version (bumped independently via scripts/bump-version.sh) and would make
every release look like an API contract change if included.
"""
import json
import sys
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).parent.parent / "openapi.snapshot.json"


def contract_shape(schema: dict) -> dict:
    return {"paths": schema["paths"], "components": schema.get("components", {})}


def current_shape() -> dict:
    from app.main import app

    return contract_shape(app.openapi())


def main() -> int:
    current = current_shape()

    if not SNAPSHOT_PATH.exists():
        SNAPSHOT_PATH.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")
        print(f"Created {SNAPSHOT_PATH}")
        return 0

    snapshot = json.loads(SNAPSHOT_PATH.read_text())
    if current != snapshot:
        print(
            "API surface changed but backend/openapi.snapshot.json was not "
            "updated.\n\n"
            "Run: cd backend && python scripts/check_openapi_snapshot.py --update\n"
            "Then check whether the frontend's hand-maintained TypeScript "
            "types (frontend/src/types/) need matching updates, and commit "
            "both.",
            file=sys.stderr,
        )
        return 1

    print("OpenAPI contract matches snapshot.")
    return 0


if __name__ == "__main__":
    if "--update" in sys.argv:
        SNAPSHOT_PATH.write_text(json.dumps(current_shape(), indent=2, sort_keys=True) + "\n")
        print(f"Updated {SNAPSHOT_PATH}")
        sys.exit(0)
    sys.exit(main())
```

- [ ] **Step 2: Generate the initial snapshot**

```bash
cd backend && source venv/bin/activate && python scripts/check_openapi_snapshot.py --update
```

Expected: `Updated backend/openapi.snapshot.json`, and the file now exists.

- [ ] **Step 3: Confirm the check passes against the snapshot just created**

```bash
python scripts/check_openapi_snapshot.py
```

Expected: `OpenAPI contract matches snapshot.`, exit code 0.

- [ ] **Step 4: Confirm the check fails on real drift**

Temporarily add a throwaway route to verify the script actually detects change — edit `backend/app/main.py`, add after the `/health` endpoint:

```python
@app.get("/__drift_check_probe")
async def _drift_check_probe():
    return {"probe": True}
```

Run:

```bash
python scripts/check_openapi_snapshot.py
```

Expected: exits 1, prints the "API surface changed" message. Then remove the throwaway route (revert the edit to `backend/app/main.py`) and confirm it's clean again:

```bash
python scripts/check_openapi_snapshot.py
```

Expected: `OpenAPI contract matches snapshot.`, exit code 0.

- [ ] **Step 5: Add the CI step**

Edit `.github/workflows/quality.yml`. In the `backend` job, after the `bandit` steps added in Task 3, add:

```yaml
      - run: python scripts/check_openapi_snapshot.py
        working-directory: backend
```

- [ ] **Step 6: Commit**

```bash
git add backend/scripts/check_openapi_snapshot.py backend/openapi.snapshot.json \
  .github/workflows/quality.yml
git commit -m "ci(backend): add OpenAPI contract-drift snapshot check"
```

---

### Task 5: Frontend test coverage reporting

**Files:**
- Modify: `frontend/package.json`
- Modify: `.github/workflows/quality.yml` (`frontend` job)

**Interfaces:** none.

Reporting-only for now (no threshold) — this repo has 3 test files across 26 feature directories, so an enforced minimum would either be trivially low (useless) or immediately fail (blocking unrelated PRs). Establish a visible baseline first; a follow-up can set thresholds once the number is known.

- [ ] **Step 1: Add the coverage devDependency**

```bash
cd frontend && npm install -D @vitest/coverage-v8
```

- [ ] **Step 2: Add a coverage script**

Edit `frontend/package.json`. Current `scripts`:

```json
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "lint": "eslint .",
    "test": "vitest run",
    "preview": "vite preview"
  },
```

New:

```json
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "lint": "eslint .",
    "test": "vitest run",
    "test:coverage": "vitest run --coverage",
    "preview": "vite preview"
  },
```

- [ ] **Step 3: Verify locally**

```bash
npm run test:coverage
```

Expected: runs the existing 3 test files, then prints a coverage summary table (expect low overall percentage — that's the honest baseline, not a bug).

- [ ] **Step 4: Add the CI step**

Edit `.github/workflows/quality.yml`. In the `frontend` job, replace:

```yaml
      - run: npm test
        working-directory: frontend
```

with:

```yaml
      - run: npm run test:coverage
        working-directory: frontend
```

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json .github/workflows/quality.yml
git commit -m "ci(frontend): report test coverage in CI"
```

---

### Task 6: Playwright smoke tests

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/smoke.spec.ts`
- Modify: `.github/workflows/quality.yml` (new `e2e` job)

**Interfaces:** none.

The backend serves the built frontend directly (`backend/app/main.py:150-153` mounts `frontend/dist` as static files under `/` when it exists) — so in CI, `npm run build` + `uvicorn app.main:app --port 8000` is enough; there's no separate frontend server or proxy config to reason about. This confirmed by reading `main.py`'s static-mount logic. Smoke tests check that each of the newest/most actively developed features (per CLAUDE.md: Kanban, Scheduled Messages, CC Bridge) loads without error — not deep interaction flows (e.g. Kanban drag-and-drop), since there are no `data-testid` hooks in this codebase yet and adding them is out of scope for a CI-drift-detection change.

- [ ] **Step 1: Install Playwright**

```bash
cd frontend && npm install -D @playwright/test && npx playwright install --with-deps chromium
```

- [ ] **Step 2: Add the Playwright config**

Create `frontend/playwright.config.ts`:

```typescript
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: 'http://localhost:8000',
  },
})
```

- [ ] **Step 3: Write the smoke tests**

Create `frontend/e2e/smoke.spec.ts`:

```typescript
import { test, expect } from '@playwright/test'

test('dashboard loads', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('h1')).toContainText('Dashboard')
})

test('kanban board loads', async ({ page }) => {
  await page.goto('/kanban')
  await expect(page.locator('h1')).toContainText('Kanban')
})

test('scheduled messages page loads', async ({ page }) => {
  await page.goto('/scheduled-messages')
  await expect(page.locator('h1')).toContainText('Scheduled Messages')
})

test('cc bridge page loads', async ({ page }) => {
  await page.goto('/cc-bridge')
  await expect(page.locator('h1')).toContainText('Agent Bridge')
})
```

- [ ] **Step 4: Add an npm script**

Edit `frontend/package.json`, add to `scripts`:

```json
    "test:e2e": "playwright test",
```

- [ ] **Step 5: Verify locally**

In one terminal:

```bash
cd frontend && npm run build
cd ../backend && source venv/bin/activate && uvicorn app.main:app --port 8000
```

In another terminal, once `curl -s http://localhost:8000/health` returns `{"status":"ok"}`:

```bash
cd frontend && npm run test:e2e
```

Expected: 4 passed. Stop the `uvicorn` process afterward.

- [ ] **Step 6: Add the CI job**

Edit `.github/workflows/quality.yml`, add a new top-level job after `frontend`:

```yaml
  e2e:
    runs-on: ubuntu-latest
    needs: [backend, frontend]
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v6
        with:
          python-version: "3.11"
          cache: pip
          cache-dependency-path: backend/requirements-dev.txt
      - run: pip install -r requirements-dev.txt
        working-directory: backend
      - uses: actions/setup-node@v6
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
        working-directory: frontend
      - run: npm run build
        working-directory: frontend
      - run: npx playwright install --with-deps chromium
        working-directory: frontend
      - name: Start backend
        run: |
          source venv/bin/activate
          nohup uvicorn app.main:app --port 8000 > /tmp/uvicorn.log 2>&1 &
          for i in $(seq 1 30); do
            curl -sf http://localhost:8000/health && break
            sleep 1
          done
        working-directory: backend
      - run: npm run test:e2e
        working-directory: frontend
    continue-on-error: true
```

This job is new and Playwright has zero track record in this repo yet — `continue-on-error: true` at the job level keeps it out of the merge-gate's required contexts (Task 8) until it's been stable for a while. Promote it to required once it's proven not to be flaky.

- [ ] **Step 7: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/playwright.config.ts \
  frontend/e2e .github/workflows/quality.yml
git commit -m "test(frontend): add Playwright smoke tests for kanban/scheduled-messages/cc-bridge"
```

---

### Task 7: Visibility — README badges and weekly drift report

**Files:**
- Modify: `README.md`
- Create: `.github/workflows/drift-report.yml`

**Interfaces:** none.

- [ ] **Step 1: Add status badges to the README**

Edit `README.md`. After the title line (`# Claude Cockpit`), add:

```markdown
[![Quality](https://github.com/guillaumevandevelde/claude-cockpit/actions/workflows/quality.yml/badge.svg)](https://github.com/guillaumevandevelde/claude-cockpit/actions/workflows/quality.yml)
[![Security](https://github.com/guillaumevandevelde/claude-cockpit/actions/workflows/security.yml/badge.svg)](https://github.com/guillaumevandevelde/claude-cockpit/actions/workflows/security.yml)
```

(Badges on a private repo only render status for viewers with repo access — that's fine, this README is read by collaborators, not the public.)

- [ ] **Step 2: Create the weekly drift-report workflow**

Create `.github/workflows/drift-report.yml`:

```yaml
name: Drift Report

on:
  schedule:
    - cron: '0 7 * * 1'  # Monday 7am UTC
  workflow_dispatch: {}

jobs:
  report:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v6
        with:
          python-version: "3.11"
          cache: pip
          cache-dependency-path: backend/requirements-dev.txt
      - run: pip install -r requirements-dev.txt
        working-directory: backend
      - uses: actions/setup-node@v6
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
        working-directory: frontend

      - name: Backend coverage
        run: pytest -q --co -q > /tmp/backend_tests.txt; wc -l < /tmp/backend_tests.txt
        working-directory: backend

      - name: Frontend coverage
        run: npm run test:coverage -- --reporter=json --outputFile=/tmp/frontend_coverage.json || true
        working-directory: frontend

      - name: OpenAPI contract check
        id: contract
        run: |
          if python scripts/check_openapi_snapshot.py; then
            echo "status=matches snapshot" >> "$GITHUB_OUTPUT"
          else
            echo "status=DRIFTED — snapshot needs updating" >> "$GITHUB_OUTPUT"
          fi
        working-directory: backend

      - name: TODO/FIXME count
        id: todos
        run: echo "count=$(grep -rEl 'TODO|FIXME' backend/app frontend/src | wc -l)" >> "$GITHUB_OUTPUT"

      - name: Write summary
        run: |
          {
            echo "## Weekly drift report"
            echo ""
            echo "- OpenAPI contract: ${{ steps.contract.outputs.status }}"
            echo "- Files containing TODO/FIXME: ${{ steps.todos.outputs.count }}"
            echo ""
            echo "See the Quality and Security workflow runs for lint/type/coverage/security detail."
          } >> "$GITHUB_STEP_SUMMARY"
```

- [ ] **Step 3: Verify the workflow syntax**

```bash
cd /mnt/c/develop/claude-cockpit && gh workflow list 2>&1 || echo "gh not authenticated locally — syntax will be validated on first push/dispatch"
```

If `gh` is authenticated, after pushing this branch it can also be dry-run with `gh workflow run drift-report.yml --ref <branch>` and checked with `gh run watch`.

- [ ] **Step 4: Commit**

```bash
git add README.md .github/workflows/drift-report.yml
git commit -m "docs: add CI status badges and a weekly drift report"
```

---

### Task 8: Enable the merge gate (branch protection + auto-merge)

**Files:** none (GitHub repo settings, applied via `gh api` / `gh repo edit`).

**Interfaces:** none.

This is a one-time repo-settings change, not a code change. It requires an authenticated `gh` CLI with admin rights on `guillaumevandevelde/claude-cockpit` — run it from a session where `gh auth status` succeeds (this could not be verified from the planning sandbox, which has no `gh` auth configured).

- [ ] **Step 1: Confirm `gh` is authenticated with the right scope**

```bash
gh auth status
```

Expected: shows a logged-in account with access to `guillaumevandevelde/claude-cockpit`. If this fails, run `gh auth login` first (interactive — the user must do this themselves).

- [ ] **Step 2: Require the `backend` and `frontend` checks on `master`**

```bash
gh api repos/guillaumevandevelde/claude-cockpit/branches/master/protection \
  --method PUT \
  --input - <<'EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["backend", "frontend"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null
}
EOF
```

`enforce_admins: true` means even the repo owner can't bypass the gate with a direct push — matches "verplichte CI-check vóór merge" with no exceptions. `required_pull_request_reviews: null` means no human-approval requirement is added (this project is solo-maintained with agent-authored PRs; the gate is the CI checks, not a review step).

- [ ] **Step 3: Verify the protection rule**

```bash
gh api repos/guillaumevandevelde/claude-cockpit/branches/master/protection --jq '.required_status_checks.contexts, .enforce_admins.enabled'
```

Expected output:

```
[
  "backend",
  "frontend"
]
true
```

- [ ] **Step 4: Enable repo-level auto-merge**

```bash
gh repo edit guillaumevandevelde/claude-cockpit --enable-auto-merge
```

- [ ] **Step 5: Verify with a throwaway PR**

```bash
git checkout -b test-branch-protection master
echo "# throwaway" >> /tmp/throwaway.md
cp /tmp/throwaway.md throwaway.md
git add throwaway.md
git commit -m "test: verify branch protection"
git push -u origin test-branch-protection
gh pr create --base master --title "test: verify branch protection" --body "throwaway, will close" --draft
gh pr ready
gh pr merge --auto --squash
```

Expected: `gh pr merge` reports the merge is queued/pending on required checks, not merged immediately. Once `quality.yml` finishes, confirm via `gh pr view --json state,mergeStateStatus`. Afterward, whether it merged or not:

```bash
gh pr close --delete-branch  # if it didn't auto-merge
```

or, if it already merged, just delete the local throwaway file's trace by reverting on `master` (or leave the harmless throwaway commit — it's a one-line markdown file, but prefer cleaning it up):

```bash
git checkout master && git pull && git rm throwaway.md && git commit -m "chore: remove branch-protection test file" && git push origin master
```

(This final push should itself now go through the same merge gate — if `master` rejects a direct push, follow the `pull-request` ship mode from Task 9 instead.)

---

### Task 9: Auto-merge + poll-to-completion in the pull-request ship mode

**Files:**
- Modify: `backend/app/kanban/dispatch.py:326-341` (`_build_ship_instructions`, `pull-request` branch only)
- Modify: `.claude/skills/git-ship/SKILL.md` (§4b)

**Interfaces:**
- `_build_ship_instructions(ship_mode: str) -> str` — signature unchanged, only the `pull-request` branch's returned text changes. The `direct` branch (lines 311-325) must not be touched — existing tests assert `"gh pr create" not in instructions` for `direct` mode.

Today, `pull-request` mode creates a draft PR and the agent immediately marks the kanban card `Done` — nothing ever merges it, and nothing notices if CI later fails. With Task 8's branch protection in place, this task closes the loop: mark the PR ready, enable auto-merge, then poll until it actually merges (or fails) before deciding the card's fate. Existing tests (`backend/tests/test_kanban_dispatch.py::TestBuildShipInstructions`) assert these exact substrings are present in the `pull-request` branch's output: `"gh pr create --draft"`, `"git push -u origin HEAD"`, `'kind="pr"'`, `"move_card"`, `'"Done"'` — all are preserved below, just with new steps and copy woven in around them.

- [ ] **Step 1: Write the failing test for the new poll step**

Add to `backend/tests/test_kanban_dispatch.py`, inside `class TestBuildShipInstructions`:

```python
    def test_pull_request_mode_polls_for_merge_before_done(self):
        instructions = dispatch._build_ship_instructions("pull-request")
        assert "gh pr ready" in instructions
        assert "gh pr merge --auto --squash" in instructions
        assert "mergeStateStatus" in instructions
        assert "report_impediment" in instructions
```

- [ ] **Step 2: Run it to confirm it fails**

```bash
cd backend && source venv/bin/activate && pytest tests/test_kanban_dispatch.py::TestBuildShipInstructions::test_pull_request_mode_polls_for_merge_before_done -v
```

Expected: FAIL — `AssertionError` (none of these substrings exist yet).

- [ ] **Step 3: Update `_build_ship_instructions`'s `pull-request` branch**

In `backend/app/kanban/dispatch.py`, replace the `else` branch (currently lines 326-341):

```python
    else:
        shipping = (
            "4. **Ship (pull-request mode)** — push your branch and open a draft PR:\n"
            "   ```bash\n"
            "   gh auth status || { echo 'gh unavailable — manual PR needed'; exit 1; }\n"
            "   git push -u origin HEAD\n"
            "   gh pr create --draft --base master --fill\n"
            "   ```\n"
            "   Capture the PR URL from ``gh pr create`` output.\n"
            "   If ``gh`` is unavailable: push the branch, ``comment`` with the "
            "branch name and note that a manual PR is needed.\n"
            "5. **Attach the deliverable** — ``attach_deliverable`` with "
            "``kind=\"pr\"`` and ``ref=<PR-URL>`` (or ``kind=\"branch\"`` if no PR).\n"
            "6. **Move the card to Done** — ``move_card`` to ``\"Done\"``.  "
            "The backend will kill this session and remove the worktree.\n"
        )
```

with:

```python
    else:
        shipping = (
            "4. **Ship (pull-request mode)** — push your branch, open a PR, and "
            "queue it to merge automatically once checks pass:\n"
            "   ```bash\n"
            "   gh auth status || { echo 'gh unavailable — manual PR needed'; exit 1; }\n"
            "   git push -u origin HEAD\n"
            "   gh pr create --draft --base master --fill\n"
            "   gh pr ready\n"
            "   gh pr merge --auto --squash\n"
            "   ```\n"
            "   Capture the PR URL from ``gh pr create`` output.\n"
            "   If ``gh`` is unavailable: push the branch, ``comment`` with the "
            "branch name and note that a manual PR is needed, then stop here — "
            "do not move the card to Done.\n"
            "5. **Wait for the merge gate** — poll until the PR merges or a "
            "check fails; do not skip this, the card's next step depends on it:\n"
            "   ```bash\n"
            "   while true; do\n"
            "     STATE=$(gh pr view --json state,mergeStateStatus "
            "-q '.state + \" \" + .mergeStateStatus')\n"
            "     echo \"PR state: $STATE\"\n"
            "     case \"$STATE\" in\n"
            "       MERGED*) break ;;\n"
            "       *DIRTY*|*BLOCKED*|CLOSED*) echo 'PR did not merge'; exit 1 ;;\n"
            "     esac\n"
            "     sleep 30\n"
            "   done\n"
            "   ```\n"
            "6. **Attach the deliverable** — ``attach_deliverable`` with "
            "``kind=\"pr\"`` and ``ref=<PR-URL>`` (or ``kind=\"branch\"`` if no PR).\n"
            "7. **Move the card** — if the PR merged, ``move_card`` to "
            "``\"Done\"``.  If the poll loop exited because a check failed or the "
            "PR was closed, call ``report_impediment`` instead so a human can "
            "look at it — do not move to Done.\n"
        )
```

- [ ] **Step 4: Run the new test to confirm it passes**

```bash
pytest tests/test_kanban_dispatch.py::TestBuildShipInstructions::test_pull_request_mode_polls_for_merge_before_done -v
```

Expected: PASS.

- [ ] **Step 5: Run the full existing test class to confirm nothing broke**

```bash
pytest tests/test_kanban_dispatch.py::TestBuildShipInstructions tests/test_kanban_dispatch.py::TestBuildCardPromptSessionEnd -v
```

Expected: all PASS — the substrings the old tests check (`gh pr create --draft`, `git push -u origin HEAD`, `kind="pr"`, `move_card`, `"Done"`, and for `direct` mode `"gh pr create" not in instructions`) are all still present/absent exactly as before.

- [ ] **Step 6: Mirror the same change in the `git-ship` skill**

Edit `.claude/skills/git-ship/SKILL.md`. Replace the `## 4b. Ship mode `pull-request` — open a draft PR` section:

```markdown
## 4b. Ship mode `pull-request` — open a draft PR

Only when every test passed. Requires the `gh` CLI authenticated:

```bash
gh auth status            # if this fails, see "gh unavailable" below
git push -u origin HEAD
gh pr create --draft --base master --fill
```

Capture the PR URL from `gh pr create` output, `attach_deliverable` (kind `pr`, ref=`<PR-URL>`),
then `move_card` to `Done`.

**gh unavailable:** if `gh auth status` fails, do not merge. Push the branch
(`git push -u origin HEAD`), `comment` "gh unavailable — manual PR needed from <branch>",
`attach_deliverable` (kind `branch`), and `move_card` to `Done`.
```

with:

```markdown
## 4b. Ship mode `pull-request` — open a PR and wait for it to merge

Only when every test passed. Requires the `gh` CLI authenticated:

```bash
gh auth status            # if this fails, see "gh unavailable" below
git push -u origin HEAD
gh pr create --draft --base master --fill
gh pr ready
gh pr merge --auto --squash
```

Then **poll until the PR actually merges** — `master` requires the `quality.yml`
checks to pass, so this can take a few minutes:

```bash
while true; do
  STATE=$(gh pr view --json state,mergeStateStatus -q '.state + " " + .mergeStateStatus')
  echo "PR state: $STATE"
  case "$STATE" in
    MERGED*) break ;;
    *DIRTY*|*BLOCKED*|CLOSED*) echo 'PR did not merge'; exit 1 ;;
  esac
  sleep 30
done
```

If it merged: `attach_deliverable` (kind `pr`, ref=`<PR-URL>`), then `move_card` to `Done`.

If the loop exited because a check failed or the PR was closed: `attach_deliverable`
(kind `pr`, ref=`<PR-URL>`), then `report_impediment` instead of moving to Done —
a human needs to look at the failing PR.

**gh unavailable:** if `gh auth status` fails, do not merge. Push the branch
(`git push -u origin HEAD`), `comment` "gh unavailable — manual PR needed from <branch>",
`attach_deliverable` (kind `branch`), and `move_card` to `Done`.
```

- [ ] **Step 7: Commit**

```bash
git add backend/tests/test_kanban_dispatch.py backend/app/kanban/dispatch.py \
  .claude/skills/git-ship/SKILL.md
git commit -m "feat(kanban): auto-merge + poll to completion in pull-request ship mode"
```

---

### Task 10: Auto-fix loop on red CI (guarded to one attempt per PR)

**Files:**
- Create: `.github/workflows/auto-fix-on-red-ci.yml`

**Interfaces:** none — reuses the existing `.github/workflows/claude.yml` trigger (`@claude` in a PR comment) and its existing `secrets.CLAUDE_CODE_OAUTH_TOKEN`. No new secrets.

- [ ] **Step 1: Create the workflow**

Create `.github/workflows/auto-fix-on-red-ci.yml`:

```yaml
name: Auto-fix on red CI

on:
  workflow_run:
    workflows: ["Quality"]
    types: [completed]

jobs:
  comment-if-first-failure:
    if: github.event.workflow_run.conclusion == 'failure' && github.event.workflow_run.event == 'pull_request'
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      issues: write
    steps:
      - name: Find the PR for this workflow run
        id: pr
        uses: actions/github-script@v8
        with:
          script: |
            const { data: pulls } = await github.rest.pulls.list({
              owner: context.repo.owner,
              repo: context.repo.repo,
              head: `${context.repo.owner}:${context.payload.workflow_run.head_branch}`,
              state: 'open',
            });
            if (pulls.length === 0) {
              core.setOutput('number', '');
              return;
            }
            core.setOutput('number', String(pulls[0].number));
            core.setOutput('labels', JSON.stringify(pulls[0].labels.map(l => l.name)));

      - name: Comment and label, once only
        if: steps.pr.outputs.number != '' && !contains(steps.pr.outputs.labels, 'auto-fix-attempted')
        uses: actions/github-script@v8
        with:
          script: |
            const prNumber = Number('${{ steps.pr.outputs.number }}');
            await github.rest.issues.createComment({
              owner: context.repo.owner,
              repo: context.repo.repo,
              issue_number: prNumber,
              body: '@claude fix the failing checks',
            });
            await github.rest.issues.addLabels({
              owner: context.repo.owner,
              repo: context.repo.repo,
              issue_number: prNumber,
              labels: ['auto-fix-attempted'],
            });
```

The `auto-fix-attempted` label is the guardrail: once applied, the `if` condition on the second step is false on every subsequent failure for that PR, so it fires at most once, protecting the Claude subscription's shared quota from a PR that keeps failing.

- [ ] **Step 2: Verify the label doesn't need pre-creation**

GitHub creates a label referenced by `addLabels` automatically if it doesn't already exist on the repo — no separate setup step needed. Confirm this is still the GitHub REST API behavior at implementation time (`gh label list` after the first real run should show `auto-fix-attempted` was created).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/auto-fix-on-red-ci.yml
git commit -m "ci: auto-comment @claude on the first red CI run per PR"
```

- [ ] **Step 4: End-to-end validation (manual, after this lands on master)**

Open a throwaway PR with a deliberately failing test, confirm: (a) `quality.yml` fails, (b) this workflow posts the `@claude fix the failing checks` comment and applies the `auto-fix-attempted` label within a minute or two, (c) `claude.yml` picks up the comment and starts a run, (d) pushing another failing commit to the same PR does **not** produce a second comment (label guard holds). Close/delete the throwaway PR and branch afterward.

---

## Self-Review Notes

- **Spec coverage:** all 7 design workstreams map to tasks — merge gate → Tasks 8-9; auto-fix loop → Task 10; ruff/mypy → Tasks 1-2; frontend coverage → Tasks 5-6; contract drift → Task 4; visibility → Task 7; GHAS-free security → Task 3. GitHub-vs-GitLab and the private-repo decision required no task (status quo).
- **Open question resolved:** the design doc flagged "how many violations will the widened ruff select surface" as open — confirmed empirically (2335, 1909 auto-fixable) and resolved via the `ignore` list in Task 1 rather than deferred further.
- **Ordering:** Tasks 1-7 are independent and can ship in any order. Task 9 assumes Task 8's branch protection exists to be meaningful (polling for a merge gate that doesn't exist yet just means it merges on the first check), but doesn't hard-depend on it code-wise — safe to land in either order, though doing Task 8 first avoids a window where PRs auto-merge instantly with no gate. Task 10 assumes PRs exist to comment on (a natural consequence of Task 8/9), but has no code dependency either.
