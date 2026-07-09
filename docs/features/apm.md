# APM (Agent Package Manager)

Project-scoped dependency management for APM modules — install, list, add, remove, and sync.

## Overview

APM is the package manager for the Agent ecosystem. The APM page surfaces the current project's APM state: whether `apm.yml` is present, declared dependencies with versions, and the set of installed modules in the project tree. You can add and remove dependencies, install with the frozen flag for CI-safe runs, and sync a dependency set from one project to another.

The page reads three endpoints in parallel:

- `/apm/status` — overall state, presence of `apm.yml`, install state
- `/apm/deps` — declared dependencies from `apm.yml`
- `/apm/modules` — currently installed APM modules on disk

## How to Use

### Status Card

The top card summarizes APM state for the active project:

| Field | Meaning |
|-------|---------|
| `apm.yml present` | Whether `apm.yml` exists at the project root |
| `dependencies` | Count of declared deps |
| `installed modules` | Count of modules on disk |
| `in sync` | `true` when `apm.yml` matches installed |

### Adding a Dependency

Click **"Add"** to open the add dialog:

1. **Name** — module name (e.g. `code-review`)
2. **Source** — module source URL or registry path
3. Click **Save** — backend edits `apm.yml` and triggers install

### Removing a Dependency

On any dependency row, click the trash icon. The row is removed from `apm.yml` and uninstalled.

### Installing Dependencies

Two install modes:

- **Install** — runs `apm install` for the project
- **Install (frozen)** — runs `apm install --frozen` for deterministic, CI-safe installs

A progress indicator shows while install runs.

### Syncing Between Projects

Click **"Sync"** to copy a dependency set from a source project to a target project. The dialog asks for the target project path; the source is the active project. Useful for promoting a new dep from a sandbox repo to a production one.

## Configuration

APM state lives in the project tree:

| Path | Purpose |
|------|---------|
| `apm.yml` | Declared dependency list |
| `.apm/modules/` | Installed module tree |

The Cockpit backend shells out to the `apm` CLI for install and module enumeration; it never edits the project tree directly outside of `apm.yml` and `.apm/`.

## Tips

- Use **frozen install** for CI — it fails loudly if `apm.yml` doesn't match the lock state, which is what you want in a pipeline.
- **Sync** is one-way (source → target) — it does not merge dep sets; it replaces. Make sure the target is the one you want to overwrite.
- An empty dependency list is valid — many projects have no APM deps at all.

## See also

- [Kanban](./kanban.md) — APM modules can be wired into the agent persona that runs kanban cards