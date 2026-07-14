import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Plus, Trash2, GripVertical } from "lucide-react";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { kanbanApi } from "../api";
import { PROVIDERS, PROVIDER_LABELS } from "../types";
import type { PoolEntry } from "../types";

/** One pool entry defaults to the most common shape: claude-code +
 *  anthropic at the 90% drempel the analyse uses as its worked example.
 *  Kept small so adding the first entry is a single click. */
function makeDefaultEntry(): PoolEntry {
  return { cli: "claude-code", provider: "anthropic", model: null, drempel: 0.9 };
}

/** Per-CLI options for the cli select. Mirrors agentic_cli.registry
 *  today (only claude-code is wired for subscription pool routing on
 *  this iteration; other CLIs are explicit "we haven't built it yet"
 *  entries so the UI is honest about the supported set). */
const CLI_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "claude-code", label: "claude-code" },
];

export function SubscriptionPool({ projectKey }: { projectKey: string }) {
  const [pool, setPool] = useState<PoolEntry[] | null | undefined>(undefined);

  useEffect(() => {
    if (!projectKey) return;
    kanbanApi
      .getSubscriptionPool(projectKey)
      .then((r) => setPool(r.pool))
      .catch(() => setPool(null));
  }, [projectKey]);

  if (!projectKey || pool === undefined) return null;

  const isUnset = pool === null;
  // When the pool is unset we present a single empty editor slot so
  // "Add first subscription" is one click — the editor then renders
  // the live list once any entries exist.
  const editable: PoolEntry[] = isUnset ? [] : pool;

  const save = async (next: PoolEntry[] | null) => {
    setPool(next);
    try {
      await kanbanApi.setSubscriptionPool(projectKey, next);
      toast.success(
        next === null
          ? "Subscription pool: cleared (column defaults)"
          : `Subscription pool saved (${next.length} entr${next.length === 1 ? "y" : "ies"})`
      );
    } catch (err) {
      // Roll back to the previous server value on save failure.
      const fresh = await kanbanApi
        .getSubscriptionPool(projectKey)
        .catch(() => ({ pool: null }));
      setPool(fresh.pool);
      toast.error(
        err instanceof Error ? err.message : "Failed to save subscription pool"
      );
    }
  };

  const update = (index: number, patch: Partial<PoolEntry>) => {
    const next = editable.map((entry, i) =>
      i === index ? { ...entry, ...patch } : entry
    );
    void save(next);
  };

  const remove = (index: number) => {
    const next = editable.filter((_, i) => i !== index);
    void save(next.length === 0 ? null : next);
  };

  const add = () => {
    void save([...editable, makeDefaultEntry()]);
  };

  const moveUp = (index: number) => {
    if (index === 0) return;
    const next = [...editable];
    [next[index - 1], next[index]] = [next[index], next[index - 1]];
    void save(next);
  };

  const moveDown = (index: number) => {
    if (index >= editable.length - 1) return;
    const next = [...editable];
    [next[index], next[index + 1]] = [next[index + 1], next[index]];
    void save(next);
  };

  const clearAll = () => {
    void save(null);
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle>Subscription pool</CardTitle>
            <CardDescription>
              Ordered list of subscriptions the dispatcher picks from at runtime. First
              entry under its threshold wins; if none qualify, the last entry is the
              fallback (the per-provider pause is the final gate). Precedence: global
              override &gt; pool &gt; per-card overrides &gt; column defaults.
            </CardDescription>
          </div>
          {!isUnset && (
            <Button
              size="sm"
              variant="ghost"
              onClick={clearAll}
              title="Clear the pool — dispatch falls back to column defaults"
            >
              Clear
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {editable.length === 0 && (
          <div className="flex items-center justify-between rounded-md border border-dashed border-border px-3 py-4">
            <p className="text-sm text-muted-foreground">
              No subscription pool configured — dispatch follows per-column defaults.
            </p>
            <Button size="sm" onClick={add}>
              <Plus className="h-3 w-3 mr-1" />
              Add first subscription
            </Button>
          </div>
        )}

        {editable.map((entry, index) => (
          <div
            key={index}
            className="flex items-start gap-2 rounded-md border border-border p-3"
          >
            <div className="flex flex-col gap-0.5 pt-1">
              <button
                type="button"
                className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                onClick={() => moveUp(index)}
                disabled={index === 0}
                title="Move up (higher priority)"
                aria-label="Move entry up"
              >
                ▲
              </button>
              <GripVertical className="h-3 w-3 text-muted-foreground" aria-hidden />
              <button
                type="button"
                className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                onClick={() => moveDown(index)}
                disabled={index >= editable.length - 1}
                title="Move down (lower priority)"
                aria-label="Move entry down"
              >
                ▼
              </button>
            </div>

            <div className="flex-1 grid grid-cols-1 sm:grid-cols-12 gap-2">
              <div className="sm:col-span-3">
                <label className="block text-xs text-muted-foreground mb-1">
                  CLI
                </label>
                <Select
                  value={entry.cli}
                  onValueChange={(v) => update(index, { cli: v })}
                >
                  <SelectTrigger className="h-8">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {CLI_OPTIONS.map((opt) => (
                      <SelectItem key={opt.value} value={opt.value}>
                        {opt.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="sm:col-span-3">
                <label className="block text-xs text-muted-foreground mb-1">
                  Provider
                </label>
                <Select
                  value={entry.provider}
                  onValueChange={(v) => update(index, { provider: v })}
                >
                  <SelectTrigger className="h-8">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PROVIDERS.map((p) => (
                      <SelectItem key={p} value={p}>
                        {PROVIDER_LABELS[p] ?? p}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="sm:col-span-3">
                <label className="block text-xs text-muted-foreground mb-1">
                  Model (optional)
                </label>
                <Input
                  className="h-8"
                  value={entry.model ?? ""}
                  placeholder="fall through"
                  onChange={(e) =>
                    update(index, {
                      model: e.target.value.trim() || null,
                    })
                  }
                />
              </div>

              <div className="sm:col-span-3">
                <label className="block text-xs text-muted-foreground mb-1">
                  Drempel
                </label>
                <Input
                  className="h-8"
                  type="number"
                  min="0.05"
                  max="1"
                  step="0.05"
                  value={entry.drempel}
                  onChange={(e) => {
                    const v = parseFloat(e.target.value);
                    if (!Number.isFinite(v)) return;
                    update(index, { drempel: Math.min(1, Math.max(0.05, v)) });
                  }}
                />
              </div>
            </div>

            <Button
              size="sm"
              variant="ghost"
              onClick={() => remove(index)}
              className="text-destructive hover:text-destructive/80"
              title="Remove this entry"
              aria-label="Remove entry"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}

        {editable.length > 0 && (
          <div className="flex justify-end">
            <Button size="sm" variant="outline" onClick={add}>
              <Plus className="h-3 w-3 mr-1" />
              Add subscription
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}