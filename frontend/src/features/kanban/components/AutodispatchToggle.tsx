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
      toast.success(next ? "Auto-pick enabled" : "Auto-pick disabled");
    } catch {
      toast.error("Failed to change auto-pick");
    }
  };

  return (
    <Button
      size="sm"
      variant={enabled ? "default" : "outline"}
      onClick={toggle}
      title="When on, this device spawns a Claude session for each unclaimed Todo card"
    >
      {enabled ? "Auto-pick: on" : "Auto-pick: off"}
    </Button>
  );
}
