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
  | "gated"
  // Child whose `parent_card_id` points at a deleted card. Distinct from
  // `awaiting_plan_ref` (which it used to masquerade as) because the wait is
  // permanent: the analyst run that owed this card its plan died with the
  // parent, so no amount of patience resolves it.
  | "missing_parent";

const STATE_LABEL: Record<ReadyState, string> = {
  ready: "Ready",
  dependent: "Dependent",
  missing_dep: "Missing dep",
  in_progress: "In Progress",
  impeded: "Impeded",
  completed: "Completed",
  awaiting_plan_ref: "Awaiting plan",
  gated: "Gated",
  missing_parent: "Orphaned",
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
  // Permanent and human-actionable, like `missing_dep` — the parent has to be
  // restored or the link cleared.
  missing_parent: "bg-red-100 text-red-800 border-red-200",
};

/**
 * How long the current hold has lasted, e.g. "5d" / "3h" / "12m".
 *
 * The impure `Date.now()` lives *inside* this helper on purpose: the
 * react-compiler ESLint rule rejects it in a component's render body,
 * including as an inline argument expression (see `isFutureSchedule` in
 * CardItem).
 *
 * Age is the signal that separates a healthy wait from a dead one. Every
 * temporary hold reason claims it will resolve on its own; only the clock
 * says whether it actually has been.
 */
function formatHeldAge(since: string): string | null {
  const ms = Date.now() - new Date(since).getTime();
  if (!Number.isFinite(ms) || ms < 0) return null;
  const minutes = Math.floor(ms / 60000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

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
  heldSince,
}: {
  state: ReadyState;
  blockerTitles?: string[];
  missingDepIds?: string[];
  gatedOn?: string;
  heldSince?: string | null;
}) {
  const label = STATE_LABEL[state];
  const variantClass = STATE_CLASS[state];
  const named =
    blockerTitles && blockerTitles.length > 0 ? blockerTitles.join(", ") : null;
  const base =
    state === "missing_dep" && missingDepIds && missingDepIds.length > 0
      ? `Depends on a deleted card (${missingDepIds.join(
          ", ",
        )}) — permanent block; clear the dependency or restore the card.`
      : state === "missing_parent"
        ? `Parent card${named ? ` "${named}"` : ""} was deleted — no analyst run ` +
          `survives to attach a plan. Clear the parent link or restore the card.`
        : state === "dependent" && named
          ? `Waiting on: ${named}`
          : state === "gated"
            ? gatedOn
              ? `Gated by business trigger "${gatedOn}" — clear metadata.gated_on to dispatch.`
              : "Card is gated by a business trigger — clear metadata.gated_on to dispatch."
            : state === "awaiting_plan_ref"
              ? // Naming the parent is the whole point: without it this badge
                // says a card is waiting but not on whom, which reads as an
                // orphan even when the link is perfectly intact.
                `Waiting on the analyst's plan_ref deliverable${
                  named ? ` from "${named}"` : ""
                } — resolves once add_plan_attachment runs.`
              : state === "ready"
                ? "No open dependencies"
                : state === "in_progress"
                  ? "Claimed by an agent session"
                  : state === "impeded"
                    ? "Waiting on a human decision"
                    : state === "completed"
                      ? "Work is done"
                      : undefined;
  const age = heldSince ? formatHeldAge(heldSince) : null;
  const tooltip = base && age ? `${base} (held ${age})` : base;
  return (
    <Badge
      variant="outline"
      className={`${variantClass} text-[10px] font-normal border`}
      title={tooltip}
      data-ready-state={state}
    >
      {(state === "missing_dep" ||
        state === "gated" ||
        state === "missing_parent") && (
        <AlertTriangle className="h-3 w-3 mr-1" aria-hidden="true" />
      )}
      {label}
    </Badge>
  );
}
