# MCP Server

Bearer-token-authed Model Context Protocol endpoint that exposes Cockpit's capabilities to external agents.

## Overview

The MCP Server is a Streamable-HTTP MCP endpoint mounted at `/api/v1/mcp-server`. It serves tools that let Claude Code, Codex CLI, or any MCP-compatible client drive Cockpit from inside a session — reading Agent Mail, querying plans, controlling sessions, and more. Authentication is by per-agent bearer token, issued from the MCP Server page.

The page is the token management UI:

- List existing tokens with name, scope, agent, status, last-used timestamp
- Create new tokens with a chosen scope and optional agent binding
- Revoke tokens
- Inspect the advertised endpoint and tool inventory

## How to Use

### Creating a Token

Click **"Create Token"** to open the dialog:

1. **Name** — friendly label (e.g. `claude-code-laptop`)
2. **Scope** — `read` (read-only) or `read_write` (can mutate)
3. **Agent name** *(optional)* — bind this token to a specific agent persona; the MCP layer enforces that the caller matches

Click **Create** — the full token is shown **once**. Copy it now; it cannot be retrieved later, only revoked and re-issued.

### Revoking a Token

On any active token row, click **Revoke**. The token is marked `revoked_at` and rejects all further requests immediately. Revocation is permanent — there's no un-revoke.

### Endpoint & Tools

The page shows the advertised MCP endpoint (typically `http://localhost:8000/api/v1/mcp-server`) and the current tool inventory. Tool changes are reflected after a backend restart.

## Token Model

| Field | Purpose |
|-------|---------|
| `id` | Internal numeric ID |
| `name` | Friendly label |
| `scope` | `read` / `read_write` |
| `agent_name` | Optional agent persona binding |
| `enabled` | Master toggle (mirrors `revoked_at`) |
| `token_prefix` | First few characters of the token (for identification) |
| `last_used_at` | Timestamp of last successful MCP call |
| `expires_at` | Optional expiry (currently `null` = never) |
| `revoked_at` | Set when revoked |

## Authentication

Clients connect with `Authorization: Bearer <token>` on each MCP request. The token's scope and agent binding are checked on every call:

| Scope | Allowed |
|-------|---------|
| `read` | Tools that don't mutate state |
| `read_write` | All tools, including mutating ones |

If `agent_name` is set, the MCP layer also verifies the caller matches that persona before accepting the call.

## Storage

Tokens and their SHA-256 hashes live in the main Cockpit SQLite (`mcp_access_token` table). Only the hash is stored — the plaintext token is shown exactly once at creation.

## Tips

- **One token per device per agent** — easier to revoke a single leak than to rotate a shared token.
- **`read` scope is enough for many Agent Mail flows** — reserve `read_write` for clients that must mutate state (e.g. sending messages).
- **The advertised endpoint** is what you paste into your MCP client config; it must match the URL the client can actually reach (localhost vs. LAN IP).

## See also

- [Agent Mail](./agent-mail.md) — the main consumer of MCP tokens
- [Kanban](./kanban.md) — MCP health badge surfaces server reachability