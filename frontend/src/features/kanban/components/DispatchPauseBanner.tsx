import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { kanbanApi } from "../api";
import type { DispatchPauseStatus } from "../types";

const POLL_MS = 30_000;

/**
 * Surfaces the account-wide auto-dispatch pause so a paused board doesn't read
 * as silently stalled: when Claude Code hits its usage limit, every project's
 * auto-dispatch tick is paused until the reset time (see dispatch_pause.py).
 * Polls rather than pushing since a pause lasts hours, not seconds.
 */
export function DispatchPauseBanner() {
  const [status, setStatus] = useState<DispatchPauseStatus | null>(null);

  useEffect(() => {
    let alive = true;
    const check = () => {
      kanbanApi
        .dispatchPause()
        .then((s) => alive && setStatus(s))
        .catch(() => alive && setStatus(null));
    };
    check();
    const id = setInterval(check, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  if (!status?.paused) return null;

  const until = status.paused_until
    ? new Date(status.paused_until).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : null;

  return (
    <div className="flex items-center gap-2 rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
      <AlertTriangle className="h-4 w-4 shrink-0" />
      <span>
        Auto-dispatch paused{until ? ` until ${until}` : ""} — Claude usage limit hit. It
        will resume automatically once the limit resets.
      </span>
    </div>
  );
}
