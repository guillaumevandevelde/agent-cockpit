import { Card as UiCard } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CLICKABLE_CARD } from "@/lib/constants";
import type { Card } from "../types";

export function CardItem({ card, onOpen }: { card: Card; onOpen: (c: Card) => void }) {
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
