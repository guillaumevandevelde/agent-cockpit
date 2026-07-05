# Code drift detection — design

## Context

Claude Cockpit runs CI today (`quality.yml`: backend ruff+pytest, frontend
lint+test+build, on push/PR to `master`) plus `codeql.yml` (weekly + push/PR)
and Dependabot (npm/pip/github-actions, weekly). The local pre-push gate was
removed 2026-07-05 due to contention on a shared box running many concurrent
dispatched agents (see `265e874`).

Gaps identified:

1. `master` has no merge gate — CI runs *after* code lands, not before.
2. Ruff's `select = ["E9","F63","F7","F82"]` only catches syntax errors — no
   unused imports, no bugbear checks, nothing resembling real lint.
3. No Python type checking (mypy/pyright) despite CLAUDE.md's "type hints
   throughout" convention — nothing verifies it.
4. Frontend has 3 test files across 26 feature directories; functional
   regressions in the UI aren't caught automatically.
5. Backend Pydantic schemas and the 15 hand-maintained frontend TS type files
   can drift apart with nothing to notice.
6. No coverage measurement, no drift visibility over time.
7. `codeql.yml` requires GitHub Advanced Security to produce real results on
   private repos — this repo is private and stays private (it manages
   provider/API credentials via the credentials UI, so going public is out of
   scope for this change). GHAS is a paid feature; `codeql.yml` likely
   produces no usable signal today.

## Decisions made during brainstorming

- **Repo stays private.** Going public for free CodeQL was considered and
  rejected — not worth the exposure risk for a project with a credentials UI.
- **Merge gate: required CI before merge**, not "CI as an after-the-fact
  net". Implemented via GitHub branch protection + the kanban dispatch
  system's *existing* `pull-request` ship mode (already the default —
  `DEFAULT_SHIP_MODE = "pull-request"` in `backend/app/kanban/dispatch.py`),
  extended to auto-merge on green and poll to completion, rather than a new
  local pre-push gate (avoids reintroducing the contention problem that got
  the old gate removed).
- **Stay on GitHub, don't evaluate GitLab further.** Already fully invested in
  GitHub-native tooling (Actions, Dependabot, `claude-code-action`, `gh` CLI
  used throughout scripts/skills). Migration cost has no offsetting benefit.
- **Auto-fix loop reuses the existing OAuth-based `claude.yml`.** It already
  authenticates via `secrets.CLAUDE_CODE_OAUTH_TOKEN` (a Claude subscription
  OAuth token, not a metered API key), so triggering it more often costs
  subscription quota, not API spend. Guardrail: at most one automatic
  `@claude` fix attempt per PR (tracked via a label), to avoid a flaky or
  genuinely-broken PR silently burning quota on repeated retries.
- **CodeQL is replaced, not fixed.** Rather than pay for GHAS, use
  visibility-independent free tools: Bandit, Semgrep CE, Gitleaks.

## Workstreams

### 1. Merge gate

- Enable branch protection on `master`: require the `backend` and `frontend`
  jobs of `quality.yml` to pass before merge.
- Extend the `git-ship` skill's pull-request path
  (`.claude/skills/git-ship/SKILL.md` §4b): after `gh pr create`, run
  `gh pr merge --auto --squash`, then poll `gh pr view --json
  mergeStateStatus,state` until the PR merges or the checks fail. Only move
  the kanban card to `Done` on an actual merge; on failure, call
  `report_impediment` instead. This polling happens inside the dispatched
  agent's own session (which has local network access to the kanban backend),
  so no new infrastructure is needed — GitHub Actions runners never need to
  reach the local backend.
- `direct` ship mode (used for interactive sessions per CLAUDE.md) keeps its
  existing fallback: if `git push origin HEAD:master` is rejected because
  master is now protected, fall back to the `pull-request` path.

### 2. Auto-fix loop on red CI

- New workflow, triggered on `workflow_run` where the triggering workflow is
  `quality.yml` and `conclusion == 'failure'`, scoped to PR branches only.
- If the PR does not already carry an `auto-fix-attempted` label: post a PR
  comment `@claude fix the failing checks` (reusing the existing `claude.yml`
  trigger — no new auth, no new secret) and apply the label.
- If the label is already present, do nothing (one attempt per PR — the
  guardrail agreed on above).

### 3. Backend lint / type-checking

- Widen ruff's `select` in `backend/pyproject.toml` from
  `["E9","F63","F7","F82"]` to a realistic set: `["E","F","I","UP","B","SIM"]`.
  Fix whatever this newly flags as part of the same change (or narrow scope
  further if the violation count is large — see open question below).
- Add `mypy` to `backend/requirements-dev.txt` and to the `backend` job in
  `quality.yml`, run with `continue-on-error: true` initially so existing
  violations don't block merges. Track cleanup separately; flip to blocking
  once clean.

### 4. Frontend test coverage

- Add `vitest run --coverage` to the `frontend` job in `quality.yml`,
  reporting-only (no threshold yet) to establish a baseline.
- Add Playwright and a handful (5-10) of smoke tests covering the
  highest-churn flows: Kanban card move, Scheduled Messages, CC Bridge —
  chosen because CLAUDE.md flags these as the newest/most actively developed
  features and thus most drift-prone.

### 5. Backend/frontend contract drift

- New CI step: start the backend, fetch `/openapi.json`, diff it against a
  committed snapshot (e.g. `backend/openapi.snapshot.json`). Fail with a
  message like "API surface changed — update frontend types and the
  snapshot" if they differ.
- Full codegen (generating the 15 TS type files from the OpenAPI schema
  instead of hand-maintaining them) is out of scope for this change — bigger
  migration, tracked separately if the snapshot-diff approach proves the
  drift is frequent enough to justify it.

### 6. Visibility

- Add a branch-protection / CI-status badge to the README.
- Add a weekly scheduled workflow that posts a "drift report" as a GitHub
  Actions step summary: coverage trend, contract-snapshot diff summary,
  TODO/FIXME count.

### 7. Security scanning without GHAS

- Remove `codeql.yml` (produces no usable signal on a private repo without
  GHAS — a false sense of security is worse than an honest gap).
- Add to the `backend` job: `bandit -r app`.
- Add a `semgrep ci` step using public rulesets (`p/security-audit`,
  `p/python`, `p/typescript`) — Semgrep Community Edition, free regardless of
  repo visibility, no GHAS dependency.
- Add `gitleaks` as a standalone Actions step (secret scanning) — relevant
  given this project's provider-credential UI features.

## Testing

- Each CI change (ruff ruleset widening, mypy, coverage, contract-snapshot,
  bandit/semgrep/gitleaks) should be validated by intentionally introducing
  a violation locally and confirming the relevant CI job fails, then fixing
  it and confirming it passes.
- The merge-gate + auto-merge change should be validated end-to-end on a real
  throwaway PR: confirm a failing PR is blocked from merging, confirm a
  passing PR auto-merges, confirm the auto-fix comment fires exactly once.

## Open questions

- How many violations will the widened ruff `select` set surface today? If
  large, the implementation plan should budget time for a cleanup pass (or
  land the new rules as `continue-on-error` first, same as mypy).
