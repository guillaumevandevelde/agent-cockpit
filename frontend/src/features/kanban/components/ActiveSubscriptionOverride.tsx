import { useEffect, useState } from "react";
import { toast } from "sonner";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { kanbanApi } from "../api";
import { PROVIDERS, PROVIDER_LABELS } from "../types";

/** Value used for the Select's "no pin" option. The Select component requires
 * a non-empty string key, but the API uses `null` to express "no override".
 * Keeping it a sentinel rather than `""` so the SelectItem value matches what
 * the backend reads back. */
const NONE_VALUE = "__none__";

export function ActiveSubscriptionOverride({ projectKey }: { projectKey: string }) {
  const [override, setOverride] = useState<
    { provider: string; model: string | null } | null | undefined
  >(undefined);

  useEffect(() => {
    if (!projectKey) return;
    kanbanApi
      .getActiveSubscriptionOverride(projectKey)
      .then((r) => setOverride(r.override))
      .catch(() => setOverride(null));
  }, [projectKey]);

  if (!projectKey || override === undefined) return null;

  const isPinned = override !== null;
  const selectedValue = override?.provider ?? NONE_VALUE;

  const onProviderChange = async (next: string) => {
    const prev = override;
    if (next === NONE_VALUE) {
      setOverride(null);
      try {
        await kanbanApi.setActiveSubscriptionOverride(projectKey, null);
        toast.success("Active subscription override: cleared");
      } catch {
        setOverride(prev);
        toast.error("Failed to clear override");
      }
      return;
    }
    // Provider-only pin: model is left to the existing column/card fallback.
    // Mirrors the backend's `None` model = "fall through" semantics so the
    // override is a one-click "send everything to provider X" affordance
    // rather than a model lock-in.
    const nextOverride = { provider: next, model: null };
    setOverride(nextOverride);
    try {
      await kanbanApi.setActiveSubscriptionOverride(projectKey, nextOverride);
      toast.success(
        `Active subscription override: ${PROVIDER_LABELS[next] ?? next}`
      );
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
    } catch {
      setOverride(prev);
      toast.error("Failed to clear override");
    }
  };

  return (
    <div className="inline-flex items-center gap-2">
      <Select value={selectedValue} onValueChange={onProviderChange}>
        <SelectTrigger
          className={
            "h-8 w-[210px] " +
            (isPinned
              ? "border-primary text-primary font-medium"
              : "")
          }
          title="Board-wide subscription pin: routes every dispatched card onto this subscription, regardless of column/card defaults"
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
      {isPinned && (
        <Button
          size="sm"
          variant="ghost"
          onClick={clearOverride}
          title="Clear the board-wide subscription pin"
        >
          Clear
        </Button>
      )}
    </div>
  );
}