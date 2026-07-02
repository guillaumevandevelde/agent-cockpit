import { Card as UiCard } from "@/components/ui/card";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { CLICKABLE_CARD } from "@/lib/constants";
import type { Card } from "../types";

const PRIORITY_VARIANT: Record<string, BadgeProps["variant"]> = {
  low: "secondary",
  medium: "default",
  high: "destructive",
};

export function CardItem({ card, onOpen }: { card: Card; onOpen: (c: Card) => void }) {
  const priority = card.priority && card.priority !== "none" ? card.priority : null;
  const labels = card.labels ?? [];

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
      <div className="font-medium text-sm">{card.title}</div>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        {priority && (
          <Badge
            variant={PRIORITY_VARIANT[priority] ?? "outline"}
            className="text-[10px] font-normal"
          >
            {priority}
          </Badge>
        )}
        {labels.map((l) => (
          <Badge key={l} variant="outline" className="text-[10px] font-normal">
            {l}
          </Badge>
        ))}
        {card.agent && (
          <Badge variant="secondary" className="text-[10px] font-normal">
            &#129302; {card.agent}
          </Badge>
        )}
        {card.claimed_by && <span>&#128100; {card.claimed_by}</span>}
        {card.deliverables.length > 0 && (
          <span>&#128206; {card.deliverables.length}</span>
        )}
      </div>
    </UiCard>
  );
}
