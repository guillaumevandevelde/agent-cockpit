import type { Card, KanbanColumn } from "../types";
import { Column } from "./Column";

export function Board({
  columns,
  cards,
  onOpen,
  onMove,
}: {
  columns: KanbanColumn[];
  cards: Card[];
  onOpen: (c: Card) => void;
  onMove: (cardId: string, column: string) => void;
}) {
  return (
    <div className="flex gap-3 overflow-x-auto pb-4">
      {columns.map((col) => (
        <Column
          key={col.id}
          column={col.name}
          cards={cards.filter((c) => c.column === col.name)}
          onOpen={onOpen}
          onDropCard={onMove}
        />
      ))}
    </div>
  );
}
