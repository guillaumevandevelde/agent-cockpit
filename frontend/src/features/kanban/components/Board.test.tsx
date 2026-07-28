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

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

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
  return render(
    <Board columns={columns} cards={cards} onOpen={() => {}} onDropCardAt={() => {}} />,
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
    expect(onDropCardAt).toHaveBeenCalledWith("a", "reviewer", 0);
  });
});
