import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { kanbanApi } from "../api";

export function AutodispatchToggle({ projectKey }: { projectKey: string }) {
  const [enabled, setEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    if (!projectKey) return;
    kanbanApi
      .getAutodispatch(projectKey)
      .then((r) => setEnabled(r.enabled))
      .catch(() => setEnabled(false));
  }, [projectKey]);

  if (!projectKey || enabled === null) return null;

  const toggle = async () => {
    const next = !enabled;
    try {
      await kanbanApi.setAutodispatch(projectKey, next);
      setEnabled(next);
      toast.success(next ? "Auto-dispatch: on" : "Auto-dispatch: off");
    } catch {
      toast.error("Failed to change auto-dispatch");
    }
  };

  return (
    <Button
      size="sm"
      variant={enabled ? "default" : "outline"}
      onClick={toggle}
      title="When on, the poller automatically picks up Backlog cards up to the session cap"
    >
      {enabled ? "Auto-dispatch: on" : "Auto-dispatch: off"}
    </Button>
  );
}
