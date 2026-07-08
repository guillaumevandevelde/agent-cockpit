# Agent Bridge API

Provider-aware live terminal monitoring for Claude Code and Codex CLI tmux sessions.

## Endpoints

### List Sessions

```http
GET /api/v1/agent-bridge/sessions?provider={provider_id}
```

`provider` is optional. Supported values are `claude-code` and `codex-cli`.

```json
{
  "sessions": [
    {
      "provider": "codex-cli",
      "provider_display_name": "Codex",
      "tmux_target": "repo-1234:0.0",
      "session_name": "repo-1234",
      "window_name": "main",
      "pane_id": "%1",
      "cwd": "/home/user/repo",
      "pid": "12345",
      "status": "active"
    }
  ],
  "count": 1
}
```

### Get Preview

```http
GET /api/v1/agent-bridge/sessions/{target}/preview
```

Returns a captured pane preview.

### Get Terminal Token

```http
GET /api/v1/agent-bridge/token
```

Returns a short-lived one-time token for WebSocket terminal access.

### Attach Terminal

```http
WS /api/v1/agent-bridge/sessions/{target}/terminal?token={token}&mode={mode}
```

`mode` can be `readonly` or `interactive`.

### Image Attachments

Use image attachments to upload a screenshot or mockup to the Claude Cockpit host, then paste a file-path prompt into a live tmux session.

All attachment endpoints require a fresh token from `GET /api/v1/agent-bridge/token` in the `X-Claude-Cockpit-Terminal-Token` header.

```http
POST /api/v1/agent-bridge/sessions/{target}/attachments
Content-Type: multipart/form-data
X-Claude-Cockpit-Terminal-Token: {token}
```

Multipart fields:

- `file`: PNG, JPEG, WebP, or GIF image
- `prompt`: optional prompt template containing `{path}`
- `created_by`: optional source label

```json
{
  "id": 123,
  "target": "repo-1234:0.0",
  "provider": "codex-cli",
  "mime_type": "image/png",
  "size_bytes": 482103,
  "agent_path": "/home/user/.claude-registry/bridge-attachments/repo-1234/2026-06-29/185422-a1b2c3d4.png",
  "prompt_text": "Please inspect this image: /home/user/.claude-registry/bridge-attachments/repo-1234/2026-06-29/185422-a1b2c3d4.png"
}
```

```http
POST /api/v1/agent-bridge/sessions/{target}/attachments/{attachment_id}/paste
X-Claude-Cockpit-Terminal-Token: {token}
```

```json
{
  "submit": false,
  "require_interactive_relay": true
}
```

`submit: true` sends Enter after a short delay. Generated prompt text strips newlines so `submit: false` cannot submit accidentally.

When `require_interactive_relay` is `true`, the backend rejects the paste with `409` unless an active websocket relay for that target exists and is currently interactive. The web UI sets this flag so read-only mode is enforced server-side for UI paste actions.

```http
GET /api/v1/agent-bridge/sessions/{target}/attachments
DELETE /api/v1/agent-bridge/sessions/{target}/attachments/{attachment_id}
```

Attachments are stored by default under `~/.claude-registry/bridge-attachments`. In remote deployments, this path is on the Claude Cockpit host and must be readable by the tmux agent process.

Configuration:

- `BRIDGE_ATTACHMENT_DIR`: host storage directory
- `BRIDGE_ATTACHMENT_AGENT_ROOT`: optional agent-visible root to use in pasted paths
- `BRIDGE_ATTACHMENT_MAX_BYTES`: maximum accepted upload size
- `BRIDGE_ATTACHMENT_RETENTION_DAYS`: retention window for startup cleanup
- `BRIDGE_ATTACHMENT_MAX_PER_SESSION_PER_DAY`: per-session daily upload limit

### Spawn Session

```http
POST /api/v1/agent-bridge/sessions
```

Claude Code example:

```json
{
  "provider": "claude-code",
  "directory": "/home/user/repo",
  "mode": "plain",
  "prompt": "Review the current branch"
}
```

Codex example:

```json
{
  "provider": "codex-cli",
  "directory": "/home/user/repo",
  "mode": "resume",
  "use_last": true,
  "model": "gpt-5.5",
  "approval_policy": "on-request"
}
```

### Delete Session

```http
DELETE /api/v1/agent-bridge/sessions/{target}
```

Kills the tmux session or pane target.

## Legacy Route

`/api/v1/cc-bridge/*` remains for compatibility. New clients should use `/api/v1/agent-bridge/*`.
