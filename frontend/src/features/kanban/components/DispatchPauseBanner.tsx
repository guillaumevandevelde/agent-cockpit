import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { kanbanApi } from "../api";
import { PROVIDER_LABELS, type DispatchPauseStatus } from "../types";

const POLL_MS = 30_000;

// Helper instead of inline `new Date(...)` in the render body — the
// react-compiler ESLint rule rejects impure calls (Date constructor +
// toLocaleTimeString) as JSX/render arguments (see CLAUDE.md Code Style).
// Returns null for an absent / unparseable paused_until so the banner can
// render the per-provider lines without a time suffix when the backend has
// no deadline to report.
function formatTimeOfDay(iso: string | null | undefined): string | null {
  if (!iso) return null;
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/**
 * Surfaces both the account-wide auto-dispatch pause and any active
 * per-provider pauses so a paused board doesn't read as silently stalled:
 * when Claude Code hits its usage limit, every project's auto-dispatch tick
 * is paused until the reset time (see dispatch_pause.py). A per-provider
 * pause (kanban-limit feature) is independent — only the providers whose
 * slot was tripped are paused, and the banner lists them individually.
 * Polls rather than pushing since a pause lasts hours, not seconds.
 */
export function DispatchPauseBanner() {
  const [status, setStatus] = useState<DispatchPauseStatus | null>(null);
  const [clearing, setClearing] = useState(false);

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

  // Older responses omit paused_providers; treat undefined as the empty list
  // so a per-provider-only pause (no global pause yet, or vice-versa) still
  // surfaces the banner instead of being silently dropped.
  const pausedProviders = status?.paused_providers ?? [];
  const showBanner = !!status?.paused || pausedProviders.length > 0;
  if (!showBanner) return null;

  const until = formatTimeOfDay(status?.paused_until);

  const handleResumeNow = async (e: React.MouseEvent) => {
    e.stopPropagation();
    setClearing(true);
    try {
      const r = await kanbanApi.clearDispatchPause();
      if (r.cleared) {
        toast.success(
          "Auto-dispatch resumed — if the underlying usage limit hasn't actually reset yet, dispatch may hit it again right away."
        );
        setStatus(await kanbanApi.dispatchPause());
      } else {
        toast.error("Nothing to clear — auto-dispatch wasn't paused.");
      }
    } catch {
      toast.error("Failed to resume auto-dispatch");
    } finally {
      setClearing(false);
    }
  };

  return (
    <div className="flex items-start gap-2 rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
      <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        {status?.paused && (
          <div>
            Auto-dispatch paused{until ? ` until ${until}` : ""} — Claude usage limit hit.
            It will resume automatically once the limit resets.
          </div>
        )}
        {pausedProviders.map((provider, idx) => {
          // First row in the column needs a separator only when the global
          // pause line is rendering above it. Any later row always gets one.
          const needsSeparator =
            idx > 0 || (idx === 0 && !!status?.paused);
          return (
            <div
              key={provider}
              className={needsSeparator ? "border-t border-amber-500/30 pt-1 mt-1" : undefined}
            >
              Auto-dispatch paused for {PROVIDER_LABELS[provider] ?? provider}
              {until ? ` until ${until}` : ""}.
            </div>
          );
        })}
      </div>
      <Button size="sm" variant="outline" disabled={clearing} onClick={handleResumeNow}>
        {clearing ? "Resuming…" : "Resume auto-dispatch now"}
      </Button>
    </div>
  );
}
