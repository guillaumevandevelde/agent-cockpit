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

describe("CardItem ReadyStateBadge", () => {
  it("renders a 'Ready' badge when readyState='ready' is supplied", () => {
    render(
      <CardItem card={baseCard} readyState="ready" onOpen={() => {}} />,
    );
    expect(screen.getByText("Ready")).not.toBeNull();
    expect(screen.queryByText("Blocked")).toBeNull();
    expect(screen.queryByText("Dispatching")).toBeNull();
  });

  it("renders a 'Blocked' badge with blocker titles in the tooltip", () => {
    // Tooltip text is exposed via the standard `title` HTML attribute, which
    // jsdom turns into the `title` property on the element. Reading it back
    // here pins the contract — the CardDrawer / KanbanPage must list the
    // blocker titles so an operator can see at a glance which other cards
    // gate this one, instead of having to open the deps one-by-one.
    render(
      <CardItem
        card={baseCard}
        readyState="blocked"
        blockerTitles={["Parent A", "Parent B"]}
        onOpen={() => {}}
      />,
    );
    const blocked = screen.getByText("Blocked");
    expect(blocked).not.toBeNull();
    expect(blocked.getAttribute("title")).toBe("Blocked by: Parent A, Parent B");
    expect(screen.queryByText("Ready")).toBeNull();
    expect(screen.queryByText("Dispatching")).toBeNull();
  });

  it("renders a 'Dispatching' badge when readyState='dispatching' is supplied", () => {
    render(
      <CardItem
        card={{ ...baseCard, claimed_by: "agent:tmux-x" }}
        readyState="dispatching"
        onOpen={() => {}}
      />,
    );
    expect(screen.getByText("Dispatching")).not.toBeNull();
    expect(screen.queryByText("Ready")).toBeNull();
    expect(screen.queryByText("Blocked")).toBeNull();
  });

  it("omits the ready-state badge entirely when no readyState prop is passed", () => {
    // Backwards compat: every existing caller that doesn't compute state
    // (e.g. a future card-detail panel rendering) shouldn't suddenly grow
    // a 'Ready' badge out of nowhere. The opt-in prop keeps behaviour for
    // untouched callers identical.
    render(<CardItem card={baseCard} onOpen={() => {}} />);
    expect(screen.queryByText("Ready")).toBeNull();
    expect(screen.queryByText("Blocked")).toBeNull();
    expect(screen.queryByText("Dispatching")).toBeNull();
  });
});
