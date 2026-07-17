import { Badge } from "@/components/ui/badge";

export type ReadyState = "ready" | "dependent" | "in_progress" | "impeded" | "completed";

const STATE_LABEL: Record<ReadyState, string> = {
  ready: "Ready",
  dependent: "Dependent",
  in_progress: "In Progress",
  impeded: "Impeded",
  completed: "Completed",
};

const STATE_CLASS: Record<ReadyState, string> = {
  ready: "bg-emerald-100 text-emerald-800 border-emerald-200",
  dependent: "bg-amber-100 text-amber-800 border-amber-200",
  in_progress: "bg-sky-100 text-sky-800 border-sky-200",
  impeded: "bg-red-100 text-red-800 border-red-200",
  completed: "bg-slate-100 text-slate-800 border-slate-200",
};

/**
 * Per-card operational-state badge. Mirrors the backend's ready/blocking
 * filter semantics + the dispatcher's `agent:` claim so the operator can
 * see at a glance whether a card is dispatchable, waiting on a dependency,
 * already being worked on, waiting on a human, or done.
 *
 * `blockerTitles` is non-empty only when `readyState === "dependent"`; when
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
    state === "dependent" && blockerTitles && blockerTitles.length > 0
      ? `Waiting on: ${blockerTitles.join(", ")}`
      : state === "ready"
        ? "No open dependencies"
        : state === "in_progress"
          ? "Claimed by an agent session"
          : state === "impeded"
            ? "Waiting on a human decision"
            : state === "completed"
              ? "Work is done"
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
