// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../api", async (importOriginal) => {
  const actual = (await importOriginal()) as { kanbanApi: Record<string, unknown> };
  const stub: Record<string, ReturnType<typeof vi.fn>> = {};
  for (const key of Object.keys(actual.kanbanApi)) {
    stub[key] = vi.fn(async () => ({}));
  }
  return { kanbanApi: stub };
});

const { kanbanApi } = await import("../api");
const { CardItem } = await import("./CardItem");
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

describe("CardItem labels", () => {
  it("renders the 'error' label as a red (destructive) badge", () => {
    render(
      <CardItem
        card={{ ...baseCard, labels: ["error", "kanban"] }}
        onOpen={() => {}}
      />,
    );
    const errorBadge = screen.getByText("error");
    // destructive variant uses the bg-destructive utility (see badge.tsx)
    expect(errorBadge.className).toContain("bg-destructive");
    // a normal label stays a plain outline badge
    const plainBadge = screen.getByText("kanban");
    expect(plainBadge.className).not.toContain("bg-destructive");
  });
});

describe("CardItem To Resume auto-resume badge", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows 'Auto in <relative>' for a To Resume card with a future scheduled_at", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    render(
      <CardItem
        card={{ ...baseCard, column: "To Resume", scheduled_at: "2026-01-01T02:14:00Z" }}
        onOpen={() => {}}
      />,
    );
    expect(screen.getByText("Auto in 2h 14m")).not.toBeNull();
  });

  it("shows only the 'Auto' label (no timestamp) when scheduled_at is unset", () => {
    render(
      <CardItem card={{ ...baseCard, column: "To Resume", scheduled_at: null }} onOpen={() => {}} />,
    );
    expect(screen.getByText("Auto")).not.toBeNull();
    expect(screen.queryByText(/Auto in/)).toBeNull();
  });

  it("shows 'Auto soon' when scheduled_at is under a minute away", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    render(
      <CardItem
        card={{ ...baseCard, column: "To Resume", scheduled_at: "2026-01-01T00:00:30Z" }}
        onOpen={() => {}}
      />,
    );
    expect(screen.getByText("Auto soon")).not.toBeNull();
  });

  it("shows 'Auto pending' when scheduled_at is in the past", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    render(
      <CardItem
        card={{ ...baseCard, column: "To Resume", scheduled_at: "2025-12-31T23:00:00Z" }}
        onOpen={() => {}}
      />,
    );
    expect(screen.getByText("Auto pending")).not.toBeNull();
  });

  it("exposes the UTC ISO timestamp and local time via the title tooltip", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    render(
      <CardItem
        card={{ ...baseCard, column: "To Resume", scheduled_at: "2026-01-01T02:14:00Z" }}
        onOpen={() => {}}
      />,
    );
    const badge = screen.getByText("Auto in 2h 14m");
    expect(badge.getAttribute("title")).toBe(
      `2026-01-01T02:14:00Z (local: ${new Date("2026-01-01T02:14:00Z").toLocaleString()})`,
    );
  });

  it("does not show the Auto label or a countdown on a Backlog card", () => {
    render(
      <CardItem
        card={{ ...baseCard, column: "Backlog", scheduled_at: "2026-01-01T02:14:00Z" }}
        onOpen={() => {}}
      />,
    );
    expect(screen.queryByText("Auto")).toBeNull();
    expect(screen.queryByText(/Auto in/)).toBeNull();
    expect(screen.queryByText("Auto soon")).toBeNull();
    expect(screen.queryByText("Auto pending")).toBeNull();
  });
});

describe("CardItem ReadyStateBadge", () => {
  it("renders a 'Ready' badge when readyState='ready' is supplied", () => {
    render(
      <CardItem card={baseCard} readyState="ready" onOpen={() => {}} />,
    );
    expect(screen.getByText("Ready")).not.toBeNull();
    expect(screen.queryByText("Dependent")).toBeNull();
    expect(screen.queryByText("In Progress")).toBeNull();
    expect(screen.queryByText("Impeded")).toBeNull();
    expect(screen.queryByText("Completed")).toBeNull();
  });

  it("renders a 'Dependent' badge with blocker titles in the tooltip", () => {
    // Tooltip text is exposed via the standard `title` HTML attribute, which
    // jsdom turns into the `title` property on the element. Reading it back
    // here pins the contract — the CardDrawer / KanbanPage must list the
    // blocker titles so an operator can see at a glance which other cards
    // gate this one, instead of having to open the deps one-by-one.
    render(
      <CardItem
        card={baseCard}
        readyState="dependent"
        blockerTitles={["Parent A", "Parent B"]}
        onOpen={() => {}}
      />,
    );
    const dependent = screen.getByText("Dependent");
    expect(dependent).not.toBeNull();
    expect(dependent.getAttribute("title")).toBe("Waiting on: Parent A, Parent B");
    expect(screen.queryByText("Ready")).toBeNull();
    expect(screen.queryByText("In Progress")).toBeNull();
  });

  it("renders an 'In Progress' badge when readyState='in_progress' is supplied", () => {
    render(
      <CardItem
        card={{ ...baseCard, claimed_by: "agent:tmux-x" }}
        readyState="in_progress"
        onOpen={() => {}}
      />,
    );
    expect(screen.getByText("In Progress")).not.toBeNull();
    expect(screen.queryByText("Ready")).toBeNull();
    expect(screen.queryByText("Dependent")).toBeNull();
  });

  it("renders an 'Impeded' badge when readyState='impeded' is supplied", () => {
    render(
      <CardItem
        card={{ ...baseCard, column: "Impediment" }}
        readyState="impeded"
        onOpen={() => {}}
      />,
    );
    const impeded = screen.getByText("Impeded");
    expect(impeded).not.toBeNull();
    expect(impeded.getAttribute("title")).toBe("Waiting on a human decision");
  });

  it("renders a 'Completed' badge when readyState='completed' is supplied", () => {
    render(
      <CardItem
        card={{ ...baseCard, column: "Done" }}
        readyState="completed"
        onOpen={() => {}}
      />,
    );
    const completed = screen.getByText("Completed");
    expect(completed).not.toBeNull();
    expect(completed.getAttribute("title")).toBe("Work is done");
  });

  it("omits the ready-state badge entirely when no readyState prop is passed", () => {
    // Backwards compat: every existing caller that doesn't compute state
    // (e.g. a future card-detail panel rendering) shouldn't suddenly grow
    // a 'Ready' badge out of nowhere. The opt-in prop keeps behaviour for
    // untouched callers identical.
    render(<CardItem card={baseCard} onOpen={() => {}} />);
    expect(screen.queryByText("Ready")).toBeNull();
    expect(screen.queryByText("Dependent")).toBeNull();
    expect(screen.queryByText("In Progress")).toBeNull();
    expect(screen.queryByText("Impeded")).toBeNull();
    expect(screen.queryByText("Completed")).toBeNull();
  });
});

// Per kanban card `c5eb6f89`: the Impediment column can hold cards for three
// different reasons (open question / dispatch failure / bare move), and the
// board UI must show a different affordance per cause so the operator can
// tell at a glance whether a blocked card needs a written answer or an
// infra redispatch.
describe("CardItem Impediment status badge", () => {
  it("renders no badge for an Impediment card without an impediment_status", () => {
    render(
      <CardItem
        card={{ ...baseCard, column: "Impediment" }}
        onOpen={() => {}}
      />,
    );
    expect(screen.queryByTestId("impediment-status-badge")).toBeNull();
  });

  it("renders a 'needs answer' badge when impediment_status='needs_answer'", () => {
    render(
      <CardItem
        card={{
          ...baseCard,
          column: "Impediment",
          impediment_status: "needs_answer",
        }}
        onOpen={() => {}}
      />,
    );
    const badge = screen.getByTestId("impediment-status-badge");
    expect(badge.getAttribute("data-impediment-status")).toBe("needs_answer");
    expect(badge.textContent).toMatch(/needs answer/);
  });

  it("renders a 'dispatch failed' (destructive) badge when impediment_status='dispatch_failed'", () => {
    render(
      <CardItem
        card={{
          ...baseCard,
          column: "Impediment",
          impediment_status: "dispatch_failed",
        }}
        onOpen={() => {}}
        projectPath="/proj"
      />,
    );
    const badge = screen.getByTestId("impediment-status-badge");
    expect(badge.getAttribute("data-impediment-status")).toBe("dispatch_failed");
    expect(badge.textContent).toMatch(/dispatch failed/);
  });

  it("renders a 'resolved' badge for an answered impediment", () => {
    render(
      <CardItem
        card={{
          ...baseCard,
          column: "Impediment",
          impediment_status: "resolved",
        }}
        onOpen={() => {}}
      />,
    );
    const badge = screen.getByTestId("impediment-status-badge");
    expect(badge.getAttribute("data-impediment-status")).toBe("resolved");
    expect(badge.textContent).toMatch(/resolved/);
  });

  it("renders a subtle 'no question' badge for a bare-move Impediment card", () => {
    render(
      <CardItem
        card={{
          ...baseCard,
          column: "Impediment",
          impediment_status: "no_question",
        }}
        onOpen={() => {}}
      />,
    );
    const badge = screen.getByTestId("impediment-status-badge");
    expect(badge.getAttribute("data-impediment-status")).toBe("no_question");
    expect(badge.textContent).toMatch(/no question/);
  });

  it("does NOT render an impediment badge for cards outside the Impediment column", () => {
    // Defensive: the field is null on the wire for non-Impediment cards, but
    // even if a stale state somehow hands a status to a Backlog card, the
    // UI must not show the badge — the operator should treat Backlog /
    // Doing / etc. the same as before.
    render(
      <CardItem
        card={{
          ...baseCard,
          column: "Backlog",
          impediment_status: "needs_answer",
        }}
        onOpen={() => {}}
      />,
    );
    expect(screen.queryByTestId("impediment-status-badge")).toBeNull();
  });
});

describe("CardItem dispatch_failed Redispatch quick-action", () => {
  afterEach(() => {
    vi.mocked(kanbanApi.redispatch).mockReset();
  });

  it("renders a Redispatch button for dispatch_failed Impediment cards when projectPath is set", () => {
    render(
      <CardItem
        card={{
          ...baseCard,
          column: "Impediment",
          impediment_status: "dispatch_failed",
        }}
        onOpen={() => {}}
        projectPath="/proj"
      />,
    );
    expect(screen.getByTestId("redispatch-quick-action")).not.toBeNull();
  });

  it("does NOT render the Redispatch button for non-dispatch_failed Impediment cards", () => {
    // `needs_answer`, `resolved`, and `no_question` should not get a
    // Redispatch quick-action — only the infrastructure-broken cause needs
    // a clickable Redispatch here.
    for (const status of ["needs_answer", "resolved", "no_question"] as const) {
      const { unmount } = render(
        <CardItem
          card={{
            ...baseCard,
            column: "Impediment",
            impediment_status: status,
          }}
          onOpen={() => {}}
          projectPath="/proj"
        />,
      );
      expect(screen.queryByTestId("redispatch-quick-action")).toBeNull();
      unmount();
    }
  });

  it("does NOT render the Redispatch button when projectPath is missing", () => {
    // Defensive: tests / stories sometimes pass a card without a project.
    // Without a path the API call would fail; better to hide the button
    // entirely than to render a broken one.
    render(
      <CardItem
        card={{
          ...baseCard,
          column: "Impediment",
          impediment_status: "dispatch_failed",
        }}
        onOpen={() => {}}
      />,
    );
    expect(screen.queryByTestId("redispatch-quick-action")).toBeNull();
  });

  it("calls kanbanApi.redispatch and stops propagation on click", async () => {
    vi.mocked(kanbanApi.redispatch).mockResolvedValue({
      session_name: "k-test-abcd",
    });
    const onOpen = vi.fn();

    render(
      <CardItem
        card={{
          ...baseCard,
          column: "Impediment",
          impediment_status: "dispatch_failed",
          agent: "engineer",
        }}
        onOpen={onOpen}
        projectPath="/proj"
      />,
    );

    fireEvent.click(screen.getByTestId("redispatch-quick-action"));

    await waitFor(() =>
      expect(kanbanApi.redispatch).toHaveBeenCalledWith(
        baseCard.id, "/proj", "engineer",
      ),
    );
    // The card's outer onClick must NOT have fired — Redispatch is a
    // quick-action that bypasses the drawer.
    expect(onOpen).not.toHaveBeenCalled();
  });

  it("falls back to passing agent=undefined when card.agent is unset", async () => {
    // Mirror the existing redispatchNow() in CardDrawer: missing card.agent
    // means the API picks the column default. CardItem mirrors this so the
    // two call sites can't drift.
    vi.mocked(kanbanApi.redispatch).mockResolvedValue({
      session_name: "k-test-efgh",
    });

    render(
      <CardItem
        card={{
          ...baseCard,
          column: "Impediment",
          impediment_status: "dispatch_failed",
        }}
        onOpen={() => {}}
        projectPath="/proj"
      />,
    );

    fireEvent.click(screen.getByTestId("redispatch-quick-action"));

    await waitFor(() =>
      expect(kanbanApi.redispatch).toHaveBeenCalledWith(
        baseCard.id, "/proj", undefined,
      ),
    );
  });
});

describe("CardItem inceptie-pipeline Promote-to-project quick-action", () => {
  it("renders the Promote button on an intake card when onPromote is wired", () => {
    const onPromote = vi.fn();
    render(
      <CardItem
        card={{ ...baseCard, column: "intake" }}
        onOpen={() => {}}
        onPromote={onPromote}
      />,
    );
    const btn = screen.getByTestId("promote-to-project-quick-action");
    expect(btn).not.toBeNull();
    expect(btn.textContent).toMatch(/Promote to project/);
  });

  it("does NOT render the Promote button on a non-intake card", () => {
    render(
      <CardItem
        card={{ ...baseCard, column: "Backlog" }}
        onOpen={() => {}}
        onPromote={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("promote-to-project-quick-action")).toBeNull();
  });

  it("does NOT render the Promote button on an intake card when onPromote is absent", () => {
    // Defensive: callers that haven't wired onPromote (legacy boards / unit
    // tests) get a read-only intake card, not a broken button.
    render(
      <CardItem
        card={{ ...baseCard, column: "intake" }}
        onOpen={() => {}}
      />,
    );
    expect(screen.queryByTestId("promote-to-project-quick-action")).toBeNull();
  });

  it("calls onPromote(card) on click, not onOpen", () => {
    const onPromote = vi.fn();
    const onOpen = vi.fn();
    render(
      <CardItem
        card={{ ...baseCard, column: "intake" }}
        onOpen={onOpen}
        onPromote={onPromote}
      />,
    );
    const btn = screen.getByTestId("promote-to-project-quick-action");
    fireEvent.click(btn);
    expect(onPromote).toHaveBeenCalledWith(
      expect.objectContaining({ id: "card-1", column: "intake" }),
    );
    // The click on the button must NOT bubble to the card's outer click
    // handler that opens the drawer — a button inside a clickable card
    // can't also trigger the card's primary action.
    expect(onOpen).not.toHaveBeenCalled();
  });
});
