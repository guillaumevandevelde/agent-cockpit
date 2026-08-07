import { useEffect, useState } from "react";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { kanbanApi, type AutodispatchStatus } from "../api";

export function AutodispatchToggle({ projectKey }: { projectKey: string }) {
  const [status, setStatus] = useState<AutodispatchStatus | null>(null);

  useEffect(() => {
    if (!projectKey) return;
    kanbanApi
      .getAutodispatch(projectKey)
      .then(setStatus)
      .catch(() => setStatus({ enabled: false }));
  }, [projectKey]);

  if (!projectKey || status === null) return null;

  const toggle = async () => {
    const next = !status.enabled;
    try {
      await kanbanApi.setAutodispatch(projectKey, next);
      setStatus({ enabled: next });
      toast.success(next ? "Auto-dispatch: on" : "Auto-dispatch: off");
    } catch {
      toast.error("Failed to change auto-dispatch");
    }
  };

  // The boot-disabled marker is only meaningful when the flag is *currently*
  // off AND we have a timestamp — the toggle component already shows "off"
  // either way, the marker just adds the "why" so the operator doesn't have
  // to dive into logs/ to learn it was a backend restart, not a dispatcher
  // bug.
  const showBootHint = !status.enabled && status.disabled_by_boot_at;

  return (
    <div className="flex items-center gap-2">
      <Button
        size="sm"
        variant={status.enabled ? "default" : "outline"}
        onClick={toggle}
        title="When on, the poller automatically picks up Backlog cards up to the session cap"
        data-testid="autodispatch-toggle"
      >
        {status.enabled ? "Auto-dispatch: on" : "Auto-dispatch: off"}
      </Button>
      {showBootHint && (
        <span
          className="text-xs text-muted-foreground"
          data-testid="autodispatch-boot-hint"
          title={status.disabled_by_boot_at}
        >
          off since backend start ({formatDistanceToNow(
            new Date(status.disabled_by_boot_at!),
            { addSuffix: true },
          )}) — click to resume
        </span>
      )}
    </div>
  );
}