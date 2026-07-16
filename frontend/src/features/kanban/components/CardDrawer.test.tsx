// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { toast } from "sonner";
import type { Card } from "../types";

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
  vi.restoreAllMocks();
});

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
      <CardDrawer
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
      <CardDrawer
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

  it("does not show the summary banner when card is not in the Done column", () => {
    const doingCard: Card = {
      ...baseCard,
      column: "Doing",
      done_summary: "stale summary",
      completed_at: "2026-07-10T12:00:00Z",
    };

    render(
      <CardDrawer
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
      <CardDrawer
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
      <CardDrawer
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
      <CardDrawer
        card={doneCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={onChanged}
      />,
    );

    const control = await screen.findByTestId("request-review-control");
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
      <CardDrawer
        card={doneCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    const state = await screen.findByTestId("review-requested-state");
    expect(state.textContent).toMatch(/the retry logic looks off/);
    // The fresh input form must not render alongside the already-requested state.
    expect(screen.queryByTestId("request-review-control")).toBeNull();
  });
});

describe("CardDrawer reopen control", () => {
  it("does not render the reopen control when the card is not in Done", () => {
    const doingCard: Card = { ...baseCard, column: "Doing" };

    render(
      <CardDrawer
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
      <CardDrawer
        card={doneCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={onChanged}
      />,
    );

    const control = await screen.findByTestId("reopen-control");
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
      <CardDrawer
        card={doneCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    const textarea = await screen.findByTestId("reopen-note");
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

describe("CardDrawer resolve impediment control", () => {
  it("does not render the resolve-impediment control when the card is not in Impediment", () => {
    const doingCard: Card = { ...baseCard, column: "Doing" };

    render(
      <CardDrawer
        card={doingCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    expect(screen.queryByTestId("resolve-impediment-control")).toBeNull();
  });

  it("surfaces the impediment question and submits the answer via resolveImpediment", async () => {
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        hlc: "1",
        op_type: "comment",
        entity_type: "comment",
        payload: { text: "**Impediment:** Which library should we use?" },
        created_at: "2026-01-01T00:00:00Z",
      },
    ]);
    const resolveMock = kanbanApi.resolveImpediment as ReturnType<typeof vi.fn>;
    resolveMock.mockResolvedValue({ ...baseCard, column: "Doing" });

    const impedimentCard: Card = { ...baseCard, column: "Impediment" };
    const onChanged = vi.fn();

    render(
      <CardDrawer
        card={impedimentCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={onChanged}
      />,
    );

    const control = await screen.findByTestId("resolve-impediment-control");
    expect(control).not.toBeNull();

    // The agent's question is surfaced for context.
    await waitFor(() =>
      expect(screen.getByTestId("impediment-question").textContent).toMatch(
        /Which library should we use\?/,
      ),
    );

    const textarea = screen.getByTestId("resolve-impediment-answer") as HTMLTextAreaElement;
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "Use library B." } });
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId("resolve-impediment-submit"));
    });

    await waitFor(() => expect(resolveMock).toHaveBeenCalled());
    const [cardId, projectPath, answer] = resolveMock.mock.calls[0];
    expect(cardId).toBe("card-1");
    expect(projectPath).toBe("/proj");
    expect(answer).toBe("Use library B.");
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
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
      <CardDrawer
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
      <CardDrawer
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
      <CardDrawer
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
      <CardDrawer
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

// --- Impediment + structured-options gate rendering -----------------------
// Acceptance criterion: when a card lands in Impediment carrying a
// `report_impediment(options=...)` gate, the CardDrawer must (a) render the
// choice buttons, (b) after the human answers show the recorded choice, and
// (c) expose a "Resolve impediment" button that hits the resolve-impediment
// REST endpoint. The legacy free-text path (no gate) must still surface the
// Resolve button so a human who wants to nudge a stuck card by hand can do
// so without first picking an option. See report_impediment in
// /mcp_server.py + the POST /cards/{cid}/resolve-impediment contract in
// router.py.

const impCard: Card = { ...baseCard, column: "Impediment" };

describe("CardDrawer Impediment column: structured-options gate", () => {
  it("renders the open-gate decision block on an Impediment card", async () => {
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: "gate-1",
        card_id: "card-1",
        project_key: "proj-1",
        question: "Postgres or SQLite?",
        options: ["Postgres", "SQLite"],
        status: "open",
        answer: null,
        created_at: "2026-07-10T10:00:00Z",
      },
    ]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    render(
      <CardDrawer
        card={impCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    // Both options render as buttons.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Postgres" })).toBeTruthy(),
    );
    expect(screen.getByRole("button", { name: "SQLite" })).toBeTruthy();
    // The Impediment-specific header is used in place of "Decision requested".
    expect(screen.getByText(/pick one to unblock/i)).toBeTruthy();
  });

  it("shows the recorded choice + Resolve button after the human answers", async () => {
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: "gate-1",
        card_id: "card-1",
        project_key: "proj-1",
        question: "Postgres or SQLite?",
        options: ["Postgres", "SQLite"],
        status: "answered",
        answer: "Postgres",
        created_at: "2026-07-10T10:00:00Z",
        answered_at: "2026-07-10T10:01:00Z",
      },
    ]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    render(
      <CardDrawer
        card={impCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    // The recorded choice is shown.
    const recorded = await screen.findByTestId("impediment-resolved-pending");
    expect(recorded.textContent).toMatch(/Postgres/);
    expect(recorded.textContent).toMatch(/Choice recorded/);

    // The Resolve button is rendered.
    const resolveBtn = screen.getByTestId("resolve-impediment-button");
    expect(resolveBtn).not.toBeNull();
  });

  it("Resolve button calls resolveImpediment and then onChanged", async () => {
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: "gate-1",
        card_id: "card-1",
        project_key: "proj-1",
        question: "Postgres or SQLite?",
        options: ["Postgres", "SQLite"],
        status: "answered",
        answer: "Postgres",
        created_at: "2026-07-10T10:00:00Z",
        answered_at: "2026-07-10T10:01:00Z",
      },
    ]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    const resolveMock = kanbanApi.resolveImpediment as ReturnType<typeof vi.fn>;
    resolveMock.mockResolvedValue({ ...impCard, id: "card-1" });

    const onChanged = vi.fn();
    render(
      <CardDrawer
        card={impCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={onChanged}
      />,
    );

    const resolveBtn = await screen.findByTestId("resolve-impediment-button");
    await act(async () => {
      fireEvent.click(resolveBtn);
    });

    await waitFor(() => expect(resolveMock).toHaveBeenCalled());
    const [cardId, projectPath] = resolveMock.mock.calls[0];
    expect(cardId).toBe("card-1");
    expect(projectPath).toBe("/proj");
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it("Resolve panel is hidden when no gate has been answered yet (free-text path)", async () => {
    // Free-text path: a card that landed in Impediment via report_impediment
    // without options=. No gate exists. The legacy free-text resolve control
    // (upstream's ResolveImpedimentControl) handles that case via its own
    // textarea — our structured-options panel must NOT also surface here,
    // because there's no recorded choice to display.
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        hlc: "1",
        op_type: "comment",
        entity_type: "comment",
        payload: { text: "**Impediment:** I need a schema review." },
        created_at: "2026-07-10T10:00:00Z",
      },
    ]);

    render(
      <CardDrawer
        card={impCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    // Wait for the polled gate fetch to land (returns []).
    await waitFor(() =>
      expect(
        (kanbanApi.listGates as ReturnType<typeof vi.fn>).mock.calls.length,
      ).toBeGreaterThan(0),
    );
    // Our structured-options "recorded choice" panel must not render.
    expect(screen.queryByTestId("impediment-resolved-pending")).toBeNull();
    // The upstream ResolveImpedimentControl textarea IS shown — that's the
    // free-text path the human uses here.
    expect(
      screen.getByTestId("resolve-impediment-control"),
    ).toBeTruthy();
  });

  it("Resolve panel is hidden when an open (unanswered) gate is still showing", async () => {
    // While a structured gate is still open, the "recorded choice" panel must
    // NOT render — there's no answer yet. The open-gate choice buttons stay
    // visible and guide the human to pick an option.
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: "gate-1",
        card_id: "card-1",
        project_key: "proj-1",
        question: "Postgres or SQLite?",
        options: ["Postgres", "SQLite"],
        status: "open",
        answer: null,
        created_at: "2026-07-10T10:00:00Z",
      },
    ]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    render(
      <CardDrawer
        card={impCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    // Wait for the polled gate fetch to land before asserting. Without this,
    // the assertion runs while gates=[] (initial state) and our panel would
    // not yet have been evaluated against the polled state.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Postgres" })).toBeTruthy(),
    );
    expect(screen.queryByTestId("impediment-resolved-pending")).toBeNull();
    expect(screen.queryByTestId("resolve-impediment-button")).toBeNull();
  });

  it("Resolve button is hidden outside the Impediment column", async () => {
    // The Resolve button is Impediment-column specific — a Doing card must
    // never expose it, even when a gate answer happens to exist.
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([
      {
        id: "gate-1",
        card_id: "card-1",
        project_key: "proj-1",
        question: "Stray gate question",
        options: ["A", "B"],
        status: "answered",
        answer: "A",
        created_at: "2026-07-10T10:00:00Z",
        answered_at: "2026-07-10T10:01:00Z",
      },
    ]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    const doingCard: Card = { ...baseCard, column: "Doing" };
    render(
      <CardDrawer
        card={doingCard}
        projectPath="/proj"
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    expect(screen.queryByTestId("impediment-resolved-pending")).toBeNull();
    expect(screen.queryByTestId("resolve-impediment-button")).toBeNull();
  });
});

describe("CardDrawer spec link", () => {
  it("shows the linked spec doc path from metadata.spec_doc", () => {
    const card: Card = {
      ...baseCard,
      metadata: { spec_doc: "docs/cockpit/agent-mail-spec.md" },
    };
    render(
      <CardDrawer card={card} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
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
      <CardDrawer card={card} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
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
      <CardDrawer card={card} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
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
      <CardDrawer card={card} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
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
      <CardDrawer card={doingCard} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
    );
    expect(screen.queryByTestId("run-this-branch-control")).toBeNull();
    expect(screen.queryByRole("button", { name: /Run this branch/i })).toBeNull();
  });

  it("renders the Run this branch control on a Done card", () => {
    const doneCard: Card = { ...baseCard, column: "Done" };
    render(
      <CardDrawer card={doneCard} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
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
      <CardDrawer card={doneCard} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
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
      <CardDrawer card={doneCard} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
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
      <CardDrawer card={doneCard} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
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
      <CardDrawer card={doneCard} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
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
      <CardDrawer card={card} projectPath="/proj" onClose={() => {}} onChanged={() => {}} />,
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
