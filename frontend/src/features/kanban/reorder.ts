/**
 * Compute the new column order after a drag-drop, when the drop target is
 * expressed in the **filtered** view the operator is looking at.
 *
 * Kanban card e9089ecad8e64b19a25bdf59804b70de: the old implementation
 * computed the drop position over the filtered card list but applied it to
 * the unfiltered column list — filter on "zenith" with 9 Backlog cards, drop
 * the second visible card above the first, and the card would land on
 * position 0-1 of the full list instead of between the visible cards. The
 * rank feeds dispatch order on Backlog, so the visible "I just moved this
 * to the top" gesture silently reordered the whole queue.
 *
 * The fix takes the drop target as a **card id** (the one above which the
 * operator released the drag) and maps it back to the right position in
 * the unfiltered list. The non-matching cards preserve their relative
 * order; only the dragged card moves.
 *
 * Pure helper extracted so the rule is testable without the DOM (the
 * dragOver geometry used by Column.tsx is fragile in jsdom — see the
 * Board.test.tsx note around line 148). KanbanPage.reorderWithin calls
 * this with `colCards` (unfiltered) and `visibleCards` (the filtered
 * subset for this column), then sends the resulting `orderedIds` to the
 * backend.
 */
export function reorderColumnByFilteredTarget<T extends { id: string }>(
  colCards: readonly T[],
  visibleCards: readonly T[],
  cardId: string,
  dropBeforeId: string | null,
): T[] {
  const oldIndex = colCards.findIndex((c) => c.id === cardId);
  if (oldIndex === -1) return [...colCards];

  let targetIndex: number;
  if (dropBeforeId === null) {
    // Drop at the end of the filtered view. Map this to "after the last
    // visible card in the unfiltered list" so the operator's intent
    // ("this card should sit at the bottom of what I see") survives the
    // map. If the filter matched nothing in this column, the visible
    // subset is empty and we fall back to append-at-end — the dragged
    // card itself is the only thing the operator could see.
    const lastVisible = visibleCards[visibleCards.length - 1];
    if (!lastVisible) {
      targetIndex = colCards.length;
    } else {
      const idx = colCards.findIndex((c) => c.id === lastVisible.id);
      targetIndex = idx === -1 ? colCards.length : idx + 1;
    }
  } else {
    targetIndex = colCards.findIndex((c) => c.id === dropBeforeId);
    // The target disappeared between the dragOver and the drop (race
    // with a poll, a concurrent move). No-op rather than guess — the
    // operator will simply retry.
    if (targetIndex === -1) return [...colCards];
  }

  // `without` is the unfiltered list with the dragged card removed, so
  // every index from `oldIndex` onwards has shifted down by one. The
  // pre-removal `targetIndex` must be re-mapped into that shorter array
  // before we splice back in.
  const without = colCards.filter((c) => c.id !== cardId);
  const insertAt = targetIndex > oldIndex ? targetIndex - 1 : targetIndex;
  if (insertAt < 0 || insertAt > without.length) return [...colCards];
  without.splice(insertAt, 0, colCards[oldIndex]);
  return without;
}