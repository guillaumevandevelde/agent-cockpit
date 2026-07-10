// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { Card } from "../types";

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

const { kanbanApi } = await import("../api");
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
