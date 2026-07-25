import { useState } from "react";
import {
  AlertTriangle,
  HelpCircle,
  MessageSquareWarning,
  RefreshCw,
  Rocket,
  type LucideProps,
} from "lucide-react";
import { toast } from "sonner";
import { Card as UiCard } from "@/components/ui/card";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CLICKABLE_CARD } from "@/lib/constants";
import { kanbanApi } from "../api";
import type { Card } from "../types";
import { WORK_TYPES, WORK_TYPE_ICONS, PROVIDER_LABELS, type WorkType } from "../types";
import { ReadyStateBadge, type ReadyState } from "./ReadyStateBadge";
import type { SubtaskSummary } from "./Column";

const PRIORITY_VARIANT: Record<string, BadgeProps["variant"]> = {
  low: "secondary",
  medium: "default",
  high: "destructive",
};

function isFutureSchedule(scheduledAt: string | null): boolean {
  return !!scheduledAt && new Date(scheduledAt).getTime() > Date.now();
}

// Formats the "To Resume" auto-resume badge text. Deliberately a compact
// "Xh Ym" style rather than Intl.RelativeTimeFormat's "in 2 hours" — the
// badge is small and stacked among other badges, so the terser form fits.
function formatAutoResumeLabel(scheduledAt: string): string {
  const deltaMs = new Date(scheduledAt).getTime() - Date.now();
  if (deltaMs <= 0) return "Auto pending";
  if (deltaMs < 60_000) return "Auto soon";
  const totalMinutes = Math.round(deltaMs / 60_000);
  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;
  if (days > 0) return `Auto in ${days}d ${hours}h`;
  if (hours > 0) return minutes > 0 ? `Auto in ${hours}h ${minutes}m` : `Auto in ${hours}h`;
  return `Auto in ${minutes}m`;
}

// Impediment-lane badge map. Per kanban card `c5eb6f89` we distinguish four
// sub-states for cards on the Impediment column so the operator can tell
// at a glance whether a blocked card needs a written answer, an infra
// redispatch, or neither:
//
//   - "needs_answer"      → an open KanbanGate / `**Impediment:**` question
//     waits for a human decision. Drives the existing ResolveImpediment
//     control inside the drawer; the column badge is just an at-a-glance hint.
//   - "dispatch_failed"   → the 3×-dispatch-failure auto-move fired; the
//     right action is `Redispatch` (infra), not a written answer. The card
//     also gets a compact Redispatch button in the badge row.
//   - "resolved"          → a `**Resolution:**` answer was posted but the
//     card hasn't been moved off Impediment yet (transient, between
//     answer-click and Resolve-click). Distinguishes "answer recorded" from
//     "no question set" so the operator doesn't panic-think the answer
//     got lost.
//   - "no_question"       → bare-move state: the card sits on Impediment
//     but has no question and no failure-marker. Subtle hint only — the
//     drawer no longer pretends an answer is needed.
type ImpedimentStatus = NonNullable<Card["impediment_status"]>;

interface ImpedimentBadgeSpec {
  label: string;
  variant: BadgeProps["variant"];
  // Lucide icon component type — accepts the same `className` /
  // `aria-hidden` props we pass at the call sites.
  Icon: React.ComponentType<LucideProps>;
  title: string;
}

const IMPEDIMENT_BADGE: Record<ImpedimentStatus, ImpedimentBadgeSpec> = {
  needs_answer: {
    label: "needs answer",
    variant: "default",
    Icon: MessageSquareWarning,
    title:
      "Card is on Impediment with a pending question — click to open the drawer and answer.",
  },
  dispatch_failed: {
    label: "dispatch failed",
    variant: "destructive",
    Icon: AlertTriangle,
    title:
      "Auto-dispatch failed 3 times in a row — fix the spawn target (stale --resume worktree, missing sandcastle config, …) and Redispatch.",
  },
  resolved: {
    label: "resolved",
    variant: "secondary",
    Icon: HelpCircle,
    title:
      "A resolution was posted but the card hasn't moved off Impediment yet — click 'Resolve impediment' in the drawer to dispatch the resumed session.",
  },
  no_question: {
    label: "no question",
    variant: "outline",
    Icon: HelpCircle,
    title:
      "Card is on Impediment without a question or a known dispatch failure — likely a manual move; no answer expected.",
  },
};

export function CardItem({
  card,
  onOpen,
  readyState,
  blockerTitles,
  missingDepIds,
  gatedOn,
  heldSince,
  subtasks,
  projectPath,
  onPromote,
}: {
  card: Card;
  onOpen: (c: Card) => void;
  readyState?: ReadyState;
  blockerTitles?: string[];
  missingDepIds?: string[];
  // Operator-set `metadata.gated_on` string. Populated only for
  // `readyState === "gated"`; surfaces the trigger in the badge tooltip
  // (kanban-pro-analyse.md §4.1 AC3).
  gatedOn?: string;
  heldSince?: string;
  // Subtask rollup counts (done/total among cards whose parent_card_id
  // points at this card) — drives the compact "N/M subtasks" counter so
  // the operator can scan progress without opening the drawer.
  subtasks?: SubtaskSummary;
  // Needed for the dispatch_failed → Redispatch quick-action so the card can
  // call `kanbanApi.redispatch` directly without bouncing through the drawer.
  projectPath?: string;
  // Inceptie-pipeline entry point. Only meaningful on intake cards;
  // CardItem renders the Promote button iff this is set AND column=intake.
  onPromote?: (c: Card) => void;
}) {
  const priority = card.priority && card.priority !== "none" ? card.priority : null;
  const labels = card.labels ?? [];
  const scheduledAt = card.scheduled_at ?? null;
  const isToResume = card.column === "To Resume";
  const isPendingSchedule = isFutureSchedule(scheduledAt);
  const autoResumeLabel = scheduledAt ? formatAutoResumeLabel(scheduledAt) : "Auto";
  const autoResumeTooltip = scheduledAt
    ? `${scheduledAt} (local: ${new Date(scheduledAt).toLocaleString()})`
    : undefined;
  const workType = (card.work_type && (WORK_TYPES as readonly string[]).includes(card.work_type)
    ? (card.work_type as WorkType)
    : null);

  const isImpediment = card.column === "Impediment";
  const impedimentStatus = isImpediment ? card.impediment_status ?? null : null;
  const impedimentSpec = impedimentStatus ? IMPEDIMENT_BADGE[impedimentStatus] : null;
  const canRedispatch =
    impedimentStatus === "dispatch_failed" && !!projectPath;

  // Inceptie-pipeline entry point (kanban card c33b2f14 / facet A). Intake
  // cards are human-only — they never auto-dispatch — but the human can
  // promote them to a brand-new project via the Promote-to-project action.
  // The button is only rendered when the parent KanbanPage wires
  // `onPromote` down; otherwise intake cards are read-only until the page
  // decides otherwise.
  const isIntake = card.column === "intake";

  // Local "redispatching…" state so the compact button can show a brief
  // busy state without taking over the card. The parent board's 5s poll
  // picks up the new session name when the call returns; toast surfaces the
  // success/failure so the operator gets immediate feedback either way.
  const [redispatching, setRedispatching] = useState(false);
  const redispatch = async (e: React.MouseEvent | React.KeyboardEvent) => {
    // Stop the click/keypress from bubbling up to the card's onOpen / onKeyDown
    // — a Redispatch button inside a clickable card must NOT open the drawer.
    e.stopPropagation();
    if (!projectPath || redispatching) return;
    setRedispatching(true);
    try {
      const r = await kanbanApi.redispatch(
        card.id, projectPath, card.agent ?? undefined,
      );
      toast.success(`Re-dispatched — session ${r.session_name}`);
    } catch {
      toast.error("Re-dispatch failed — the spawn may have errored");
    } finally {
      setRedispatching(false);
    }
  };

  return (
    <UiCard
      className={`${CLICKABLE_CARD} p-3 mb-2`}
      role="button"
      tabIndex={0}
      onClick={() => onOpen(card)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen(card);
        }
      }}
      data-card-id={card.id}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="font-medium text-sm">{card.title}</div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
          {isToResume && (
            <Badge
              variant="outline"
              className="text-[10px] font-normal border text-muted-foreground"
              title={autoResumeTooltip}
            >
              <RefreshCw className="mr-1 h-3 w-3" aria-hidden="true" />
              {autoResumeLabel}
            </Badge>
          )}
          {impedimentSpec && (
            <Badge
              variant={impedimentSpec.variant}
              className="text-[10px] font-normal"
              title={impedimentSpec.title}
              data-testid="impediment-status-badge"
              data-impediment-status={impedimentStatus}
            >
              <impedimentSpec.Icon className="mr-1 h-3 w-3" aria-hidden="true" />
              {impedimentSpec.label}
            </Badge>
          )}
        </div>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {readyState && (
          <ReadyStateBadge
            state={readyState}
            blockerTitles={blockerTitles}
            missingDepIds={missingDepIds}
            gatedOn={gatedOn}
            heldSince={heldSince}
          />
        )}
        {subtasks && subtasks.total > 0 && (
          <Badge
            variant="outline"
            className="text-[10px] font-normal"
            data-testid="subtask-count-badge"
            title="Subtasks completed / total"
          >
            {subtasks.done}/{subtasks.total} subtasks
          </Badge>
        )}
        {workType && (
          <Badge variant="secondary" className="text-[10px] font-normal">
            {WORK_TYPE_ICONS[workType]} {workType}
          </Badge>
        )}
        {priority && (
          <Badge
            variant={PRIORITY_VARIANT[priority] ?? "outline"}
            className="text-[10px] font-normal"
          >
            {priority}
          </Badge>
        )}
        {labels.map((l) => (
          <Badge
            key={l}
            variant={l === "error" ? "destructive" : "outline"}
            className="text-[10px] font-normal"
          >
            {l}
          </Badge>
        ))}
        {card.analyst_agent_id && (
          <span className="inline-flex items-center gap-1 rounded bg-purple-100 px-2 py-0.5 text-xs font-medium text-purple-800">
            🪄 Multi-agent
          </span>
        )}
        {card.agent && (
          <Badge variant="secondary" className="text-[10px] font-normal">
            &#129302; {card.agent}
          </Badge>
        )}
        {card.dispatch_provider && (
          <Badge
            variant="outline"
            className="text-[10px] font-normal"
            data-testid="provider-badge"
            title={`Picked up by ${PROVIDER_LABELS[card.dispatch_provider] ?? card.dispatch_provider}`}
          >
            &#127760; {PROVIDER_LABELS[card.dispatch_provider] ?? card.dispatch_provider}
          </Badge>
        )}
        {isPendingSchedule && !isToResume && (
          <Badge variant="outline" className="text-[10px] font-normal">
            &#8987; {new Date(scheduledAt!).toLocaleString()}
          </Badge>
        )}
        {card.claimed_by && !readyState && (
          <span>&#128100; {card.claimed_by}</span>
        )}
        {card.deliverables.length > 0 && (
          <span>&#128206; {card.deliverables.length}</span>
        )}
        {canRedispatch && (
          <Button
            size="sm"
            variant="outline"
            className="h-6 px-2 text-[10px]"
            disabled={redispatching}
            onClick={redispatch}
            onKeyDown={(e) => {
              // Buttons natively handle Enter/Space via onClick; we only
              // need to stop propagation so the card's outer onKeyDown
              // (which opens the drawer) doesn't double-fire.
              e.stopPropagation();
            }}
            data-testid="redispatch-quick-action"
            title="Re-attempt dispatch after fixing the spawn target"
          >
            <RefreshCw className="mr-1 h-3 w-3" aria-hidden="true" />
            {redispatching ? "Redispatching…" : "Redispatch"}
          </Button>
        )}
        {isIntake && onPromote && (
          <Button
            size="sm"
            variant="default"
            className="h-6 px-2 text-[10px]"
            onClick={(e) => {
              // Same rationale as Redispatch: a button inside a clickable
              // card must NOT open the drawer — only the button's own
              // handler runs.
              e.stopPropagation();
              onPromote(card);
            }}
            onKeyDown={(e) => e.stopPropagation()}
            data-testid="promote-to-project-quick-action"
            title="Promote this intake card to a brand-new project on the kanban board"
          >
            <Rocket className="mr-1 h-3 w-3" aria-hidden="true" />
            Promote to project
          </Button>
        )}
      </div>
    </UiCard>
  );
}
