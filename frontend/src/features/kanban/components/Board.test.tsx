// @vitest-environment jsdom
//
// Kanban card 1fafd87c ("nog veel te vaak scrollen"). Measured on the live board
// before this change: 7 lanes × 256px + gaps = 1864px of content, against 1136px
// of viewport at 1440x900 — 728px of permanent horizontal scroll, with two of the
// seven lanes (reviewer, analyst) empty and each still holding a full lane width
// hostage. These tests pin the lane-width budget: empty lanes start as rails, and
// an explicit operator choice always wins over that default.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

// CardItem now calls useNavigate() for Impediment cards (kaart 626e05e3…).
// The Board tests render CardItem in isolation (some through `renderBoard`
// wrapped in a MemoryRouter, others directly). Stub useNavigate so the
// Impediment-specific navigation path doesn't blow up in either shape —
// the navigation behaviour itself is covered by ImpedimentPage.test.tsx
// and the CardItem Impediment click test.
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom",
  );
  const StubMemoryRouter = ({ children }: { children: React.ReactNode }) => children;
  const StubLink = ({
    children,
    to,
  }: {
    children: React.ReactNode;
    to: string;
  }) => <a href={typeof to === "string" ? to : "#"}>{children}</a>;
  return {
    ...actual,
    MemoryRouter: StubMemoryRouter,
    Link: StubLink,
    useNavigate: () => vi.fn(),
  };
});

vi.mock("../api", async (importOriginal) => {
  const actual = (await importOriginal()) as { kanbanApi: Record<string, unknown> };
  const stub: Record<string, ReturnType<typeof vi.fn>> = {};
  for (const key of Object.keys(actual.kanbanApi)) stub[key] = vi.fn(async () => ({}));
  return { kanbanApi: stub };
});

const { Board } = await import("./Board");
import type { Card, KanbanColumn } from "../types";

const col = (id: string, name: string): KanbanColumn => ({
  id,
  project_key: "proj-1",
  name,
  rank: id,
  default_agent: null,
  default_provider: null,
  default_model: null,
  max_sessions: null,
  token_saver_enabled: false,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
});

const card = (id: string, column: string): Card => ({
  id,
  project_key: "proj-1",
  title: `Card ${id}`,
  description: "",
  column,
  rank: "0001",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  deliverables: [],
});

const columns = [col("c1", "Backlog"), col("c2", "reviewer")];

function renderBoard(cards: Card[]) {
  // CardItem now calls useNavigate() for Impediment cards (kaart 626e05e3…),
  // so the test tree must provide a Router. MemoryRouter keeps the URL
  // pinned to "/" so a navigate() call is a no-op for non-impediment tests.
  return render(
    <MemoryRouter>
      <Board columns={columns} cards={cards} onOpen={() => {}} onDropCardAt={() => {}} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  cleanup();
});

describe("Board lane collapsing", () => {
  it("starts an empty lane as a rail and a populated lane expanded", () => {
    renderBoard([card("a", "Backlog")]);
    expect(
      screen.getByTestId("kanban-column-Backlog").getAttribute("data-collapsed"),
    ).toBe("false");
    expect(
      screen.getByTestId("kanban-column-reviewer").getAttribute("data-collapsed"),
    ).toBe("true");
  });

  it("keeps the card count visible on a collapsed rail", () => {
    // A rail that hides its count would make an operator expand every lane just
    // to find out whether anything is in it.
    renderBoard([card("a", "Backlog"), card("b", "reviewer")]);
    fireEvent.click(screen.getByTestId("kanban-column-collapse-reviewer"));
    const rail = screen.getByTestId("kanban-column-expand-reviewer");
    expect(rail.textContent).toContain("1");
    expect(rail.textContent).toContain("reviewer");
  });

  it("expands a collapsed lane when its rail is clicked", () => {
    renderBoard([card("a", "Backlog")]);
    fireEvent.click(screen.getByTestId("kanban-column-expand-reviewer"));
    expect(
      screen.getByTestId("kanban-column-reviewer").getAttribute("data-collapsed"),
    ).toBe("false");
  });

  it("collapses a populated lane when its header chevron is clicked", () => {
    renderBoard([card("a", "Backlog")]);
    fireEvent.click(screen.getByTestId("kanban-column-collapse-Backlog"));
    expect(
      screen.getByTestId("kanban-column-Backlog").getAttribute("data-collapsed"),
    ).toBe("true");
    // Cards in a collapsed lane are not rendered — that is the whole point.
    expect(screen.queryByText("Card a")).toBeNull();
  });

  it("persists the explicit choice so a reload keeps the operator's layout", () => {
    const { unmount } = renderBoard([card("a", "Backlog")]);
    fireEvent.click(screen.getByTestId("kanban-column-collapse-Backlog"));
    expect(JSON.parse(localStorage.getItem("kanban-collapsed-columns")!)).toEqual({
      c1: true,
    });
    unmount();
    renderBoard([card("a", "Backlog")]);
    expect(
      screen.getByTestId("kanban-column-Backlog").getAttribute("data-collapsed"),
    ).toBe("true");
  });

  it("lets an explicit expand survive a lane going empty", () => {
    // The "empty lanes start collapsed" rule is a default, not an override: an
    // operator who pinned a lane open (e.g. to drop cards into it) keeps it open.
    localStorage.setItem("kanban-collapsed-columns", JSON.stringify({ c2: false }));
    renderBoard([card("a", "Backlog")]);
    expect(
      screen.getByTestId("kanban-column-reviewer").getAttribute("data-collapsed"),
    ).toBe("false");
  });

  it("ignores corrupt persisted state instead of failing to render", () => {
    localStorage.setItem("kanban-collapsed-columns", "not json");
    renderBoard([card("a", "Backlog")]);
    expect(screen.getByTestId("kanban-column-Backlog")).not.toBeNull();
  });

  it("still accepts a card drop onto a collapsed lane", () => {
    // Moving a card into a lane must not require expanding it first.
    const onDropCardAt = vi.fn();
    render(
      <Board
        columns={columns}
        cards={[card("a", "Backlog")]}
        onOpen={() => {}}
        onDropCardAt={onDropCardAt}
      />,
    );
    const rail = screen.getByTestId("kanban-column-reviewer");
    fireEvent.drop(rail, {
      dataTransfer: { getData: () => "a" },
    });
    // Kanban card e9089ecad8e64b19a25bdf59804b70de: drop target is now
    // an id-or-null (the card above which to drop), not a numeric index.
    // null here means "after the last visible card" — the rail has zero
    // visible cards, so it collapses to "append at end of the column".
    expect(onDropCardAt).toHaveBeenCalledWith("a", "reviewer", null);
  });
});

// Kanban card 4f0677c7…: the board used to render *only* the columns with a
// `kanban_columns` row and bucket cards by `c.column === col.name`, so a card
// on any other column fell out of every lane and was invisible — 25 cards on
// `To Resume` on the live board, while the toolbar still counted them in
// "Dispatch all (41)". The backend backfills the fixed column names; these
// tests pin the frontend's guarantee for everything else: no card is ever
// silently dropped.
describe("Board lanes for columns without a kanban_columns row", () => {
  it("renders a card whose column has no row instead of dropping it", () => {
    renderBoard([card("a", "Backlog"), card("b", "To Resume")]);
    const lane = screen.getByTestId("kanban-column-To Resume");
    expect(lane.getAttribute("data-unconfigured")).toBe("true");
    expect(screen.getByText("Card b")).not.toBeNull();
  });

  it("flags the lane so the operator can see the column is not configured", () => {
    renderBoard([card("b", "Doing")]);
    const marker = screen.getByTestId("kanban-column-unconfigured-Doing");
    expect(marker.getAttribute("title")).toContain("geen kolomrij");
    // Configured lanes stay unflagged.
    expect(
      screen.getByTestId("kanban-column-Backlog").getAttribute("data-unconfigured"),
    ).toBe("false");
    expect(screen.queryByTestId("kanban-column-unconfigured-Backlog")).toBeNull();
  });

  it("adds no lane when every card's column has a row", () => {
    renderBoard([card("a", "Backlog"), card("b", "reviewer")]);
    expect(
      document.querySelectorAll('[data-unconfigured="true"]').length,
    ).toBe(0);
  });

  it("groups every stranded column into its own lane, sorted", () => {
    renderBoard([
      card("a", "To Resume"),
      card("b", "Doing"),
      card("c", "To Resume"),
    ]);
    const lanes = [...document.querySelectorAll('[data-unconfigured="true"]')].map(
      (el) => el.getAttribute("data-testid"),
    );
    expect(lanes).toEqual(["kanban-column-Doing", "kanban-column-To Resume"]);
    expect(screen.getByTestId("kanban-column-To Resume").textContent).toContain("(2)");
  });

  it("starts expanded — a stranded card must be readable, not hidden in a rail", () => {
    renderBoard([card("b", "To Resume")]);
    expect(
      screen.getByTestId("kanban-column-To Resume").getAttribute("data-collapsed"),
    ).toBe("false");
  });

  it("lets a card be dragged out of a stranded lane onto a real one", () => {
    const onDropCardAt = vi.fn();
    render(
      <Board
        columns={columns}
        cards={[card("b", "To Resume")]}
        onOpen={() => {}}
        onDropCardAt={onDropCardAt}
      />,
    );
    fireEvent.drop(screen.getByTestId("kanban-column-Backlog"), {
      dataTransfer: { getData: () => "b" },
    });
    // Same drop-target-by-id contract as above — null here because the
    // test fires drop without a preceding dragOver (no card id to land
    // on), so the helper receives "end of filtered view".
    expect(onDropCardAt).toHaveBeenCalledWith("b", "Backlog", null);
  });
});

// Kanban card e9089ecad8e64b19a25bdf59804b70de revisitation. The drag-reorder
// fix (commit 1a76980f) switched the end-of-column indicator from a numeric
// `dropIndex === cards.length` check to `dropBeforeId === null`. The new
// condition is also the rest state, so every populated column rendered a
// permanent blue strip — the board looked broken in its idle state. The fix
// adds a `dragOver` gate in Column.tsx; these tests pin the rest-state
// behaviour so the regression cannot silently come back.
describe("Board drop-strip in rest state", () => {
  it("renders no end-of-column strip when no drag is in progress", () => {
    // Populated column (cards.length > 0 fulfils the population gate; the
    // strip must still NOT render because dragOver is false).
    renderBoard([card("a", "Backlog"), card("b", "Backlog")]);
    expect(screen.queryByTestId("kanban-column-drop-strip-Backlog")).toBeNull();
  });

  it("renders the strip only on a column that has at least one card", () => {
    // Backlog has cards, reviewer does not. Before the fix, the strip
    // appeared on Backlog (the populated column) because `dropBeforeId ===
    // null && cards.length > 0` was satisfied at rest. The fix tightens
    // that to `dragOver && dropBeforeId === null && cards.length > 0`,
    // which is false at rest → no strip on either column.
    renderBoard([card("a", "Backlog")]);
    expect(screen.queryByTestId("kanban-column-drop-strip-Backlog")).toBeNull();
    expect(screen.queryByTestId("kanban-column-drop-strip-reviewer")).toBeNull();
  });

  it("treats every populated column the same — no column-wide permanent strip", () => {
    // Regression-shaped assertion: render the board with a card in each
    // configured lane, then count the rest-state strips. The pre-fix code
    // rendered one per populated column ⇒ 1 here. The fix must render 0.
    renderBoard([card("a", "Backlog"), card("b", "reviewer")]);
    expect(
      document.querySelectorAll('[data-testid^="kanban-column-drop-strip-"]').length,
    ).toBe(0);
  });
});
