/**
 * MiMoCode presence plugin — sends session events to Agent Cockpit's
 * presence API so that the Attention notification system works for MiMoCode.
 *
 * Install:  mimo plugin ./plugins/mimo-presence
 * Config:   set PRESENCE_URL env var or it defaults to http://localhost:8000
 */

const PRESENCE_URL = process.env.PRESENCE_URL || "http://localhost:8000";
const EVENTS_ENDPOINT = `${PRESENCE_URL}/api/v1/presence/events`;

let _tmuxPane = null;

async function sendPresenceEvent(payload) {
  try {
    await fetch(EVENTS_ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    // Presence API unreachable — non-fatal
  }
}

function getCwd(input) {
  return input?.cwd || process.cwd();
}

function getTmuxPane() {
  if (_tmuxPane) return _tmuxPane;
  try {
    const { execSync } = require("child_process");
    _tmuxPane = execSync("echo $TMUX_PANE", { encoding: "utf-8" }).trim();
    if (!_tmuxPane) _tmuxPane = null;
  } catch {
    // not in tmux
  }
  return _tmuxPane;
}

/** @type {import("@mimo-ai/plugin").Plugin} */
export default async function presencePlugin(input) {
  const sessionId = input.project?.id || "mimo-unknown";
  const cwd = input.directory || process.cwd();
  const tmuxPane = getTmuxPane();

  return {
    // Generic event passthrough
    event: async ({ event }) => {
      // Forward interesting lifecycle events
      if (event?.type === "session.start") {
        await sendPresenceEvent({
          session_id: sessionId,
          hook_event_name: "SessionStart",
          cwd,
          tmux_pane: tmuxPane,
        });
      } else if (event?.type === "session.end") {
        await sendPresenceEvent({
          session_id: sessionId,
          hook_event_name: "SessionEnd",
          cwd,
          tmux_pane: tmuxPane,
        });
      }
    },

    // Before tool execution → PreToolUse
    "tool.execute.before": async (toolInput, output) => {
      const toolName = toolInput?.tool || "unknown";
      await sendPresenceEvent({
        session_id: sessionId,
        hook_event_name: "PreToolUse",
        tool_name: toolName,
        tool_input: output?.args || {},
        cwd,
        tmux_pane: tmuxPane,
      });
    },

    // After tool execution → PostToolUse
    "tool.execute.after": async (toolInput, output) => {
      const toolName = toolInput?.tool || "unknown";
      await sendPresenceEvent({
        session_id: sessionId,
        hook_event_name: "PostToolUse",
        tool_name: toolName,
        tool_input: toolInput?.args || {},
        tool_result: {
          content: output?.output || "",
          is_error: false,
        },
        cwd,
        tmux_pane: tmuxPane,
      });
    },

    // Actor stopped → Stop (session waiting for input)
    "actor.postStop": async (stopInput) => {
      await sendPresenceEvent({
        session_id: sessionId,
        hook_event_name: "Stop",
        cwd,
        tmux_pane: tmuxPane,
        message: stopInput?.finalText || "Waiting for input",
      });
    },

    // Permission requested → Notification
    "permission.ask": async (permInput, output) => {
      const msg = permInput?.message || permInput?.description || "Permission requested";
      await sendPresenceEvent({
        session_id: sessionId,
        hook_event_name: "Notification",
        message: msg,
        cwd,
        tmux_pane: tmuxPane,
      });
    },

    // User message submitted → UserPromptSubmit
    "chat.message": async (chatInput) => {
      await sendPresenceEvent({
        session_id: sessionId,
        hook_event_name: "UserPromptSubmit",
        user_prompt: chatInput?.message?.content || "",
        cwd,
        tmux_pane: tmuxPane,
      });
    },
  };
}
