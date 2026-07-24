# Security Policy

## Reporting a Vulnerability

Please **do not open a public GitHub issue** for security vulnerabilities.

Instead, report privately via [GitHub Security Advisories](https://github.com/guillaumevandevelde/claude-cockpit/security/advisories/new) for this repository. This opens a private discussion with the maintainers before any details become public.

Include, where possible:

- A description of the vulnerability and its potential impact
- Steps to reproduce (or a proof-of-concept)
- The affected version (see [`VERSION`](./VERSION)) and environment (OS, browser)

You can expect an initial response within **5 business days**. We'll work with you to confirm the issue, assess severity, and agree on a disclosure timeline before any public write-up.

## Impact Model

Agent Cockpit is a **local-only** tool (see the [Trust Model](./README.md#trust-model) in the README): no cloud backend, no telemetry, no hosted service. It runs on your machine and reads/writes your real Claude Code and Codex CLI configuration files.

As a result, a vulnerability here typically affects **the local machine running Agent Cockpit** (e.g. arbitrary file read/write beyond intended scope, path traversal, or code execution via the local backend/frontend) rather than a hosted service or other users' data. Please describe the local impact you found — that's still very much worth reporting.

The surface here is wider than a typical local config editor, because Agent Cockpit also **dispatches its own AI sessions against your projects** — see [Trust Model](#trust-model) below for who/what is trusted, and [By Design (Not a Defect)](#by-design-not-a-defect) for what is deliberately not a bug.

## Supported Versions

Agent Cockpit does not yet maintain multiple release branches. Security fixes are applied to the latest version on `master`; please make sure you can reproduce the issue there before reporting.

## Automated Scanning

This repository runs [gitleaks](https://github.com/gitleaks/gitleaks) (secret scanning) and [Semgrep](https://semgrep.dev/) (static analysis) on every push and pull request via [`security.yml`](./.github/workflows/security.yml). These catch classes of issues automatically, but manual reports for anything they miss are welcome and appreciated.

## Trust Model

Agent Cockpit is a local tool, but the local boundary includes an **autonomous agent** that can execute commands, edit files, and spawn further processes with your real user privileges. The distinction between "trusted" and "untrusted" data below is the part that matters most for this project.

### What's trusted

- **You, the local user, and your operating system.** Anything your shell, your user account, and your own Git history can do, an Agent Cockpit-dispatched session can do too — because that's the privilege set it runs as. Multi-user isolation is not in scope; if you let someone else log into your account while a dispatch loop is running, that's on you.
- **Configuration files you created yourself** under `~/.claude`, `~/.codex`, or in your project's `.claude/` / `.mcp.json` / `CLAUDE.md`. Agent Cockpit's editor is read/write against these paths by design.
- **Backups that you, or a tool you ran, exported to a chosen location.** The [Backup & Restore](./README.md#features) feature writes to a path you select.

### What's untrusted

Treat all of the following as **untrusted input** — i.e. data you would not let an arbitrary executable on your machine act on without a sanity check:

- **Kanban card text — title, description, acceptance criteria, comments, attached plan/scenario/spec/notes deliverables, and any other `deliverable` payload.** When the auto-dispatcher spawns a session, it builds the `claude` / `codex` command line and appends the card's prompt as a positional argument (`backend/app/services/agentic_cli/claude_code.py`, `codex_cli.py`, `open_code.py`, `mimo_code.py`, `copilot_cli.py`). The card **is** the prompt. A board imported from someone else, a card moved between projects, or a description rewritten by a tool you didn't write is functionally equivalent to copy-pasting an unknown shell instruction into `bash`.
- **Plan and spec attachments** (everything attached under the analyst- or designer-led `plan` / `spec` / `plan_ref` deliverable kinds). Same status: they are injected into the dispatched session.
- **`CLAUDE.md` / `AGENTS.md` / `.claude/` and `.mcp.json` files inside a project you didn't author.** Cockpit reads them and exposes them in the editor UI; their *contents* become part of the agent context the next time that project is dispatched.
- **Output and tool-call payloads from other agent sessions visible in [Session Transcripts](./README.md#features).** If you paste transcript content back into another card or project, you've carried the trust boundary with it — don't.
- **Anything fetched from the [MCP Registry](https://registry.modelcontextprotocol.io) or [skills.sh](https://skills.sh).** Browsing is read-only, but installing is privileged — same posture as `apt install` from an unverified PPA.

### What a dispatched session is

A dispatched session is a child process of the same user account that started the Agent Cockpit backend. It runs against a project that Agent Cockpit has either **registered** (one of your projects, with a security profile you can inspect) or that was just discovered. It can:

- Read and write any file the user can read or write.
- Execute any binary on `PATH` (including `bash`, `git`, `curl`, `ssh`).
- Open outbound network connections (subject to transport-level constraints described below).

It is **not sandboxed when no security-profile row exists or when the project uses the `meta` risk class**. In both cases the dispatcher falls back to host-worktree transport with permission skipping enabled. A newly registered project remains in that permissive fallback until its profile is materialised (for example by opening the security-profile UI or REST endpoint) or explicit per-project overrides are set. See [By Design (Not a Defect)](#by-design-not-a-defect) and [Honest Note on Controls We Do Not Yet Have](#honest-note-on-controls-we-do-not-yet-have).

## By Design (Not a Defect)

These are deliberate, load-bearing design choices. Reporting one of them as a vulnerability will be closed as not applicable — please open a Discussion first if you think a tradeoff here is wrong.

- **Skip-permissions dispatch (`--dangerously-skip-permissions`).** When the resolved permission mode for a card's project is "skip", the dispatcher appends `--dangerously-skip-permissions` to the spawn command line (`backend/app/services/agentic_cli/claude_code.py`), and the child `claude` / `codex` process is expected to act on every tool call without a confirmation prompt. This is what makes unattended dispatch across a kanban board workable; without it, the dispatcher would stall on an unanswerable prompt. The permission-skip *only* applies to the spawned CLI for the lifetime of one session — it does not change Agent Cockpit's own backend or frontend, and it does not persist into other projects. The toggle that decides per-project whether skip is on lives in the security profile (`backend/app/models/security_profile.py`) and is audited by [`scripts/check-kanban-meta-security-conflicts.sh`](./scripts/check-kanban-meta-security-conflicts.sh), which compares the per-project `KanbanMeta` override against what the project's `risk_class` would dictate and flags any disagreement.
- **Spawned agent's shell and filesystem access.** A dispatched session can run `bash`, `git`, read your `~/.ssh`, write to your home directory, and `curl` outbound. That is the point: it's the same shell you would open yourself. Agent Cockpit is the *operator* of that process, not an isolation layer between you and it.
- **Plaintext configuration on disk.** Claude Code / Codex CLI store their configuration as JSON / TOML files on disk. Agent Cockpit reads and writes those files directly. Anyone who can read your user account can read those files; if that bothers you, restrict the user account, not the app.
- **No human-in-the-loop on auto-dispatch.** The kanban dispatcher claims and spawns cards on its own when auto-dispatch is enabled for a project. This is the feature; the human-in-the-loop escape hatches are [Impediment](./docs/cockpit/kanban-conventions.md) (a reporter pauses auto-dispatch on a per-card basis) and the per-project `KanbanMeta: autodispatch:<project_key>` row, which removes the project from the auto-dispatch set when set to `"0"` (`backend/app/kanban/dispatch.py`). There is no global kill switch on this branch; the closest is the per-card `MAX_DISPATCH_FAILURES` retry-circuit-breaker in the same file, which marks a repeatedly-failing card `Impediment` automatically.
- **Sandbox / container isolation only when a persisted security profile asks for it.** When a persisted `ProjectSecurityProfile` classifies a project as `product-staging`, `product-prod`, or `untrusted`, the dispatcher derives the Sandcastle container transport and enforced permissions from that `risk_class`, unless explicit `KanbanMeta` overrides win. A persisted `meta` profile runs directly on the host in a git worktree. **A registered project with no profile row also follows that permissive host-worktree + skip-permissions fallback**; the model defaults do not protect it until the REST/UI path has materialised a profile row. Host execution for an explicit `meta` profile is by design; the missing-profile fallback is a known gap listed below.

## Honest Note on Controls We Do Not Yet Have

Reporting a "missing" feature below will be closed unless the report includes a concrete exploit that current code does not block. We would rather list the gaps here than imply protections that aren't there.

### In place

- **Worktree isolation** (`backend/app/kanban/dispatch.py` + `git worktree`): dispatched sessions for the default `worktree` transport each get their own git worktree on the host, so concurrent cards do not stomp on each other's branches.
- **`ProjectSecurityProfile` table** (`backend/app/models/security_profile.py`): per-project `risk_class`, `default_transport`, `default_skip_permissions`, `network_policy`, `resource_quota`. The REST + UI editor that reads and materialises these rows is documented in [`docs/cockpit/platform-als-app-factory.md`](./docs/cockpit/platform-als-app-factory.md) facet D.
- **Skip-permissions vs. profile audit** ([`scripts/check-kanban-meta-security-conflicts.sh`](./scripts/check-kanban-meta-security-conflicts.sh)): flags any `KanbanMeta` row (`skip_permissions:<project_key>` or `transport:<project_key>`) whose value disagrees with the `risk_class`-derived default for the same project. Read-only; does not auto-reclassify.
- **`gitleaks` + `Semgrep` CI** (see [Automated Scanning](#automated-scanning)) catches leaked secrets and a class of static-analysis issues on every push.
- **Local-only: no telemetry, no cloud backend, no account.** The README [Trust Model](./README.md#trust-model) section's four points are accurate as written.

### Not in place (yet)

- **No safe default before a security-profile row exists.** The dispatcher reads profiles without creating them. If a registered project has no persisted `ProjectSecurityProfile` row, risk-class resolution returns no profile and deliberately keeps the historical host-worktree transport with permission skipping enabled. The model's `product-staging` / Sandcastle defaults apply only after the REST/UI profile path has materialised a row. Check a newly registered project's security profile before its first dispatch.
- **Risk-class-driven dispatch defaults are advisory only.** The `_skip_permissions_for_risk_class` / `_transport_for_risk_class` defaults exist in `backend/app/kanban/dispatch.py` and are honoured only when no `KanbanMeta` override is present for the project. A `KanbanMeta: skip_permissions:<project_key>=1` row overrides the profile regardless of `risk_class`; the audit script (above) surfaces the disagreement but does not correct it. This is documented as SecurityProfileService follow-up #12 — out of scope for this document.
- **No OS-keychain storage for secrets.** Agent-coded secrets live in config files; rotate and back them up like the rest of your filesystem.
- **No agent-side approval prompt before each tool call.** That is what skip-permissions turns off (above). Re-enabling it is per-project and is the documented way to make a single project more cautious.
- **No "Workspace Trust" gate.** Agent Cockpit does not require a project to be marked "trusted" before the first dispatch. A persisted security profile and explicit per-project overrides can constrain later dispatches, but neither is guaranteed to exist for a freshly registered project.
- **No automatic prompt-injection scanning of card text.** Card text is treated as instructions, not as data, throughout the dispatcher. If you want defensive scanning, it is your responsibility today.

## In-Scope vs. Out-of-Scope for Reports

To save you time and ours, this is what we will and will not triage from `security-advisories`.

**In scope** — please report any of these:

- Arbitrary file read/write outside an Agent Cockpit user's intentional action (path traversal, IDOR, CSRF-like cross-user issues given the single-user product — see "out of scope" — N/A in practice but listed for completeness).
- Code execution by **the Agent Cockpit backend or frontend**, or by a dispatched session, that bypasses the per-project profile / transport decision.
- Privileged actions taken by a dispatched session that the active `risk_class` for that project should have blocked (i.e. a transport / skip-permission leak across `risk_class` boundaries).
- A `KanbanMeta` override that the audit script missed, or an audit script that fails to run when the CI is fully green.
- A documented "honest note" control that turned out to be wrong or absent at runtime.
- A gitleaks / Semgrep misconfiguration that lets a known-bad pattern reach `master`.

**Out of scope** — these are by design or by limitation; please Discussion-them, don't report:

- "The agent ran `rm -rf ~` and I didn't approve it": covered by [By Design](#by-design-not-a-defect) — that is the shell, operating as you, on a card you (or your dispatcher, on your behalf) decided to dispatch.
- "I imported a board from someone I don't trust and it ran": covered by [Trust Model — What's Untrusted](#whats-untrusted) — kanban card text is untrusted input by definition; a board from an untrusted source is treated identically.
- "I want a Workspace Trust gate / OS-keychain storage / prompt-injection scanner — feature request": covered by [Honest Note on Controls We Do Not Yet Have](#honest-note-on-controls-we-do-not-yet-have) — those are known gaps on the follow-up roadmap.
- "The Claude / Codex CLI has a vulnerability": please report it upstream; Agent Cockpit is a client of those CLIs, not their maintainer.
- "I'm running Agent Cockpit as root / as a shared account / inside a container I don't control": Agent Cockpit assumes a single trusted user on a trusted machine; privilege escalation via the OS is not a Cockpit-layer vulnerability.
