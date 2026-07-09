import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { MODAL_SIZES } from "@/lib/constants";
import { kanbanApi } from "../api";
import {
  WORK_TYPES,
  WORK_TYPE_PERSONA_DEFAULTS,
  type WorkType,
} from "../types";

export function WorkTypeMappingDialog({
  open,
  projectKey,
  onClose,
  onChanged,
}: {
  open: boolean;
  projectKey: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  // merged: server-merged {work_type: persona} (defaults + overrides).
  const [merged, setMerged] = useState<Record<WorkType, string> | null>(null);
  // pending: local edits, applied via Save (bulk endpoint).
  const [pending, setPending] = useState<Record<WorkType, string>>(
    WORK_TYPE_PERSONA_DEFAULTS,
  );
  const [saving, setSaving] = useState(false);

  const reload = useCallback(async () => {
    if (!projectKey) return;
    try {
      const r = await kanbanApi.listWorkTypeMappings(projectKey);
      setMerged(r.mappings);
      setPending(r.mappings);
    } catch {
      toast.error("Failed to load work-type mappings");
    }
  }, [projectKey]);

  useEffect(() => {
    if (open) void reload();
  }, [open, reload]);

  // Dropdown contents: at minimum the four defaults, plus anything already
  // present in the merged map (so a user-set non-default persona stays
  // selectable after a refresh). No new fetches needed.
  const personaOptions = useMemo(() => {
    const set = new Set<string>(Object.values(WORK_TYPE_PERSONA_DEFAULTS));
    if (merged) {
      for (const v of Object.values(merged)) set.add(v);
    }
    return Array.from(set).sort();
  }, [merged]);

  const isOverridden = (wt: WorkType): boolean => {
    if (!merged) return false;
    return merged[wt] !== WORK_TYPE_PERSONA_DEFAULTS[wt];
  };

  const handleReset = async (wt: WorkType) => {
    if (!projectKey) return;
    try {
      await kanbanApi.deleteWorkTypeMapping(projectKey, wt);
      const defaultPersona = WORK_TYPE_PERSONA_DEFAULTS[wt];
      const nextPending = { ...pending, [wt]: defaultPersona };
      const nextMerged = merged ? { ...merged, [wt]: defaultPersona } : null;
      setPending(nextPending);
      setMerged(nextMerged);
      onChanged();
      toast.success(`Reset ${wt} to default (${defaultPersona})`);
    } catch {
      toast.error(`Failed to reset ${wt}`);
    }
  };

  const handleSave = async () => {
    if (!projectKey) return;
    setSaving(true);
    try {
      const payload = WORK_TYPES.map((wt) => ({
        work_type: wt,
        persona: pending[wt],
      }));
      await kanbanApi.bulkPutWorkTypeMappings(projectKey, payload);
      await reload();
      onChanged();
      toast.success("Work-type mappings saved");
    } catch {
      toast.error("Failed to save work-type mappings");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader>
          <DialogTitle>Work Type → Persona</DialogTitle>
          <DialogDescription>
            Per-project override of the work-type routing. Defaults route
            <span className="font-mono"> analysis</span> to{" "}
            <span className="font-mono">analyst</span> and the rest to{" "}
            <span className="font-mono">engineer</span>. Anything you change
            here is saved as a per-project override.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 max-h-80 overflow-y-auto">
          {WORK_TYPES.map((wt) => (
            <div
              key={wt}
              className="flex items-center gap-2 p-2 rounded border"
            >
              <div className="flex-1">
                <div className="text-sm font-medium font-mono">{wt}</div>
                <div className="text-xs text-muted-foreground">
                  Default: {WORK_TYPE_PERSONA_DEFAULTS[wt]}
                  {isOverridden(wt) && merged && (
                    <> · currently overridden → {merged[wt]}</>
                  )}
                </div>
              </div>
              <Select
                value={pending[wt]}
                onValueChange={(v) =>
                  setPending((p) => ({ ...p, [wt]: v }))
                }
              >
                <SelectTrigger className="w-48">
                  <SelectValue placeholder="Persona" />
                </SelectTrigger>
                <SelectContent>
                  {personaOptions.map((p) => (
                    <SelectItem key={p} value={p}>
                      {p}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                size="sm"
                variant="ghost"
                disabled={!isOverridden(wt)}
                onClick={() => handleReset(wt)}
              >
                Reset
              </Button>
            </div>
          ))}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={saving}>
            Close
          </Button>
          <Button onClick={handleSave} disabled={saving || !merged}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
