import { Badge } from "@/components/ui/badge";

export type ReadyState = "ready" | "blocked" | "dispatching";

const STATE_LABEL: Record<ReadyState, string> = {
  ready: "Ready",
  blocked: "Blocked",
  dispatching: "Dispatching",
};

const STATE_CLASS: Record<ReadyState, string> = {
  ready: "bg-emerald-100 text-emerald-800 border-emerald-200",
  blocked: "bg-amber-100 text-amber-800 border-amber-200",
  dispatching: "bg-sky-100 text-sky-800 border-sky-200",
};

/**
 * Per-card operational-state badge. Mirrors the backend's ready/blocking
 * filter semantics + the dispatcher's `agent:` claim so the operator can
 * see at a glance whether a card is dispatchable, gated on a dep, or
 * already being worked on.
 *
 * `blockerTitles` is non-empty only when `readyState === "blocked"`; when
 * supplied, we surface it through the standard `title` attribute so the
 * browser's built-in tooltip kicks in without pulling in a tooltip library.
 */
export function ReadyStateBadge({
  state,
  blockerTitles,
}: {
  state: ReadyState;
  blockerTitles?: string[];
}) {
  const label = STATE_LABEL[state];
  const variantClass = STATE_CLASS[state];
  const tooltip =
    state === "blocked" && blockerTitles && blockerTitles.length > 0
      ? `Blocked by: ${blockerTitles.join(", ")}`
      : state === "ready"
        ? "No open dependencies"
        : state === "dispatching"
          ? "Claimed by an agent session"
          : undefined;
  return (
    <Badge
      variant="outline"
      className={`${variantClass} text-[10px] font-normal border`}
      title={tooltip}
      data-ready-state={state}
    >
      {label}
    </Badge>
  );
}
