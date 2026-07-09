// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { CardItem } from "./CardItem";
import type { Card } from "../types";

const baseCard: Card = {
  id: "card-1",
  project_key: "proj-1",
  title: "Test card",
  description: "",
  column: "Backlog",
  rank: "0001",
  work_type: "feature",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  deliverables: [],
};

afterEach(() => {
  cleanup();
});

describe("CardItem work_type badge", () => {
  it("renders the chosen work_type as a badge on the card", () => {
    render(<CardItem card={baseCard} onOpen={() => {}} />);
    // The badge text content is the icon + space + work_type, rendered as
    // separate text nodes inside the badge; match the rendered concatenated
    // text via a regex (textContent joins adjacent text nodes).
    const badge = screen.getByText(/feature/);
    expect(badge.textContent).toBe("✨ feature");
  });

  it("does not render any work_type label when card.work_type is unset", () => {
    render(
      <CardItem
        card={{ ...baseCard, work_type: null }}
        onOpen={() => {}}
      />,
    );
    expect(screen.queryByText("feature")).toBeNull();
    expect(screen.queryByText("analysis")).toBeNull();
    expect(screen.queryByText("bug")).toBeNull();
    expect(screen.queryByText("chore")).toBeNull();
  });

  it("still calls onOpen when the badge-bearing card is clicked", () => {
    const onOpen = vi.fn();
    render(<CardItem card={baseCard} onOpen={onOpen} />);
    screen.getByRole("button", { name: /test card/i }).click();
    expect(onOpen).toHaveBeenCalledWith(baseCard);
  });
});
