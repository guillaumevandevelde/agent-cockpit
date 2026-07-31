# Headless MCP-config startup detection design

## Context

Claude Code 2.1.219 added `mcp_server_errors` to the headless `stream-json` `system/init` event. Invalid MCP entries are skipped while the CLI continues running. Cockpit's headless transport currently schedules the subprocess and immediately returns `status: started`, before the init event is consumed. A later event-consumer failure is only logged and therefore bypasses the dispatcher's claim compensation and board-visible dispatch-failure path.

A Claude Code 2.1.220 probe verified that the field is an array of objects. For an invalid stdio entry without `command`, the event contains:

```json
{
  "mcp_server_errors": [
    {
      "name": "broken",
      "type": "invalid_config",
      "message": "Skipped — invalid MCP server config for \"broken\": command: expected string, received undefined"
    }
  ]
}
```

An unknown server transport uses `type: "unknown_type"`. The field is absent on a clean init. A whitespace-only command is a connection failure in `mcp_servers`, not a skipped config entry, and is outside this card's scope.

## Design

The headless runner validates the raw `system/init` payload before mapping it to the provider-neutral structured event schema. A non-empty `mcp_server_errors` list raises a typed startup exception whose text preserves each entry's name, type, and message.

The headless transport exposes a startup readiness handshake. It starts the durable runner task, then waits until either:

1. the runner consumes a clean init event and signals readiness; or
2. the runner exits or raises before readiness.

Only the first outcome returns `status: started`. The second propagates the original startup exception. The runner's existing tailer/process race then terminates and reaps the subprocess, removes its pidfile and registry entry, and releases the external session slot.

Because the dispatch transport protocol is currently synchronous, `_run_card` will accept either a normal transport result or an awaitable and await only the latter. Existing worktree, sandcastle, resume, and test transports remain unchanged. The headless startup exception therefore enters the existing `_run_card` exception path, which releases the claim, increments `dispatch_failures`, moves the card back to its source column, and after the configured threshold posts the board-visible `[dispatch-failure]` comment including the last error.

## Testing

Add a fake Claude CLI fixture that emits a verified-shape init event with `mcp_server_errors` and remains alive briefly. Assert that headless startup raises rather than returning success, the exception includes the skipped entry details, the subprocess is reaped, and the headless registry is clean. Existing dispatch failure tests cover the compensating board path; add or extend a focused dispatch test only if awaitable transport propagation is not otherwise pinned.

## Scope

This change detects validation-skipped MCP config entries in the headless transport. It does not add tmux pane-warning parsing, classify ordinary MCP connection failures in `mcp_servers`, change MCP configuration, or alter frontend behavior.
