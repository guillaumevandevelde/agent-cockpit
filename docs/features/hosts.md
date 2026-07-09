# Hosts

Registry of remote machines that can run Claude Code and Codex CLI sessions over SSH.

## Overview

The Hosts page lets you register remote machines (alias + hostname + SSH username + key path) that the system can spawn sessions on. Once a host is registered, the CC Bridge can discover agent sessions running on it just like it discovers local panes, and the kanban dispatcher can spawn cards on remote hosts as well as local ones.

Each host card shows:

- **Alias** — friendly name (e.g. `gpu-box`)
- **Connection string** — `user@host:port`
- **SSH key path** — optional key override
- **Status badge** — `online` / `offline` / `unknown`
- Test and delete actions

## How to Use

### Adding a Host

Click **"Add Host"** to open the dialog:

1. **Alias** — short label used in spawns and dropdowns
2. **Hostname** — DNS name or IP
3. **Port** — SSH port (defaults to 22)
4. **Username** — SSH user
5. **SSH key path** — optional path to a non-default private key

Click **Save** to register. The host appears as `unknown` until the first connectivity test.

### Testing Connectivity

Click the **test** icon on any host card to run an SSH reachability check. The result toast shows `✅ <alias> is reachable` or `❌ <alias> is not reachable`, and the status badge updates accordingly.

### Editing a Host

Click any host card to reopen the dialog in edit mode. Changes are saved on submit.

### Deleting a Host

Click the trash icon and confirm. The host is removed from the registry; any sessions that were spawned on it stay running but no longer appear in discovery.

## Status States

| Status | Meaning |
|--------|---------|
| `online` | Most recent connectivity test succeeded |
| `offline` | Most recent connectivity test failed |
| `unknown` | Never tested (just registered) |

Status is **only** updated by explicit test actions — there's no background reachability probe. If you need always-fresh status, click the test icon periodically or wire one into a hook.

## Configuration

Hosts are stored in the Cockpit SQLite (`host_registry` table) — local to this Cockpit instance. They are not synced across devices.

## Tips

- Use a unique alias per machine — the alias is what you reference when spawning on a specific host.
- The **SSH key path** is optional; if omitted, ssh-agent / `~/.ssh/config` handles authentication as normal.
- Discovery on remote hosts requires the agent CLI to actually be running on the remote — the host record itself only opens the SSH connection.
- For local-only setups you don't need this page — the dispatcher uses the local host by default.

## See also

- [CC Bridge](./cc-bridge.md) — discovers sessions across all registered hosts
- [Kanban](./kanban.md) — dispatcher can spawn cards on a chosen host