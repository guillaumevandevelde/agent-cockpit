// @vitest-environment jsdom
import type { ComponentProps } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { toast } from "sonner";
import type { Card } from "../types";
import type { RunLedger } from "../runLedger";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/contexts/ProviderContext", () => ({
  useProviderContext: () => ({
    providers: [],
    selectedProviderId: null,
  }),
}));

vi.mock("../api", async (importOriginal) => {
  const actual = (await importOriginal()) as { kanbanApi: Record<string, unknown> };
  const stub: Record<string, ReturnType<typeof vi.fn>> = {};
  for (const key of Object.keys(actual.kanbanApi)) {
    stub[key] = vi.fn(async () => ({}));
  }
  stub.listGates = vi.fn(async () => []);
  stub.activity = vi.fn(async () => []);
  return { kanbanApi: stub };
});

// CardRunTab mounts the CC-Bridge PTY relay + Sessions transcript hooks, which
// poll live endpoints. None of that is under test here — stub it so an
// agent-claimed card (which defaults the drawer to the Run tab) doesn't fire
// real network polls during these unit tests.
vi.mock("./CardRunTab", () => ({
  CardRunTab: () => null,
}));

vi.mock("../appsApi", () => {
  return {
    appsApi: {
      startRun: vi.fn(async () => ({})),
      getRun: vi.fn(async () => ({})),
      listRuns: vi.fn(async () => ({ runs: [] })),
      stopRun: vi.fn(async () => ({ success: true, instance_id: "" })),
    },
  };
});

const { kanbanApi } = await import("../api");
const { appsApi } = await import("../appsApi");
const { CardDrawer } = await import("./CardDrawer");

// CardDrawer (Parent plan / Depends on navigation) and MarkdownRenderer
// (internal link navigation) both call `useNavigate`, which requires a
// Router ancestor — every render() below goes through this wrapper instead
// of mounting <CardDrawer> directly.
function LocationDisplay() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
}

function CardDrawerWithRouter(props: ComponentProps<typeof CardDrawer>) {
  return (
    <MemoryRouter initialEntries={["/kanban"]}>
      <LocationDisplay />
      <Routes>
        <Route path="/kanban" element={<CardDrawer {...props} />} />
      </Routes>
    </MemoryRouter>
  );
}

const baseCard: Card = {
  id: "card-1",
  project_key: "proj-1",
  title: "Test card",
  description: "",
  column: "Doing",
  rank: "0001",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  deliverables: [],
};

afterEach(() => {
  cleanup();
  // clearAllMocks (not restoreAllMocks) — the file's `vi.mock("../api", …)`
  // factory installs `vi.fn(async () => …)` defaults once at module load.
  // For a bare vi.fn() with no real implementation, mockRestore() strips that
  // factory-installed default (equivalent to mockReset()), and any subsequent
  // test that relies on the shared default — even one that sets its own
  // mockResolvedValue before render — silently breaks when an earlier test
  // runs and hits this afterEach. clearAllMocks() clears call history /
  // results but preserves the factory-installed implementation, so file order
  // is no longer load-bearing.
  vi.clearAllMocks();
});

// Done cards wrap their two rare-action controls (RequestReviewControl +
// ReopenControl) in a collapsible <DoneActionsPanel> so the body of the
// drawer (description, spec, subtasks, tabs) gets the full 85vh height back
// — kanban-kaart d4012bd1 "Done kaarten nog altijd niet goed leesbaar".
// Tests that previously expected the controls to be visible immediately on
// Done-render must click the toggle first; this helper centralises that
// step so each test stays focused on the behaviour it actually asserts.
async function expandDoneActions() {
  const toggle = screen.getByTestId("done-actions-toggle");
  await act(async () => {
    fireEvent.click(toggle);
  });
}

describe("CardDrawer live activity", () => {
  it("picks up new activity entries while the drawer stays open, without being closed and reopened", async () => {
    const activityMock = kanbanApi.activity as ReturnType<typeof vi.fn>;
    activityMock
      .mockResolvedValueOnce([
        { hlc: "1", op_type: "comment", entity_type: "comment", payload: { text: "first" }, created_at: "2026-01-01T00:00:00Z" },
      ])
      .mockResolvedValue([
        { hlc: "1", op_type: "comment", entity_type: "comment", payload: { text: "first" }, created_at: "2026-01-01T00:00:00Z" },
        { hlc: "2", op_type: "comment", entity_type: "comment", payload: { text: "second" }, created_at: "2026-01-01T00:01:00Z" },
      ]);

    render(
      <CardDrawerWithRouter
        card={baseCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    await waitFor(() => expect(activityMock).toHaveBeenCalledTimes(1));

    // Switch to the Activity tab and confirm the first entry is shown.
    // Radix's Tabs activates on `mousedown`, not `click` — a plain `.click()`
    // call never dispatches a mousedown and silently no-ops.
    await act(async () => {
      fireEvent.mouseDown(screen.getByRole("tab", { name: "Activity" }));
    });
    await waitFor(() => expect(screen.getByText(/first/)).toBeTruthy());

    // Without closing/reopening the drawer, a second poll tick should surface
    // the new activity entry an agent posted while the card was open.
    await waitFor(() => expect(activityMock.mock.calls.length).toBeGreaterThanOrEqual(2), {
      timeout: 6000,
      interval: 100,
    });
    await waitFor(() => expect(screen.getByText(/second/)).toBeTruthy());
  }, 8000);
});

describe("CardDrawer Done summary banner", () => {
  it("shows the green summary banner with summary text, completed date, and duration when card is Done with done_summary", () => {
    const doneCard: Card = {
      ...baseCard,
      column: "Done",
      created_at: "2026-07-10T10:00:00Z",
      done_summary: "Added done_summary + completed_at to CardResponse via op-log enrichment.",
      completed_at: "2026-07-10T12:15:00Z",
    };

    render(
      <CardDrawerWithRouter
        card={doneCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    // The banner is a single element with a stable testid — query by it so
    // we're not coupling to the exact phrasing of "Completed".
    const banner = screen.getByTestId("done-summary-banner");
    expect(banner).not.toBeNull();
    // Summary text rendered verbatim
    expect(banner.textContent).toMatch(
      /Added done_summary \+ completed_at to CardResponse/,
    );
    // Completed-on date appears (formatted)
    expect(banner.textContent).toMatch(/Completed on 10 July 2026 at/);
    // Duration text "2h 15m" appears between created_at and completed_at
    expect(banner.textContent).toMatch(/Took 2h 15m/);
  });

  it("renders markdown formatting in the Done summary banner", () => {
    const doneCard: Card = {
      ...baseCard,
      column: "Done",
      done_summary: "**Outcome.** Done.\n\n- First change\n- Second change",
      completed_at: "2026-07-10T12:15:00Z",
    };

    render(
      <CardDrawerWithRouter
        card={doneCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    const banner = screen.getByTestId("done-summary-banner");
    expect(banner.querySelector("strong")?.textContent).toBe("Outcome.");
    expect(banner.querySelectorAll("li")).toHaveLength(2);
    expect(banner.textContent).not.toContain("**Outcome.**");
  });

  it("does not show the summary banner when card is not in the Done column", () => {
    const doingCard: Card = {
      ...baseCard,
      column: "Doing",
      done_summary: "stale summary",
      completed_at: "2026-07-10T12:00:00Z",
    };

    render(
      <CardDrawerWithRouter
        card={doingCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    // The banner is Done-column gated; for any other column the testid must
    // not be present, regardless of whether done_summary/completed_at are set.
    expect(screen.queryByTestId("done-summary-banner")).toBeNull();
  });

  it("shows only a minimal Completed indicator when card is Done but done_summary is empty", () => {
    const doneCard: Card = {
      ...baseCard,
      column: "Done",
      created_at: "2026-07-10T10:00:00Z",
      completed_at: "2026-07-10T11:00:00Z",
    };

    render(
      <CardDrawerWithRouter
        card={doneCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    // Banner is still rendered (Done column), but without summary text or
    // duration. The "Took ..." row is suppressed because it only anchors
    // when a summary is present.
    const banner = screen.getByTestId("done-summary-banner");
    expect(banner.textContent).toMatch(/Completed/);
    expect(banner.textContent).not.toMatch(/Took/);
  });
});

describe("CardDrawer request review control", () => {
  it("does not render the request-review control when the card is not in Done", () => {
    const doingCard: Card = { ...baseCard, column: "Doing" };

    render(
      <CardDrawerWithRouter
        card={doingCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    expect(screen.queryByTestId("request-review-control")).toBeNull();
    expect(screen.queryByTestId("review-requested-state")).toBeNull();
  });

  it("submits the note via requestReview and calls onChanged on a Done card", async () => {
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    const requestReviewMock = kanbanApi.requestReview as ReturnType<typeof vi.fn>;
    requestReviewMock.mockResolvedValue({ ...baseCard, id: "review-card-9" });

    const doneCard: Card = { ...baseCard, column: "Done" };
    const onChanged = vi.fn();

    render(
      <CardDrawerWithRouter
        card={doneCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={onChanged}
      />,
    );

    // The controls live inside <DoneActionsPanel>; expand it first.
    await expandDoneActions();
    const control = screen.getByTestId("request-review-control");
    expect(control).not.toBeNull();

    const textarea = screen.getByTestId("request-review-note") as HTMLTextAreaElement;
    await act(async () => {
      fireEvent.change(textarea, {
        target: { value: "I doubt the edge case is handled" },
      });
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("request-review-submit"));
    });

    await waitFor(() => expect(requestReviewMock).toHaveBeenCalled());
    const [cardId, note] = requestReviewMock.mock.calls[0];
    expect(cardId).toBe("card-1");
    expect(note).toBe("I doubt the edge case is handled");
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("renders the already-requested state when a matching activity entry exists", async () => {
    const activityMock = kanbanApi.activity as ReturnType<typeof vi.fn>;
    activityMock.mockResolvedValue([
      {
        hlc: "1",
        op_type: "comment",
        entity_type: "comment",
        payload: { text: "**Review requested:** the retry logic looks off" },
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);

    const doneCard: Card = { ...baseCard, column: "Done" };

    render(
      <CardDrawerWithRouter
        card={doneCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    // The controls live inside <DoneActionsPanel>; expand it first so the
    // already-requested amber panel can render inside it.
    await expandDoneActions();

    const state = await screen.findByTestId("review-requested-state");
    expect(state.textContent).toMatch(/the retry logic looks off/);
    // The fresh input form must not render alongside the already-requested state.
    expect(screen.queryByTestId("request-review-control")).toBeNull();
  });
});

// Done card collapsed-state contract (kanban-kaart d4012bd1). The two
// rare-action controls must collapse into a single toggle by default so the
// drawer body keeps its full 85vh height. Expanding the toggle surfaces
// both controls; clicking it again collapses them.
describe("CardDrawer Done actions panel — collapsed by default", () => {
  it("does not render the panel when the card is not in Done", () => {
    const doingCard: Card = { ...baseCard, column: "Doing" };
    render(
      <CardDrawerWithRouter
        card={doingCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    expect(screen.queryByTestId("done-actions-panel")).toBeNull();
    expect(screen.queryByTestId("done-actions-toggle")).toBeNull();
  });

  it("renders only the toggle on a Done card by default; both controls stay hidden", () => {
    const doneCard: Card = { ...baseCard, column: "Done" };
    render(
      <CardDrawerWithRouter
        card={doneCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    expect(screen.getByTestId("done-actions-toggle")).not.toBeNull();
    expect(screen.queryByTestId("request-review-control")).toBeNull();
    expect(screen.queryByTestId("reopen-control")).toBeNull();
  });

  it("expands both controls when the toggle is clicked, and collapses them again on a second click", async () => {
    // Pin activity to empty so RequestReviewControl renders the input form
    // (not the amber "review requested" panel) — an earlier test in this file
    // already mocked activity with a `**Review requested:**` entry, and
    // `vi.clearAllMocks()` only clears call history (not mockResolvedValue),
    // so the leak would otherwise swap the form for the amber panel here.
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    const doneCard: Card = { ...baseCard, column: "Done" };
    render(
      <CardDrawerWithRouter
        card={doneCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    const toggle = screen.getByTestId("done-actions-toggle");

    // First click → expand
    await act(async () => {
      fireEvent.click(toggle);
    });
    expect(screen.getByTestId("request-review-control")).not.toBeNull();
    expect(screen.getByTestId("reopen-control")).not.toBeNull();

    // Second click → collapse
    await act(async () => {
      fireEvent.click(toggle);
    });
    expect(screen.queryByTestId("request-review-control")).toBeNull();
    expect(screen.queryByTestId("reopen-control")).toBeNull();
  });
});

describe("CardDrawer reopen control", () => {
  it("does not render the reopen control when the card is not in Done", () => {
    const doingCard: Card = { ...baseCard, column: "Doing" };

    render(
      <CardDrawerWithRouter
        card={doingCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    expect(screen.queryByTestId("reopen-control")).toBeNull();
  });

  it("submits the rebuttal via reopen and calls onChanged on a Done card", async () => {
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    const reopenMock = kanbanApi.reopen as ReturnType<typeof vi.fn>;
    reopenMock.mockResolvedValue({ ...baseCard, id: "card-1", column: "Backlog" });

    const doneCard: Card = { ...baseCard, column: "Done" };
    const onChanged = vi.fn();

    render(
      <CardDrawerWithRouter
        card={doneCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={onChanged}
      />,
    );

    // The controls live inside <DoneActionsPanel>; expand it first.
    await expandDoneActions();
    const control = screen.getByTestId("reopen-control");
    expect(control).not.toBeNull();

    const textarea = screen.getByTestId("reopen-note") as HTMLTextAreaElement;
    await act(async () => {
      fireEvent.change(textarea, {
        target: { value: "X is wrong because Y." },
      });
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("reopen-submit"));
    });

    await waitFor(() => expect(reopenMock).toHaveBeenCalled());
    const [cardId, note] = reopenMock.mock.calls[0];
    expect(cardId).toBe("card-1");
    expect(note).toBe("X is wrong because Y.");
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("disables the submit button while the request is in flight", async () => {
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    const reopenMock = kanbanApi.reopen as ReturnType<typeof vi.fn>;
    // Resolve only on the explicit call below — keep the in-flight promise pending.
    let resolveReopen!: (value: Card) => void;
    reopenMock.mockImplementation(
      () =>
        new Promise<Card>((resolve) => {
          resolveReopen = resolve;
        }),
    );

    const doneCard: Card = { ...baseCard, column: "Done" };

    render(
      <CardDrawerWithRouter
        card={doneCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    // The controls live inside <DoneActionsPanel>; expand it first.
    await expandDoneActions();
    const textarea = screen.getByTestId("reopen-note") as HTMLTextAreaElement;
    await act(async () => {
      fireEvent.change(textarea, {
        target: { value: "Rebuttal text" },
      });
    });
    const submit = screen.getByTestId("reopen-submit");
    await act(async () => {
      fireEvent.click(submit);
    });

    // While in flight, the button must be disabled so a double-click
    // doesn't fire two reopen ops.
    expect((submit as HTMLButtonElement).disabled).toBe(true);

    // Resolve so cleanup doesn't hang on an unsettled promise.
    await act(async () => {
      resolveReopen({ ...baseCard, column: "Backlog" });
    });
  });
});

describe("CardDrawer Impediment pointer", () => {
  // Impediment resolution moved to a dedicated `/kanban/impediment/:cardId`
  // page (kaart 626e05e3…). The drawer is no longer the entry point — it
  // shows a small "Open resolve page" pointer for the rare stale-tab case
  // so the operator isn't stranded without an action surface.
  it("does not render the Impediment pointer when the card is not in Impediment", () => {
    const doingCard: Card = { ...baseCard, column: "Doing" };

    render(
      <CardDrawerWithRouter
        card={doingCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    expect(screen.queryByTestId("impediment-drawer-pointer")).toBeNull();
  });

  it("renders the Impediment pointer with an Open resolve page button when the card is in Impediment", () => {
    const impedimentCard: Card = { ...baseCard, column: "Impediment" };

    render(
      <CardDrawerWithRouter
        card={impedimentCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    const pointer = screen.getByTestId("impediment-drawer-pointer");
    const button = screen.getByTestId("impediment-open-page");
    expect(pointer).toBeTruthy();
    expect(button.textContent).toMatch(/open resolve page/i);
  });

  it("does not render the old inline resolve-impediment-control in the drawer", () => {
    // Regression for the old control — once it lives on the page, the
    // drawer must not re-render it under any column (the route redirect
    // in KanbanPage.openCard handles the navigation).
    const impedimentCard: Card = { ...baseCard, column: "Impediment" };

    render(
      <CardDrawerWithRouter
        card={impedimentCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    expect(screen.queryByTestId("resolve-impediment-control")).toBeNull();
  });
});

describe("CardDrawer deliverables tab per-kind rendering", () => {
  it("renders each deliverable kind with its own icon and ref formatting", () => {
    const card: Card = {
      ...baseCard,
      deliverables: [
        { id: "d1", kind: "branch", ref: "k-mijn-branch-naam", created_at: "2026-07-10T10:00:00Z" },
        { id: "d2", kind: "pr", ref: "https://github.com/org/repo/pull/123", created_at: "2026-07-10T10:01:00Z" },
        { id: "d3", kind: "commit", ref: "abcdef1234567890", created_at: "2026-07-10T10:02:00Z" },
        { id: "d4", kind: "note", ref: "Hand-tested via the UI", created_at: "2026-07-10T10:03:00Z" },
        { id: "d5", kind: "plan", ref: "# My plan\n- step 1\n- step 2", created_at: "2026-07-10T10:04:00Z" },
        {
          id: "d6",
          kind: "plan_ref",
          ref: JSON.stringify({ parent_card_id: "parent-abcdef0123" }),
          created_at: "2026-07-10T10:05:00Z",
        },
        { id: "d7", kind: "spec", ref: "# Spec\n\n## Problem\nbrainstorming output is invisible.", created_at: "2026-07-10T10:06:00Z" },
      ],
    };

    render(
      <CardDrawerWithRouter
        card={card}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    // The Radix Dialog content is portaled to document.body, so query there
    // directly. `container` only holds the trigger stub.
    const scope = document.body;

    // Each deliverable row carries a `data-deliverable-kind` attribute so the
    // test can target it directly without depending on textContent layout.
    const branchRow = scope.querySelector('[data-deliverable-kind="branch"]');
    expect(branchRow).not.toBeNull();
    expect(branchRow!.textContent).toMatch(/🔀/);
    expect(branchRow!.textContent).toMatch(/k-mijn-branch-naam/);
    // Old "branch: <ref>" prefix must be gone.
    expect(branchRow!.textContent).not.toMatch(/^branch:/);

    const prRow = scope.querySelector('[data-deliverable-kind="pr"]');
    expect(prRow).not.toBeNull();
    expect(prRow!.textContent).toMatch(/github\.com\/org\/repo\/pull\/123/);
    // PR row contains an <a> with the URL.
    const prLink = prRow!.querySelector("a");
    expect(prLink).not.toBeNull();
    expect(prLink!.getAttribute("href")).toBe(
      "https://github.com/org/repo/pull/123",
    );

    const commitRow = scope.querySelector('[data-deliverable-kind="commit"]');
    expect(commitRow).not.toBeNull();
    expect(commitRow!.textContent).toMatch(/💻/);
    expect(commitRow!.textContent).toMatch(/abcdef1/);
    // Full 16-char hash must NOT appear in the commit row.
    expect(commitRow!.textContent).not.toMatch(/abcdef1234567890/);

    const noteRow = scope.querySelector('[data-deliverable-kind="note"]');
    expect(noteRow).not.toBeNull();
    expect(noteRow!.textContent).toMatch(/📝/);
    expect(noteRow!.textContent).toMatch(/Hand-tested via the UI/);

    const planRow = scope.querySelector('[data-deliverable-kind="plan"]');
    expect(planRow).not.toBeNull();
    expect(planRow!.textContent).toMatch(/📋/);
    expect(planRow!.textContent).toMatch(/Plan document/);

    const planRefRow = scope.querySelector('[data-deliverable-kind="plan_ref"]');
    expect(planRefRow).not.toBeNull();
    // parent_card_id "parent-abcdef0123" → first 8 chars = "parent-a"
    expect(planRefRow!.textContent).toMatch(/Verwijst naar parent-plan parent-a/);

    const specRow = scope.querySelector('[data-deliverable-kind="spec"]');
    expect(specRow).not.toBeNull();
    // spec rows render their markdown body inline, like plan; the icon must be
    // visually distinct from the 📋 plan icon so a card carrying both reads as
    // "design + plan", not "two plans".
    expect(specRow!.textContent).not.toMatch(/📋/);
    expect(specRow!.textContent).toMatch(/brainstorming output is invisible/);
  });
});

describe("CardDrawer Plan tab", () => {
  it("renders the parent plan markdown editable with a Save button that calls updatePlanAttachment", async () => {
    const planDeliverable = {
      id: "plan-1",
      kind: "plan" as const,
      ref: "# Original plan\n- step 1",
      created_at: "2026-07-10T10:00:00Z",
    };
    const cardWithPlan: Card = {
      ...baseCard,
      deliverables: [planDeliverable],
    };

    const updatePlanMock = kanbanApi.updatePlanAttachment as ReturnType<typeof vi.fn>;
    updatePlanMock.mockResolvedValue({ ...cardWithPlan });

    const onChanged = vi.fn();
    render(
      <CardDrawerWithRouter
        card={cardWithPlan}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={onChanged}
      />,
    );

    // Switch to the Plan tab (Radix Tabs activates on mousedown, not click).
    await act(async () => {
      fireEvent.mouseDown(screen.getByRole("tab", { name: "Plan" }));
    });

    // The Save button is rendered alongside the editable markdown.
    const saveButton = await screen.findByRole("button", { name: "Save plan" });
    expect(saveButton).not.toBeNull();

    // Default to preview mode — switch to edit, change text, then save.
    await act(async () => {
      fireEvent.mouseDown(screen.getByRole("tab", { name: "Edit" }));
    });
    const textarea = (await screen.findByPlaceholderText(/write markdown/i)) as HTMLTextAreaElement;
    await act(async () => {
      fireEvent.change(textarea, {
        target: { value: "# Updated plan\n- step A" },
      });
    });
    await act(async () => {
      fireEvent.click(saveButton);
    });

    await waitFor(() => expect(updatePlanMock).toHaveBeenCalled());
    const [cardId, planMarkdown] = updatePlanMock.mock.calls[0];
    expect(cardId).toBe(cardWithPlan.id);
    expect(planMarkdown).toBe("# Updated plan\n- step A");
    expect(onChanged).toHaveBeenCalled();
  });

  it("renders a child plan_ref card without a Save button (read-only)", async () => {
    const cardWithRef: Card = {
      ...baseCard,
      parent_card_id: "parent-abcdef0123",
      depends_on: [],
      deliverables: [
        {
          id: "ref-1",
          kind: "plan_ref",
          ref: JSON.stringify({ parent_card_id: "parent-abcdef0123" }),
          created_at: "2026-07-10T10:00:00Z",
        },
      ],
    };

    render(
      <CardDrawerWithRouter
        card={cardWithRef}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    await act(async () => {
      fireEvent.mouseDown(screen.getByRole("tab", { name: "Plan" }));
    });

    // No Save button for plan_ref children — only the parent plan is editable.
    expect(screen.queryByRole("button", { name: "Save plan" })).toBeNull();
  });
});

describe("CardDrawer edit dialog round-trip", () => {
  it("preserves analyst_agent_id and executor_agent_id when editing a multi-agent card", async () => {
    const updateCardMock = kanbanApi.updateCard as ReturnType<typeof vi.fn>;
    updateCardMock.mockResolvedValue(baseCard);

    const splitCard: Card = {
      ...baseCard,
      analyst_agent_id: "claude-code",
      executor_agent_id: "open-code",
    };

    render(
      <CardDrawerWithRouter
        card={splitCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    // Open the Edit dialog.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    });

    // The dialog should pre-select the existing analyst/executor split. We
    // don't assert the underlying <Select> DOM state directly because Radix's
    // Select renders the value via portal; instead, the strongest contract is
    // that the update payload preserves those fields when the user submits
    // without touching them.
    const updateButton = await screen.findByRole("button", { name: "Update" });
    await act(async () => {
      fireEvent.click(updateButton);
    });

    await waitFor(() => expect(updateCardMock).toHaveBeenCalled());
    const [, body] = updateCardMock.mock.calls[0];
    expect(body).toMatchObject({
      analyst_agent_id: "claude-code",
      executor_agent_id: "open-code",
    });
  });
});

// --- Impediment pointer on the drawer ------------------------------------
// Impediment resolution moved to a dedicated `/kanban/impediment/:cardId`
// page (kaart 626e05e3…). The drawer is no longer the entry point — it now
// shows only a small "Open resolve page" pointer so a stale tab landing on
// `?card=<impediment-id>` still surfaces an action. The full structured-options
// flow (open gate, recorded choice, free-text) moved to the page; see
// `ImpedimentPage.test.tsx` for that surface.

describe("CardDrawer spec link", () => {
  it("shows the linked spec doc path from metadata.spec_doc", () => {
    const card: Card = {
      ...baseCard,
      metadata: { spec_doc: "docs/cockpit/agent-mail-spec.md" },
    };
    render(
      <CardDrawerWithRouter card={card} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
    );
    const section = screen.getByTestId("spec-link-section");
    expect(section.textContent).toMatch(/docs\/cockpit\/agent-mail-spec\.md/);
  });

  it("renders a URL spec_doc as a clickable link", () => {
    const card: Card = {
      ...baseCard,
      metadata: { spec_doc: "https://example.com/spec.md" },
    };
    render(
      <CardDrawerWithRouter card={card} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
    );
    const link = screen.getByTestId("spec-link-value").querySelector("a");
    expect(link).not.toBeNull();
    expect(link?.getAttribute("href")).toBe("https://example.com/spec.md");
  });

  it("treats a plan-attachment as the spec when no explicit spec_doc is set", () => {
    const card: Card = {
      ...baseCard,
      deliverables: [
        { id: "d1", kind: "plan", ref: "# Plan\n...", created_at: "2026-01-01T00:00:00Z" },
      ],
    };
    render(
      <CardDrawerWithRouter card={card} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
    );
    expect(screen.getByTestId("spec-from-plan")).not.toBeNull();
  });

  it("saves an edited spec_doc into metadata via updateCard, preserving other keys", async () => {
    const updateMock = kanbanApi.updateCard as ReturnType<typeof vi.fn>;
    updateMock.mockResolvedValue({});
    const card: Card = {
      ...baseCard,
      metadata: { reviewed_card_id: "abc" },
    };
    render(
      <CardDrawerWithRouter card={card} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
    );

    fireEvent.click(screen.getByTestId("spec-link-edit"));
    fireEvent.change(screen.getByTestId("spec-link-input"), {
      target: { value: "docs/cockpit/foo.md" },
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("spec-link-save"));
    });

    await waitFor(() => expect(updateMock).toHaveBeenCalled());
    expect(updateMock).toHaveBeenCalledWith("card-1", {
      metadata: { reviewed_card_id: "abc", spec_doc: "docs/cockpit/foo.md" },
    });
  });
});

// --- Preview-URL per kanban-kaart (kanban-card d2689f2d) -----------------
// Done cards expose a "Run this branch" control that spins up a RunService
// instance, posts the live URL as an activity comment, and renders a
// PreviewPane with an iframe + Stop button. The backend already exposes
// /api/v1/runs/app; this layer only wires the UI on top.

function makeRunInstance(overrides: Partial<{
  instance_id: string;
  url: string;
  status: string;
  error: string | null;
}> = {}) {
  return {
    id: 1,
    instance_id: overrides.instance_id ?? "inst-abc",
    project_path: "/proj",
    command: ["python3", "-m", "http.server", "4123"],
    env_keys: [],
    port: 4123,
    url: overrides.url ?? "http://127.0.0.1:4123",
    health_path: "/",
    status: overrides.status ?? "starting",
    transport: "subprocess",
    container_id: null,
    pid: 1234,
    log_path: null,
    error: overrides.error ?? null,
    started_at: "2026-07-14T10:00:00Z",
    stopped_at: null,
  };
}

describe("CardDrawer preview control — rendering", () => {
  it("does not render the Run this branch control when the card is not Done", () => {
    const doingCard: Card = { ...baseCard, column: "Doing" };
    render(
      <CardDrawerWithRouter card={doingCard} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
    );
    expect(screen.queryByTestId("run-this-branch-control")).toBeNull();
    expect(screen.queryByRole("button", { name: /Run this branch/i })).toBeNull();
  });

  it("renders the Run this branch control on a Done card", () => {
    const doneCard: Card = { ...baseCard, column: "Done" };
    render(
      <CardDrawerWithRouter card={doneCard} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
    );
    const control = screen.getByTestId("run-this-branch-control");
    expect(control).not.toBeNull();
    expect(screen.getByRole("button", { name: /Run this branch/i })).not.toBeNull();
  });
});

describe("CardDrawer preview control — start", () => {
  it("starts a run, polls until healthy, posts activity comment with the preview URL, and shows the PreviewPane", async () => {
    const startMock = appsApi.startRun as ReturnType<typeof vi.fn>;
    const getRunMock = appsApi.getRun as ReturnType<typeof vi.fn>;
    const commentMock = kanbanApi.comment as ReturnType<typeof vi.fn>;
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    startMock.mockResolvedValue(makeRunInstance({ status: "starting" }));
    // First poll returns starting; second poll returns healthy — drives the
    // terminal-state branch the component watches for.
    getRunMock
      .mockResolvedValueOnce(makeRunInstance({ status: "starting" }))
      .mockResolvedValue(makeRunInstance({ status: "healthy" }));
    commentMock.mockResolvedValue({ ...baseCard });

    const doneCard: Card = { ...baseCard, column: "Done" };
    render(
      <CardDrawerWithRouter card={doneCard} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Run this branch/i }));
    });

    await waitFor(() => expect(startMock).toHaveBeenCalledTimes(1));
    const [startBody] = startMock.mock.calls[0];
    expect(startBody.project_path).toBe("/proj");
    expect(Array.isArray(startBody.command)).toBe(true);

    await waitFor(() => expect(getRunMock).toHaveBeenCalled());
    await waitFor(() =>
      expect(commentMock).toHaveBeenCalledWith(
        "card-1",
        "Preview live: http://127.0.0.1:4123",
      ),
    );

    // PreviewPane renders with the iframe pointing at the live URL.
    await waitFor(() => expect(screen.getByTestId("preview-pane")).not.toBeNull());
    const iframe = screen.getByTestId("preview-pane-iframe") as HTMLIFrameElement;
    expect(iframe.getAttribute("src")).toBe("http://127.0.0.1:4123");
    expect(screen.getByRole("button", { name: /Stop preview/i })).not.toBeNull();
  });

  it("posts an error activity comment when the run fails the health check", async () => {
    const startMock = appsApi.startRun as ReturnType<typeof vi.fn>;
    const getRunMock = appsApi.getRun as ReturnType<typeof vi.fn>;
    const commentMock = kanbanApi.comment as ReturnType<typeof vi.fn>;
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    startMock.mockResolvedValue(makeRunInstance({ status: "starting" }));
    getRunMock.mockResolvedValue(
      makeRunInstance({ status: "failed", error: "health check did not pass within timeout" }),
    );
    commentMock.mockResolvedValue({ ...baseCard });

    const doneCard: Card = { ...baseCard, column: "Done" };
    render(
      <CardDrawerWithRouter card={doneCard} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Run this branch/i }));
    });

    await waitFor(() =>
      expect(commentMock).toHaveBeenCalledWith(
        "card-1",
        expect.stringMatching(/Preview failed:.*health check/),
      ),
    );
    // The PreviewPane container is shown with the error block, but the iframe
    // is NOT rendered when the run failed.
    expect(screen.queryByTestId("preview-pane")).not.toBeNull();
    expect(screen.queryByTestId("preview-pane-iframe")).toBeNull();
  });
});

describe("CardDrawer preview control — stop", () => {
  it("Stop preview button calls appsApi.stopRun with the active instance id", async () => {
    const startMock = appsApi.startRun as ReturnType<typeof vi.fn>;
    const getRunMock = appsApi.getRun as ReturnType<typeof vi.fn>;
    const stopRunMock = appsApi.stopRun as ReturnType<typeof vi.fn>;
    const commentMock = kanbanApi.comment as ReturnType<typeof vi.fn>;
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    startMock.mockResolvedValue(makeRunInstance({ status: "starting" }));
    getRunMock.mockResolvedValue(makeRunInstance({ status: "healthy" }));
    commentMock.mockResolvedValue({ ...baseCard });
    stopRunMock.mockResolvedValue({ success: true, instance_id: "inst-abc" });

    const doneCard: Card = { ...baseCard, column: "Done" };
    render(
      <CardDrawerWithRouter card={doneCard} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Run this branch/i }));
    });

    const stopBtn = await screen.findByRole("button", { name: /Stop preview/i });
    await act(async () => {
      fireEvent.click(stopBtn);
    });

    await waitFor(() =>
      expect(stopRunMock).toHaveBeenCalledWith("inst-abc"),
    );
  });
});

describe("CardDrawer preview control — backend reports stopped", () => {
  it("does not render the iframe when the polled run is stopped", async () => {
    const startMock = appsApi.startRun as ReturnType<typeof vi.fn>;
    const getRunMock = appsApi.getRun as ReturnType<typeof vi.fn>;
    const commentMock = kanbanApi.comment as ReturnType<typeof vi.fn>;
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    startMock.mockResolvedValue(makeRunInstance({ status: "starting" }));
    // First poll still says "starting"; all subsequent polls say "stopped"
    // — the backend has externally torn the run down (or another tab hit
    // DELETE). The PreviewPane must not point an iframe at the dead URL.
    getRunMock
      .mockResolvedValueOnce(makeRunInstance({ status: "starting" }))
      .mockResolvedValue(makeRunInstance({ status: "stopped" }));
    commentMock.mockResolvedValue({ ...baseCard });

    const doneCard: Card = { ...baseCard, column: "Done" };
    render(
      <CardDrawerWithRouter card={doneCard} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
    );

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Run this branch/i }));
    });

    // Wait for the polling tick to land on "stopped". The pane stays
    // mounted (the run was attached) but the iframe must NOT be in the
    // DOM and a clear "stopped" message is rendered in its place.
    await waitFor(() =>
      expect(
        screen.getByTestId("preview-status-badge").getAttribute("data-status"),
      ).toBe("stopped"),
    );
    expect(screen.queryByTestId("preview-pane-iframe")).toBeNull();
    expect(screen.getByTestId("preview-stopped-message")).not.toBeNull();
  });
});

describe("CardDrawer id chip", () => {
  it("shows an abbreviated id chip and copies the full id to the clipboard on click", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    const card: Card = { ...baseCard, id: "9eaa600d1b58408aa3773df7d2d4edee" };
    render(
      <CardDrawerWithRouter card={card} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
    );

    const chip = screen.getByTestId("card-id-chip");
    // Abbreviated form is shown, not the full id.
    expect(chip.textContent).toMatch(/9eaa600d/);
    expect(chip.textContent).not.toContain(card.id);

    await act(async () => {
      fireEvent.click(chip);
    });

    // The FULL id is copied — never the abbreviated form.
    expect(writeText).toHaveBeenCalledWith(card.id);
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(expect.stringMatching(/card id copied/i)),
    );
  });
});

describe("CardDrawer copy reference action", () => {
  it("copies a markdown link to this card, distinct from the Copy id action", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    const card: Card = {
      ...baseCard,
      id: "9eaa600d1b58408aa3773df7d2d4edee",
      title: "Some card",
    };
    render(
      <CardDrawerWithRouter card={card} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
    );

    await act(async () => {
      fireEvent.click(screen.getByTestId("card-copy-reference"));
    });

    expect(writeText).toHaveBeenCalledWith(`[Some card](/kanban?card=${card.id})`);
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(expect.stringMatching(/reference copied/i)),
    );

    // The pre-existing "Copy id" action is unchanged — it still copies the
    // bare id, not the markdown link.
    await act(async () => {
      fireEvent.click(screen.getByTestId("card-id-chip"));
    });
    expect(writeText).toHaveBeenLastCalledWith(card.id);
  });
});

describe("CardDrawer Plan tab dead-link navigation", () => {
  it("Parent plan button navigates to the parent card via the ?card= deep-link", async () => {
    const cardWithRef: Card = {
      ...baseCard,
      parent_card_id: "parent-abcdef0123",
      depends_on: [],
      deliverables: [
        {
          id: "ref-1",
          kind: "plan_ref",
          ref: JSON.stringify({ parent_card_id: "parent-abcdef0123" }),
          created_at: "2026-07-10T10:00:00Z",
        },
      ],
    };

    render(
      <CardDrawerWithRouter
        card={cardWithRef}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    await act(async () => {
      fireEvent.mouseDown(screen.getByRole("tab", { name: "Plan" }));
    });

    const parentButton = await screen.findByRole("button", { name: "parent-a" });
    await act(async () => {
      fireEvent.click(parentButton);
    });

    expect(screen.getByTestId("location").textContent).toBe(
      "/kanban?card=parent-abcdef0123",
    );
  });

  it("Depends on badges navigate to the dependency card via the ?card= deep-link", async () => {
    const cardWithDeps: Card = {
      ...baseCard,
      parent_card_id: "parent-abcdef0123",
      depends_on: ["dep-11112222"],
      deliverables: [
        {
          id: "ref-1",
          kind: "plan_ref",
          ref: JSON.stringify({ parent_card_id: "parent-abcdef0123" }),
          created_at: "2026-07-10T10:00:00Z",
        },
      ],
    };

    render(
      <CardDrawerWithRouter
        card={cardWithDeps}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    await act(async () => {
      fireEvent.mouseDown(screen.getByRole("tab", { name: "Plan" }));
    });

    const depButton = await screen.findByRole("button", { name: "dep-1111" });
    await act(async () => {
      fireEvent.click(depButton);
    });

    expect(screen.getByTestId("location").textContent).toBe("/kanban?card=dep-11112222");
  });
});

// kanban card 81797046: the parent_card_id relation only rendered one
// direction (child → parent) before this. These tests pin the new
// child-listing view on the parent.
describe("CardDrawer Subtasks section", () => {
  it("renders nothing when the card has no children", () => {
    render(
      <CardDrawerWithRouter
        card={baseCard}
        projectPath="/proj"
        cards={[baseCard]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    expect(screen.queryByTestId("subtasks-section")).toBeNull();
  });

  it("lists each child's title with its ReadyStateBadge, reusing the supplied cardMeta", () => {
    const parent: Card = { ...baseCard, id: "parent-1" };
    const childReady: Card = {
      ...baseCard,
      id: "child-ready",
      title: "Child A",
      parent_card_id: "parent-1",
      column: "Backlog",
    };
    const childDone: Card = {
      ...baseCard,
      id: "child-done",
      title: "Child B",
      parent_card_id: "parent-1",
      column: "Done",
    };

    const cardMeta = new Map([
      ["child-ready", { readyState: "ready" as const, blockerTitles: [] }],
      ["child-done", { readyState: "completed" as const, blockerTitles: [] }],
    ]);

    render(
      <CardDrawerWithRouter
        card={parent}
        projectPath="/proj"
        cards={[parent, childReady, childDone]}
        cardMeta={cardMeta}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    const section = screen.getByTestId("subtasks-section");
    expect(section.textContent).toMatch(/Child A/);
    expect(section.textContent).toMatch(/Child B/);
    expect(section.textContent).toMatch(/Ready/);
    expect(section.textContent).toMatch(/Completed/);
  });

  // Pins the (held …) suffix in the drawer-subtask badge tooltip (kaart
  // bf8cc64e…). The suffix is the operator's only signal that distinguishes
  // a healthy temporary wait from a dead one — the column CardItem has had
  // it since heldSince landed; the drawer was still passing 3/5 CardMeta
  // fields, so the suffix never showed up here. `formatHeldAge` reads
  // `Date.now()` (impure — see ReadyStateBadge docstring), so freeze the
  // clock to make the expected age deterministic.
  it("forwards heldSince to the child's ReadyStateBadge so the tooltip gets the '(held …)' suffix", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-01T00:00:00Z"));
    try {
      const parent: Card = { ...baseCard, id: "parent-1" };
      const childHeld: Card = {
        ...baseCard,
        id: "child-held",
        title: "Child held",
        parent_card_id: "parent-1",
        column: "Backlog",
      };

      // 5 days before the frozen clock — formatHeldAge returns "5d".
      const cardMeta = new Map([
        [
          "child-held",
          {
            readyState: "dependent" as const,
            blockerTitles: ["sibling-1"],
            heldSince: "2026-06-26T00:00:00Z",
          },
        ],
      ]);

      render(
        <CardDrawerWithRouter
          card={parent}
          projectPath="/proj"
          cards={[parent, childHeld]}
          cardMeta={cardMeta}
          onClose={() => {}}
          onChanged={() => {}}
        />,
      );

      const badge = screen
        .getByTestId("subtasks-section")
        .querySelector('[data-ready-state="dependent"]');
      expect(badge).not.toBeNull();
      expect(badge?.getAttribute("title")).toMatch(/Waiting on: sibling-1 \(held 5d\)/);
    } finally {
      vi.useRealTimers();
    }
  });

  it("navigates to the clicked child card via the ?card= deep-link", async () => {
    const parent: Card = { ...baseCard, id: "parent-1" };
    const child: Card = {
      ...baseCard,
      id: "child-1",
      title: "Child card",
      parent_card_id: "parent-1",
    };

    render(
      <CardDrawerWithRouter
        card={parent}
        projectPath="/proj"
        cards={[parent, child]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    const childRow = screen.getByTestId("subtask-row");
    await act(async () => {
      fireEvent.click(childRow);
    });

    expect(screen.getByTestId("location").textContent).toBe("/kanban?card=child-1");
  });

  it("does not include cards belonging to a different parent", () => {
    const parent: Card = { ...baseCard, id: "parent-1" };
    const unrelated: Card = {
      ...baseCard,
      id: "other-child",
      title: "Someone else's subtask",
      parent_card_id: "parent-2",
    };

    render(
      <CardDrawerWithRouter
        card={parent}
        projectPath="/proj"
        cards={[parent, unrelated]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    expect(screen.queryByTestId("subtasks-section")).toBeNull();
  });
});

// --- Run-ledger tab (docs/cockpit/run-ledger-decision.md §3-5) ------------
// The Ledger tab renders the task → context → files → tests → outcome+model
// spine from GET /kanban/cards/{cid}/run-ledger as a vertical timeline. Each
// step is best-effort: an `available: false` step renders an empty/note state
// instead of crashing, and the outcome step LINKS to the Run/Tokens tabs
// rather than re-rendering them.

function makeLedger(overrides: Partial<RunLedger> = {}): RunLedger {
  return {
    card_id: "card-1",
    task: { title: "Test card", description: "Do the thing" },
    context: {
      available: true,
      prompt: "You are an engineer. Ship the thing.",
      phase: "execute",
      ship_mode: "direct",
      impediment_question: null,
      impediment_answer: null,
      revisit_question: null,
    },
    files: {
      available: true,
      branch: "k-my-branch",
      files: [{ path: "frontend/src/foo.tsx", insertions: 12, deletions: 3 }],
      files_changed: 1,
      insertions_total: 12,
      deletions_total: 3,
      note: null,
    },
    tests: {
      available: true,
      status: "clean",
      iteration_count: 2,
      last_line: "iter 2 | clean",
      ci_url: "https://github.com/org/repo/pull/7",
      note: null,
    },
    outcome: {
      column: "Done",
      outcome_text: "Shipped the Ledger tab.",
      outcome_source: "summary",
      model: "sonnet",
      completed_at: "2026-07-18T12:00:00Z",
    },
    usage_url: "/api/v1/kanban/cards/card-1/usage",
    ...overrides,
  };
}

async function openLedgerTab() {
  await act(async () => {
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Ledger" }));
  });
}

describe("CardDrawer Ledger tab", () => {
  it("renders the five-step spine from the run-ledger endpoint", async () => {
    (kanbanApi.getRunLedger as ReturnType<typeof vi.fn>).mockResolvedValue(
      makeLedger(),
    );

    render(
      <CardDrawerWithRouter
        card={baseCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    await openLedgerTab();

    const timeline = await screen.findByTestId("ledger-timeline");
    expect(timeline).not.toBeNull();
    // Step headers.
    expect(timeline.textContent).toMatch(/Task/);
    expect(timeline.textContent).toMatch(/Context/);
    expect(timeline.textContent).toMatch(/Files/);
    expect(timeline.textContent).toMatch(/Tests/);
    expect(timeline.textContent).toMatch(/Outcome & model/);
    // Concrete data from the stitched sources.
    expect(screen.getByTestId("ledger-context-phase").textContent).toMatch(/execute/);
    expect(screen.getByTestId("ledger-files").textContent).toMatch(/frontend\/src\/foo\.tsx/);
    expect(screen.getByTestId("ledger-tests-status").textContent).toMatch(/clean/);
    expect(screen.getByTestId("ledger-outcome-column").textContent).toMatch(/Done/);
    expect(timeline.textContent).toMatch(/model:/);
    expect(timeline.textContent).toMatch(/sonnet/);
  });

  it("renders per-step empty/note states without crashing when sources are missing", async () => {
    (kanbanApi.getRunLedger as ReturnType<typeof vi.fn>).mockResolvedValue(
      makeLedger({
        context: {
          available: false,
          prompt: null,
          phase: null,
          ship_mode: null,
          impediment_question: null,
          impediment_answer: null,
          revisit_question: null,
        },
        files: {
          available: false,
          branch: null,
          files: [],
          files_changed: 0,
          insertions_total: 0,
          deletions_total: 0,
          note: "no branch deliverable yet",
        },
        tests: {
          available: false,
          status: null,
          iteration_count: null,
          last_line: null,
          ci_url: null,
          note: "no iteration-loop progress file found",
        },
        outcome: {
          column: "Backlog",
          outcome_text: null,
          outcome_source: null,
          model: null,
          completed_at: null,
        },
      }),
    );

    render(
      <CardDrawerWithRouter
        card={baseCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    await openLedgerTab();

    const timeline = await screen.findByTestId("ledger-timeline");
    // The note strings surface as the per-step empty states; nothing throws.
    expect(timeline.textContent).toMatch(/no branch deliverable yet/);
    expect(timeline.textContent).toMatch(/no iteration-loop progress file found/);
    expect(timeline.textContent).toMatch(/No dispatch context yet/);
    expect(timeline.textContent).toMatch(/No outcome recorded yet/);
    // The unavailable steps must not render their data tables.
    expect(screen.queryByTestId("ledger-files")).toBeNull();
  });

  it("links to the Tokens tab from the outcome step instead of re-rendering tokens", async () => {
    (kanbanApi.getRunLedger as ReturnType<typeof vi.fn>).mockResolvedValue(
      makeLedger(),
    );

    render(
      <CardDrawerWithRouter
        card={baseCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    await openLedgerTab();

    const tokensLink = await screen.findByTestId("ledger-link-tokens");
    await act(async () => {
      fireEvent.click(tokensLink);
    });

    // The drawer switches the active tab to Tokens (controlled Tabs).
    await waitFor(() =>
      expect(
        screen.getByRole("tab", { name: "Tokens" }).getAttribute("aria-selected"),
      ).toBe("true"),
    );
  });

  it("only offers the transcript link when the card has an agent run session", async () => {
    (kanbanApi.getRunLedger as ReturnType<typeof vi.fn>).mockResolvedValue(
      makeLedger(),
    );

    // No claimed_by → no runSession → no Run tab, and no transcript link.
    render(
      <CardDrawerWithRouter
        card={baseCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    await openLedgerTab();
    await screen.findByTestId("ledger-timeline");
    expect(screen.queryByTestId("ledger-link-run")).toBeNull();
  });

  it("offers the transcript link for a card with an active agent session", async () => {
    (kanbanApi.getRunLedger as ReturnType<typeof vi.fn>).mockResolvedValue(
      makeLedger(),
    );

    const agentCard: Card = { ...baseCard, claimed_by: "agent:sess-1" };
    render(
      <CardDrawerWithRouter
        card={agentCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    // A run session defaults the drawer to the Run tab — switch to Ledger.
    await openLedgerTab();
    const runLink = await screen.findByTestId("ledger-link-run");
    expect(runLink).not.toBeNull();
  });
});

// --- Done-kaart bottom-clip (kaart d4012bd1…) ----------------------------
// "Done kaarten nog altijd niet goed leesbaar, nu is het onderste deel niet
// langer leesbaar." — the body must remain scrollable for a Done card so the
// operator can reach the Deliverables / TabsContent below the description.
// jsdom doesn't compute layout (scrollHeight/clientHeight stay 0), so the
// structural contract is pinned instead: the body must declare
// `min-h-0 + overflow-auto` on a flex parent that itself owns a fixed height
// and `overflow-hidden`. Together these are the CSS preconditions for the
// browser to actually paint a vertical scrollbar on overflow content.
describe("CardDrawer Done-kaart body keeps the scroll preconditions", () => {
  it("Done card: the body sits in a fixed-height flex parent that itself clips — so the body's overflow-auto can reach the full content height", () => {
    const doneCard: Card = {
      ...baseCard,
      column: "Done",
      done_summary: "Shipped.",
      completed_at: "2026-07-10T12:00:00Z",
    };
    render(
      <CardDrawerWithRouter
        card={doneCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    const body = screen.getByTestId("card-drawer-body");

    // Body must shrink below its content height and own its own scrollbar —
    // a missing `min-h-0` reverts the flex child to min-height:auto, which
    // makes the body grow to fit the description + TabsContent and pushes the
    // bottom past the DialogContent's `overflow-hidden` clip (the symptom
    // shown in the screenshot: "10 self-improve 366…" cut off at the modal
    // edge with no scrollbar in sight).
    expect(body.className).toMatch(/\bmin-h-0\b/);
    expect(body.className).toMatch(/\boverflow-auto\b/);
    expect(body.className).toMatch(/\bflex-1\b/);

    // The body's flex parent (DialogContent) must cap the drawer's height so
    // the body's flex-1 has a finite height to fill. Without the explicit
    // `h-[85vh]`, the dialog would grow to the body content's full height and
    // the bottom of the drawer would be off-screen on a 1080p viewport.
    // (jsdom doesn't run Tailwind so getComputedStyle returns defaults; check
    // the className directly, which is what the browser actually compiles.)
    const dialog = document.body.querySelector('[role="dialog"]') as HTMLElement;
    expect(dialog).not.toBeNull();
    const dialogClass = dialog.className;
    expect(dialogClass).toMatch(/\bflex\b/);
    expect(dialogClass).toMatch(/\bflex-col\b/);
    expect(dialogClass).toMatch(/\boverflow-hidden\b/);
    expect(dialogClass).toMatch(/h-\[85vh\]/);
  });
});

// --- Scroll contract (kanban-kaart 72476d8e…) -----------------------------
// The drawer must own exactly one scroll container: the body between a sticky
// DialogHeader and the modal border. No other element inside the drawer
// (CardRunTab transcript, CardLedgerTab prompt, MarkdownPreviewToggle preview,
// CardDrawer's outer modal, etc.) may declare its own height-cap + overflow.
// The two viewport-bound widgets (xterm in CardRunTab, preview iframe in
// PreviewPane) flip the body into "full-area mode": overflow-hidden flex-col
// so the widget fills the body and the body itself doesn't scroll.
//
// Test the three required cases: Backlog card (default body scroll), Done
// card (same), and an agent-claimed card whose active Run tab triggers
// full-area mode. The selector here matches `overflow-auto`,
// `overflow-y-auto`, `overflow-x-auto`, `overflow-scroll`,
// `overflow-y-scroll`, `overflow-x-scroll` — i.e. every Tailwind utility that
// produces a scrollbar. `overflow-hidden` is intentionally excluded because
// it clips overflow without scrolling (and is what the outer modal + the
// xterm container use today).

function scrollableElementsInDialog(): HTMLElement[] {
  const dialog = document.body.querySelector('[role="dialog"]') as HTMLElement | null;
  if (!dialog) return [];
  return Array.from(dialog.querySelectorAll<HTMLElement>("*")).filter((el) => {
    const cls = el.className;
    if (typeof cls !== "string") return false;
    return /\boverflow-(?:[xy]-)?(?:auto|scroll)\b/.test(cls);
  });
}

describe("CardDrawer scroll contract — single scrollable body", () => {
  it("Backlog card: the drawer has exactly one scroll-class element (the body), and the header sits outside it", () => {
    render(
      <CardDrawerWithRouter
        card={baseCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    const body = screen.getByTestId("card-drawer-body");
    // The body itself is the scroll container.
    expect(body.className).toMatch(/\boverflow-auto\b/);

    const scrollables = scrollableElementsInDialog();
    expect(scrollables).toHaveLength(1);
    expect(scrollables[0]).toBe(body);

    // Header (the title) is a sibling of the body, not a descendant.
    const title = screen.getByRole("heading", { name: "Test card" });
    expect(body.contains(title)).toBe(false);
  });

  it("Done card: the body is the body scroll container and the DoneSummaryBanner carries its OWN scrollable area inside a max-h cap", () => {
    // Human decision on kaart d4012bd1…: "Groene samenvatting krijgt een
    // eigen scrollbalk en mag hoogstens 40% van het venster pakken —
    // lange samenvatting blijft volledig leesbaar, kaartinhoud houdt
    // altijd minstens 60%". The body contract is unchanged; the banner
    // owns a SECOND scroll surface (capped at 40vh) so an unbounded
    // summary never pushes the rest of the drawer below the modal edge.
    const doneCard: Card = {
      ...baseCard,
      column: "Done",
      done_summary: "Shipped.",
      completed_at: "2026-07-10T12:00:00Z",
    };
    render(
      <CardDrawerWithRouter
        card={doneCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    const body = screen.getByTestId("card-drawer-body");
    expect(body.className).toMatch(/\boverflow-auto\b/);

    const banner = screen.getByTestId("done-summary-banner");
    // The banner caps its own height at 40vh so a long summary never
    // grows past that slice of the modal — that's the visual half of the
    // 60/40 split.
    expect(banner.className).toMatch(/max-h-\[40vh\]/);
    // The banner is a flex column so its inner content area can claim
    // the leftover height and host its own overflow-auto.
    expect(banner.className).toMatch(/\bflex\b/);
    expect(banner.className).toMatch(/\bflex-col\b/);

    // The summary text lives in a dedicated scrollable child; together
    // with `min-h-0` + `flex-1` this is what makes the inner scrollbar
    // actually paint in a real browser (jsdom doesn't compute layout).
    const summaryScroll = screen.getByTestId("done-summary-content");
    expect(summaryScroll.className).toMatch(/\boverflow-auto\b/);
    expect(summaryScroll.className).toMatch(/\bflex-1\b/);
    expect(summaryScroll.className).toMatch(/\bmin-h-0\b/);

    // Two scroll-class elements now: the body and the banner's content
    // scroll area. Anything more means a regression introduced a
    // nested scrollbar the user has to chase (see CardRunTab /
    // CardLedgerTab history in this file's commit log).
    const scrollables = scrollableElementsInDialog();
    expect(scrollables).toHaveLength(2);
    expect(scrollables).toContain(body);
    expect(scrollables).toContain(summaryScroll);

    // The title still sits outside the body — header hierarchy is intact.
    const title = screen.getByRole("heading", { name: "Test card" });
    expect(body.contains(title)).toBe(false);
    // And the summary scroll area is a descendant of the banner, not of
    // the body — they're independent scroll surfaces, not nested ones.
    expect(banner.contains(summaryScroll)).toBe(true);
    expect(body.contains(summaryScroll)).toBe(false);
  });

  it("Agent-claimed card with the Run tab active: the body switches to full-area mode (overflow-hidden), no scroll-class element exists, and the header is outside the full-area container", () => {
    // `claimed_by: "agent:<session>"` makes `runSession` truthy, which
    // defaults `activeTab` to "run" (CardDrawer.tsx initial state).
    const agentCard: Card = { ...baseCard, claimed_by: "agent:sess-1" };
    render(
      <CardDrawerWithRouter
        card={agentCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    // The scrolling body must NOT render in full-area mode — the widget
    // owns the scrollbar (xterm.js / iframe native).
    expect(screen.queryByTestId("card-drawer-body")).toBeNull();

    const fullArea = screen.getByTestId("card-drawer-full-area");
    expect(fullArea.className).toMatch(/\boverflow-hidden\b/);
    // It also lays out its single child as a flex column so the widget's
    // `flex-1 h-full` actually fills it.
    expect(fullArea.className).toMatch(/\bflex\b/);
    expect(fullArea.className).toMatch(/\bflex-col\b/);

    // Zero scroll-class elements in the dialog when the widget owns
    // scrolling — the body's overflow-hidden, the xterm container's
    // overflow-hidden, and the xterm.js internal scrollbar all live
    // outside the `overflow-*-(auto|scroll)` selector.
    expect(scrollableElementsInDialog()).toHaveLength(0);

    const title = screen.getByRole("heading", { name: "Test card" });
    expect(fullArea.contains(title)).toBe(false);
  });
});
