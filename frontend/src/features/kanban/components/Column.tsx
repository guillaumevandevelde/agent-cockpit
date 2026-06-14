import type { Card, Column as Col } from "../types";
import { CardItem } from "./CardItem";

export function Column({
  column,
  cards,
  onOpen,
  onDropCard,
}: {
  column: Col;
  cards: Card[];
  onOpen: (c: Card) => void;
  onDropCard: (cardId: string, column: Col) => void;
}) {
  return (
    <div
      className="flex-1 min-w-56 bg-muted/40 rounded-lg p-2"
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => onDropCard(e.dataTransfer.getData("text/card-id"), column)}
    >
      <div className="px-1 pb-2 text-xs font-semibold uppercase text-muted-foreground">
        {column} <span className="ml-1">({cards.length})</span>
      </div>
      {cards.map((c) => (
        <div
          key={c.id}
          draggable
          onDragStart={(e) => e.dataTransfer.setData("text/card-id", c.id)}
        >
          <CardItem card={c} onOpen={onOpen} />
        </div>
      ))}
    </div>
  );
}
