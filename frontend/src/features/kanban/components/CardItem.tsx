import { RefreshCw } from "lucide-react";
import { Card as UiCard } from "@/components/ui/card";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { CLICKABLE_CARD } from "@/lib/constants";
import type { Card } from "../types";
import { WORK_TYPES, WORK_TYPE_ICONS, type WorkType } from "../types";
import { ReadyStateBadge, type ReadyState } from "./ReadyStateBadge";

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

export function CardItem({
  card,
  onOpen,
  readyState,
  blockerTitles,
}: {
  card: Card;
  onOpen: (c: Card) => void;
  readyState?: ReadyState;
  blockerTitles?: string[];
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
    >
      <div className="flex items-start justify-between gap-2">
        <div className="font-medium text-sm">{card.title}</div>
        {isToResume && (
          <Badge
            variant="outline"
            className="shrink-0 text-[10px] font-normal border text-muted-foreground"
            title={autoResumeTooltip}
          >
            <RefreshCw className="mr-1 h-3 w-3" aria-hidden="true" />
            {autoResumeLabel}
          </Badge>
        )}
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {readyState && (
          <ReadyStateBadge state={readyState} blockerTitles={blockerTitles} />
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
      </div>
    </UiCard>
  );
}
