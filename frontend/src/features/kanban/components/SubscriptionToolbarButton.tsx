import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { kanbanApi } from "../api";
import type { PoolEntry } from "../types";
import { PROVIDER_LABELS } from "../types";
import { SubscriptionPoolDialog } from "./SubscriptionPoolDialog";

/** Toolbar button that opens the unified subscription dialog (pool + active
 *  override in one place). Shows the effective state in the button label so
 *  the information doesn't get hidden behind the dialog.
 *
 *  Label priority matches the dispatcher precedence (analysis §1.1):
 *  - "Pinned: <provider>" when the board-wide override is set (override
 *    wins over the pool).
 *  - "Pool (N)" when a subscription pool is configured.
 *  - "Subscriptions" otherwise (column defaults apply).
 *
 *  See `docs/cockpit/subscription-pool-dispatch-analyse.md` §4 for the
 *  rationale (the previous layout had the pool Card always-visible above
 *  the board, with `ActiveSubscriptionOverride` as a sibling toolbar control
 *  that silently disabled it — see §1.2). */
export function SubscriptionToolbarButton({ projectKey }: { projectKey: string }) {
  const [override, setOverride] = useState<
    { provider: string; model: string | null } | null | undefined
  >(undefined);
  const [pool, setPool] = useState<PoolEntry[] | null | undefined>(undefined);
  const [open, setOpen] = useState(false);

  const reload = useCallback(async () => {
    if (!projectKey) return;
    // Both fetches are independent; race-to-state is harmless — the dialog
    // shows "loading" until both succeed.
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
    void reload();
  }, [reload]);

  if (!projectKey || pool === undefined || override === undefined) return null;

  // Label priority: override first (it wins), then pool, then the no-config
  // default. Override's `provider` is the same vocabulary as `PROVIDER_LABELS`
  // for display, but fall back to the raw id for non-built-in providers.
  const label = override
    ? `Pinned: ${PROVIDER_LABELS[override.provider] ?? override.provider}`
    : pool && pool.length > 0
      ? `Pool (${pool.length})`
      : "Subscriptions";

  // Kaart 7411d25e…: spillover status surfaces in the tooltip so an
  // operator can answer "why is my card stuck?" without opening the
  // dialog. The pool is a spillover chain with the column-default as
  // the implicit head, so a configured pool always means spillover is
  // ON; the dialog adds the per-column chain detail once opened.
  const tooltip = override
    ? `Board-wide subscription pin: ${PROVIDER_LABELS[override.provider] ?? override.provider} (pool is bypassed)`
    : pool && pool.length > 0
      ? `Spillover: ON — pool has ${pool.length} entr${pool.length === 1 ? "y" : "ies"}; cards spill over when their column default is paused or above its threshold`
      : "Spillover: OFF — no pool configured, a card that hits its column-default limit waits until the reset";

  return (
    <>
      <Button
        size="sm"
        variant="outline"
        onClick={() => setOpen(true)}
        className={
          override
            ? "border-primary text-primary font-medium"
            : undefined
        }
        title={tooltip}
      >
        {label}
      </Button>
      {open && (
        <SubscriptionPoolDialog
          open
          projectKey={projectKey}
          onClose={() => setOpen(false)}
          onChanged={reload}
        />
      )}
    </>
  );
}
