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

// Per-deliverable-kind glyph for the Done-column card view. Instead of one
// generic 📎 count, a finished card shows one "{icon} {count}" chip per kind so
// an operator can see at a glance what shipped (a branch, a PR, N commits …).
// Keep the keys in sync with the Deliverable["kind"] union in types.ts; any
// kind absent here falls back to the generic 📦 in `deliverablesByKind`.
const DELIVERABLE_KIND_ICONS: Record<string, string> = {
  pr: "🔗",
  branch: "🔀",
  commit: "💻",
  link: "🔗",
  note: "📝",
  plan: "📋",
  plan_ref: "📋",
  spec: "📄",
};

const DONE_SUMMARY_MAX = 80;

// Board cards render at most this many free-form labels; the rest collapse into
// a single "+N" chip whose tooltip lists them. Free-form labels were the single
// biggest driver of card height on the real board — three 10-15 char labels
// ("tokens", "providers", "prompt-injectie") wrapped the meta row to three
// lines, so a card spent more vertical space on labels than on its own title.
const LABELS_SHOWN_MAX = 2;

function isFutureSchedule(scheduledAt: string | null): boolean {
  return !!scheduledAt && new Date(scheduledAt).getTime() > Date.now();
}

// Compact completion date for a Done card. "vandaag"/"gisteren" for the two most
// recent calendar days (the common case an operator scans), otherwise a short
// locale date like "10 Jul". The impure `new Date()` lives inside the helper —
// calling it directly in the component render body trips the react-compiler
// ESLint rule (see the note in CLAUDE.md / `isFutureSchedule`).
function formatCompletedDate(completedAt: string): string {
  const then = new Date(completedAt);
  const now = new Date();
  const dayMs = 24 * 60 * 60 * 1000;
  const startOfDay = (d: Date) =>
    new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const dayDiff = Math.round((startOfDay(now) - startOfDay(then)) / dayMs);
  if (dayDiff === 0) return "vandaag";
  if (dayDiff === 1) return "gisteren";
  return then.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

// Group a card's deliverables by kind, preserving first-seen order, so the
// Done-column view can render one "{icon} {count}" chip per kind instead of a
// single generic 📎 count. A kind with no specific glyph falls back to 📦.
function deliverablesByKind(
  deliverables: Card["deliverables"],
): { kind: string; icon: string; count: number }[] {
  const order: string[] = [];
  const counts: Record<string, number> = {};
  for (const d of deliverables) {
    if (!(d.kind in counts)) {
      counts[d.kind] = 0;
      order.push(d.kind);
    }
    counts[d.kind] += 1;
  }
  return order.map((kind) => ({
    kind,
    icon: DELIVERABLE_KIND_ICONS[kind] ?? "📦",
    count: counts[kind],
  }));
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

  // Done-column completion summary (kanban card e46f8d12). A finished card
  // shows a ✅ Done badge + short completion date, a truncated summary snippet,
  // and per-kind deliverable icons — so the Done column reads as "what shipped"
  // at a glance instead of a wall of bare titles. All gated on `isDone`; no
  // other column's rendering changes.
  const isDone = card.column === "Done";
  const completedLabel = card.completed_at
    ? formatCompletedDate(card.completed_at)
    : null;
  const doneSummary = card.done_summary?.trim() || null;
  const doneSummarySnippet = doneSummary
    ? doneSummary.slice(0, DONE_SUMMARY_MAX) +
      (doneSummary.length > DONE_SUMMARY_MAX ? "…" : "")
    : null;
  const deliverableChips = isDone ? deliverablesByKind(card.deliverables) : [];

  // Label diet (see LABELS_SHOWN_MAX): show the first two verbatim, fold the
  // rest into one "+N" chip that names them in its tooltip. Nothing is lost —
  // the drawer still lists every label.
  const shownLabels = labels.slice(0, LABELS_SHOWN_MAX);
  const hiddenLabels = labels.slice(LABELS_SHOWN_MAX);

  // Two ready-state chips are the same fact twice, and both land on the two
  // fullest columns — so they are the cheapest height to give back:
  //   - "Completed" next to ✅ Done. `readyState="completed"` is set iff
  //     `column === "Done"` (KanbanPage.tsx:243-245), so the chip carries no
  //     information the Done badge doesn't already carry.
  //   - "Impeded" next to a `needs answer` / `dispatch failed` chip. The
  //     impediment-status badge is strictly more specific; when it is absent
  //     (older card without an op-log status) the generic chip still renders.
  const showReadyState =
    !!readyState &&
    !(isDone && readyState === "completed") &&
    !(isImpediment && readyState === "impeded" && !!impedimentSpec);

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
      className={`${CLICKABLE_CARD} p-2 mb-1.5`}
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
      {/* The title owns the full card width and up to five lines. Three changes
          vs the previous layout, all aimed at "kan niet alles lezen" (kanban
          card 1fafd87c):
            - the status badges no longer sit beside the title stealing ~110px
              of its width on exactly the busiest columns (Done/Impediment);
            - the clamp went 2 → 5 lines. Measured title lengths on the real
              board: median 96 chars, p90 130, max 180 — a 2-line clamp cut 38
              of 49 rendered titles. The clamp is a ceiling, not a reserved
              height, so a short title still renders one line; 5 is where the
              measured curve knees (live board, 1440x900: clamp-3 → 27/40 titles
              cut at a 98px median card, clamp-4 → 12/40 at 115px, clamp-5 →
              6/40 at the *same* 115px, clamp-6 → 3/40 but one fewer card on
              screen). Five buys almost all the readability that six does and
              costs nothing extra in height;
            - `break-words` keeps a long unbroken token (a backticked flag, a
              branch name) inside the card instead of widening the lane.
          The tooltip carries the full title for whatever the ceiling still
          cuts. */}
      <div
        className="font-medium text-sm leading-tight line-clamp-5 break-words"
        title={card.title}
        data-testid="card-title"
      >
        {card.title}
      </div>
      {isDone && doneSummarySnippet && (
        <div
          className="mt-1 truncate text-xs text-muted-foreground"
          data-testid="done-summary-snippet"
          title={doneSummary ?? undefined}
        >
          {doneSummarySnippet}
        </div>
      )}
      {/* Metadata is a scan surface, not a read surface: it stays on ONE line,
          ordered most-informative-first, and clips instead of wrapping. Before,
          a card with three labels wrapped this row to three lines and pushed the
          median card to 144px tall — only 4 of 16 Backlog cards fitted on a
          1440x900 screen. Every fact here is still available in full in the
          drawer, and the chips that can be acted on (Redispatch / Promote)
          live in their own row below so they can never be clipped. */}
      <div
        className={
          "mt-1.5 flex items-center gap-1.5 overflow-hidden text-xs text-muted-foreground [&>*]:shrink-0 " +
          // The mask turns a hard clip into a fade, so a chip cut off at the
          // right edge reads as "there is more here" instead of as a rendering
          // bug. It is a no-op when the row fits: the gradient sits at the
          // row's right edge, where there is then nothing to fade.
          "[mask-image:linear-gradient(to_right,black_calc(100%-1.5rem),transparent)]"
        }
        data-testid="card-meta-row"
      >
        {isDone && (
          <Badge
            variant="secondary"
            className="text-[10px] font-normal"
            data-testid="done-badge"
            title={
              card.completed_at
                ? `Voltooid: ${new Date(card.completed_at).toLocaleString()}`
                : "Voltooid"
            }
          >
            &#9989; Done{completedLabel ? ` ${completedLabel}` : ""}
          </Badge>
        )}
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
        {showReadyState && readyState && (
          <ReadyStateBadge
            state={readyState}
            blockerTitles={blockerTitles}
            missingDepIds={missingDepIds}
            gatedOn={gatedOn}
            heldSince={heldSince}
          />
        )}
        {/* Deliverables sit high in the row on purpose: on a Done card "what
            shipped" is the second thing an operator looks for after the title,
            so it must never be the chip that gets clipped. */}
        {card.deliverables.length > 0 &&
          (isDone ? (
            <span
              className="inline-flex items-center gap-1.5"
              data-testid="done-deliverable-icons"
            >
              {deliverableChips.map((c) => (
                <span key={c.kind} title={`${c.count} ${c.kind}`}>
                  {c.icon} {c.count}
                </span>
              ))}
            </span>
          ) : (
            <span>&#128206; {card.deliverables.length}</span>
          ))}
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
        {shownLabels.map((l) => (
          <Badge
            key={l}
            variant={l === "error" ? "destructive" : "outline"}
            className="text-[10px] font-normal"
          >
            {l}
          </Badge>
        ))}
        {hiddenLabels.length > 0 && (
          <Badge
            variant="outline"
            className="text-[10px] font-normal"
            data-testid="labels-overflow-badge"
            title={hiddenLabels.join(", ")}
          >
            +{hiddenLabels.length}
          </Badge>
        )}
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
          <span className="truncate">&#128100; {card.claimed_by}</span>
        )}
      </div>
      {/* Action row. Kept out of the clipped metadata row above: a quick-action
          that is half-clipped is a quick-action you cannot click. Renders only
          for the two states that have one, so it costs zero height otherwise. */}
      {(canRedispatch || (isIntake && onPromote)) && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
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
      )}
    </UiCard>
  );
}
