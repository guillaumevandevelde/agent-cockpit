import { describe, expect, it } from "vitest";

import { reorderColumnByFilteredTarget } from "./reorder";

const id = (s: string) => ({ id: s });

// Kanban card e9089ecad8e64b19a25bdf59804b70de: the old implementation
// computed the drop position over the filtered card list but applied it to
// the unfiltered column list. These tests pin the fix: the drop target is
// expressed in the filtered view (by card id) and the result preserves the
// relative order of non-matching cards. The unfiltered list is what gets
// sent to the backend as `orderedIds`, so this is the function that
// decides what the user actually sees after the reorder settles.
describe("reorderColumnByFilteredTarget", () => {
  // Sanity: when the filter matches everything, the result matches the
  // legacy numeric-index behaviour — drop-before-X means insert before X,
  // drop-end means append. Without this baseline the fix could silently
  // change behaviour for operators who never type a filter.
  describe("without a filter (visibleCards === colCards)", () => {
    it("moves the dragged card before the named target", () => {
      const col = [id("a"), id("b"), id("c"), id("d")];
      expect(
        reorderColumnByFilteredTarget(col, col, "d", "b").map((c) => c.id),
      ).toEqual(["a", "d", "b", "c"]);
    });

    it("appends at the end when dropBeforeId is null", () => {
      const col = [id("a"), id("b"), id("c")];
      expect(
        reorderColumnByFilteredTarget(col, col, "a", null).map((c) => c.id),
      ).toEqual(["b", "c", "a"]);
    });

    it("is a no-op when the dragged card drops on itself", () => {
      // Drop target = the dragged card's own id (operator dragged it
      // into its own slot). The list must not shuffle.
      const col = [id("a"), id("b"), id("c")];
      expect(
        reorderColumnByFilteredTarget(col, col, "b", "b").map((c) => c.id),
      ).toEqual(["a", "b", "c"]);
    });

    it("is a no-op when the dragged card is missing from the column", () => {
      const col = [id("a"), id("b"), id("c")];
      expect(
        reorderColumnByFilteredTarget(col, col, "missing", "a").map((c) => c.id),
      ).toEqual(["a", "b", "c"]);
    });
  });

  // The actual bug scenario from the card. Filter "zenith" matches B and
  // D out of nine Backlog cards. Dropping D above B must land D right
  // before B in the unfiltered list, with A, C, E untouched. The
  // pre-fix code took the filtered index 0 and applied it to the full
  // list, producing [D, A, B, C, E] — a completely different reorder.
  describe("with an active filter", () => {
    const col = [
      id("a"),
      id("b"),
      id("c"),
      id("d"),
      id("e"),
      id("f"),
      id("g"),
      id("h"),
      id("i"),
    ];
    const visible = [id("b"), id("d"), id("f"), id("h")];

    it("moves D above B without disturbing A, C, E, F, G, H, I", () => {
      expect(
        reorderColumnByFilteredTarget(col, visible, "d", "b").map((c) => c.id),
      ).toEqual(["a", "d", "b", "c", "e", "f", "g", "h", "i"]);
    });

    it("moves F above D without disturbing the rest", () => {
      expect(
        reorderColumnByFilteredTarget(col, visible, "f", "d").map((c) => c.id),
      ).toEqual(["a", "b", "c", "f", "d", "e", "g", "h", "i"]);
    });

    it("moving F to the end of the filtered view places it after the last visible (H)", () => {
      // The visible cards are [b, d, f, h] — operator drags F to the
      // end. "End of filtered view" must mean "after H" (the rightmost
      // visible card in the unfiltered list), NOT "after I" (the
      // unfiltered tail). Without the target-by-id mapping, the old
      // code took the filtered end index and applied it to the full
      // list, dropping F past I.
      expect(
        reorderColumnByFilteredTarget(col, visible, "f", null).map((c) => c.id),
      ).toEqual(["a", "b", "c", "d", "e", "g", "h", "f", "i"]);
    });

    it("dragging the last visible card to the end is a no-op (it is already last)", () => {
      // H is already the last visible card; dragging it to the end of
      // the filtered view is a no-op (the result must equal the input).
      expect(
        reorderColumnByFilteredTarget(col, visible, "h", null).map((c) => c.id),
      ).toEqual(["a", "b", "c", "d", "e", "f", "g", "h", "i"]);
    });

    it("moving B to the end of the filtered view places it after H", () => {
      // The trickier case: the dragged card (B) is the FIRST visible
      // card, but the user drops at the end of the filtered view. The
      // expected result is "B moves to after H, the last visible card" —
      // the unfiltered cards between them (C, D, E, F, G) keep their
      // relative order. Without the target-by-id mapping the old code
      // would compute insertAt from the *filtered* index and put B at
      // the literal end of the unfiltered list, after I.
      expect(
        reorderColumnByFilteredTarget(col, visible, "b", null).map((c) => c.id),
      ).toEqual(["a", "c", "d", "e", "f", "g", "h", "b", "i"]);
    });

    it("drops a non-visible card above a visible one correctly", () => {
      // Operator drags card A (not in the filtered view) onto B — A
      // was sourced from another column or from the same column via a
      // cross-lane drop. A lands before B, the rest stays put.
      expect(
        reorderColumnByFilteredTarget(col, visible, "a", "b").map((c) => c.id),
      ).toEqual(["a", "b", "c", "d", "e", "f", "g", "h", "i"]);
    });

    it("drops a non-visible card at the end of the filtered view after the last visible", () => {
      // Symmetric edge: dragged card not visible, drop at end of
      // filtered view → after H, before I.
      expect(
        reorderColumnByFilteredTarget(col, visible, "c", null).map((c) => c.id),
      ).toEqual(["a", "b", "d", "e", "f", "g", "h", "c", "i"]);
    });

    it("falls back to append-at-end when the filter matches no card in this column", () => {
      // Visible subset for THIS column is empty (the filter matches
      // cards in a different column). dropBeforeId === null must then
      // mean "append at end of the unfiltered column" — there is no
      // visible anchor to map to.
      const visibleNone: typeof col = [];
      expect(
        reorderColumnByFilteredTarget(col, visibleNone, "a", null).map(
          (c) => c.id,
        ),
      ).toEqual(["b", "c", "d", "e", "f", "g", "h", "i", "a"]);
    });
  });

  it("is a no-op when the drop target id is not in the column (race with a concurrent poll/move)", () => {
    // The card may have moved out of the column between the dragOver
    // (which set the target) and the drop (which fires this). Dropping
    // onto a stale target would guess where to insert; we'd rather
    // leave the order untouched so the operator notices the move and
    // retries.
    const col = [id("a"), id("b"), id("c")];
    expect(
      reorderColumnByFilteredTarget(col, col, "a", "deleted-card").map(
        (c) => c.id,
      ),
    ).toEqual(["a", "b", "c"]);
  });

  it("preserves the caller's input arrays (does not mutate)", () => {
    // The caller passes `colCards` straight from React state. Mutating
    // it would corrupt subsequent renders. Return a new array every time.
    const col = [id("a"), id("b"), id("c"), id("d")];
    const snapshot = col.map((c) => c.id);
    reorderColumnByFilteredTarget(col, col, "d", "b");
    expect(col.map((c) => c.id)).toEqual(snapshot);
  });
});