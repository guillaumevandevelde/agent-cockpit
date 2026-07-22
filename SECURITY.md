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

## Supported Versions

Agent Cockpit does not yet maintain multiple release branches. Security fixes are applied to the latest version on `master`; please make sure you can reproduce the issue there before reporting.

## Automated Scanning

This repository runs [gitleaks](https://github.com/gitleaks/gitleaks) (secret scanning) and [Semgrep](https://semgrep.dev/) (static analysis) on every push and pull request via [`security.yml`](./.github/workflows/security.yml). These catch classes of issues automatically, but manual reports for anything they miss are welcome and appreciated.
