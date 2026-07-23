import { AlertTriangle } from "lucide-react";

import { Badge } from "@/components/ui/badge";

export type ReadyState =
  | "ready"
  | "dependent"
  | "missing_dep"
  | "in_progress"
  | "impeded"
  | "completed"
  // Child card (has `parent_card_id`) whose analyst has not yet attached the
  // `plan_ref` deliverable. The dispatcher holds these out until the
  // analyst's `add_plan_attachment` lands, but the UI used to read them as
  // green "Ready" (kanban-pro-analyse.md §4.1).
  | "awaiting_plan_ref"
  // Card carries a non-empty `metadata.gated_on` business trigger. The
  // dispatcher holds these until a human clears the gate; amber tier
  // signals "permanent, human-actionable" alongside `missing_dep`.
  | "gated";

const STATE_LABEL: Record<ReadyState, string> = {
  ready: "Ready",
  dependent: "Dependent",
  missing_dep: "Missing dep",
  in_progress: "In Progress",
  impeded: "Impeded",
  completed: "Completed",
  awaiting_plan_ref: "Awaiting plan",
  gated: "Gated",
};

const STATE_CLASS: Record<ReadyState, string> = {
  ready: "bg-emerald-100 text-emerald-800 border-emerald-200",
  dependent: "bg-amber-100 text-amber-800 border-amber-200",
  // A dangling dep is a permanent, human-fixable block — surface it red like
  // `impeded` (both need a human), but the ⚠ icon + label keeps it distinct.
  missing_dep: "bg-red-100 text-red-800 border-red-200",
  in_progress: "bg-sky-100 text-sky-800 border-sky-200",
  impeded: "bg-red-100 text-red-800 border-red-200",
  completed: "bg-slate-100 text-slate-800 border-slate-200",
  // Temporary, self-resolving — same "warning amber" tier as `dependent`
  // (the wait clears on its own as soon as the analyst attaches the plan).
  awaiting_plan_ref: "bg-amber-100 text-amber-800 border-amber-200",
  // Permanent, human-actionable — same "red" tier as `missing_dep` /
  // `impeded`. The ⚠ icon + "Gated" label keeps it distinct from the live
  // "Dependent" wait (amber = will resolve on its own) and from the
  // dangling-dep block (red but ⚠ says "deleted parent").
  gated: "bg-red-100 text-red-800 border-red-200",
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
 *
 * `missing_dep` distinguishes a dep on a *deleted* card (permanent block, needs
 * a human) from `dependent` (a live sibling that will resolve on its own). The
 * dangling dep ids ride in `missingDepIds` so the tooltip can name them
 * (dangling-depends-on-analyse.md §1.3/§4).
 *
 * `gatedOn` is the operator-set `metadata.gated_on` string that explains why
 * the card is parked. Surfaced in the tooltip so the operator can see WHAT
 * the card is waiting on, not just that it is. Optional — callers that
 * don't pass it get a generic fallback.
 */
export function ReadyStateBadge({
  state,
  blockerTitles,
  missingDepIds,
  gatedOn,
}: {
  state: ReadyState;
  blockerTitles?: string[];
  missingDepIds?: string[];
  gatedOn?: string;
}) {
  const label = STATE_LABEL[state];
  const variantClass = STATE_CLASS[state];
  const tooltip =
    state === "missing_dep" && missingDepIds && missingDepIds.length > 0
      ? `Depends on a deleted card (${missingDepIds.join(
          ", ",
        )}) — permanent block; clear the dependency or restore the card.`
      : state === "dependent" && blockerTitles && blockerTitles.length > 0
        ? `Waiting on: ${blockerTitles.join(", ")}`
        : state === "gated"
          ? gatedOn
            ? `Gated by business trigger "${gatedOn}" — clear metadata.gated_on to dispatch.`
            : "Card is gated by a business trigger — clear metadata.gated_on to dispatch."
          : state === "awaiting_plan_ref"
            ? "Waiting on the analyst's plan_ref deliverable — will resolve once add_plan_attachment runs."
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
      {(state === "missing_dep" || state === "gated") && (
        <AlertTriangle className="h-3 w-3 mr-1" aria-hidden="true" />
      )}
      {label}
    </Badge>
  );
}
