import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { kanbanApi } from "../api";

export function MaxSessionsControl({ projectKey }: { projectKey: string }) {
  const [value, setValue] = useState<number | null>(null);

  useEffect(() => {
    if (!projectKey) return;
    kanbanApi
      .getMaxSessions(projectKey)
      .then((r) => setValue(r.max_sessions))
      .catch(() => setValue(4));
  }, [projectKey]);

  if (!projectKey || value === null) return null;

  const commit = async (next: number) => {
    if (next < 1) return;
    const prev = value;
    setValue(next);
    try {
      await kanbanApi.setMaxSessions(projectKey, next);
    } catch {
      setValue(prev);
      toast.error("Failed to set max sessions");
    }
  };

  return (
    <div
      className="inline-flex items-center gap-1 rounded-md border px-1"
      title="Maximum concurrent agent sessions auto-dispatched for this project"
    >
      <Button size="sm" variant="ghost" className="h-7 w-7 p-0"
        onClick={() => commit(value - 1)} disabled={value <= 1}>−</Button>
      <span className="min-w-[5.5rem] text-center text-xs tabular-nums">
        Max sessions: {value}
      </span>
      <Button size="sm" variant="ghost" className="h-7 w-7 p-0"
        onClick={() => commit(value + 1)}>+</Button>
    </div>
  );
}
