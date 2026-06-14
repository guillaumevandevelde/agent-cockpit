import { COLUMNS, type Card, type Column as Col } from "../types";
import { Column } from "./Column";

export function Board({
  cards,
  onOpen,
  onMove,
}: {
  cards: Card[];
  onOpen: (c: Card) => void;
  onMove: (cardId: string, column: Col) => void;
}) {
  return (
    <div className="flex gap-3 overflow-x-auto pb-4">
      {COLUMNS.map((col) => (
        <Column
          key={col}
          column={col}
          cards={cards.filter((c) => c.column === col)}
          onOpen={onOpen}
          onDropCard={onMove}
        />
      ))}
    </div>
  );
}
