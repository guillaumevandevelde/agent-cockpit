import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Plus, Trash2, GripVertical } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MODAL_SIZES } from "@/lib/constants";
import { kanbanApi } from "../api";
import {
  KNOWN_POOL_CLIS,
  POOL_CLI_LABELS,
  PROVIDERS,
  PROVIDER_LABELS,
} from "../types";
import type { PoolEntry } from "../types";

/** Sentinel value used by the override's provider <Select>. The Radix
 *  SelectItem requires a non-empty string; the API expresses "no override"
 *  as `null`. Mirrors the previous `ActiveSubscriptionOverride` value. */
const NONE_VALUE = "__none__";

/** Sensible-default entry for the "Add first subscription" affordance —
 *  matches what the previous standalone SubscriptionPool seeded. Kaart
 *  8f40d443… (quota-pool CLI-agnostisch): the default targets the
 *  canonical claude-code transport so legacy / pre-feature rows keep
 *  building without ceremony (the backend also back-fills
 *  ``DEFAULT_POOL_CLI`` on read for legacy JSON). */
function makeDefaultEntry(): PoolEntry {
  return {
    cli: "claude-code",
    provider: "anthropic",
    model: null,
    drempel: 0.9,
  };
}

/** Resolve the per-entry CLI for picker state. PoolEntry.cli is
 *  optional on the wire (legacy rows omit it; the backend back-fills
 *  DEFAULT_POOL_CLI). The picker needs a concrete string for the
 *  controlled <Select>; falling back to DEFAULT_POOL_CLI keeps the
 *  Radix SelectItem happy without showing "no CLI selected" on
 *  legacy rows. */
function entryCli(entry: PoolEntry): string {
  return entry.cli ?? "claude-code";
}

/** Combined dialog for board-wide subscription routing:
 *
 *    - "Active subscription override" — the per-board pin (a one-click
 *      "send everything to provider X" affordance). WINS over the pool and
 *      over per-card / column defaults.
 *
 *    - "Subscription pool" — the ordered fallback list. Disabled (no
 *      add/remove/edit/reorder) while the override is active, because the
 *      dispatcher never reaches the pool in that case (`dispatch.py:2256`).
 *      The explicit rule is shown above the editor so the silent-disable
 *      from the previous layout (analysis §1.2) becomes a visible signal.
 *
 *  Both fields share the same dialog because they were two unrelated
 *  toolbar controls with a silent cross-effect — grouping them turns the
 *  precedence chain into the dialog's first artifact.
 */
export function SubscriptionPoolDialog({
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
  const [pool, setPool] = useState<PoolEntry[] | null | undefined>(undefined);
  const [override, setOverride] = useState<
    { provider: string; model: string | null } | null | undefined
  >(undefined);

  const reload = useCallback(async () => {
    if (!projectKey) return;
    try {
      const r = await kanbanApi.getSubscriptionPool(projectKey);
      setPool(r.pool);
    } catch {
      setPool(null);
    }
    try {
      const r = await kanbanApi.getActiveSubscriptionOverride(projectKey);
      setOverride(r.override);
    } catch {
      setOverride(null);
    }
  }, [projectKey]);

  useEffect(() => {
    if (open) void reload();
  }, [open, reload]);

  if (!open || pool === undefined || override === undefined) {
    // Render nothing (or the dialog frame) until we've loaded — keeps the
    // precedence rule and lock-state from flashing on every open.
    return (
      <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
        <DialogContent className={MODAL_SIZES.MD}>
          <span aria-hidden />
        </DialogContent>
      </Dialog>
    );
  }

  const isOverrideActive = override !== null;
  const isPoolSet = pool !== null;
  const editable: PoolEntry[] = isPoolSet ? (pool as PoolEntry[]) : [];
  const overrideLockedPool = isOverrideActive;

  // -------- Pool mutations (disabled when override is active) -----------

  const savePool = async (next: PoolEntry[] | null) => {
    if (overrideLockedPool) return;
    setPool(next);
    try {
      await kanbanApi.setSubscriptionPool(projectKey, next);
      toast.success(
        next === null
          ? "Subscription pool: cleared (column defaults)"
          : `Subscription pool saved (${next.length} entr${next.length === 1 ? "y" : "ies"})`,
      );
      onChanged();
    } catch (err) {
      // Roll back to the previous server value on save failure.
      const fresh = await kanbanApi
        .getSubscriptionPool(projectKey)
        .catch(() => ({ pool: null }));
      setPool(fresh.pool);
      toast.error(
        err instanceof Error ? err.message : "Failed to save subscription pool",
      );
    }
  };

  const updateEntry = (index: number, patch: Partial<PoolEntry>) => {
    const next = editable.map((entry, i) =>
      i === index ? { ...entry, ...patch } : entry,
    );
    void savePool(next);
  };

  const removeEntry = (index: number) => {
    const next = editable.filter((_, i) => i !== index);
    void savePool(next.length === 0 ? null : next);
  };

  const addEntry = () => {
    if (overrideLockedPool) return;
    void savePool([...editable, makeDefaultEntry()]);
  };

  const moveEntry = (index: number, delta: -1 | 1) => {
    const target = index + delta;
    if (target < 0 || target >= editable.length) return;
    const next = [...editable];
    [next[index], next[target]] = [next[target], next[index]];
    void savePool(next);
  };

  const clearPool = () => {
    void savePool(null);
  };

  // -------- Override mutations -----------

  const setOverrideProvider = async (next: string) => {
    const prev = override;
    if (next === NONE_VALUE) {
      setOverride(null);
      try {
        await kanbanApi.setActiveSubscriptionOverride(projectKey, null);
        toast.success("Active subscription override: cleared");
        onChanged();
      } catch {
        setOverride(prev);
        toast.error("Failed to clear override");
      }
      return;
    }
    const nextOverride = { provider: next, model: null };
    setOverride(nextOverride);
    try {
      await kanbanApi.setActiveSubscriptionOverride(projectKey, nextOverride);
      toast.success(
        `Active subscription override: ${PROVIDER_LABELS[next] ?? next}`,
      );
      onChanged();
    } catch {
      setOverride(prev);
      toast.error("Failed to set subscription override");
    }
  };

  const clearOverride = async () => {
    const prev = override;
    setOverride(null);
    try {
      await kanbanApi.setActiveSubscriptionOverride(projectKey, null);
      toast.success("Active subscription override: cleared");
      onChanged();
    } catch {
      setOverride(prev);
      toast.error("Failed to clear override");
    }
  };

  // -------- Render -----------

  const overrideProviderLabel = isOverrideActive
    ? `Subscription: ${PROVIDER_LABELS[override!.provider] ?? override!.provider}`
    : "Subscription: column defaults";

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className={MODAL_SIZES.MD}>
        <DialogHeader>
          <DialogTitle>Subscriptions</DialogTitle>
          <DialogDescription>
            Two routing layers control where dispatched cards land. Effective
            precedence (high → low):{" "}
            <span className="font-mono">global override</span> &gt;{" "}
            <span className="font-mono">pool</span> &gt;{" "}
            <span className="font-mono">per-card column_overrides</span> &gt;{" "}
            <span className="font-mono">column defaults</span>.
          </DialogDescription>
        </DialogHeader>

        {/* --------------------------- Override --------------------------- */}
        <section
          aria-labelledby="subscription-override-heading"
          className="space-y-2"
        >
          <div className="flex items-center justify-between">
            <h3
              id="subscription-override-heading"
              className="text-sm font-semibold"
            >
              Active subscription override
            </h3>
            <span
              className="text-xs text-muted-foreground"
              aria-live="polite"
              data-testid="override-state"
            >
              {overrideProviderLabel}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <Select
              value={override?.provider ?? NONE_VALUE}
              onValueChange={setOverrideProvider}
            >
              <SelectTrigger
                className={
                  "h-8 w-[260px] " +
                  (isOverrideActive
                    ? "border-primary text-primary font-medium"
                    : "")
                }
                aria-label="Active subscription override provider"
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE_VALUE}>
                  Subscription: column defaults
                </SelectItem>
                {PROVIDERS.map((p) => (
                  <SelectItem key={p} value={p}>
                    Subscription: {PROVIDER_LABELS[p] ?? p}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              size="sm"
              variant="ghost"
              onClick={clearOverride}
              disabled={!isOverrideActive}
              aria-label="Clear override"
              title="Clear the board-wide subscription pin"
            >
              Clear override
            </Button>
          </div>
        </section>

        {/* ----------------------------- Pool ----------------------------- */}
        <section
          aria-labelledby="subscription-pool-heading"
          className="space-y-2 border-t pt-3"
        >
          <div className="flex items-center justify-between">
            <h3
              id="subscription-pool-heading"
              className="text-sm font-semibold"
            >
              Subscription pool
            </h3>
            {!isPoolSet && (
              <span className="text-xs text-muted-foreground">
                Unset — column defaults apply
              </span>
            )}
            {isPoolSet && editable.length > 0 && (
              <Button
                size="sm"
                variant="ghost"
                onClick={clearPool}
                disabled={overrideLockedPool}
                aria-label="Clear pool"
                title="Clear the pool — dispatch falls back to column defaults"
              >
                Clear pool
              </Button>
            )}
          </div>

          {overrideLockedPool && (
            <p
              className="rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300"
              role="note"
              data-testid="override-active-rule"
            >
              <strong>Override actief → pool staat uit.</strong>{" "}
              Clear or change the override above to re-enable pool editing.
              While the override is active the dispatcher never reaches the
              pool, so mutating it would be cosmetic.
            </p>
          )}

          <div
            className={
              "space-y-2 max-h-72 overflow-y-auto pr-1 " +
              (overrideLockedPool ? "opacity-60 pointer-events-none" : "")
            }
            aria-disabled={overrideLockedPool}
          >
            {editable.length === 0 ? (
              <div className="flex items-center justify-between rounded-md border border-dashed border-border px-3 py-4">
                <p className="text-sm text-muted-foreground">
                  No subscription pool configured — dispatch follows
                  per-column defaults.
                </p>
                <Button
                  size="sm"
                  onClick={addEntry}
                  disabled={overrideLockedPool}
                >
                  <Plus className="h-3 w-3 mr-1" />
                  Add first subscription
                </Button>
              </div>
            ) : (
              <>
                {editable.map((entry, index) => (
                  <div
                    key={index}
                    className="flex items-start gap-2 rounded-md border border-border p-3"
                  >
                    <div className="flex flex-col gap-0.5 pt-1">
                      <button
                        type="button"
                        className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                        onClick={() => moveEntry(index, -1)}
                        disabled={overrideLockedPool || index === 0}
                        title="Move up (higher priority)"
                        aria-label="Move entry up"
                      >
                        ▲
                      </button>
                      <GripVertical
                        className="h-3 w-3 text-muted-foreground"
                        aria-hidden
                      />
                      <button
                        type="button"
                        className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                        onClick={() => moveEntry(index, 1)}
                        disabled={
                          overrideLockedPool ||
                          index >= editable.length - 1
                        }
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
                          value={entryCli(entry)}
                          onValueChange={(v) =>
                            updateEntry(index, { cli: v })
                          }
                          disabled={overrideLockedPool}
                        >
                          <SelectTrigger className="h-8">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {KNOWN_POOL_CLIS.map((c) => (
                              <SelectItem key={c} value={c}>
                                {POOL_CLI_LABELS[c] ?? c}
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
                          onValueChange={(v) =>
                            updateEntry(index, { provider: v })
                          }
                          disabled={overrideLockedPool}
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
                            updateEntry(index, {
                              model: e.target.value.trim() || null,
                            })
                          }
                          disabled={overrideLockedPool}
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
                            updateEntry(index, {
                              drempel: Math.min(1, Math.max(0.05, v)),
                            });
                          }}
                          disabled={overrideLockedPool}
                        />
                      </div>
                    </div>

                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => removeEntry(index)}
                      className="text-destructive hover:text-destructive/80"
                      disabled={overrideLockedPool}
                      title="Remove this entry"
                      aria-label="Remove entry"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                ))}

                <div className="flex justify-end">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={addEntry}
                    disabled={overrideLockedPool}
                  >
                    <Plus className="h-3 w-3 mr-1" />
                    Add subscription
                  </Button>
                </div>
              </>
            )}
          </div>
        </section>

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={onClose}
            data-testid="close-dialog"
          >
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
