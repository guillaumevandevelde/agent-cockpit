// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import type { ReactElement } from "react";
import type { Card } from "./types";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/contexts/ProjectContext", () => ({
  useProjectContext: () => ({
    activeProject: { path: "/proj", id: "1", name: "proj", is_active: true },
  }),
}));

vi.mock("@/contexts/ProviderContext", () => ({
  useProviderContext: () => ({
    providers: [],
    selectedProviderId: null,
  }),
}));

vi.mock("./api", async (importOriginal) => {
  const actual = (await importOriginal()) as { kanbanApi: Record<string, unknown> };
  const stub: Record<string, ReturnType<typeof vi.fn>> = {};
  for (const key of Object.keys(actual.kanbanApi)) {
    stub[key] = vi.fn(async () => ({}));
  }
  stub.projectKey = vi.fn(async () => ({ project_key: "proj-1" }));
  stub.listColumns = vi.fn(async () => ({ columns: [] }));
  stub.listCards = vi.fn(async () => ({ items: [] }));
  stub.listGates = vi.fn(async () => []);
  stub.activity = vi.fn(async () => []);
  stub.mcpHealth = vi.fn(async () => ({
    ok: true,
    advertised_endpoint: null,
    routes_to_mount: true,
    message_post_status: null,
    tool_call_ok: true,
    protocol_version: null,
    tools: [],
    db_ok: true,
    error: null,
  }));
  stub.mcpStatus = vi.fn(async () => ({ enabled: true }));
  stub.dispatchPause = vi.fn(async () => ({ paused: false, paused_until: null }));
  stub.getShipMode = vi.fn(async () => ({ mode: "direct" }));
  stub.getSkipPermissions = vi.fn(async () => ({ enabled: false }));
  stub.getAutodispatch = vi.fn(async () => ({ enabled: false }));
  stub.getDefaultTransport = vi.fn(async () => ({ transport: "tmux" }));
  return { kanbanApi: stub };
});

const { kanbanApi } = await import("./api");
const { default: KanbanPage } = await import("./KanbanPage");

const setHidden = (hidden: boolean) => {
  Object.defineProperty(document, "hidden", {
    value: hidden,
    configurable: true,
  });
};

afterEach(() => {
  cleanup();
  // clearAllMocks (not restoreAllMocks) — the vi.mock("./api", …) factory above
  // installs vi.fn() defaults once at module load. For a bare vi.fn() with no
  // real implementation, mockRestore() strips the factory default (equivalent
  // to mockReset()), silently breaking any subsequent test that relies on the
  // shared default. clearAllMocks() clears call history / results but
  // preserves the factory-installed implementation, so file order is no longer
  // load-bearing. See kanban card 3097ebadd3… for the full analysis.
  vi.clearAllMocks();
  setHidden(false);
});

function makeCard(overrides: Partial<Card> = {}): Card {
  return {
    id: "card-1",
    project_key: "proj-1",
    title: "Card One",
    description: "",
    column: "Backlog",
    rank: "0000",
    created_at: "2026-07-16T00:00:00Z",
    updated_at: "2026-07-16T00:00:00Z",
    deliverables: [],
    ...overrides,
  };
}

const BACKLOG_COLUMN: import("./types").KanbanColumn = {
  id: "col-backlog",
  project_key: "proj-1",
  name: "Backlog",
  rank: "0000",
  default_agent: null,
  default_provider: null,
  default_model: null,
  max_sessions: null,
  token_saver_enabled: false,
  caveman_enabled: false,
  ponytail_enabled: false,
  created_at: "2026-07-16T00:00:00Z",
  updated_at: "2026-07-16T00:00:00Z",
};

function LocationDisplay() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
}

// Programmatic navigate trigger. Used by tests below to simulate the
// already-mounted case where the command palette (or any other consumer)
// navigates to `/kanban?card=<id>` while the KanbanPage is already mounted.
// Renders a clickable button so the test fires the navigation inside
// `act()` and observes the resulting state change.
function NavigateOnClick({ to, testId }: { to: string; testId: string }) {
  const navigate = useNavigate();
  return (
    <button data-testid={testId} onClick={() => navigate(to)}>
      navigate
    </button>
  );
}

function renderAt(initialEntry: string, extra?: ReactElement) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <LocationDisplay />
      <Routes>
        <Route
          path="/kanban"
          element={
            <>
              <KanbanPage />
              {extra}
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("KanbanPage live refresh", () => {
  it("refetches immediately when the tab regains visibility, not just on the next poll tick", async () => {
    renderAt("/kanban");

    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    setHidden(true);
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(kanbanApi.listCards).toHaveBeenCalledTimes(1);

    setHidden(false);
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(2));
  });
});

describe("KanbanPage new-card dialog", () => {
  it("forwards analyst_agent_id and executor_agent_id to kanbanApi.createCard (multi-agent create path)", async () => {
    const createCardMock = kanbanApi.createCard as ReturnType<typeof vi.fn>;
    createCardMock.mockResolvedValue({});

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    // Open the New card dialog. Both analyst/executor defaults are AUTO,
    // which the dialog translates to null on submit.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "New card" }));
    });
    // Title is required — fill it.
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Multi-agent card" },
    });
    await act(() => {
      screen.getByRole("button", { name: "Create" }).click();
    });

    await waitFor(() => expect(createCardMock).toHaveBeenCalledTimes(1));
    const body = createCardMock.mock.calls[0][0];
    // CardEditDialog already emits both fields with AUTO → null; the bug
    // was that KanbanPage.tsx's destructure + createCard body type dropped
    // them, so the keys were missing from the POST.
    expect(body).toHaveProperty("analyst_agent_id");
    expect(body).toHaveProperty("executor_agent_id");
    expect(body.analyst_agent_id).toBeNull();
    expect(body.executor_agent_id).toBeNull();
  });

  it("uploads each staged attachment right after createCard resolves", async () => {
    const createCardMock = kanbanApi.createCard as ReturnType<typeof vi.fn>;
    const uploadMock = kanbanApi.uploadAttachment as ReturnType<typeof vi.fn>;
    createCardMock.mockResolvedValue({ id: "card-123" });
    uploadMock.mockResolvedValue({});

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "New card" }));
    });
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "With attachment" },
    });
    // Drive the same code path the dialog would: pass a fake File into the
    // hidden input, then submit. createObjectURL/revokeObjectURL aren't in
    // jsdom, so the CardEditDialog test covers the staging side; here we
    // only care that KanbanPage hands the files to uploadAttachment with
    // the id returned by createCard.
    const dialogInput = screen.getByLabelText("Bijlage kiezen") as HTMLInputElement;
    const file1 = new File(["a"], "shot-a.png", { type: "image/png" });
    const file2 = new File(["b"], "shot-b.png", { type: "image/png" });
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: () => "blob:fake",
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: () => {},
    });
    await act(async () => {
      fireEvent.change(dialogInput, { target: { files: [file1, file2] } });
    });
    await act(() => {
      screen.getByRole("button", { name: "Create" }).click();
    });

    await waitFor(() => expect(createCardMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(uploadMock).toHaveBeenCalledTimes(2));
    expect(uploadMock.mock.calls[0]).toEqual(["card-123", file1]);
    expect(uploadMock.mock.calls[1]).toEqual(["card-123", file2]);
  });
});

describe("KanbanPage ?card= deep link", () => {
  it("opens the drawer for a card already present on the board", async () => {
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN],
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [makeCard()],
    });

    renderAt("/kanban?card=card-1");

    expect(await screen.findByTestId("card-id-chip")).toBeInTheDocument();
    expect(kanbanApi.getCard).not.toHaveBeenCalled();
  });

  it("falls back to kanbanApi.getCard for a card from another project", async () => {
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({ items: [] });
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockResolvedValue(
      makeCard({ id: "card-2", project_key: "other-proj", title: "Other Project Card" })
    );

    renderAt("/kanban?card=card-2");

    await waitFor(() => expect(kanbanApi.getCard).toHaveBeenCalledWith("card-2"));
    expect(await screen.findByText("Other Project Card")).toBeInTheDocument();
  });

  it("shows an error toast and strips the param for an unknown card id", async () => {
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({ items: [] });
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("404"));

    const { getByTestId } = renderAt("/kanban?card=missing-card");

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    await waitFor(() => expect(getByTestId("location").textContent).toBe("/kanban"));
    expect(screen.queryByTestId("card-id-chip")).not.toBeInTheDocument();
  });

  it("opening a card updates the URL and closing it removes the param", async () => {
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN],
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [makeCard()],
    });

    const { getByTestId } = renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    await act(async () => {
      fireEvent.click(screen.getByText("Card One"));
    });
    await screen.findByTestId("card-id-chip");
    expect(getByTestId("location").textContent).toBe("/kanban?card=card-1");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Close" }));
    });
    await waitFor(() => expect(screen.queryByTestId("card-id-chip")).not.toBeInTheDocument());
    expect(getByTestId("location").textContent).toBe("/kanban");
  });
});

describe("KanbanPage ready-state precedence", () => {
  const DONE_COLUMN: import("./types").KanbanColumn = {
    ...BACKLOG_COLUMN,
    id: "col-done",
    name: "Done",
  };
  const IMPEDIMENT_COLUMN: import("./types").KanbanColumn = {
    ...BACKLOG_COLUMN,
    id: "col-impediment",
    name: "Impediment",
  };

  it("applies completed > impeded > in_progress > missing_dep > dependent > ready precedence", async () => {
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN, DONE_COLUMN, IMPEDIMENT_COLUMN],
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        // A stale agent claim in Done must still read "completed", not
        // "in_progress" — column-based terminal states win over the claim.
        makeCard({ id: "card-done", column: "Done", claimed_by: "agent:tmux-x" }),
        makeCard({ id: "card-impeded", column: "Impediment", claimed_by: "agent:tmux-y" }),
        makeCard({ id: "card-in-progress", claimed_by: "agent:tmux-z" }),
        // Dep on a card that no longer exists → permanent "missing_dep" block,
        // distinct from a live non-Done sibling (dangling-depends-on-analyse.md).
        makeCard({ id: "card-missing-dep", depends_on: ["deleted-parent"] }),
        // Dep on a live, non-Done sibling → temporary "dependent" block.
        makeCard({ id: "card-live-parent", column: "Backlog" }),
        makeCard({ id: "card-dependent", depends_on: ["card-live-parent"] }),
        makeCard({ id: "card-ready" }),
      ],
    });

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    const stateOf = (cardId: string) =>
      document
        .querySelector(`[data-card-id="${cardId}"]`)
        ?.querySelector("[data-ready-state]")
        ?.getAttribute("data-ready-state");

    await waitFor(() => expect(stateOf("card-impeded")).toBe("impeded"));
    // A Done card no longer renders a "Completed" chip — it duplicates the ✅
    // Done badge, so CardItem suppresses it (kanban card 1fafd87c). The
    // guarantee under test survives: the terminal column must beat the stale
    // `agent:` claim, so this card must NOT read "in_progress". A regression to
    // the claim-derived state renders the chip and fails this assertion.
    expect(stateOf("card-done")).toBeUndefined();
    expect(
      document.querySelector('[data-card-id="card-done"] [data-testid="done-badge"]'),
    ).not.toBeNull();
    expect(stateOf("card-in-progress")).toBe("in_progress");
    expect(stateOf("card-missing-dep")).toBe("missing_dep");
    expect(stateOf("card-dependent")).toBe("dependent");
    expect(stateOf("card-ready")).toBe("ready");
  });

  it("prefers the backend's held_reason over the locally derived state", async () => {
    // The board must report the dispatcher's actual decision, not a local
    // re-derivation of its filters. The local copy could only ever agree by
    // luck — and because it was written by mirroring those filters, it
    // reproduced their blind spots faithfully instead of exposing them.
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN],
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        // Locally this looks like a plain "ready" card: no deps, no parent.
        // The backend says it is orphaned, and the backend decides.
        makeCard({
          id: "card-held",
          column: "Backlog",
          held_reason: "missing_parent",
          held_blocker: ["deleted-parent"],
          held_since: new Date(Date.now() - 3600_000).toISOString(),
        }),
        // No held_reason → the local fallback still answers.
        makeCard({ id: "card-untick", column: "Backlog" }),
      ],
    });

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    const stateOf = (cardId: string) =>
      document
        .querySelector(`[data-card-id="${cardId}"]`)
        ?.querySelector("[data-ready-state]")
        ?.getAttribute("data-ready-state");

    await waitFor(() => expect(stateOf("card-held")).toBe("missing_parent"));
    expect(stateOf("card-untick")).toBe("ready");
  });

  // Bug kaart 8b54be53…: the dispatcher holds a future-scheduled card out via
  // `held_reason='scheduled'` (dep_resolver.py:154-172), but the UI used to
  // map that reason back to `ready`, producing a green "No open dependencies"
  // badge right next to the date chip — two contradictory signals. Pin both:
  // the state IS scheduled, and the tooltip does not lie about dependencies.
  it("flags a future-scheduled Backlog card as 'scheduled', not 'ready'", async () => {
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN],
    });
    const future = new Date(Date.now() + 86_400_000).toISOString();
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        makeCard({
          id: "card-scheduled",
          column: "Backlog",
          scheduled_at: future,
          held_reason: "scheduled",
          held_since: new Date(Date.now() - 60_000).toISOString(),
        }),
      ],
    });

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    const stateOf = (cardId: string) =>
      document
        .querySelector(`[data-card-id="${cardId}"]`)
        ?.querySelector("[data-ready-state]");

    const badge = await waitFor(() => stateOf("card-scheduled"));
    expect(badge?.getAttribute("data-ready-state")).toBe("scheduled");
    expect(badge?.textContent).toBe("Scheduled");
    // "No open dependencies" is the `ready` tooltip — must not surface here.
    expect(badge?.getAttribute("title") ?? "").not.toContain("No open dependencies");
  });

  // Restgat van kaart 8b54be53…: dezelfde tegenstrijdigheid herhaalt zich in
  // de lokale fallback-ladder wanneer `held_reason` leeg is — typisch als
  // auto-dispatch uit of gepauzeerd staat, of in het venster tussen aanmaken
  // en de eerstvolgende tick. Zonder `held_reason` moet de fallback zelf op
  // `scheduled_at` letten, anders valt de kaart door naar `ready` met groene
  // "No open dependencies"-badge naast de ⌛-chip.
  it("flags a future-scheduled Backlog card as 'scheduled' even when the dispatcher has not yet ticked (held_reason === null)", async () => {
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN],
    });
    const future = new Date(Date.now() + 86_400_000).toISOString();
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        makeCard({
          id: "card-scheduled-unticked",
          column: "Backlog",
          scheduled_at: future,
          // held_reason is null — the dispatcher hasn't classified this
          // card yet, so the local fallback is what the operator sees.
          held_reason: null,
        }),
      ],
    });

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    const stateOf = (cardId: string) =>
      document
        .querySelector(`[data-card-id="${cardId}"]`)
        ?.querySelector("[data-ready-state]");

    const badge = await waitFor(() => stateOf("card-scheduled-unticked"));
    expect(badge?.getAttribute("data-ready-state")).toBe("scheduled");
    expect(badge?.textContent).toBe("Scheduled");
    // Same tooltip-guard as the held_reason-bearing sibling: the `ready`
    // tooltip must not surface here.
    expect(badge?.getAttribute("title") ?? "").not.toContain("No open dependencies");
  });

  // Negative case: a past `scheduled_at` is meaningless — the wait already
  // cleared, so the card is dispatchable and must read as `ready`.
  it("does NOT flag a card with a past scheduled_at as 'scheduled'", async () => {
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN],
    });
    const past = new Date(Date.now() - 86_400_000).toISOString();
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        makeCard({
          id: "card-scheduled-past",
          column: "Backlog",
          scheduled_at: past,
          held_reason: null,
        }),
      ],
    });

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    const stateOf = (cardId: string) =>
      document
        .querySelector(`[data-card-id="${cardId}"]`)
        ?.querySelector("[data-ready-state]")
        ?.getAttribute("data-ready-state");

    await waitFor(() => expect(stateOf("card-scheduled-past")).toBe("ready"));
  });

  // kanban-pro-analyse.md §4.1: the dispatcher holds two additional filters
  // the UI didn't mirror — child cards awaiting the analyst's plan_ref
  // delivery, and operator-set `metadata.gated_on` business gates. Both used
  // to read as green "Ready" while the dispatcher silently skipped them.
  it("flags a child card without a plan_ref deliverable as 'awaiting_plan_ref'", async () => {
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN],
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        makeCard({ id: "parent-card", column: "Backlog" }),
        makeCard({
          id: "child-without-plan",
          parent_card_id: "parent-card",
          deliverables: [],
        }),
      ],
    });

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    const stateOf = (cardId: string) =>
      document
        .querySelector(`[data-card-id="${cardId}"]`)
        ?.querySelector("[data-ready-state]")
        ?.getAttribute("data-ready-state");

    await waitFor(() =>
      expect(stateOf("child-without-plan")).toBe("awaiting_plan_ref"),
    );
  });

  it("does NOT flag a child card that already has a plan_ref deliverable", async () => {
    // Negative case: a child with its plan_ref attached is dispatch-eligible
    // and must read as "ready" — pinning this guards against a regression
    // where the UI blanket-blocks every child card.
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN],
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        makeCard({
          id: "child-with-plan",
          parent_card_id: "parent-card",
          deliverables: [
            {
              id: "deliv-1",
              kind: "plan_ref",
              ref: "plan-deliverable-id",
              created_at: "2026-07-16T00:00:00Z",
            },
          ],
        }),
      ],
    });

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    const stateOf = (cardId: string) =>
      document
        .querySelector(`[data-card-id="${cardId}"]`)
        ?.querySelector("[data-ready-state]")
        ?.getAttribute("data-ready-state");

    await waitFor(() => expect(stateOf("child-with-plan")).toBe("ready"));
  });

  it("flags a card with a non-empty metadata.gated_on as 'gated'", async () => {
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN],
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        makeCard({
          id: "gated-card",
          metadata: { gated_on: "second-executor-provider-onboarded" },
        }),
      ],
    });

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    const stateOf = (cardId: string) =>
      document
        .querySelector(`[data-card-id="${cardId}"]`)
        ?.querySelector("[data-ready-state]")
        ?.getAttribute("data-ready-state");

    await waitFor(() => expect(stateOf("gated-card")).toBe("gated"));
  });

  it("does NOT flag a card with an empty metadata.gated_on as gated", async () => {
    // Gated state mirrors the backend's `bool(gated_on)` semantics — empty
    // string and missing key both mean "no gate", so the UI must NOT show
    // "Gated" by accident (otherwise the user setting ``gated_on: ""`` to
    // clear the gate would still flag the card as blocked).
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN],
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        makeCard({ id: "empty-gate", metadata: { gated_on: "" } }),
        makeCard({ id: "no-metadata" }),
      ],
    });

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    const stateOf = (cardId: string) =>
      document
        .querySelector(`[data-card-id="${cardId}"]`)
        ?.querySelector("[data-ready-state]")
        ?.getAttribute("data-ready-state");

    await waitFor(() => expect(stateOf("empty-gate")).toBe("ready"));
    expect(stateOf("no-metadata")).toBe("ready");
  });

  it("precedence: gated > missing_dep > awaiting_plan_ref > dependent > ready", async () => {
    // Same tier as missing_dep: a permanent, human-actionable block. Mirrors
    // the dispatcher's own filter ordering (gated is checked alongside
    // meets_dep_prerequisites in _next_card).
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN],
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        // gated AND missing_dep — gated wins (explicit human signal beats
        // accidental dangling-dep).
        makeCard({
          id: "gated-and-missing",
          depends_on: ["deleted-parent"],
          metadata: { gated_on: "trigger-x" },
        }),
        // gated AND awaiting_plan_ref — gated wins.
        makeCard({
          id: "gated-child-no-plan",
          parent_card_id: "parent-card",
          deliverables: [],
          metadata: { gated_on: "trigger-x" },
        }),
        // gated AND live-dep — gated wins.
        makeCard({
          id: "gated-and-dependent",
          depends_on: ["card-live-parent"],
          metadata: { gated_on: "trigger-x" },
        }),
        makeCard({ id: "card-live-parent", column: "Backlog" }),
      ],
    });

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    const stateOf = (cardId: string) =>
      document
        .querySelector(`[data-card-id="${cardId}"]`)
        ?.querySelector("[data-ready-state]")
        ?.getAttribute("data-ready-state");

    await waitFor(() => expect(stateOf("gated-and-missing")).toBe("gated"));
    expect(stateOf("gated-child-no-plan")).toBe("gated");
    expect(stateOf("gated-and-dependent")).toBe("gated");
  });
});

// kanban-pro-analyse.md §4.4 — problem 1: the palette finds a card but
// can't open it. Acceptance criterion 1 demands the drawer's existing
// `useEffect` on `searchParams` reacts to a *programmatic* navigation that
// happens while the board is already mounted, so the same navigate call
// works from any other tab.
describe("KanbanPage already-mounted ?card= deep link", () => {
  it("opens the drawer for a card already on the board after a same-mount navigate", async () => {
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN],
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [makeCard()],
    });

    const { getByTestId } = renderAt(
      "/kanban",
      <NavigateOnClick to="/kanban?card=card-1" testId="navigate-trigger" />,
    );
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    await act(async () => {
      fireEvent.click(getByTestId("navigate-trigger"));
    });

    expect(await screen.findByTestId("card-id-chip")).toBeInTheDocument();
    expect(kanbanApi.getCard).not.toHaveBeenCalled();
    expect(getByTestId("location").textContent).toBe("/kanban?card=card-1");
  });
});

// kanban-pro-analyse.md §4.4 — problem 2: the board had no filter at all.
// The input sits above the board, does not push to the URL, and silently
// leaves columns in place when filtered down to zero.
describe("KanbanPage board filter", () => {
  const DOING_COLUMN: import("./types").KanbanColumn = {
    ...BACKLOG_COLUMN,
    id: "col-doing",
    name: "Doing",
  };

  const visibleCardIds = () =>
    Array.from(document.querySelectorAll("[data-card-id]")).map(
      (el) => el.getAttribute("data-card-id") ?? "",
    );

  const columnHeaderTexts = () =>
    Array.from(document.querySelectorAll("div"))
      .filter((el) => /BACKLOG|DOING|IMPARTMENT|DONE|TODO/i.test(el.textContent ?? ""))
      .map((el) => el.textContent ?? "")
      .filter((t) => /^\s*[A-Z][a-z]+/i.test(t));

  it("reduces visible cards to title matches (case-insensitive substring)", async () => {
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN, DOING_COLUMN],
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        makeCard({ id: "card-zenith", title: "Zenith rollout", column: "Backlog" }),
        makeCard({ id: "card-other", title: "Other thing", column: "Backlog" }),
        makeCard({ id: "card-doing", title: "Doomed effort", column: "Doing" }),
      ],
    });

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    // Baseline: every card is rendered.
    expect(visibleCardIds().sort()).toEqual([
      "card-doing",
      "card-other",
      "card-zenith",
    ]);

    await act(async () => {
      fireEvent.change(screen.getByTestId("board-filter"), {
        target: { value: "zen" },
      });
    });

    // Only card-zenith matches "zen" (case-insensitive on title). The other
    // two are filtered out but the columns themselves stay rendered.
    expect(visibleCardIds().sort()).toEqual(["card-zenith"]);
    expect(screen.getByTestId("board-filter")).toBeInTheDocument();
  });

  it("reduces visible cards when the query matches a label but not the title", async () => {
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN],
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        makeCard({
          id: "card-with-label",
          title: "Plain title",
          column: "Backlog",
          labels: ["db-migration", "ops"],
        }),
        makeCard({
          id: "card-other-label",
          title: "Different title",
          column: "Backlog",
          labels: ["frontend"],
        }),
      ],
    });

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    await act(async () => {
      fireEvent.change(screen.getByTestId("board-filter"), {
        target: { value: "db-mig" },
      });
    });

    expect(visibleCardIds().sort()).toEqual(["card-with-label"]);
  });

  it("leaves all columns rendered (with zero visible cards) when the filter matches nothing", async () => {
    // Columns must NOT disappear — the board keeps its layout so the
    // structure doesn't jump while the operator types. Empty queries
    // show everything (existing behaviour).
    const columns = [
      BACKLOG_COLUMN,
      { ...BACKLOG_COLUMN, id: "col-doing", name: "Doing" },
      { ...BACKLOG_COLUMN, id: "col-done", name: "Done" },
    ];
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns,
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [makeCard({ id: "card-1", title: "Real card" })],
    });

    const { getByTestId } = renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    await act(async () => {
      fireEvent.change(screen.getByTestId("board-filter"), {
        target: { value: "zzz-no-match" },
      });
    });

    expect(visibleCardIds()).toEqual([]);
    // Column headers persist (Backlog, Doing, Done all rendered) — order
    // independent and whitespace-tolerant.
    const headers = columnHeaderTexts().join("|");
    expect(headers).toContain("Backlog");
    expect(headers).toContain("Doing");
    expect(headers).toContain("Done");
    expect(getByTestId("location").textContent).toBe("/kanban");
  });
});
// Kanban card 4f0677c7…: "Dispatch all (41)" over 18 visible Backlog cards.
// The counter counts unclaimed Backlog + To Resume cards; the board rendered
// only the columns with a `kanban_columns` row, so the 25 cards on `To Resume`
// were counted but drawn nowhere. The counter and the lanes must agree.
describe("KanbanPage toolbar counters vs. rendered lanes", () => {
  const visibleCardIds = () =>
    Array.from(document.querySelectorAll("[data-card-id]")).map(
      (el) => el.getAttribute("data-card-id") ?? "",
    );

  it("renders every card the Dispatch-all counter promises, incl. lane-less columns", async () => {
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN],
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        makeCard({ id: "card-backlog", column: "Backlog" }),
        makeCard({ id: "card-resume-1", column: "To Resume" }),
        makeCard({ id: "card-resume-2", column: "To Resume" }),
      ],
    });

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    expect(screen.getByText("Dispatch all (3)")).toBeInTheDocument();
    expect(visibleCardIds().sort()).toEqual([
      "card-backlog",
      "card-resume-1",
      "card-resume-2",
    ]);
    // …and the operator can see WHY that lane looks different from the rest.
    expect(
      screen.getByTestId("kanban-column-To Resume").getAttribute("data-unconfigured"),
    ).toBe("true");
  });
});
